"""Personal study notes, stored in data/notes.db. Two kinds:

  Note        one general note per section (the box under the text)
  Annotation  any number of notes attached to a passage the user selected in
              the text, each with a kind (فائدة، سؤال، إشكال، للتدريس)

Notes live in their own SQLite file, separate from data/fiqh.db, because
`zawiyah build` empties and reloads the book tables there, and section ids
change. A note is linked to a section by Section.source_key
("<shamela_id>:<page>:<title>"), which survives rebuilds. A note whose key no
longer matches any section (e.g. after a scraper change) is never deleted:
list_notes() returns it as unlinked so it can be reattached.

An annotation is anchored by the selected text itself (quote) plus up to
ANCHOR_CONTEXT characters before and after it, and the paragraph it was in, the
way web annotation tools do. anchor_annotations() finds each one again in the
current text (exactly, then ignoring tashkeel), so annotations survive rebuilds
and small text changes. One that can't be found is never dropped: it is
returned as unanchored and shown under the section with its quote.

notes.db is git-ignored and never exported to the static site. Back it up
with `zawiyah backup`.

Usage: attach_notes(conn) on a fiqh.db connection, then call the functions
below with that connection. The notes tables are under the schema `notes`.
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from zawiyah_ulom.arabic import fuzzy_pattern, has_arabic, normalize, tokens
from zawiyah_ulom.config import BACKUP_DIR, NOTES_PATH

NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes.Note (
    id          INTEGER PRIMARY KEY,
    section_key TEXT NOT NULL UNIQUE,   -- Section.source_key in fiqh.db
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Normalized note text for search (same rules as the books, see arabic.py).
CREATE VIRTUAL TABLE IF NOT EXISTS notes.NoteFTS USING fts5(
    body,
    section_key UNINDEXED,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS notes.Annotation (
    id          INTEGER PRIMARY KEY,
    section_key TEXT NOT NULL,          -- Section.source_key in fiqh.db
    kind        TEXT NOT NULL CHECK (kind IN ('faida', 'question', 'ishkal', 'teaching')),
    quote       TEXT NOT NULL,          -- the selected text, as displayed
    prefix      TEXT NOT NULL DEFAULT '',  -- context before / after the quote,
    suffix      TEXT NOT NULL DEFAULT '',  -- to pick the right one of repeated phrases
    paragraph   INTEGER,                -- paragraph index when created (a hint only)
    scope       TEXT NOT NULL DEFAULT 'passage',  -- 'passage' (highlighted) or 'paragraph' (whole paragraph)
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS notes.idx_annotation_section ON Annotation(section_key);

CREATE VIRTUAL TABLE IF NOT EXISTS notes.AnnotationFTS USING fts5(
    body,
    quote,
    annotation_id UNINDEXED,
    tokenize = 'unicode61'
);
"""


# Columns added after notes.db was first created: (table, column, declaration).
NOTES_MIGRATIONS = [
    ("Annotation", "scope", "TEXT NOT NULL DEFAULT 'passage'"),
]


def attach_notes(conn: sqlite3.Connection, path: Path = NOTES_PATH) -> None:
    """Attach notes.db to `conn` as schema `notes`, creating it if needed and
    adding any columns introduced since it was created (existing notes untouched)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn.execute("ATTACH DATABASE ? AS notes", (str(path),))
    conn.executescript(NOTES_SCHEMA)
    for table, column, decl in NOTES_MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA notes.table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE notes.{table} ADD COLUMN {column} {decl}")
            conn.commit()


def get_note(conn: sqlite3.Connection, section_key: str) -> dict | None:
    row = conn.execute(
        "SELECT body, created_at, updated_at FROM notes.Note WHERE section_key = ?",
        (section_key,),
    ).fetchone()
    return dict(row) if row else None


def save_note(conn: sqlite3.Connection, section_key: str, body: str) -> None:
    """Create or replace a section's note. An empty body deletes the note."""
    body = body.replace("\r\n", "\n").strip()
    with conn:
        conn.execute("DELETE FROM notes.NoteFTS WHERE section_key = ?", (section_key,))
        if not body:
            conn.execute("DELETE FROM notes.Note WHERE section_key = ?", (section_key,))
            return
        conn.execute(
            """INSERT INTO notes.Note (section_key, body) VALUES (?, ?)
               ON CONFLICT(section_key) DO UPDATE
               SET body = excluded.body, updated_at = datetime('now', 'localtime')""",
            (section_key, body),
        )
        conn.execute("INSERT INTO notes.NoteFTS (body, section_key) VALUES (?, ?)",
                     (normalize(body), section_key))


