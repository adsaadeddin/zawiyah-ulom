"""«قراءة مقارنة»: a matn read paragraph by paragraph next to its commentaries.

data/commentaries.json says which books comment on which matn (by file key).
align.align() places each matn paragraph in each commentary; this module
turns that into rows for one matn chapter: the paragraph, then for each
commentary the text from that paragraph's anchor up to the next placed one.
A paragraph that couldn't be placed in a commentary is shown as explained
with the previous one (its text is inside the previous block), so no
commentary text is ever skipped.

Alignments are cached in memory per (matn, commentary) and recomputed when
either book's text changes (e.g. after a rebuild).
"""
import sqlite3

from zawiyah_ulom import notes
from zawiyah_ulom.align import align, load_pairs, matn_paragraphs

_cache: dict[tuple, list] = {}


def book_key(url: str) -> str:
    """The data/books file key for a book URL (Shamela id, or ketabonline-<id>)."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return f"ketabonline-{tail}" if "ketabonline" in url else tail


def short_title(title: str) -> str:
    """A book's common short name, for column headings:
      'فتح القريب المجيب في شرح ألفاظ التقريب = …'        -> 'فتح القريب المجيب'
      'كفاية الأخيار في حل غاية الإختصار'                  -> 'كفاية الأخيار'
      'شرح المقدمة الحضرمية المسمى بشرى الكريم بشرح …'     -> 'بشرى الكريم'
    Cut at '=' or '('; prefer the name after المسمى; otherwise cut before 'في'."""
    from zawiyah_ulom.arabic import normalize

    words = title.split("=")[0].split("(")[0].split()
    norm = [normalize(w) for w in words]
    for marker in ("مسمي", "مسماه"):
        if marker in norm and norm.index(marker) + 1 < len(words):
            return " ".join(words[norm.index(marker) + 1:norm.index(marker) + 3])
    if "في" in words[1:]:
        cut = words.index("في", 1)
        if cut >= 2:
            return " ".join(words[:cut])
    return " ".join(words[:3]) if len(words) > 4 else " ".join(words)


def _books_by_key(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("""SELECT b.id, b.title, s.name AS author, u.url FROM Book b
                           JOIN URL u ON u.entity_type = 'book' AND u.entity_id = b.id
                           LEFT JOIN Scholar s ON s.id = b.author_id""").fetchall()
    return {book_key(r["url"]): {**dict(r), "short": short_title(r["title"])} for r in rows}


def study_sets(conn: sqlite3.Connection) -> list[dict]:
    """Configured matn books (present in the library) with their commentaries."""
    books = _books_by_key(conn)
    sets = []
    for matn_key, sharh_keys in load_pairs().items():
        if matn_key in books:
            shuruh = [books[k] for k in sharh_keys if k in books]
            if shuruh:
                sets.append({"matn": books[matn_key], "shuruh": shuruh})
    return sets


def _sections(conn: sqlite3.Connection, book_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT s.id, s.name, s.content, s.source_key, c.id AS chapter_id, c.name AS chapter_name
        FROM Section s JOIN Chapter c ON c.id = s.chapter_id
        WHERE c.book_id = ? ORDER BY c.chapter_order, s.section_order""", (book_id,))]


def _anchors(matn: list[dict], sharh: list[dict], ids: tuple[int, int]) -> list:
    fingerprint = (ids, len(matn), len(sharh), sum(len(s["content"]) for s in matn),
                   sum(len(s["content"]) for s in sharh))
    if fingerprint not in _cache:
        _cache[fingerprint] = align([s["content"] for s in matn], [s["content"] for s in sharh])
    return _cache[fingerprint]


def _chunk(sharh: list[dict], anchors: list, k: int) -> list[dict]:
    """Commentary text for matn paragraph k: from its anchor to the next placed
    anchor, as parts per commentary section [{section, text, lo, hi}]."""
    a = anchors[k]
    b = next((x for x in anchors[k + 1:] if x is not None), None)
    end_sec, end_off = (b.section, b.start) if b else (len(sharh) - 1, None)
    parts = []
    for si in range(a.section, end_sec + 1):
        text = sharh[si]["content"]
        lo = a.start if si == a.section else 0
        hi = end_off if si == end_sec else None
        piece = text[lo:hi].strip()
        if piece:
            parts.append({"section": sharh[si], "text": piece, "lo": lo, "hi": len(text) if hi is None else hi})
    return parts


def study_chapter(conn: sqlite3.Connection, matn_id: int, sharh_ids: list[int],
                  chapter_id: int | None) -> dict:
    """Rows for one chapter of a matn: [{number, text, matn_section, cols: [...]}]."""
    matn = _sections(conn, matn_id)
    chapters = []
    for s in matn:
        if not chapters or chapters[-1]["id"] != s["chapter_id"]:
            chapters.append({"id": s["chapter_id"], "name": s["chapter_name"]})
    chapter_id = chapter_id if any(c["id"] == chapter_id for c in chapters) else chapters[0]["id"]

    paras = matn_paragraphs([s["content"] for s in matn])
    shuruh = []
    for sid in sharh_ids:
        sharh = _sections(conn, sid)
        anchors = _anchors(matn, sharh, (matn_id, sid))
        placed = sum(a is not None for a in anchors)
        shuruh.append({"book_id": sid, "sections": sharh, "anchors": anchors,
                       "coverage": round(100 * placed / max(len(paras), 1))})

    ann_cache: dict[str, list[dict]] = {}

    def anns(section: dict) -> list[dict]:  # notes on one section (passage + paragraph scope)
        if section["source_key"] not in ann_cache:
            ann_cache[section["source_key"]] = notes.annotations_for(conn, section["source_key"])
        return ann_cache[section["source_key"]]

    def display(section: dict, lo: int, hi: int) -> list[dict]:
        passage = [a for a in anns(section) if a.get("scope", "passage") == "passage"]
        return notes.annotated_slice(section["content"], passage, lo, hi)

    rows, ann_kind = [], {}
    for k, (si, pi, text) in enumerate(paras):
        if matn[si]["chapter_id"] != chapter_id:
            continue
        section = matn[si]
        pars = section["content"].split("\n")
        start = sum(len(p) + 1 for p in pars[:pi])
        matn_display = display(section, start, start + len(pars[pi]))
        para_notes = [a for a in anns(section) if a.get("scope") == "paragraph"
                      and notes.paragraph_of(pars, a) == pi]
        cols, passage_notes = [], [c for d in matn_display for c in d["cards"]]
        for sh in shuruh:
            if sh["anchors"][k] is None:
                cols.append({"merged": True})
                continue
            parts = _chunk(sh["sections"], sh["anchors"], k)
            for part in parts:
                part["display"] = display(part["section"], part["lo"], part["hi"])
                passage_notes += [c for d in part["display"] for c in d["cards"]]
            cols.append({"merged": False, "parts": parts})
        for a in para_notes + passage_notes:
            ann_kind[a["id"]] = a["kind"]
        rows.append({"number": k + 1, "text": text, "matn_section": section, "matn_p": pi,
                     "matn_display": matn_display, "cols": cols,
                     "para_notes": para_notes, "passage_notes": passage_notes})

    i = next(n for n, c in enumerate(chapters) if c["id"] == chapter_id)
    return {"chapters": chapters, "chapter": chapters[i], "rows": rows, "ann_kind": ann_kind,
            "prev": chapters[i - 1] if i > 0 else None,
            "next": chapters[i + 1] if i + 1 < len(chapters) else None,
            "coverage": [sh["coverage"] for sh in shuruh]}
