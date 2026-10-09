"""Full-text search over sections (SQLite FTS5). Used by the Flask API.

CLI:  fiqh search "فروض الوضوء" [--tag طهارة]
"""
import html
import sqlite3

from fiqh_search.arabic import tokens


def build_match_query(query: str) -> str:
    """Turn free text into an FTS5 query: every word must appear (AND).
    Each word is quoted so user input can't inject FTS5 syntax."""
    return " ".join(f'"{w}"' for w in tokens(query))


def search(conn: sqlite3.Connection, query: str, tag: str | None = None, limit: int = 20,
           mark: tuple[str, str] = ("<mark>", "</mark>")) -> list[dict]:
    """Snippets are HTML-escaped with hits wrapped in `mark` (safe to render).
    If `tag` is given, only sections in chapters with that tag are returned."""
    match = build_match_query(query)
    if not match:
        return []
    tag_filter = """AND c.id IN (SELECT ct.chapter_id FROM ChapterTag ct
                                 JOIN Tag t ON t.id = ct.tag_id WHERE t.name = :tag)"""
    rows = conn.execute(
        f"""
        SELECT s.id AS section_id, s.name AS section_name,
               c.id AS chapter_id, c.name AS chapter_name,
               b.id AS book_id, b.title AS book_title,
               replace(snippet(SectionFTS, 1, char(2), char(3), ' … ', 16), char(10), ' ') AS snippet
        FROM SectionFTS
        JOIN Section s ON s.id = SectionFTS.section_id
        JOIN Chapter c ON c.id = s.chapter_id
        JOIN Book b    ON b.id = c.book_id
        WHERE SectionFTS MATCH :match {tag_filter if tag else ""}
        ORDER BY bm25(SectionFTS, 5.0, 1.0)  -- title hits weigh 5x
        LIMIT :limit
        """,
        {"match": match, "tag": tag, "limit": limit},
    ).fetchall()
    results = []
    for r in rows:
        r = dict(r)
        r["snippet"] = html.escape(r["snippet"]).replace("\x02", mark[0]).replace("\x03", mark[1])
        results.append(r)
    return results
