"""SQLite schema and connection helpers."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "fiqh.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS Scholar (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    full_arabic_name     TEXT,
    country              TEXT,
    bio                  TEXT,
    year_birth_hijri     INTEGER,
    year_birth_gregorian INTEGER,
    year_death_hijri     INTEGER,
    year_death_gregorian INTEGER,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Publisher (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    location   TEXT,
    website    TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Book (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    author_id    INTEGER REFERENCES Scholar(id),
    publisher_id INTEGER REFERENCES Publisher(id),
    language     TEXT NOT NULL DEFAULT 'ar',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Chapter (
    id            INTEGER PRIMARY KEY,
    book_id       INTEGER NOT NULL REFERENCES Book(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    chapter_order INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Section (
    id            INTEGER PRIMARY KEY,
    chapter_id    INTEGER NOT NULL REFERENCES Chapter(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    content       TEXT NOT NULL,
    section_order INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Tag (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ChapterTag (
    chapter_id INTEGER NOT NULL REFERENCES Chapter(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES Tag(id) ON DELETE CASCADE,
    PRIMARY KEY (chapter_id, tag_id)
);

CREATE TABLE IF NOT EXISTS URL (
    id          INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('book', 'scholar_bio')),
    entity_id   INTEGER NOT NULL,
    url         TEXT NOT NULL,
    source      TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entity_type, url)
);

CREATE INDEX IF NOT EXISTS idx_chapter_book   ON Chapter(book_id, chapter_order);
CREATE INDEX IF NOT EXISTS idx_section_chapter ON Section(chapter_id, section_order);
CREATE INDEX IF NOT EXISTS idx_url_entity     ON URL(entity_type, entity_id);

-- Full-text index over normalized (diacritic-free) text. It is filled by
-- index_section() rather than triggers because normalization runs in Python.
CREATE VIRTUAL TABLE IF NOT EXISTS SectionFTS USING fts5(
    name,
    content,
    section_id UNINDEXED,
    tokenize = 'unicode61'
);

-- Keep the index in sync when sections are deleted (e.g. on book reload).
CREATE TRIGGER IF NOT EXISTS section_fts_delete AFTER DELETE ON Section BEGIN
    DELETE FROM SectionFTS WHERE section_id = old.id;
END;
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


if __name__ == "__main__":
    with get_connection() as c:
        init_db(c)
    print(f"Initialized {DB_PATH}")
