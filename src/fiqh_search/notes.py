"""Personal study notes: one note per section, stored in data/notes.db.

Notes live in their own SQLite file, separate from data/fiqh.db, because
fiqh.db is disposable: `fiqh build` deletes and rebuilds it, and section ids
change. A note is linked to a section by Section.source_key
("<shamela_id>:<page>:<title>"), which survives rebuilds. A note whose key no
longer matches any section (e.g. after a scraper change) is never deleted:
list_notes() returns it as unlinked so it can be reattached.

notes.db is git-ignored and never exported to the static site. Back it up
with `fiqh backup-notes`.

Usage: attach_notes(conn) on a fiqh.db connection, then call the functions
below with that connection. The notes tables are under the schema `notes`.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from fiqh_search.arabic import normalize, tokens
from fiqh_search.config import BACKUP_DIR, NOTES_PATH

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
"""


def attach_notes(conn: sqlite3.Connection, path: Path = NOTES_PATH) -> None:
    """Attach notes.db to `conn` as schema `notes`, creating it if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn.execute("ATTACH DATABASE ? AS notes", (str(path),))
    conn.executescript(NOTES_SCHEMA)


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
