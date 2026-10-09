"""Load scraped books (data/books/*.json) into SQLite.

Re-running is safe: a book already in the DB (matched by its Shamela URL)
is deleted and reloaded, so chapters/sections are never duplicated.
Use fresh=True (CLI: --fresh) to empty the book tables first, so that IDs,
and therefore static-site URLs, are stable. Scholars (the tarajem you enter
on the /tarajem page) live in the same database and are never deleted by a
load.

CLI:  zawiyah load [--fresh] [path/to/book.json ...]
"""
import json
import sqlite3
from pathlib import Path

from zawiyah_ulom.arabic import normalize
from zawiyah_ulom.config import BOOKS_DIR
from zawiyah_ulom.db import get_connection, init_db
from zawiyah_ulom.tags import tag_chapters

SOURCE = "shamela"


def _nickname(author: dict) -> str | None:
    """Optional author.nickname from book JSON; blank or missing -> None."""
    value = author.get("nickname")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"author.nickname must be a string, got {type(value).__name__}: {value!r}")
    return value.strip() or None


def get_or_create_scholar(conn: sqlite3.Connection, author: dict) -> int:
    """Find the book's author in Scholar, or create it.

    Matched by Shamela author URL first, then by name, so a scholar whose
    name you edited on the tarajem page is still found. An existing scholar's
    fields are never overwritten (they may hold your edits), except nickname,
    when the JSON provides one."""
    nickname = _nickname(author)
    row = None
    if author.get("url"):
        row = conn.execute(
            "SELECT entity_id AS id FROM URL WHERE entity_type = 'scholar_bio' AND url = ?",
            (author["url"],),
        ).fetchone()
    row = row or conn.execute("SELECT id FROM Scholar WHERE name = ?", (author["name"],)).fetchone()
    if row:
        scholar_id = row["id"]
        if nickname:  # JSON is the source of truth, but never erase with a blank
            conn.execute("UPDATE Scholar SET nickname = ? WHERE id = ?", (nickname, scholar_id))
    else:
        scholar_id = conn.execute(
            "INSERT INTO Scholar (name, full_arabic_name, nickname, year_death_hijri) VALUES (?, ?, ?, ?)",
            (author["name"], author["full_arabic_name"], nickname, author["year_death_hijri"]),
        ).lastrowid
    if author.get("url"):
        conn.execute(
            "INSERT OR IGNORE INTO URL (entity_type, entity_id, url, source, is_primary) "
            "VALUES ('scholar_bio', ?, ?, ?, 1)",
            (scholar_id, author["url"], SOURCE),
        )
    return scholar_id


def get_or_create_publisher(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    conn.execute("INSERT OR IGNORE INTO Publisher (name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM Publisher WHERE name = ?", (name,)).fetchone()["id"]


def delete_existing_book(conn: sqlite3.Connection, url: str) -> None:
    row = conn.execute(
        "SELECT entity_id FROM URL WHERE entity_type = 'book' AND url = ?", (url,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM Book WHERE id = ?", (row["entity_id"],))
        conn.execute("DELETE FROM URL WHERE entity_type = 'book' AND entity_id = ?",
                     (row["entity_id"],))


def index_section(conn: sqlite3.Connection, section_id: int, name: str, content: str) -> None:
    conn.execute(
        "INSERT INTO SectionFTS (name, content, section_id) VALUES (?, ?, ?)",
        (normalize(name), normalize(content), section_id),
    )


def section_keys(data: dict) -> list[str]:
    """Stable keys for a book's sections in reading order: "<book>:<page>:<title>",
    where <book> is the Shamela id for Shamela books (as always) and the file key
    (e.g. "ketabonline-92681") for other sources.

    They depend only on the book's TOC, not on load order or DB ids, so personal
    notes stay attached across `zawiyah build` and when books are added. A repeated
    (page, title) pair gets "#2", "#3", ... in order of appearance."""
    keys, seen = [], {}
    for ch in data["chapters"]:
        for sec in ch["sections"]:
            base = f"{data.get('key') or data['shamela_id']}:{sec['page']}:{sec['title']}"
            seen[base] = seen.get(base, 0) + 1
            keys.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return keys


def load_book(conn: sqlite3.Connection, data: dict) -> int:
    delete_existing_book(conn, data["url"])
    keys = iter(section_keys(data))

    book_id = conn.execute(
        "INSERT INTO Book (title, author_id, publisher_id, language) VALUES (?, ?, ?, 'ar')",
        (data["title"],
         get_or_create_scholar(conn, data["author"]),
         get_or_create_publisher(conn, data["publisher"])),
    ).lastrowid
    conn.execute(
        "INSERT INTO URL (entity_type, entity_id, url, source, is_primary) "
        "VALUES ('book', ?, ?, ?, 1)",
        (book_id, data["url"], SOURCE),
    )

    n_sections = 0
    for ch_order, ch in enumerate(data["chapters"], start=1):
        chapter_id = conn.execute(
            "INSERT INTO Chapter (book_id, name, chapter_order) VALUES (?, ?, ?)",
            (book_id, ch["title"], ch_order),
        ).lastrowid
        for sec_order, sec in enumerate(ch["sections"], start=1):
            section_id = conn.execute(
                "INSERT INTO Section (chapter_id, name, content, section_order, source_key) "
                "VALUES (?, ?, ?, ?, ?)",
                (chapter_id, sec["title"], sec["content"], sec_order, next(keys)),
            ).lastrowid
            index_section(conn, section_id, sec["title"], sec["content"])
            n_sections += 1

    print(f"  {data['title'][:60]}: {len(data['chapters'])} chapters, {n_sections} sections")
    return book_id


# Tables rebuilt from data/books/*.json. Everything else in fiqh.db (Scholar,
# TeacherStudent, Publisher, scholar_bio URLs) is curated data that a rebuild keeps.
CORPUS_TABLES = ["ChapterTag", "Tag", "Section", "Chapter", "Book"]


def reset_corpus(conn: sqlite3.Connection) -> None:
    """Empty the book tables, keeping scholars (tarajem) and their links.

    The tables have INTEGER PRIMARY KEYs without AUTOINCREMENT, so once empty
    their ids restart at 1: a fresh load gives the same ids, and the same
    static-site URLs, as a brand-new database."""
    for table in CORPUS_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM SectionFTS")
    conn.execute("DELETE FROM URL WHERE entity_type = 'book'")


def load_books(paths: list[Path] | None = None, fresh: bool = False) -> None:
    """Load book JSON files (default: every file in data/books/), then retag.
    fresh=True first empties the book tables (reset_corpus). Scholars are kept."""
    paths = paths or sorted(BOOKS_DIR.glob("*.json"))
    conn = get_connection()
    init_db(conn)
    with conn:  # one transaction for the whole load
        if fresh:
            reset_corpus(conn)
        for path in paths:
            load_book(conn, json.loads(path.read_text(encoding="utf-8")))
        print(f"  tagged: {tag_chapters(conn)} chapter-tag links")
    conn.close()
