# Fiqh Search Engine

Full-text search over classical Shafi'i fiqh books from [shamela.ws](https://shamela.ws).
Python + Flask + SQLite (FTS5).

**Live demo:** https://adsaadeddin.github.io/fiqh-search/. This is a static export where
search runs in the browser.

| Book | Author |
|---|---|
| فتح القريب المجيب (شرح ابن قاسم على متن أبي شجاع) | ابن قاسم الغزي (ت 918 هـ) |
| كفاية الأخيار في حل غاية الإختصار | تقي الدين الحصني (ت 829 هـ) |
| بشرى الكريم بشرح مسائل التعليم | سعيد باعشن (ت 1270 هـ) |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## Build the data

```powershell
.\.venv\Scripts\python scraper.py        # all 3 books -> data/books/*.json (pages cached in data/raw/)
.\.venv\Scripts\python load_db.py        # JSON -> data/fiqh.db, then tags chapters
```

A full scrape takes about 30 minutes (1 request/second). Re-runs read from the
cache and take seconds. Both steps are safe to re-run.

To retag after editing `RULES` in `tags.py`: `.\.venv\Scripts\python tags.py`

## Run

```powershell
.\.venv\Scripts\python app.py            # http://127.0.0.1:5000
```

## Static site (GitHub Pages)

```powershell
.\.venv\Scripts\python export_static.py  # data/fiqh.db -> docs/
```

`docs/` is served by GitHub Pages (main branch, `/docs`). It contains one HTML page per
book and per section, plus `data/search.json`, which holds the normalized text. Search
runs in `site/assets/search.js`, and its `normalize` must stay in sync with `arabic.py`.
Re-run the export and commit `docs/` after any data change.

## Files

| File | Purpose |
|---|---|
| `database.py` | Schema (8 tables + `SectionFTS` index) and connection helper |
| `arabic.py` | Normalization used for both indexing and queries: strips tashkeel, unifies أ/إ/آ→ا, ى→ي, ة→ه, drops ال/وال/بال… prefixes |
| `scraper.py` | Shamela book card, TOC, and page text → JSON |
| `load_db.py` | JSON → SQLite (reloading a book replaces it) |
| `tags.py` | Rule-based chapter tags (topic + category) |
| `search.py` | FTS5 search; `python search.py "فروض الوضوء"` for quick checks |
| `app.py` | JSON API + web UI |
| `export_static.py`, `site/` | Static site generator and its templates/assets |

## API

| Endpoint | Returns |
|---|---|
| `GET /books` | All books with author, publisher, source URL |
| `GET /books/<id>` | Book with chapters and their tags |
| `GET /chapters/<id>` | Chapter with section list |
| `GET /sections/<id>` | Section content, plus previous/next section |
| `GET /tags` | Tags with chapter counts |
| `GET /search?query=…` | Matching sections with highlighted snippets. Optional `&tag=` and `&limit=` (max 100) |
| `GET /search?tag=…` | Chapters with that tag |

## Known limitations

- Matching is whole-word, after normalization: «فرائض» does not find «فروض».
- Section boundaries are found by locating each title in its page text, ignoring
  tashkeel. If that fails, the scraper takes a paragraph that opens with "(فصل…" /
  "(باب…". Located: 204/204 (35120), 145/145 (6140), 118/124 (26535). For the other
  6, the boundary page is included in both neighbouring sections.
- Book and section IDs, and so the static page URLs, are assigned on load. To keep
  them stable, rebuild with a fresh `data/fiqh.db` and the same book set.
- Search snippets show normalized text (no tashkeel). The reading view shows the original.
