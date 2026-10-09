"""Align a matn with its commentaries (شروح), paragraph by paragraph.

A commentary quotes the matn, usually in parentheses, with explanation in
between, often splitting one matn sentence: "(والذي ينقض) أي يبطل (الوضوء ستة
أشياء)". And a commentator may use a slightly different recension of the matn
(كفاية الأخيار quotes «خمسة أشياء» where this matn has «ستة أشياء»). So:

1. Candidates. For each matn paragraph, take its opening words (KEY_WORDS).
   Seed from the rarest of them in the commentary; around each seed, count
   how many key words appear in order with at most MAX_GAP words between
   them. A candidate needs at least MIN_RATIO of the key words.
2. Global order. The commentary follows the matn's order, so pick one
   candidate per paragraph such that positions increase with the paragraph
   order, maximising the total score (a weighted longest increasing
   subsequence). A stray match, e.g. «بسم الله» far ahead, can't derail the rest.

Matching is on normalized words (arabic.normalize): tashkeel, letter
variants and ال don't matter.

Pairs are configured in data/commentaries.json: {"<matn key>": ["<sharh key>", ...]}.
"""
import json
import re
from dataclasses import dataclass

from zawiyah_ulom.arabic import normalize
from zawiyah_ulom.config import DATA_DIR

COMMENTARIES_FILE = DATA_DIR / "commentaries.json"
KEY_WORDS = 6         # opening matn words used to place a paragraph
MIN_KEY = 3           # paragraphs with fewer usable words aren't placed on their own
MIN_RATIO = 0.6       # share of key words that must be found in order
MAX_GAP = 8           # commentary words allowed between two consecutive matched key words
MAX_CANDIDATES = 12   # best candidates kept per paragraph
_WORD = re.compile(r"\S+")


def load_pairs() -> dict[str, list[str]]:
    if not COMMENTARIES_FILE.exists():
        return {}
    return json.loads(COMMENTARIES_FILE.read_text(encoding="utf-8"))


def save_pairs(pairs: dict[str, list[str]]) -> None:
    """Write data/commentaries.json (study sets), dropping empty sets."""
    pairs = {m: list(dict.fromkeys(s)) for m, s in pairs.items() if s}
    COMMENTARIES_FILE.write_text(json.dumps(pairs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


@dataclass
class Word:
    norm: str      # normalized word
    section: int   # index of the section in reading order
    start: int     # character offset of the word in that section's content


def _norm(word: str) -> str:
    return re.sub(r"\W", "", normalize(word))


def words_of(sections: list[str]) -> list[Word]:
    """All words of a book (sections in reading order), normalized, with positions."""
    out = []
    for si, text in enumerate(sections):
        for m in _WORD.finditer(text):
            norm = _norm(m.group())
            if norm:
                out.append(Word(norm, si, m.start()))
    return out


def matn_paragraphs(sections: list[str]) -> list[tuple[int, int, str]]:
    """(section index, paragraph index, text) for every non-empty matn paragraph."""
    return [(si, pi, p) for si, text in enumerate(sections)
            for pi, p in enumerate(text.split("\n")) if p.strip()]


def _key_words(paragraph: str) -> list[str]:
    toks = [_norm(w) for w in _WORD.findall(paragraph)]
    toks = [t for t in toks if t and t != "فصل"]  # the matn's "فصل" marker isn't quoted reliably
    return toks[:KEY_WORDS] if len(toks) >= MIN_KEY else []


def _score_at(words: list[Word], key: list[str], seed_k: int, seed_pos: int) -> tuple[int, int]:
    """(matched key words, position of the earliest matched one) around a seed:
    key[seed_k] is at seed_pos; match the other key words in order, forward
    and backward, each within MAX_GAP words of the previous match. A key word
    that isn't found is skipped (recension differences)."""
    score, first = 1, seed_pos
    j = seed_pos
    for k in key[seed_k + 1:]:
        hit = next((x for x in range(j + 1, min(j + 2 + MAX_GAP, len(words))) if words[x].norm == k), None)
        if hit is not None:
            score, j = score + 1, hit
    j = seed_pos
    for k in reversed(key[:seed_k]):
        hit = next((x for x in range(j - 1, max(j - 2 - MAX_GAP, -1), -1) if words[x].norm == k), None)
        if hit is not None:
            score, j, first = score + 1, hit, hit
    return score, first


def _candidates(words: list[Word], index: dict[str, list[int]], key: list[str]) -> list[tuple[int, int]]:
    """(score, position) candidates for one paragraph, best first."""
    need = max(MIN_KEY, int(len(key) * MIN_RATIO + 0.999))
    # Seed from the two rarest key words present in the commentary.
    seeds = sorted((len(index.get(k, ())), i) for i, k in enumerate(key) if k in index)[:2]
    found: dict[int, int] = {}
    for _, ki in seeds:
        for pos in index[key[ki]]:
            score, first = _score_at(words, key, ki, pos)
            if score >= need and found.get(first, 0) < score:
                found[first] = score
    best = sorted(((sc, pos) for pos, sc in found.items()), key=lambda t: (-t[0], t[1]))
    return best[:MAX_CANDIDATES]


def align(matn_sections: list[str], sharh_sections: list[str]) -> list[Word | None]:
    """For each matn paragraph (in matn_paragraphs order), the commentary word where
    its explanation starts, or None if it couldn't be placed."""
    words = words_of(sharh_sections)
    index: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        index.setdefault(w.norm, []).append(i)
    paras = matn_paragraphs(matn_sections)
    cands = []
    for _, _, p in paras:
        key = _key_words(p)
        cands.append(_candidates(words, index, key) if key else [])

    # Weighted longest increasing subsequence over (paragraph order, position),
    # with a Fenwick tree over candidate positions holding (best total, state).
    positions = sorted({pos for cs in cands for _, pos in cs})
    rank = {p: i + 1 for i, p in enumerate(positions)}
    tree: list[tuple[int, tuple | None]] = [(0, None)] * (len(positions) + 1)

    def query(r: int) -> tuple[int, tuple | None]:  # best over ranks 1..r
        res = (0, None)
        while r > 0:
            if tree[r][0] > res[0]:
                res = tree[r]
            r -= r & -r
        return res

    def update(r: int, val: tuple[int, tuple]) -> None:
        while r <= len(positions):
            if val[0] > tree[r][0]:
                tree[r] = val
            r += r & -r

    best: dict[tuple, int] = {}
    back: dict[tuple, tuple | None] = {}
    for k, cs in enumerate(cands):
        new = []
        for c, (sc, pos) in enumerate(cs):
            prev_total, prev_state = query(rank[pos] - 1)  # strictly earlier position, earlier paragraph
            best[(k, c)] = prev_total + sc
            back[(k, c)] = prev_state
            new.append((rank[pos], (prev_total + sc, (k, c))))
        for r, val in new:  # committed after the paragraph: one candidate per paragraph
            update(r, val)

    anchors: list[Word | None] = [None] * len(paras)
    state = max(best, key=best.get) if best else None
    while state:
        k, c = state
        anchors[k] = words[cands[k][c][1]]
        state = back[state]
    return anchors
