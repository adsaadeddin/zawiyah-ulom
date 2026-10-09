"""«المكتبة»: add books from the app, from shamela.ws or ketabonline.com.

Flow: paste a link -> preview (scraper.preview) -> background download into
data/staging/<key>.json (one job at a time) -> quality report -> "add to
library" (move to data/books/ and load, without --fresh, so existing ids don't
change) or "discard". <key> is the Shamela id, or "ketabonline-<id>".

Jobs run in a thread of the Flask process and live in memory. If the app
restarts mid-download, the job is lost, but the page cache (data/raw/) makes
starting it again resume where it stopped.
"""
import json
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from zawiyah_ulom.config import BOOKS_DIR, STAGING_DIR
from zawiyah_ulom.tags import tags_for

# A Shamela link or bare id, or a ketabonline link (/ar/books/92681[/read?…]).
SHAMELA_REF = re.compile(r"^\s*(?:(?:https?://)?(?:www\.)?shamela\.ws/book/)?(\d{1,7})(?:[/?#]\S*)?\s*$")
KETABONLINE_REF = re.compile(
    r"^\s*(?:https?://)?(?:www\.)?ketabonline\.com/(?:[a-z]{2}/)?books/(\d{1,8})(?:[/?#]\S*)?\s*$")
KEY_RE = re.compile(r"^(?:\d{1,7}|ketabonline-\d{1,8})$")  # file keys accepted from forms
SAMPLE_SECTIONS = 3


class LibraryError(ValueError):
    """User-facing error (Arabic message)."""


def parse_book_ref(text: str) -> tuple[str, int]:
    """(source, id) from a Shamela link or bare number, or a ketabonline link."""
    if m := SHAMELA_REF.match(text or ""):
        return "shamela", int(m.group(1))
    if m := KETABONLINE_REF.match(text or ""):
        return "ketabonline", int(m.group(1))
    raise LibraryError("أدخل رابط كتاب من المكتبة الشاملة (https://shamela.ws/book/9973) أو من "
                       "جامع الكتب الإسلامية (https://ketabonline.com/ar/books/92681)، أو رقم كتاب الشاملة")


def book_key(source: str, book_id: int) -> str:
    return str(book_id) if source == "shamela" else f"{source}-{book_id}"


def _check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise LibraryError("مفتاح كتاب غير صالح")
    return key


def in_library(key: str) -> bool:
    return (BOOKS_DIR / f"{_check_key(key)}.json").exists()


# ------------------------------------------------------------- download job

@dataclass
class Job:
    source: str
    book_id: int
    key: str
    title: str
    state: str = "running"  # running | done | failed
    pages_read: int = 0
    total_pages: int = 0
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def as_dict(self) -> dict:
        pct = round(100 * self.pages_read / self.total_pages) if self.total_pages else 0
        return {**self.__dict__, "percent": min(pct, 100)}


_job: Job | None = None
_lock = threading.Lock()


def current_job() -> Job | None:
    return _job


def start_download(source: str, book_id: int, title: str) -> Job:
    """Scrape a book into data/staging/ in a background thread."""
    global _job
    from zawiyah_ulom.scraper import get_source, scrape  # heavy imports only when needed

    src = get_source(source, book_id)
    with _lock:
        if _job and _job.state == "running":
            raise LibraryError(f"يجري تنزيل «{_job.title}» الآن. انتظر حتى ينتهي.")
        if in_library(src.key):
            raise LibraryError("هذا الكتاب موجود في المكتبة بالفعل")
        job = _job = Job(source=source, book_id=book_id, key=src.key, title=title)

    def progress(done: int, total: int) -> None:
        job.pages_read, job.total_pages = done, total

    def run() -> None:
        try:
            STAGING_DIR.mkdir(parents=True, exist_ok=True)
            scrape(src, out_dir=STAGING_DIR, progress=progress)
            job.state = "done"
        except Exception as exc:  # shown on the page; the cache keeps what was fetched
            job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    threading.Thread(target=run, name=f"scrape-{src.key}", daemon=True).start()
    return job


