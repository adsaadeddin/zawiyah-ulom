"""`zawiyah` command-line entry point (also: `python -m zawiyah_ulom`).

  zawiyah scrape [LINK ...]      Shamela id/link or ketabonline link -> data/books/<key>.json
                              (default: re-scrape every book in the library)
  zawiyah load [--fresh] [FILE]  data/books/*.json -> data/fiqh.db, then tag chapters
  zawiyah tag                    re-run tag rules on every chapter (after editing tags.RULES)
  zawiyah export                 data/fiqh.db -> docs/ (static site)
  zawiyah build                  load --fresh + export, from already-scraped JSON
  zawiyah search QUERY [--tag T] FTS5 search from the terminal
  zawiyah backup                 copy notes.db and fiqh.db (notes + tarajem) to data/backups/
  zawiyah serve [--port N]       Flask API + UI on http://127.0.0.1:5000
"""
import argparse
import sys
from pathlib import Path

from zawiyah_ulom.config import book_refs


def cmd_scrape(args: argparse.Namespace) -> None:
    from zawiyah_ulom.library import parse_book_ref
    from zawiyah_ulom.scraper import get_source, scrape
    refs = [parse_book_ref(a) for a in args.refs] if args.refs else book_refs()
    for source, book_id in refs:
        scrape(get_source(source, book_id))


def cmd_load(args: argparse.Namespace) -> None:
    from zawiyah_ulom.loader import load_books
    load_books([Path(f) for f in args.files] or None, fresh=args.fresh)


def cmd_tag(args: argparse.Namespace) -> None:
    from zawiyah_ulom.db import get_connection, init_db
    from zawiyah_ulom.tags import tag_chapters, tags_for
    conn = get_connection()
    init_db(conn)
    with conn:
        print(f"{tag_chapters(conn)} chapter-tag links")
        for ch in conn.execute("SELECT name FROM Chapter ORDER BY book_id, chapter_order"):
            print(f"  {ch['name'][:50]:50} -> {', '.join(tags_for(ch['name'])) or '—'}")
    conn.close()


def cmd_export(args: argparse.Namespace) -> None:
    from zawiyah_ulom.static_site.export import export_site
    export_site()


def cmd_build(args: argparse.Namespace) -> None:
    from zawiyah_ulom.loader import load_books
    from zawiyah_ulom.static_site.export import export_site
    load_books(fresh=True)
    export_site()


def cmd_search(args: argparse.Namespace) -> None:
    from zawiyah_ulom.db import get_connection
    from zawiyah_ulom.search import search
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
    from zawiyah_ulom.config import BACKUP_DIR, DB_PATH, NOTES_PATH
    from zawiyah_ulom.notes import backup_sqlite
    for path, prefix, what in ((NOTES_PATH, "notes", "notes"), (DB_PATH, "fiqh", "tarajem + library")):
        target = backup_sqlite(path, BACKUP_DIR, prefix)
        print(f"{what}: {target}" if target else f"{what}: nothing to back up ({path.name} doesn't exist)")
    print(f"Copy {BACKUP_DIR} to another drive or cloud storage regularly.")


def cmd_serve(args: argparse.Namespace) -> None:
    from zawiyah_ulom.web.app import app
    app.run(port=args.port, debug=args.debug)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zawiyah", description="زاوية العلوم (Zawiyah Ulom): library pipeline and app.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="scrape books from Shamela into data/books/")
    p.add_argument("refs", nargs="*", metavar="LINK",
                   help="Shamela id or link, or a ketabonline link (default: every book in data/books/)")
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
