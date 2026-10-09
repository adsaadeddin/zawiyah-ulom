"""Export the database as a static site (for GitHub Pages) into docs/.

  docs/index.html          search + tags + book list (search runs in the browser)
  docs/book/<id>.html      table of contents
  docs/section/<id>.html   reading view
  docs/data/search.json    normalized text index used by assets/search.js

Search text is normalized here with arabic.normalize(); assets/search.js
applies the same rules to the query, so results match the Flask app.

Usage:  python export_static.py
"""
import json
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from arabic import normalize
from database import get_connection

ROOT = Path(__file__).parent
OUT = ROOT / "docs"
SITE_ASSETS = ROOT / "site"  # templates + static assets for the static site

env = Environment(loader=FileSystemLoader(SITE_ASSETS / "templates"),
                  autoescape=select_autoescape(["html"]))


def load(conn) -> dict:
    books = [dict(r) for r in conn.execute("""
        SELECT b.id, b.title, s.name AS author, s.year_death_hijri, p.name AS publisher,
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

    for b in books:
        b["chapters"] = [dict(r) for r in conn.execute(
            "SELECT id, name FROM Chapter WHERE book_id = ? ORDER BY chapter_order", (b["id"],))]
        for ch in b["chapters"]:
            ch["tags"] = tags_by_chapter.get(ch["id"], [])
            ch["sections"] = [dict(r) for r in conn.execute(
                "SELECT id, name, content FROM Section WHERE chapter_id = ? ORDER BY section_order",
                (ch["id"],))]
        b["section_count"] = sum(len(ch["sections"]) for ch in b["chapters"])

    tags =[dict(r) for r in conn.execute("""
        SELECT t.name, COUNT(*) AS chapter_count FROM Tag t
        JOIN ChapterTag ct ON ct.tag_id = t.id GROUP BY t.id ORDER BY chapter_count DESC, t.name
    """)]
    return {"books": books, "tags": tags}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def export(data: dict) -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SITE_ASSETS / "assets", OUT / "assets")
    write(OUT / ".nojekyll", "")  # serve files as-is, no Jekyll processing

    index = {"books": [], "chapters": [], "sections": []}
    n_sections = 0
    for b in data["books"]:
        index["books"].append({"id": b["id"], "title": b["title"]})
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

    write(OUT / "index.html", env.get_template("index.html").render(root="", **data))
    write(OUT / "data" / "search.json", json.dumps(index, ensure_ascii=False, separators=(",", ":")))

    size = (OUT / "data" / "search.json").stat().st_size / 1e6
    print(f"{len(data['books'])} books, {n_sections} sections -> {OUT} (search index {size:.1f} MB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with get_connection() as conn:
        export(load(conn))
