"""Flask app: JSON API + a small server-rendered search UI.

JSON API
  GET /books                  all books
  GET /books/<id>             book with chapters
  GET /chapters/<id>          chapter with sections (no content)
  GET /sections/<id>          section with content
  GET /tags                   all tags with chapter counts
  GET /search?query=...       full-text search (optional &tag=... &limit=...)
  GET /search?tag=...         chapters with that tag

Web UI
  /                           search page + book list
  /read/book/<id>             table of contents
  /read/section/<id>          reading view

Run:  python app.py   (then open http://127.0.0.1:5000)
"""
import sqlite3

from flask import Flask, abort, g, jsonify, render_template, request

from database import get_connection
from search import search

app = Flask(__name__)
app.json.ensure_ascii = False  # return Arabic as-is, not \uXXXX escapes
app.json.sort_keys = False

MAX_LIMIT = 100


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = get_connection()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# --------------------------------------------------------------- queries

def get_books() -> list[dict]:
    rows = db().execute("""
        SELECT b.id, b.title, b.language,
               s.id AS author_id, s.name AS author, s.year_death_hijri,
               p.name AS publisher,
               (SELECT url FROM URL WHERE entity_type = 'book' AND entity_id = b.id
                ORDER BY is_primary DESC LIMIT 1) AS source_url,
               (SELECT COUNT(*) FROM Chapter WHERE book_id = b.id) AS chapter_count
        FROM Book b
        LEFT JOIN Scholar s   ON s.id = b.author_id
        LEFT JOIN Publisher p ON p.id = b.publisher_id
        ORDER BY s.year_death_hijri
    """).fetchall()
    return [dict(r) for r in rows]


def get_book(book_id: int) -> dict:
    book = next((b for b in get_books() if b["id"] == book_id), None)
    if not book:
        abort(404, description="book not found")
    chapters = db().execute("""
        SELECT c.id, c.name, c.chapter_order,
               (SELECT COUNT(*) FROM Section WHERE chapter_id = c.id) AS section_count
        FROM Chapter c WHERE c.book_id = ? ORDER BY c.chapter_order
    """, (book_id,)).fetchall()
    book["chapters"] = [{**dict(c), "tags": chapter_tags(c["id"])} for c in chapters]
    return book


def chapter_tags(chapter_id: int) -> list[str]:
    return [r["name"] for r in db().execute("""
        SELECT t.name FROM Tag t JOIN ChapterTag ct ON ct.tag_id = t.id
        WHERE ct.chapter_id = ? ORDER BY t.name
    """, (chapter_id,))]


def get_chapter(chapter_id: int) -> dict:
    row = db().execute("""
        SELECT c.id, c.name, c.description, c.chapter_order, b.id AS book_id, b.title AS book_title
        FROM Chapter c JOIN Book b ON b.id = c.book_id WHERE c.id = ?
    """, (chapter_id,)).fetchone()
    if not row:
        abort(404, description="chapter not found")
    chapter = dict(row)
    chapter["tags"] = chapter_tags(chapter_id)
    chapter["sections"] = [dict(r) for r in db().execute("""
        SELECT id, name, section_order, length(content) AS content_length
        FROM Section WHERE chapter_id = ? ORDER BY section_order
    """, (chapter_id,))]
    return chapter


def get_section(section_id: int) -> dict:
    row = db().execute("""
        SELECT s.id, s.name, s.description, s.content, s.section_order,
               c.id AS chapter_id, c.name AS chapter_name, c.chapter_order,
               b.id AS book_id, b.title AS book_title
        FROM Section s JOIN Chapter c ON c.id = s.chapter_id JOIN Book b ON b.id = c.book_id
        WHERE s.id = ?
    """, (section_id,)).fetchone()
    if not row:
        abort(404, description="section not found")
    section = dict(row)
    # Neighbours in reading order across chapter boundaries of the same book.
    order = (section["chapter_order"], section["section_order"])
    neighbour_sql = """
        SELECT s.id, s.name FROM Section s JOIN Chapter c ON c.id = s.chapter_id
        WHERE c.book_id = :book AND (c.chapter_order, s.section_order) {op} (:co, :so)
        ORDER BY c.chapter_order {dir}, s.section_order {dir} LIMIT 1
    """
    params = {"book": section["book_id"], "co": order[0], "so": order[1]}
    prev = db().execute(neighbour_sql.format(op="<", dir="DESC"), params).fetchone()
    nxt = db().execute(neighbour_sql.format(op=">", dir="ASC"), params).fetchone()
    section["previous"] = dict(prev) if prev else None
    section["next"] = dict(nxt) if nxt else None
    return section


