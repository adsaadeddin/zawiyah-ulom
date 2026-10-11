"""Export the database as a static site (for GitHub Pages) into docs/.

  docs/index.html          search + tags + book list (search runs in the browser)
  docs/book/<id>.html      table of contents
  docs/section/<id>.html   reading view
  docs/data/search.json    normalized text index + normalization rules for assets/search.js
  docs/tarajem/…           read-only tarajem: list + one page per scholar
  docs/study/…             read-only قراءة مقارنة: per matn chapter, the commentaries side by side

Science tabs (الفقه، العقيدة، …) work in the browser: lists carry
data-sci="<slugs>" and assets/science.js hides what is outside the chosen
science (remembered in localStorage, or ?sci=<slug>). search.json carries
each book's sciences so search filters the same way.

Personal notes are never exported: the study pages are built with an empty,
in-memory notes database attached instead of data/notes.db.

Search text is normalized here with arabic.normalize(). The rules themselves
are exported too (arabic.rules_for_js()), and search.js applies them to the
query, so arabic.py stays the single source of truth for normalization.

CLI:  zawiyah export
"""
import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from zawiyah_ulom.arabic import normalize, rules_for_js
from zawiyah_ulom.config import DOCS_DIR as OUT
from zawiyah_ulom import sciences as sci
from zawiyah_ulom.db import get_connection

SITE_ASSETS = Path(__file__).parent  # templates/ + assets/ for the static site

env = Environment(loader=FileSystemLoader(SITE_ASSETS / "templates"),
                  autoescape=select_autoescape(["html"]))


def load(conn) -> dict:
    books = [dict(r) for r in conn.execute("""
        SELECT b.id, b.title, s.id AS author_id, s.name AS author, s.year_death_hijri, p.name AS publisher,
               (SELECT url FROM URL WHERE entity_type = 'book' AND entity_id = b.id
                ORDER BY is_primary DESC LIMIT 1) AS source_url
        FROM Book b LEFT JOIN Scholar s ON s.id = b.author_id
        LEFT JOIN Publisher p ON p.id = b.publisher_id
        ORDER BY s.year_death_hijri
    """)]
    tags_by_chapter = {}
    for r in conn.execute("""SELECT ct.chapter_id, t.name FROM ChapterTag ct
                             JOIN Tag t ON t.id = ct.tag_id ORDER BY t.name"""):
        tags_by_chapter.setdefault(r["chapter_id"], []).append(r["name"])

    book_sci = sci.book_sciences(conn)
    for b in books:
        b["sci"] = book_sci.get(b["id"], [sci.DEFAULT])
        b["chapters"] = [dict(r) for r in conn.execute(
            "SELECT id, name FROM Chapter WHERE book_id = ? ORDER BY chapter_order", (b["id"],))]
        for ch in b["chapters"]:
            ch["tags"] = tags_by_chapter.get(ch["id"], [])
            ch["sections"] = [dict(r) for r in conn.execute(
                "SELECT id, name, content FROM Section WHERE chapter_id = ? ORDER BY section_order",
                (ch["id"],))]
        b["section_count"] = sum(len(ch["sections"]) for ch in b["chapters"])

    tags = [dict(r) for r in conn.execute("""
        SELECT t.name, COUNT(*) AS chapter_count FROM Tag t
        JOIN ChapterTag ct ON ct.tag_id = t.id GROUP BY t.id ORDER BY chapter_count DESC, t.name
    """)]
    for t in tags:  # a tag belongs to the sciences of the books it tags
        ids = [r[0] for r in conn.execute("""SELECT DISTINCT c.book_id FROM ChapterTag ct
            JOIN Tag t ON t.id = ct.tag_id JOIN Chapter c ON c.id = ct.chapter_id WHERE t.name = ?""", (t["name"],))]
        t["sci"] = sorted({x for i in ids for x in book_sci.get(i, [sci.DEFAULT])})
    return {"books": books, "tags": tags}