def note_keys(conn: sqlite3.Connection) -> set[str]:
    """Section keys that have a note (for marking sections in a table of contents)."""
    return {r[0] for r in conn.execute("SELECT section_key FROM notes.Note")}


_NOTE_WITH_SECTION = """
    SELECT n.section_key, n.body, n.created_at, n.updated_at,
           s.id AS section_id, s.name AS section_name,
           c.id AS chapter_id, c.name AS chapter_name,
           b.id AS book_id, b.title AS book_title
    FROM notes.Note n
    LEFT JOIN Section s ON s.source_key = n.section_key
    LEFT JOIN Chapter c ON c.id = s.chapter_id
    LEFT JOIN Book b    ON b.id = c.book_id
"""


def list_notes(conn: sqlite3.Connection) -> list[dict]:
    """All notes in reading order (book, chapter, section). Unlinked notes, with
    section_id None, come last."""
    rows = conn.execute(_NOTE_WITH_SECTION + """
        ORDER BY s.id IS NULL, b.id, c.chapter_order, s.section_order, n.section_key
    """).fetchall()
    return [dict(r) for r in rows]


def search_notes(conn: sqlite3.Connection, query: str, tag: str | None = None,
                 limit: int = 50) -> list[dict]:
    """Notes containing every word of `query` (normalized). Optional tag filter
    by the note's chapter, as in search.search()."""
    match = " ".join(f'"{w}"' for w in tokens(query))
    if not match:
        return []
    tag_filter = """AND c.id IN (SELECT ct.chapter_id FROM ChapterTag ct
                                 JOIN Tag t ON t.id = ct.tag_id WHERE t.name = :tag)"""
    rows = conn.execute(_NOTE_WITH_SECTION + f"""
        WHERE n.section_key IN (SELECT section_key FROM notes.NoteFTS WHERE NoteFTS MATCH :match)
        {tag_filter if tag else ""}
        ORDER BY n.updated_at DESC
        LIMIT :limit
    """, {"match": match, "tag": tag, "limit": limit}).fetchall()
    return [dict(r) for r in rows]


def backup_notes(path: Path = NOTES_PATH, backup_dir: Path = BACKUP_DIR) -> Path | None:
    """Copy notes.db to data/backups/notes-<timestamp>.db with SQLite's online
    backup API (safe while the app is running). Returns the backup path, or
    None if there are no notes yet."""
    return backup_sqlite(path, backup_dir, "notes")


def backup_sqlite(path: Path, backup_dir: Path, prefix: str) -> Path | None:
    """Copy a SQLite file to <backup_dir>/<prefix>-<timestamp>.db with the
    online backup API (safe while the app is running). None if it doesn't exist."""
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(path), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return target


# ---------------------------------------------------------- annotations

# kind -> Arabic label; order is the display order. The CSS class is "kind-<kind>".
KINDS: dict[str, str] = {
    "faida": "فائدة",
    "question": "سؤال",
    "ishkal": "إشكال",
    "teaching": "للتدريس",
}
ANCHOR_CONTEXT = 32   # characters of context stored before and after a quote
MAX_QUOTE = 2000


class AnnotationError(ValueError):
    """User-facing validation error (Arabic message)."""


def _clean(text: str) -> str:
    return (text or "").replace("\r\n", "\n").strip()


def _index_annotation(conn: sqlite3.Connection, ann_id: int, body: str, quote: str) -> None:
    conn.execute("DELETE FROM notes.AnnotationFTS WHERE annotation_id = ?", (ann_id,))
    conn.execute("INSERT INTO notes.AnnotationFTS (body, quote, annotation_id) VALUES (?, ?, ?)",
                 (normalize(body), normalize(quote), ann_id))