def get_tags() -> list[dict]:
    return [dict(r) for r in db().execute("""
        SELECT t.name, COUNT(ct.chapter_id) AS chapter_count
        FROM Tag t LEFT JOIN ChapterTag ct ON ct.tag_id = t.id
        GROUP BY t.id ORDER BY chapter_count DESC, t.name
    """)]


def chapters_by_tag(tag: str) -> list[dict]:
    return [dict(r) for r in db().execute("""
        SELECT c.id AS chapter_id, c.name AS chapter_name, b.id AS book_id, b.title AS book_title,
               (SELECT COUNT(*) FROM Section WHERE chapter_id = c.id) AS section_count
        FROM Chapter c
        JOIN Book b ON b.id = c.book_id
        JOIN ChapterTag ct ON ct.chapter_id = c.id
        JOIN Tag t ON t.id = ct.tag_id
        WHERE t.name = ?
        ORDER BY b.id, c.chapter_order
    """, (tag,))]


def search_args() -> tuple[str, str | None, int]:
    query = request.args.get("query", "").strip()
    tag = request.args.get("tag", "").strip() or None
    limit = min(max(request.args.get("limit", 20, type=int), 1), MAX_LIMIT)
    return query, tag, limit


# --------------------------------------------------------------- JSON API

@app.get("/books")
def api_books():
    return jsonify(get_books())


@app.get("/books/<int:book_id>")
def api_book(book_id):
    return jsonify(get_book(book_id))


@app.get("/chapters/<int:chapter_id>")
def api_chapter(chapter_id):
    return jsonify(get_chapter(chapter_id))


@app.get("/sections/<int:section_id>")
def api_section(section_id):
    return jsonify(get_section(section_id))


@app.get("/tags")
def api_tags():
    return jsonify(get_tags())


@app.get("/search")
def api_search():
    query, tag, limit = search_args()
    if query:
        results = search(db(), query, tag=tag, limit=limit)
        return jsonify({"query": query, "tag": tag, "type": "sections",
                        "count": len(results), "results": results})
    if tag:
        results = chapters_by_tag(tag)
        return jsonify({"query": None, "tag": tag, "type": "chapters",
                        "count": len(results), "results": results})
    return jsonify({"error": "provide ?query= and/or ?tag="}), 400


@app.errorhandler(404)
def not_found(err):
    if request.path.startswith("/read/"):
        return render_template("message.html", message="الصفحة غير موجودة"), 404
    return jsonify({"error": err.description}), 404


# ----------------------------------------------------------------- Web UI

@app.get("/")
def ui_home():
    query, tag, limit = search_args()
    results, tag_chapters = None, None
    if query:
        results = search(db(), query, tag=tag, limit=MAX_LIMIT)
    elif tag:
        tag_chapters = chapters_by_tag(tag)
    return render_template("index.html", query=query, tag=tag, tags=get_tags(),
                           books=get_books(), results=results, tag_chapters=tag_chapters)


@app.get("/read/book/<int:book_id>")
def ui_book(book_id):
    book = get_book(book_id)
    for ch in book["chapters"]:
        ch["sections"] = get_chapter(ch["id"])["sections"]
    return render_template("book.html", book=book)


@app.get("/read/section/<int:section_id>")
def ui_section(section_id):
    return render_template("section.html", s=get_section(section_id))


if __name__ == "__main__":
    app.run(debug=True)
