"""`fiqh` command-line entry point (also: `python -m fiqh_search`).

  fiqh scrape [ID ...]        Shamela -> data/books/<id>.json (default: config.BOOK_IDS)
  fiqh load [--fresh] [FILE]  data/books/*.json -> data/fiqh.db, then tag chapters
  fiqh tag                    re-run tag rules on every chapter (after editing tags.RULES)
  fiqh export                 data/fiqh.db -> docs/ (static site)
  fiqh build                  load --fresh + export, from already-scraped JSON
  fiqh search QUERY [--tag T] FTS5 search from the terminal
  fiqh backup                 copy notes.db and fiqh.db (notes + tarajem) to data/backups/
  fiqh serve [--port N]       Flask API + UI on http://127.0.0.1:5000
"""
import argparse
import sys
from pathlib import Path

from fiqh_search.config import BOOK_IDS


def cmd_scrape(args: argparse.Namespace) -> None:
    from fiqh_search.scraper import scrape_book
    for book_id in args.ids or BOOK_IDS:
        scrape_book(book_id)


def cmd_load(args: argparse.Namespace) -> None:
    from fiqh_search.loader import load_books
    load_books([Path(f) for f in args.files] or None, fresh=args.fresh)


def cmd_tag(args: argparse.Namespace) -> None:
    from fiqh_search.db import get_connection, init_db
    from fiqh_search.tags import tag_chapters, tags_for
    conn = get_connection()
    init_db(conn)
    with conn:
        print(f"{tag_chapters(conn)} chapter-tag links")
        for ch in conn.execute("SELECT name FROM Chapter ORDER BY book_id, chapter_order"):
            print(f"  {ch['name'][:50]:50} -> {', '.join(tags_for(ch['name'])) or '—'}")
    conn.close()


def cmd_export(args: argparse.Namespace) -> None:
    from fiqh_search.static_site.export import export_site
    export_site()


def cmd_build(args: argparse.Namespace) -> None:
    from fiqh_search.loader import load_books
    from fiqh_search.static_site.export import export_site
    load_books(fresh=True)
    export_site()


def cmd_search(args: argparse.Namespace) -> None:
    from fiqh_search.db import get_connection
    from fiqh_search.search import search
    conn = get_connection()
    results = search(conn, " ".join(args.query), tag=args.tag, limit=args.limit, mark=("⟦", "⟧"))
    conn.close()
    for r in results:
        print(f"[{r['section_id']}] {r['book_title'][:30]} › {r['chapter_name']} › {r['section_name']}")
        print(f"    {r['snippet']}\n")
    print(f"{len(results)} result(s)")


def cmd_backup(args: argparse.Namespace) -> None:
    """Back up everything you entered by hand: notes (notes.db) and tarajem
    (Scholar, TeacherStudent, links — stored in fiqh.db)."""
    from fiqh_search.config import BACKUP_DIR, DB_PATH, NOTES_PATH
    from fiqh_search.notes import backup_sqlite
    for path, prefix, what in ((NOTES_PATH, "notes", "notes"), (DB_PATH, "fiqh", "tarajem + library")):
        target = backup_sqlite(path, BACKUP_DIR, prefix)
        print(f"{what}: {target}" if target else f"{what}: nothing to back up ({path.name} doesn't exist)")
    print(f"Copy {BACKUP_DIR} to another drive or cloud storage regularly.")


def cmd_serve(args: argparse.Namespace) -> None:
    from fiqh_search.web.app import app
    app.run(port=args.port, debug=args.debug)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fiqh", description="Fiqh search engine pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="scrape books from Shamela into data/books/")
    p.add_argument("ids", nargs="*", type=int, help="Shamela book ids (default: config.BOOK_IDS)")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("load", help="load scraped JSON into SQLite and tag chapters")
    p.add_argument("files", nargs="*", help="book JSON files (default: data/books/*.json)")
    p.add_argument("--fresh", action="store_true", help="delete the database first (stable IDs)")
    p.set_defaults(func=cmd_load)

    sub.add_parser("tag", help="re-run chapter tag rules").set_defaults(func=cmd_tag)
    sub.add_parser("export", help="export the static site to docs/").set_defaults(func=cmd_export)
    sub.add_parser("build", help="load --fresh, then export").set_defaults(func=cmd_build)
    for name in ("backup", "backup-notes"):  # backup-notes: earlier name, kept as an alias
        sub.add_parser(name, help="snapshot notes.db and fiqh.db (tarajem) into data/backups/"
                       ).set_defaults(func=cmd_backup)

    p = sub.add_parser("search", help="search from the terminal")
    p.add_argument("query", nargs="+")
    p.add_argument("--tag")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("serve", help="run the Flask app")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--no-debug", dest="debug", action="store_false")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Arabic output on Windows consoles
    args = build_parser().parse_args(argv)
    args.func(args)
