"""Sciences (فنون) as workspaces: فقه، عقيدة، تصوف، علوم القرآن، تفاسير القرآن.

Every page of the app works inside one science (or «الكل», chosen on
purpose): only that science's books, search results, tags, notes, study sets
and authors are shown. A book can belong to several sciences. Assignments live
in BookScience, keyed by the book's file key so they survive rebuilds; a book
without any assignment counts as فقه (the library's original science).
"""
import sqlite3

ALL = "all"            # the «الكل» workspace: no filtering
DEFAULT = "fiqh"


def sciences(conn: sqlite3.Connection) -> list[dict]:
    """All sciences in display order, each with its number of books."""
    rows = [dict(r) for r in conn.execute("SELECT slug, name, structure FROM Science ORDER BY sort")]
    counts: dict[str, int] = {}
    for slugs in book_sciences(conn).values():
        for s in slugs:
            counts[s] = counts.get(s, 0) + 1
    for r in rows:
        r["books"] = counts.get(r["slug"], 0)
    return rows


def is_science(conn: sqlite3.Connection, slug: str) -> bool:
    return slug == ALL or conn.execute("SELECT 1 FROM Science WHERE slug = ?", (slug,)).fetchone() is not None


def _book_keys(conn: sqlite3.Connection) -> dict[int, str]:
    """Book.id -> file key (Shamela id, or ketabonline-<id>)."""
    from zawiyah_ulom.study import book_key
    return {r["entity_id"]: book_key(r["url"]) for r in
            conn.execute("SELECT entity_id, url FROM URL WHERE entity_type = 'book'")}


def book_sciences(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """Book.id -> its sciences (default فقه when none is assigned)."""
    assigned: dict[str, list[str]] = {}
    for r in conn.execute("SELECT book_key, science FROM BookScience ORDER BY science"):
        assigned.setdefault(r["book_key"], []).append(r["science"])
    return {bid: assigned.get(key, [DEFAULT]) for bid, key in _book_keys(conn).items()}


def book_ids_in(conn: sqlite3.Connection, slug: str) -> set[int] | None:
    """Ids of the books in a science; None for «الكل» (no filtering)."""
    if slug == ALL:
        return None
    return {bid for bid, slugs in book_sciences(conn).items() if slug in slugs}


def set_book_sciences(conn: sqlite3.Connection, key: str, slugs: list[str]) -> None:
    """Replace a book's sciences (by file key). At least one is required."""
    valid = {r[0] for r in conn.execute("SELECT slug FROM Science")}
    slugs = [s for s in dict.fromkeys(slugs) if s in valid]
    if not slugs:
        raise ValueError("اختر علمًا واحدًا على الأقل")
    with conn:
        conn.execute("DELETE FROM BookScience WHERE book_key = ?", (key,))
        conn.executemany("INSERT INTO BookScience (book_key, science) VALUES (?, ?)", [(key, s) for s in slugs])
