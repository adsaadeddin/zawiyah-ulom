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

CLI:  zawiyah serve   (then open http://127.0.0.1:5000)
"""
import sqlite3

from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for

from zawiyah_ulom.db import get_connection
from zawiyah_ulom.notes import attach_notes, get_note, list_notes, note_keys, save_note, search_notes
from zawiyah_ulom import notes
from zawiyah_ulom.search import search
from zawiyah_ulom import library, sciences, scraper, study, tarajem

app = Flask(__name__)
app.json.ensure_ascii = False  # return Arabic as-is, not \uXXXX escapes
app.json.sort_keys = False
app.jinja_env.add_extension("jinja2.ext.do")  # {% do list.append(x) %} in study_print.html

MAX_LIMIT = 100


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = get_connection()
        if not app.config.get("_schema_checked"):  # once per process: add new tables/columns
            from zawiyah_ulom.db import init_db
            init_db(g.db)
            app.config["_schema_checked"] = True
        attach_notes(g.db)  # personal notes: data/notes.db, schema `notes`
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------- sciences (tabs)

def current_science() -> str:
    """The workspace for this request: ?sci= overrides, else the remembered one, else فقه."""
    if "science" not in g:
        slug = request.args.get("sci") or request.cookies.get("science") or sciences.DEFAULT
        g.science = slug if sciences.is_science(db(), slug) else sciences.DEFAULT
    return g.science


def science_books() -> set[int] | None:
    """Ids of the current science's books (None in «الكل»)."""
    if "science_books" not in g:
        g.science_books = sciences.book_ids_in(db(), current_science())
    return g.science_books


def in_science(book_id) -> bool:
    ids = science_books()
    return ids is None or book_id in ids


@app.context_processor
def inject_sciences():
    if request.path.startswith(("/static/",)) or request.path in ("/books", "/tags", "/search"):
        return {}
    return {"sciences": sciences.sciences(db()), "current_science": current_science()}


@app.get("/science/<slug>")
def ui_switch_science(slug):
    """Switch workspace (remembered in a cookie) and return to the page you were on."""
    if not sciences.is_science(db(), slug):
        abort(404)
    back = request.args.get("next") or "/"
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    resp = redirect(back)
    resp.set_cookie("science", slug, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


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
        SELECT id, name, section_order, length(content) AS content_length, source_key
        FROM Section WHERE chapter_id = ? ORDER BY section_order
    """, (chapter_id,))]
    return chapter


def get_section(section_id: int) -> dict:
    row = db().execute("""
        SELECT s.id, s.name, s.description, s.content, s.section_order, s.source_key,
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


def get_tags(book_ids: set[int] | None = None) -> list[dict]:
    """Tags with chapter counts; with book_ids, only tags used in those books."""
    from zawiyah_ulom.search import books_clause
    return [dict(r) for r in db().execute(f"""
        SELECT t.name, COUNT(ct.chapter_id) AS chapter_count
        FROM Tag t JOIN ChapterTag ct ON ct.tag_id = t.id JOIN Chapter c ON c.id = ct.chapter_id
        WHERE 1 {books_clause(book_ids, "c.book_id")}
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
    if request.path.startswith(("/read/", "/notes", "/tarajem", "/library", "/study")):
        return render_template("message.html", message="الصفحة غير موجودة"), 404
    return jsonify({"error": err.description}), 404


# ----------------------------------------------------------------- Web UI

@app.get("/")
def ui_home():
    query, tag, limit = search_args()
    results, tag_chapters, note_results, ann_results = None, None, None, None
    ids = science_books()
    if query:
        results = search(db(), query, tag=tag, limit=MAX_LIMIT, book_ids=ids)
        note_results = search_notes(db(), query, tag=tag, book_ids=ids)
        ann_results = notes.search_annotations(db(), query, tag=tag, book_ids=ids)
    elif tag:
        tag_chapters = [c for c in chapters_by_tag(tag) if in_science(c["book_id"])]
    return render_template("index.html", query=query, tag=tag, tags=get_tags(ids),
                           books=[b for b in get_books() if in_science(b["id"])], results=results,
                           tag_chapters=tag_chapters,
                           note_results=note_results, ann_results=ann_results, kinds=notes.KINDS)


@app.get("/read/book/<int:book_id>")
def ui_book(book_id):
    book = get_book(book_id)
    with_notes = note_keys(db())
    ann_counts = notes.annotation_counts(db())
    for ch in book["chapters"]:
        ch["sections"] = get_chapter(ch["id"])["sections"]
        for s in ch["sections"]:
            s["has_note"] = s["source_key"] in with_notes
            s["ann_count"] = ann_counts.get(s["source_key"], 0)
        ch["note_count"] = sum(s["has_note"] or s["ann_count"] > 0 for s in ch["sections"])
    is_matn = any(st["matn"]["id"] == book_id for st in study.study_sets(db()))
    return render_template("book.html", book=book, is_matn=is_matn)


@app.get("/read/section/<int:section_id>")
def ui_section(section_id):
    s = get_section(section_id)
    annotations = notes.annotations_for(db(), s["source_key"])
    paragraphs = s["content"].split("\n") if s["content"] else []
    segments, by_paragraph, unanchored = notes.anchor_annotations(paragraphs, annotations)
    return render_template("section.html", s=s, note=get_note(db(), s["source_key"]),
                           saved=request.args.get("saved") == "1",
                           segments=segments, by_paragraph=by_paragraph, unanchored=unanchored,
                           ann_kind={a["id"]: a["kind"] for a in annotations},
                           ann_total=len(annotations), kinds=notes.KINDS,
                           open_ann=request.args.get("ann", type=int), error=request.args.get("error"))


@app.post("/read/section/<int:section_id>/note")
def ui_save_note(section_id):
    s = get_section(section_id)  # 404 if the section doesn't exist
    save_note(db(), s["source_key"], request.form.get("body", ""))
    return redirect(url_for("ui_section", section_id=section_id, saved=1) + "#note")


def _safe_next() -> str | None:
    """A local page to return to after a form (the study view sends its own URL)."""
    nxt = request.form.get("next") or ""
    return nxt.split("#")[0] if nxt.startswith("/") and not nxt.startswith("//") else None


def _with_param(url: str, **params) -> str:
    from urllib.parse import urlencode, urlsplit, parse_qsl, urlunsplit
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in params] +             [(k, v) for k, v in params.items() if v is not None]
    return urlunsplit(parts._replace(query=urlencode(query)))


@app.post("/read/section/<int:section_id>/annotations")
def ui_add_annotation(section_id):
    s = get_section(section_id)
    f = request.form
    nxt = _safe_next()
    try:
        ann_id = notes.add_annotation(
            db(), s["source_key"], quote=f.get("quote", ""), body=f.get("body", ""),
            kind=f.get("kind", ""), prefix=f.get("prefix", ""), suffix=f.get("suffix", ""),
            paragraph=f.get("paragraph", type=int), scope=f.get("scope", "passage"))
    except notes.AnnotationError as e:
        if nxt:
            return redirect(_with_param(nxt, error=str(e)))
        return redirect(url_for("ui_section", section_id=section_id, error=str(e)))
    if nxt:
        return redirect(_with_param(nxt, ann=ann_id, error=None) + f"#ann-{ann_id}")
    return redirect(url_for("ui_section", section_id=section_id, ann=ann_id) + f"#ann-{ann_id}")


def _annotation_section(ann_id: int) -> tuple[dict, int | None]:
    """The annotation and the id of the section it belongs to (None if unlinked)."""
    ann = notes.get_annotation(db(), ann_id)
    if not ann:
        abort(404)
    row = db().execute("SELECT id FROM Section WHERE source_key = ?", (ann["section_key"],)).fetchone()
    return ann, row["id"] if row else None


def _back_to(section_id: int | None, ann_id: int | None = None, error: str | None = None):
    nxt = _safe_next()
    if nxt:
        return redirect(_with_param(nxt, ann=ann_id, error=error) + (f"#ann-{ann_id}" if ann_id else ""))
    if section_id is None:
        return redirect(url_for("ui_notes", error=error))
    url = url_for("ui_section", section_id=section_id, ann=ann_id, error=error)
    return redirect(url + (f"#ann-{ann_id}" if ann_id else ""))


@app.post("/annotations/<int:ann_id>/edit")
def ui_edit_annotation(ann_id):
    _, section_id = _annotation_section(ann_id)
    try:
        notes.update_annotation(db(), ann_id, body=request.form.get("body", ""),
                                kind=request.form.get("kind", ""))
    except notes.AnnotationError as e:
        return _back_to(section_id, ann_id, str(e))
    return _back_to(section_id, ann_id)


@app.post("/annotations/<int:ann_id>/delete")
def ui_delete_annotation(ann_id):
    _, section_id = _annotation_section(ann_id)
    notes.delete_annotation(db(), ann_id)
    return _back_to(section_id)


@app.get("/notes")
def ui_notes():
    all_notes = list_notes(db())  # already in reading order
    by_book: dict[str, list[dict]] = {}
    all_notes = [n for n in all_notes if n["book_id"] is None or in_science(n["book_id"])]
    for n in all_notes:
        if n["section_id"] is not None:
            by_book.setdefault(n["book_title"], []).append(n)
    unlinked = [n for n in all_notes if n["section_id"] is None]

    kind = request.args.get("kind")
    kind = kind if kind in notes.KINDS else None
    annotations = [a for a in notes.list_annotations(db(), kind) if a["book_id"] is None or in_science(a["book_id"])]
    ann_by_book: dict[str, list[dict]] = {}
    for a in annotations:
        ann_by_book.setdefault(a["book_title"] or "ملاحظات غير مرتبطة", []).append(a)
    kind_counts = {k: sum(1 for a in notes.list_annotations(db(), k) if a["book_id"] is None or in_science(a["book_id"]))
                   for k in notes.KINDS}
    return render_template("notes.html", by_book=by_book, unlinked=unlinked,
                           count=len(all_notes) - len(unlinked), ann_by_book=ann_by_book,
                           ann_count=len(annotations), kinds=notes.KINDS, kind=kind,
                           kind_counts=kind_counts, error=request.args.get("error"))


# ---------------------------------------------------------------- Tarajem

def _tarjama_form(scholar: dict | None, values: dict | None = None, errors: dict | None = None):
    """Render the add/edit form. `values` re-fills it after a validation error."""
    exclude = scholar["id"] if scholar else None
    if values is None and scholar:  # editing: start from the stored record
        values = {**scholar,
                  "teacher_ids": [t["id"] for t in scholar["teachers"]],
                  "student_ids": [s["id"] for s in scholar["students"]],
                  "user_urls": [u["url"] for u in scholar["urls"] if u["source"] == "user"]}
    return render_template("tarajem_form.html", scholar=scholar, values=values or {},
                           errors=errors or {}, fields=tarajem.FIELDS,
                           choices=tarajem.scholar_choices(db(), exclude_id=exclude))


@app.get("/tarajem")
def ui_tarajem():
    query = request.args.get("q", "").strip()
    show_all = request.args.get("all") == "1" or science_books() is None
    scholars = tarajem.list_scholars(db(), query)
    if not show_all:
        authors = {r["author_id"] for r in db().execute("SELECT id, author_id FROM Book") if in_science(r["id"])}
        scholars = [x for x in scholars if x["id"] in authors]
    return render_template("tarajem_list.html", scholars=scholars, show_all=show_all,
                           total=db().execute("SELECT COUNT(*) FROM Scholar").fetchone()[0],
                           query=query, message=request.args.get("msg"))


@app.get("/tarajem/new")
def ui_tarjama_new():
    return _tarjama_form(None)


@app.post("/tarajem/new")
def ui_tarjama_create():
    data, errors = tarajem.parse_form(request.form)
    if errors:
        return _tarjama_form(None, data, errors), 400
    scholar_id = tarajem.save_scholar(db(), data)
    return redirect(url_for("ui_tarjama", scholar_id=scholar_id, saved=1))


@app.get("/tarajem/<int:scholar_id>")
def ui_tarjama(scholar_id):
    s = tarajem.get_scholar(db(), scholar_id)
    if not s:
        abort(404)
    return render_template("tarajem_detail.html", s=s, saved=request.args.get("saved") == "1",
                           error=request.args.get("error"))


@app.get("/tarajem/<int:scholar_id>/edit")
def ui_tarjama_edit(scholar_id):
    s = tarajem.get_scholar(db(), scholar_id)
    if not s:
        abort(404)
    return _tarjama_form(s)


@app.post("/tarajem/<int:scholar_id>/edit")
def ui_tarjama_update(scholar_id):
    s = tarajem.get_scholar(db(), scholar_id)
    if not s:
        abort(404)
    data, errors = tarajem.parse_form(request.form)
    if errors:
        return _tarjama_form(s, data, errors), 400
    tarajem.save_scholar(db(), data, scholar_id)
    return redirect(url_for("ui_tarjama", scholar_id=scholar_id, saved=1))


@app.post("/tarajem/<int:scholar_id>/delete")
def ui_tarjama_delete(scholar_id):
    s = tarajem.get_scholar(db(), scholar_id)
    if not s:
        abort(404)
    try:
        tarajem.delete_scholar(db(), scholar_id)
    except tarajem.TarjamaError as e:
        return redirect(url_for("ui_tarjama", scholar_id=scholar_id, error=str(e)))
    return redirect(url_for("ui_tarajem", msg=f"حُذفت ترجمة {s['name']}"))


# ---------------------------------------------------------------- Library

@app.get("/library")
def ui_library():
    ref = request.args.get("ref", "").strip()
    preview, error = None, request.args.get("error")
    if ref:
        try:
            source, book_id = library.parse_book_ref(ref)
            preview = scraper.preview(scraper.get_source(source, book_id))
            preview["in_library"] = library.in_library(preview["key"])
            preview["staged"] = any(s["key"] == preview["key"] for s in library.staged_books())
        except library.LibraryError as e:
            error = str(e)
        except Exception as e:  # network errors, missing book
            error = f"تعذّر جلب الكتاب: {e}"
    job = library.current_job()
    book_sci = sciences.book_sciences(db())
    books = [{**b, "sciences": book_sci.get(b["id"], []), "key": study.book_key(b["source_url"] or "")}
             for b in library.library_books(db()) if in_science(b["id"])]
    return render_template("library.html", books=books, staged=library.staged_books(),
                           job=job.as_dict() if job else None, preview=preview, ref=ref,
                           error=error, message=request.args.get("msg"))


@app.post("/library/download")
def ui_library_download():
    try:
        chosen = request.form.getlist("science")
        if not chosen:
            raise library.LibraryError("اختر العلم الذي يُضاف إليه الكتاب")
        library.start_download(request.form["source"], int(request.form["book_id"]),
                               request.form.get("title", ""), chosen)
    except (library.LibraryError, KeyError, ValueError) as e:
        return redirect(url_for("ui_library", error=str(e)))
    return redirect(url_for("ui_library"))


@app.get("/library/job")
def api_library_job():
    job = library.current_job()
    return jsonify(job.as_dict() if job else None)


@app.post("/library/staged/<key>/accept")
def ui_library_accept(key):
    chosen = request.form.getlist("science")
    if not chosen:
        return redirect(url_for("ui_library", error="اختر علمًا واحدًا على الأقل للكتاب"))
    try:
        library.accept(key)
    except library.LibraryError as e:
        return redirect(url_for("ui_library", error=str(e)))
    sciences.set_book_sciences(db(), key, chosen)
    (library.STAGING_DIR / f"{key}.sciences").unlink(missing_ok=True)
    names = {sc["slug"]: sc["name"] for sc in sciences.sciences(db())}
    return redirect(url_for("ui_library", msg="أُضيف الكتاب إلى: " + "، ".join(names.get(c, c) for c in chosen)))


@app.post("/library/book/<key>/sciences")
def ui_book_sciences(key):
    """Set which sciences a book belongs to (several allowed)."""
    library._check_key(key)
    try:
        sciences.set_book_sciences(db(), key, request.form.getlist("science"))
    except ValueError as e:
        return redirect(url_for("ui_library", error=str(e)))
    return redirect(url_for("ui_library", msg="تم حفظ علوم الكتاب"))


@app.post("/library/staged/<key>/discard")
def ui_library_discard(key):
    try:
        library.discard(key)
    except library.LibraryError as e:
        return redirect(url_for("ui_library", error=str(e)))
    return redirect(url_for("ui_library", msg="تم تجاهل الكتاب المنزَّل"))


# ------------------------------------------------------------ Study (matn + shuruh)

@app.get("/study")
def ui_study_index():
    return render_template("study_index.html", sets=[st for st in study.study_sets(db()) if in_science(st["matn"]["id"])])


@app.get("/study/<int:matn_id>")
def ui_study(matn_id):
    st, shuruh = _study_set(matn_id)
    notes_flags = request.args.getlist("notes")
    show_notes = "1" in notes_flags or not notes_flags   # on unless explicitly turned off
    view = study.study_chapter(db(), matn_id, [b["id"] for b in shuruh], request.args.get("ch", type=int))
    here = url_for("ui_study", matn_id=matn_id, ch=view["chapter"]["id"], s=[b["id"] for b in shuruh],
                   notes=1 if show_notes else 0)
    return render_template("study.html", st=st, shuruh=shuruh, view=view, chosen=[b["id"] for b in shuruh],
                           show_notes=show_notes, kinds=notes.KINDS, here=here,
                           open_ann=request.args.get("ann", type=int), error=request.args.get("error"))


def _study_set(matn_id: int) -> tuple[dict, list[dict]]:
    """The configured set for a matn and the commentaries chosen in ?s= (default: all)."""
    sets = {s["matn"]["id"]: s for s in study.study_sets(db())}
    if matn_id not in sets:
        abort(404)
    st = sets[matn_id]
    chosen = [int(x) for x in request.args.getlist("s") if x.isdigit()]
    return st, [b for b in st["shuruh"] if b["id"] in chosen] or st["shuruh"]


@app.get("/study/<int:matn_id>/print")
def ui_study_print(matn_id):
    st, shuruh = _study_set(matn_id)
    include_notes = request.args.get("notes", "1") == "1"
    kinds_on = [k for k in request.args.getlist("k") if k in notes.KINDS] or list(notes.KINDS)
    view = study.study_chapter(db(), matn_id, [b["id"] for b in shuruh], request.args.get("ch", type=int))
    for row in view["rows"]:  # number this row's notes for footnote-style references
        row["print_notes"] = [a for a in row["para_notes"] + row["passage_notes"]
                              if include_notes and a["kind"] in kinds_on]
        row["note_no"] = {a["id"]: i + 1 for i, a in enumerate(row["print_notes"])}
    return render_template("study_print.html", st=st, shuruh=shuruh, view=view, kinds=notes.KINDS,
                           include_notes=include_notes, kinds_on=kinds_on, chosen=[b["id"] for b in shuruh])


@app.route("/study/sets", methods=["GET", "POST"])
def ui_study_sets():
    """Create study sets and add/remove commentaries (data/commentaries.json)."""
    from zawiyah_ulom.align import load_pairs, save_pairs
    books = {study.book_key(b["source_url"]): b for b in library.library_books(db()) if b["source_url"]}
    if request.method == "POST":
        pairs = load_pairs()
        action, matn = request.form.get("action"), request.form.get("matn", "")
        sharh = request.form.get("sharh", "")
        if matn not in books or (action != "remove_set" and sharh not in books) or matn == sharh:
            return redirect(url_for("ui_study_sets", error="اختر كتابين مختلفين من المكتبة"))
        if action == "add":
            pairs.setdefault(matn, [])
            if sharh not in pairs[matn]:
                pairs[matn].append(sharh)
        elif action == "remove":
            pairs[matn] = [k for k in pairs.get(matn, []) if k != sharh]
        save_pairs(pairs)
        return redirect(url_for("ui_study_sets", msg="تم الحفظ"))
    sets = [{"matn_key": m, "matn": books.get(m), "shuruh": [(k, books.get(k)) for k in ks]}
            for m, ks in load_pairs().items()]
    return render_template("study_sets.html", sets=sets, books=books,
                           msg=request.args.get("msg"), error=request.args.get("error"))
