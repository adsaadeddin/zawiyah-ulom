"""SQLite schema and connection helpers."""
import sqlite3
from pathlib import Path

from zawiyah_ulom.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS Scholar (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    full_arabic_name     TEXT,
    nickname             VARCHAR,          -- laqab/kunya, e.g. ابن قاسم
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
    -- Stable identity across rebuilds, "<shamela_id>:<page>:<title>" (see loader.section_keys).
    -- Personal notes (notes.py) link to this, never to id, which changes on reload.
    source_key    TEXT,
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

-- Sciences (فنون): each is a workspace in the app. structure: book | ayah | hadith
-- (ayah/hadith get their own navigation in later phases).
CREATE TABLE IF NOT EXISTS Science (
    slug       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    sort       INTEGER NOT NULL,
    structure  TEXT NOT NULL DEFAULT 'book'
);
INSERT OR IGNORE INTO Science (slug, name, sort, structure) VALUES
    ('fiqh', 'الفقه', 1, 'book'),
    ('aqeedah', 'العقيدة', 2, 'book'),
    ('tasawwuf', 'التصوف', 3, 'book'),
    ('ulum-quran', 'علوم القرآن', 4, 'book'),
    ('tafsir', 'تفاسير القرآن', 5, 'ayah');

-- Which sciences a book belongs to (several allowed). Keyed by the book's file
-- key (Shamela id or ketabonline-<id>), not Book.id, so it survives rebuilds
-- like notes do. A book with no row here counts as فقه.
CREATE TABLE IF NOT EXISTS BookScience (
    book_key TEXT NOT NULL,
    science  TEXT NOT NULL REFERENCES Science(slug),
    PRIMARY KEY (book_key, science)
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

-- Scholar chains (isnad of teaching). The ids are INTEGER to match
-- Scholar.id: SQLite compares TEXT '5' and INTEGER 5 as different values,
-- so VARCHAR ids would fail every foreign-key check.
CREATE TABLE IF NOT EXISTS TeacherStudent (
    teacher_id INTEGER NOT NULL REFERENCES Scholar(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES Scholar(id) ON DELETE CASCADE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (teacher_id, student_id),
    CHECK (teacher_id <> student_id)
);

CREATE INDEX IF NOT EXISTS idx_chapter_book   ON Chapter(book_id, chapter_order);
-- The primary key already serves lookups by teacher_id; this one serves "teachers of X".
CREATE INDEX IF NOT EXISTS idx_teacherstudent_student ON TeacherStudent(student_id);
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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS doesn't
# touch existing tables, so databases created earlier get them via ALTER TABLE.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("Scholar", "nickname", "VARCHAR"),
    ("Section", "source_key", "TEXT"),
]

# Indexes on migrated columns: created after MIGRATIONS, since on an old
# database the column doesn't exist yet while SCHEMA runs.
POST_MIGRATION = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_section_source_key ON Section(source_key);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.executescript(POST_MIGRATION)
    conn.commit()