# ---------------------------------------------------------- staged books

def staged_books() -> list[dict]:
    """Downloaded books awaiting a decision, each with its quality report."""
    out = []
    for path in sorted(STAGING_DIR.glob("*.json")) if STAGING_DIR.exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append({"key": path.stem, "title": data["title"], "url": data["url"],
                    "author": data["author"]["name"], "report": quality_report(data)})
    return out


def quality_report(data: dict) -> dict:
    """The checks done by hand when adding a book, as data for the page."""
    chapters = data["chapters"]
    sections = [s for ch in chapters for s in ch["sections"]]
    unlocated = [{"title": s["title"], "page": s["page"]} for s in sections if not s.get("located")]
    stray = [s["title"] for s in sections
             if re.search(r"[\[(•*=\-]\s*$", s["content"]) or re.match(r"\s*[\])•=\-]", s["content"])]
    single = sum(len(ch["sections"]) == 1 for ch in chapters)
    return {
        "chapters": len(chapters),
        "sections": len(sections),
        "located": len(sections) - len(unlocated),
        "unlocated": unlocated,
        "untagged": [ch["title"] for ch in chapters if not tags_for(ch["title"])],
        "stray_markers": stray,
        "empty": [s["title"] for s in sections if not s["content"].strip()],
        # Flat TOC: most chapters hold a single فصل. Needs data/outlines/<id>.json.
        "looks_flat": len(chapters) > 15 and single / len(chapters) > 0.8,
        "characters": sum(len(s["content"]) for s in sections),
        "samples": [{"title": s["title"], "start": s["content"][:120], "end": s["content"][-80:]}
                    for s in sections[:SAMPLE_SECTIONS]],
    }


def accept(key: str) -> None:
    """Move a staged book into the library and load it. Not a --fresh load:
    the new book is appended, so existing ids (and note links) don't change."""
    from zawiyah_ulom.loader import load_books

    staged = STAGING_DIR / f"{_check_key(key)}.json"
    if not staged.exists():
        raise LibraryError("لا يوجد كتاب منزَّل بهذا المفتاح")
    if in_library(key):
        raise LibraryError("هذا الكتاب موجود في المكتبة بالفعل")
    target = BOOKS_DIR / f"{key}.json"
    shutil.move(staged, target)
    try:
        load_books([target])
    except Exception:
        shutil.move(target, staged)  # keep library and DB consistent
        raise


def discard(key: str) -> None:
    (STAGING_DIR / f"{_check_key(key)}.json").unlink(missing_ok=True)


# --------------------------------------------------------------- library

def library_books(conn: sqlite3.Connection) -> list[dict]:
    """Books in the library with counts. `conn` must have notes attached (schema notes)."""
    return [dict(r) for r in conn.execute("""
        SELECT b.id, b.title, s.id AS author_id, s.name AS author, s.year_death_hijri,
               u.url AS source_url,
               (SELECT COUNT(*) FROM Chapter c WHERE c.book_id = b.id) AS chapters,
               (SELECT COUNT(*) FROM Section x JOIN Chapter c ON c.id = x.chapter_id
                WHERE c.book_id = b.id) AS sections,
               (SELECT COUNT(*) FROM notes.Note n JOIN Section x ON x.source_key = n.section_key
                JOIN Chapter c ON c.id = x.chapter_id WHERE c.book_id = b.id)
             + (SELECT COUNT(*) FROM notes.Annotation a JOIN Section x ON x.source_key = a.section_key
                JOIN Chapter c ON c.id = x.chapter_id WHERE c.book_id = b.id) AS notes
        FROM Book b
        LEFT JOIN Scholar s ON s.id = b.author_id
        LEFT JOIN URL u ON u.entity_type = 'book' AND u.entity_id = b.id
        ORDER BY s.year_death_hijri, b.title
    """)]
