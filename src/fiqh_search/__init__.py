"""Full-text search over classical Shafi'i fiqh books from shamela.ws.

Pipeline: scraper -> data/books/*.json -> loader (+ tags) -> data/fiqh.db
          -> web (Flask, local) and static_site (GitHub Pages export).
See DEVELOPER.md for details.
"""