def add_annotation(conn: sqlite3.Connection, section_key: str, *, quote: str, body: str,
                   kind: str, prefix: str = "", suffix: str = "", paragraph: int | None = None,
                   scope: str = "passage") -> int:
    """scope 'passage': a note on selected words (highlighted in the text).
    scope 'paragraph': a note on a whole paragraph (study view's notes column);
    quote is the paragraph's text, used to find it again; nothing is highlighted."""
    if scope not in ("passage", "paragraph"):
        raise AnnotationError("نطاق الملاحظة غير معروف")
    quote, body = (quote or "").strip(), _clean(body)
    if not quote:
        raise AnnotationError("حدد نصًا في الفصل أولًا")
    if len(quote) > MAX_QUOTE:
        raise AnnotationError("النص المحدد طويل جدًا؛ حدد جملة أو فقرة")
    if not body:
        raise AnnotationError("اكتب الملاحظة")
    if kind not in KINDS:
        raise AnnotationError("نوع الملاحظة غير معروف")
    with conn:
        ann_id = conn.execute(
            """INSERT INTO notes.Annotation (section_key, kind, quote, prefix, suffix, paragraph, scope, body)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (section_key, kind, quote, (prefix or "")[-ANCHOR_CONTEXT:], (suffix or "")[:ANCHOR_CONTEXT],
             paragraph, scope, body)).lastrowid
        _index_annotation(conn, ann_id, body, quote)
    return ann_id


def get_annotation(conn: sqlite3.Connection, ann_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM notes.Annotation WHERE id = ?", (ann_id,)).fetchone()
    return dict(row) if row else None


def update_annotation(conn: sqlite3.Connection, ann_id: int, *, body: str, kind: str) -> None:
    body = _clean(body)
    if not body:
        raise AnnotationError("اكتب الملاحظة (أو احذفها)")
    if kind not in KINDS:
        raise AnnotationError("نوع الملاحظة غير معروف")
    ann = get_annotation(conn, ann_id)
    if not ann:
        raise AnnotationError("الملاحظة غير موجودة")
    with conn:
        conn.execute("""UPDATE notes.Annotation SET body = ?, kind = ?,
                        updated_at = datetime('now', 'localtime') WHERE id = ?""", (body, kind, ann_id))
        _index_annotation(conn, ann_id, body, ann["quote"])


def delete_annotation(conn: sqlite3.Connection, ann_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM notes.AnnotationFTS WHERE annotation_id = ?", (ann_id,))
        conn.execute("DELETE FROM notes.Annotation WHERE id = ?", (ann_id,))


def annotations_for(conn: sqlite3.Connection, section_key: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM notes.Annotation WHERE section_key = ? ORDER BY paragraph, id", (section_key,))]


def _find_quote(par: str, ann: dict) -> tuple[int, int] | None:
    """(start, end) of the annotation's quote in one paragraph, or None.
    Exact match first; among repeats, the one whose surroundings best match
    the stored prefix/suffix. Then a match that ignores tashkeel, letter
    variants and punctuation between words (arabic.fuzzy_pattern)."""
    quote = ann["quote"]
    starts = [m.start() for m in re.finditer(re.escape(quote), par)]
    if starts:
        def context_score(i):
            before = par[max(0, i - len(ann["prefix"])):i]
            after = par[i + len(quote):i + len(quote) + len(ann["suffix"])]
            return (before == ann["prefix"]) + (after == ann["suffix"])
        best = max(starts, key=context_score)
        return best, best + len(quote)
    if has_arabic(quote):
        m = re.search(fuzzy_pattern(quote), par)
        if m:
            return m.start(), m.end()
    return None


def paragraph_of(paragraphs: list[str], ann: dict) -> int | None:
    """Index of the paragraph a whole-paragraph note belongs to: the hinted one if
    its text still matches the stored quote, otherwise the paragraph containing it."""
    quote = ann["quote"]
    hint = ann.get("paragraph")
    key = re.sub(r"\s+", " ", quote)[:120]
    def matches(par):
        return key in re.sub(r"\s+", " ", par) or (has_arabic(key) and re.search(fuzzy_pattern(key), par))
    if hint is not None and 0 <= hint < len(paragraphs) and matches(paragraphs[hint]):
        return hint
    return next((i for i, par in enumerate(paragraphs) if matches(par)), None)


def anchor_annotations(paragraphs: list[str], annotations: list[dict]):
    """Locate annotations in a section's paragraphs.

    Returns (segments, by_paragraph, unanchored):
      segments[i]      list of (text, [annotation ids]) covering paragraph i, so
                       overlapping annotations each keep their highlight
      by_paragraph[i]  the annotations anchored in paragraph i
      unanchored       annotations whose quote is no longer in the text
    """
    spans: dict[int, list[tuple[int, int, int]]] = {}
    by_paragraph: dict[int, list[dict]] = {}
    unanchored = []
    for ann in annotations:
        if ann.get("scope", "passage") == "paragraph":
            i = paragraph_of(paragraphs, ann)
            if i is None:
                unanchored.append(ann)
            else:
                by_paragraph.setdefault(i, []).append(ann)
            continue
        hint = ann.get("paragraph")
        order = ([hint] if hint is not None and 0 <= hint < len(paragraphs) else []) + \
                [i for i in range(len(paragraphs)) if i != hint]
        for i in order:
            hit = _find_quote(paragraphs[i], ann)
            if hit:
                spans.setdefault(i, []).append((hit[0], hit[1], ann["id"]))
                by_paragraph.setdefault(i, []).append(ann)
                break
        else:
            unanchored.append(ann)

    segments = []
    for i, par in enumerate(paragraphs):
        marks = spans.get(i, [])
        cuts = sorted({0, len(par), *[a for a, _, _ in marks], *[b for _, b, _ in marks]})
        segs = [(par[a:b], [aid for s, e, aid in marks if s <= a and b <= e])
                for a, b in zip(cuts, cuts[1:])]
        segments.append(segs or [(par, [])])
    return segments, by_paragraph, unanchored


_ANNOTATION_WITH_SECTION = """
    SELECT a.id, a.section_key, a.kind, a.quote, a.body, a.scope, a.created_at, a.updated_at,
           s.id AS section_id, s.name AS section_name,
           c.id AS chapter_id, c.name AS chapter_name,
           b.id AS book_id, b.title AS book_title
    FROM notes.Annotation a
    LEFT JOIN Section s ON s.source_key = a.section_key
    LEFT JOIN Chapter c ON c.id = s.chapter_id
    LEFT JOIN Book b    ON b.id = c.book_id
"""


def list_annotations(conn: sqlite3.Connection, kind: str | None = None) -> list[dict]:
    """All annotations in reading order, optionally of one kind. Those whose
    section no longer exists (section_id None) come last."""
    where = "WHERE a.kind = ?" if kind else ""
    return [dict(r) for r in conn.execute(_ANNOTATION_WITH_SECTION + f"""
        {where}
        ORDER BY s.id IS NULL, b.id, c.chapter_order, s.section_order, a.paragraph, a.id
    """, (kind,) if kind else ()).fetchall()]


def search_annotations(conn: sqlite3.Connection, query: str, tag: str | None = None,
                       limit: int = 50) -> list[dict]:
    """Annotations whose note or quoted passage contains every word of `query`."""
    match = " ".join(f'"{w}"' for w in tokens(query))
    if not match:
        return []
    tag_filter = """AND c.id IN (SELECT ct.chapter_id FROM ChapterTag ct
                                 JOIN Tag t ON t.id = ct.tag_id WHERE t.name = :tag)"""
    return [dict(r) for r in conn.execute(_ANNOTATION_WITH_SECTION + f"""
        WHERE a.id IN (SELECT annotation_id FROM notes.AnnotationFTS WHERE AnnotationFTS MATCH :match)
        {tag_filter if tag else ""}
        ORDER BY a.updated_at DESC
        LIMIT :limit
    """, {"match": match, "tag": tag, "limit": limit}).fetchall()]


def annotation_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """section_key -> number of annotations (for marking tables of contents)."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT section_key, COUNT(*) FROM notes.Annotation GROUP BY section_key")}


