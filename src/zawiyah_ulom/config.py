"""Project paths and settings shared by every module."""
from pathlib import Path

# Repo root: src/zawiyah_ulom/config.py -> ../../
ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"        # cached Shamela HTML (git-ignored)
BOOKS_DIR = DATA_DIR / "books"    # scraped books, one JSON per Shamela id (committed)
OUTLINES_DIR = DATA_DIR / "outlines"  # optional per-book chapter headings for flat TOCs (committed)
DB_PATH = DATA_DIR / "fiqh.db"    # SQLite database (git-ignored, rebuilt from BOOKS_DIR)
NOTES_PATH = DATA_DIR / "notes.db"  # personal study notes (git-ignored, NEVER rebuilt or exported)
BACKUP_DIR = DATA_DIR / "backups" # `zawiyah backup-notes` snapshots (git-ignored)
DOCS_DIR = ROOT / "docs"          # static site output, served by GitHub Pages

STAGING_DIR = DATA_DIR / "staging"   # downloaded books awaiting "add to library" (git-ignored)


def book_refs() -> list[tuple[str, int]]:
    """(source, id) of every book in the library: one data/books/<key>.json each,
    where key is the Shamela id ("35120") or "<source>-<id>" ("ketabonline-92681").
    Books are added on the «المكتبة» page (or `zawiyah scrape <link>`)."""
    refs = []
    for p in sorted(BOOKS_DIR.glob("*.json")):
        source, _, num = p.stem.rpartition("-")
        if num.isdigit():
            refs.append((source or "shamela", int(num)))
    return refs

SHAMELA_BASE = "https://shamela.ws"
REQUEST_DELAY = 1.0  # seconds between live requests to Shamela; be polite
