"""Tarajem: scholar biographies and teacher→student links, edited on /tarajem.

Uses the Scholar, TeacherStudent and URL ('scholar_bio') tables in fiqh.db.
These are curated data: `fiqh load --fresh` / `fiqh build` empty only the
book tables (loader.reset_corpus), so everything entered here survives
rebuilds. Back it up with `fiqh backup`.

Book authors are created automatically by the loader, linked to their book
(Book.author_id), and matched on rebuild by their Shamela author URL. Those
URLs (source 'shamela') are read-only here; links you add get source 'user'.
"""
import sqlite3

from fiqh_search.arabic import normalize

# Editable Scholar columns: (column, label, kind). kind: text | textarea | year
FIELDS: list[tuple[str, str, str]] = [
    ("name", "الاسم المشهور", "text"),
    ("nickname", "اللقب أو الكنية", "text"),
    ("full_arabic_name", "الاسم الكامل", "text"),
    ("country", "البلد", "text"),
    ("year_birth_hijri", "سنة الولادة (هـ)", "year"),
    ("year_birth_gregorian", "سنة الولادة (م)", "year"),
    ("year_death_hijri", "سنة الوفاة (هـ)", "year"),
    ("year_death_gregorian", "سنة الوفاة (م)", "year"),
    ("bio", "الترجمة", "textarea"),
]
YEAR_RANGES = {"hijri": (1, 1500), "gregorian": (500, 2100)}


class TarjamaError(ValueError):
    """A user-facing validation or integrity error (message in Arabic)."""


# ------------------------------------------------------------------ reading

def list_scholars(conn: sqlite3.Connection, query: str = "") -> list[dict]:
    """Scholars ordered by death year (then name). `query` matches name,
    nickname or full name, ignoring tashkeel and ال (arabic.normalize)."""
    rows = [dict(r) for r in conn.execute("""
        SELECT s.id, s.name, s.nickname, s.full_arabic_name, s.country,
               s.year_death_hijri, s.year_death_gregorian,
               (SELECT COUNT(*) FROM Book b WHERE b.author_id = s.id) AS book_count,
               (SELECT COUNT(*) FROM TeacherStudent t WHERE t.student_id = s.id) AS teacher_count,
               (SELECT COUNT(*) FROM TeacherStudent t WHERE t.teacher_id = s.id) AS student_count,
               (s.bio IS NOT NULL AND s.bio <> '') AS has_bio
        FROM Scholar s
        ORDER BY s.year_death_hijri IS NULL, s.year_death_hijri, s.name
    """)]
    terms = normalize(query).split()
    if terms:
        def haystack(r):
            return normalize(" ".join(filter(None, (r["name"], r["nickname"], r["full_arabic_name"]))))
        rows = [r for r in rows if all(t in haystack(r) for t in terms)]
    return rows