def annotated_slice(content: str, annotations: list[dict], lo: int = 0, hi: int | None = None) -> list[dict]:
    """The paragraphs of content[lo:hi], ready to display with highlights.

    Used where only part of a section is shown (the study view shows a matn
    paragraph, or a commentary passage between two anchors). Returns one dict
    per displayed paragraph:
      p         paragraph index within the section (what new notes record)
      offset    where the displayed text starts within that paragraph
      segments  [(text, [annotation ids])] covering the displayed text
      cards     annotations whose highlight starts in this displayed text
    Each annotation's card appears once, where its highlight starts."""
    hi = len(content) if hi is None else hi
    paragraphs = content.split("\n")
    segments, _, _ = anchor_annotations(paragraphs, annotations)
    by_id = {a["id"]: a for a in annotations}
    out, start = [], 0
    for i, par in enumerate(paragraphs):
        end = start + len(par)
        a, b = max(lo, start) - start, min(hi, end) - start
        if a < b and par[a:b].strip():
            segs, cards, pos, seen = [], [], 0, set()
            for text, ids in segments[i]:
                s0, s1 = pos, pos + len(text)
                pos = s1
                for aid in ids:  # where each highlight starts in this paragraph
                    if aid not in seen:
                        seen.add(aid)
                        if a <= s0 < b:
                            cards.append(by_id[aid])
                if s1 <= a or s0 >= b:  # segment entirely outside the slice
                    continue
                segs.append((text[max(a, s0) - s0:min(b, s1) - s0], ids))
            # trim the slice's outer whitespace, as the plain view does
            if segs:
                segs[0] = (segs[0][0].lstrip(), segs[0][1])
                segs[-1] = (segs[-1][0].rstrip(), segs[-1][1])
            out.append({"p": i, "offset": a + (len(par[a:b]) - len(par[a:b].lstrip())),
                        "segments": [s for s in segs if s[0]], "cards": cards})
        start = end + 1  # the "\n" between paragraphs
    return out
