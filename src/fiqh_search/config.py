"""Project paths and settings shared by every module."""
from pathlib import Path

# Repo root: src/fiqh_search/config.py -> ../../
ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"        # cached Shamela HTML (git-ignored)
BOOKS_DIR = DATA_DIR / "books"    # scraped books, one JSON per Shamela id (committed)
OUTLINES_DIR = DATA_DIR / "outlines"  # optional per-book chapter headings for flat TOCs (committed)
DB_PATH = DATA_DIR / "fiqh.db"    # SQLite database (git-ignored, rebuilt from BOOKS_DIR)
NOTES_PATH = DATA_DIR / "notes.db"  # personal study notes (git-ignored, NEVER rebuilt or exported)
BACKUP_DIR = DATA_DIR / "backups" # `fiqh backup-notes` snapshots (git-ignored)
DOCS_DIR = ROOT / "docs"          # static site output, served by GitHub Pages

# Shamela ids of the books in the corpus. Add new books here.
BOOK_IDS = [35120, 6140, 26535, 6125]

SHAMELA_BASE = "https://shamela.ws"
REQUEST_DELAY = 1.0  # seconds between live requests to Shamela; be polite