def get_scholar(conn: sqlite3.Connection, scholar_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM Scholar WHERE id = ?", (scholar_id,)).fetchone()
    if not row:
        return None
    s = dict(row)
    link = """SELECT s.id, s.name, s.nickname, s.year_death_hijri FROM Scholar s
              JOIN TeacherStudent t ON s.id = t.{other} WHERE t.{me} = ?
              ORDER BY s.year_death_hijri IS NULL, s.year_death_hijri, s.name"""
    s["teachers"] = [dict(r) for r in conn.execute(link.format(other="teacher_id", me="student_id"), (scholar_id,))]
    s["students"] = [dict(r) for r in conn.execute(link.format(other="student_id", me="teacher_id"), (scholar_id,))]
    s["books"] = [dict(r) for r in conn.execute(
        "SELECT id, title FROM Book WHERE author_id = ? ORDER BY title", (scholar_id,))]
    s["urls"] = [dict(r) for r in conn.execute(
        """SELECT url, source FROM URL WHERE entity_type = 'scholar_bio' AND entity_id = ?
           ORDER BY is_primary DESC, id""", (scholar_id,))]
    return s


def scholar_choices(conn: sqlite3.Connection, exclude_id: int | None = None) -> list[dict]:
    """Everyone who can be picked as a teacher or student, in death-year order."""
    return [r for r in list_scholars(conn) if r["id"] != exclude_id]


# ------------------------------------------------------------------ writing

def parse_form(form) -> tuple[dict, dict[str, str]]:
    """Validate a submitted form (any mapping with .get/.getlist, e.g. Flask's
    request.form). Returns (data, errors); errors maps field -> Arabic message."""
    data: dict = {}
    errors: dict[str, str] = {}
    for col, label, kind in FIELDS:
        raw = (form.get(col) or "").replace("\r\n", "\n").strip()
        if kind != "year":
            data[col] = raw or None
            continue
        if not raw:
            data[col] = None
            continue
        try:
            year = int(raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
        except ValueError:
            errors[col] = f"{label}: أدخل رقمًا صحيحًا"
            continue
        lo, hi = YEAR_RANGES["hijri" if col.endswith("hijri") else "gregorian"]
        if not lo <= year <= hi:
            errors[col] = f"{label}: يجب أن تكون بين {lo} و{hi}"
        data[col] = year
    if not data["name"]:
        errors["name"] = "الاسم المشهور مطلوب"
    for era in ("hijri", "gregorian"):
        born, died = data.get(f"year_birth_{era}"), data.get(f"year_death_{era}")
        if born and died and died < born:
            errors[f"year_death_{era}"] = "سنة الوفاة قبل سنة الولادة"

    def ids(key):
        return sorted({int(v) for v in form.getlist(key) if str(v).isdigit()})
    data["teacher_ids"], data["student_ids"] = ids("teachers"), ids("students")
    data["user_urls"] = [u.strip() for u in (form.get("urls") or "").splitlines() if u.strip()]
    bad = [u for u in data["user_urls"] if not u.startswith(("http://", "https://"))]
    if bad:
        errors["urls"] = f"روابط غير صحيحة (يجب أن تبدأ بـ http): {', '.join(bad[:3])}"
    return data, errors


def save_scholar(conn: sqlite3.Connection, data: dict, scholar_id: int | None = None) -> int:
    """Insert or update a scholar with their teachers, students and user links,
    in one transaction. Returns the scholar id."""
    cols = [c for c, _, _ in FIELDS]
    with conn:
        if scholar_id is None:
            scholar_id = conn.execute(
                f"INSERT INTO Scholar ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [data[c] for c in cols],
            ).lastrowid
        else:
            conn.execute(f"UPDATE Scholar SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                         [data[c] for c in cols] + [scholar_id])

        # A scholar can't be their own teacher (also enforced by a CHECK constraint).
        teachers = [t for t in data["teacher_ids"] if t != scholar_id]
        students = [s for s in data["student_ids"] if s != scholar_id]
        conn.execute("DELETE FROM TeacherStudent WHERE student_id = ?", (scholar_id,))
        conn.execute("DELETE FROM TeacherStudent WHERE teacher_id = ?", (scholar_id,))
        conn.executemany("INSERT OR IGNORE INTO TeacherStudent (teacher_id, student_id) VALUES (?, ?)",
                         [(t, scholar_id) for t in teachers] + [(scholar_id, s) for s in students])

        conn.execute("DELETE FROM URL WHERE entity_type = 'scholar_bio' AND entity_id = ? AND source = 'user'",
                     (scholar_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO URL (entity_type, entity_id, url, source, is_primary) "
            "VALUES ('scholar_bio', ?, ?, 'user', 0)",
            [(scholar_id, u) for u in data["user_urls"]],
        )
    return scholar_id


def delete_scholar(conn: sqlite3.Connection, scholar_id: int) -> None:
    """Delete a scholar and their links. Refused for the author of a book in
    the library (Book.author_id would point nowhere)."""
    if conn.execute("SELECT 1 FROM Book WHERE author_id = ?", (scholar_id,)).fetchone():
        raise TarjamaError("لا يمكن حذف مؤلف كتاب موجود في المكتبة")
    with conn:
        conn.execute("DELETE FROM URL WHERE entity_type = 'scholar_bio' AND entity_id = ?", (scholar_id,))
        conn.execute("DELETE FROM Scholar WHERE id = ?", (scholar_id,))  # TeacherStudent cascades
