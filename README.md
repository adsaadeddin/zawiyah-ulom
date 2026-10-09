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
| المقدمة الحضرمية (مسائل التعليم) — the matn of بشرى الكريم | عبد الله بافضل الحضرمي (ت 918 هـ) |
Search ignores tashkeel, letter variants (أ/إ/ا، ة/ه، ى/ي) and the definite article,
so «الوُضُوء», «والوضوء» and «وضوء» all match. Chapters are tagged by topic (طهارة، صلاة،
بيوع…) and category (عبادات، معاملات…).

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\fiqh build       # data/books/*.json -> data/fiqh.db -> docs/
.\.venv\Scripts\fiqh serve       # http://127.0.0.1:5000
```

The scraped books are committed in `data/books/`, so no scraping is needed. To
re-scrape: `fiqh scrape`. It takes about 30 minutes, and pages are cached in
`data/raw/`.

**Full pipeline, project layout, how to add a book, and troubleshooting:
[DEVELOPER.md](DEVELOPER.md).**

## API (`fiqh serve`)

| Endpoint | Returns |
|---|---|
| `GET /books` | All books with author, publisher, source URL |
| `GET /books/<id>` | Book with chapters and their tags |
| `GET /chapters/<id>` | Chapter with section list |
| `GET /sections/<id>` | Section content, plus previous/next section |
| `GET /tags` | Tags with chapter counts |
| `GET /search?query=…` | Matching sections with highlighted snippets. Optional `&tag=` and `&limit=` (max 100) |
| `GET /search?tag=…` | Chapters with that tag |

## Sources

The texts come from [المكتبة الشاملة](https://shamela.ws). Rights to each printed edition
belong to its publisher.