def science_tabs(conn) -> list[dict]:
    """Sciences that have books, for the tabs in base.html."""
    return [s for s in sci.sciences(conn) if s["books"]]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def export(data: dict) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SITE_ASSETS / "assets", OUT / "assets")
    write(OUT / ".nojekyll", "")  # serve files as-is, no Jekyll processing

    index = {"normalize": rules_for_js(), "books": [], "chapters": [], "sections": []}
    n_sections = 0
    for b in data["books"]:
        index["books"].append({"id": b["id"], "title": b["title"], "sci": b["sci"]})
        flat = [(ch, s) for ch in b["chapters"] for s in ch["sections"]]
        for i, (ch, s) in enumerate(flat):
            write(OUT / "section" / f"{s['id']}.html", env.get_template("section.html").render(
                root="../", book=b, chapter=ch, s=s,
                prev=flat[i - 1][1] if i > 0 else None,
                next=flat[i + 1][1] if i + 1 < len(flat) else None))
            index["sections"].append({"id": s["id"], "c": ch["id"], "name": s["name"],
                                      "nn": normalize(s["name"]), "t": normalize(s["content"])})
        for ch in b["chapters"]:
            index["chapters"].append({"id": ch["id"], "b": b["id"], "name": ch["name"],
                                      "tags": ch["tags"], "n": len(ch["sections"])})
        write(OUT / "book" / f"{b['id']}.html",
              env.get_template("book.html").render(root="../", book=b))
        n_sections += len(flat)

    write(OUT / "index.html", env.get_template("index.html").render(root="", filters=True, **data))
    write(OUT / "data" / "search.json", json.dumps(index, ensure_ascii=False, separators=(",", ":")))

    size = (OUT / "data" / "search.json").stat().st_size / 1e6
    print(f"{len(data['books'])} books, {n_sections} sections -> {OUT} (search index {size:.1f} MB)")


def export_tarajem(conn) -> int:
    """docs/tarajem/index.html and docs/tarajem/<id>.html (read-only)."""
    from zawiyah_ulom import tarajem
    scholars = tarajem.list_scholars(conn)
    book_sci = sci.book_sciences(conn)
    for s in scholars:  # authors belong to their books' sciences; other scholars show in «الكل»
        s["sci"] = sorted({x for (bid,) in conn.execute("SELECT id FROM Book WHERE author_id = ?", (s["id"],))
                           for x in book_sci.get(bid, [sci.DEFAULT])})
    write(OUT / "tarajem" / "index.html", env.get_template("tarajem_list.html").render(root="../", filters=True, scholars=scholars))
    for row in scholars:
        s = tarajem.get_scholar(conn, row["id"])
        write(OUT / "tarajem" / f"{s['id']}.html", env.get_template("tarajem.html").render(root="../", s=s))
    return len(scholars)


def export_study(conn) -> int:
    """docs/study/index.html and docs/study/<matn id>/<chapter id>.html (read-only, no notes)."""
    from zawiyah_ulom import notes, study
    notes.attach_notes(conn, Path(":memory:"))  # an empty notes db: personal notes can't leak
    sets = study.study_sets(conn)
    book_sci = sci.book_sciences(conn)
    for st in sets:
        st["sci"] = book_sci.get(st["matn"]["id"], [sci.DEFAULT])
    pages = 0
    for st in sets:
        ids = [b["id"] for b in st["shuruh"]]
        first = study.study_chapter(conn, st["matn"]["id"], ids, None)
        st["first_chapter"] = first["chapter"]["id"]
        for ch in first["chapters"]:
            view = study.study_chapter(conn, st["matn"]["id"], ids, ch["id"])
            write(OUT / "study" / str(st["matn"]["id"]) / f"{ch['id']}.html",
                  env.get_template("study.html").render(root="../../", st=st, shuruh=st["shuruh"], view=view))
            pages += 1
    write(OUT / "study" / "index.html", env.get_template("study_index.html").render(root="../", filters=True, sets=sets))
    return pages


def export_site() -> None:
    """Export data/fiqh.db to docs/ (books, search, tarajem, study view)."""
    conn = get_connection()
    try:
        env.globals["sciences"] = science_tabs(conn)
        export(load(conn))
        n_scholars = export_tarajem(conn)
        n_study = export_study(conn)
        print(f"  + {n_scholars} tarajem pages, {n_study} study pages")
    finally:
        conn.close()
