"""Load scraped books (data/books/*.json) into SQLite.

Re-running is safe: a book already in the DB (matched by its Shamela URL)
is deleted and reloaded, so chapters/sections are never duplicated.

Usage:  python load_db.py [path/to/book.json ...]
"""
import json
import sqlite3
import sys
from pathlib import Path

from arabic import normalize
from database import get_connection, init_db
from tags import tag_chapters

BOOKS_DIR = Path(__file__).parent / "data" / "books"
SOURCE = "shamela"


def get_or_create_scholar(conn: sqlite3.Connection, author: dict) -> int:
    row = conn.execute("SELECT id FROM Scholar WHERE name = ?", (author["name"],)).fetchone()
    if row:
        scholar_id = row["id"]
    else:
        scholar_id = conn.execute(
            "INSERT INTO Scholar (name, full_arabic_name, year_death_hijri) VALUES (?, ?, ?)",
            (author["name"], author["full_arabic_name"], author["year_death_hijri"]),
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


def load_book(conn: sqlite3.Connection, data: dict) -> int:
    delete_existing_book(conn, data["url"])

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
                "INSERT INTO Section (chapter_id, name, content, section_order) "
                "VALUES (?, ?, ?, ?)",
                (chapter_id, sec["title"], sec["content"], sec_order),
            ).lastrowid
            index_section(conn, section_id, sec["title"], sec["content"])
            n_sections += 1

    print(f"  {data['title'][:60]}: {len(data['chapters'])} chapters, {n_sections} sections")
    return book_id


def main(paths: list[Path]) -> None:
    conn = get_connection()
    init_db(conn)
    with conn:  # one transaction for the whole load
        for path in paths:
            load_book(conn, json.loads(path.read_text(encoding="utf-8")))
        print(f"  tagged: {tag_chapters(conn)} chapter-tag links")
    conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    files = [Path(p) for p in sys.argv[1:]] or sorted(BOOKS_DIR.glob("*.json"))
    main(files)
