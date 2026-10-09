# زاوية العلوم · Zawiyah Ulom

A personal library and search engine for the Islamic sciences, in an Ash'ari, Shafi'i and
Sufi framework. It started with Shafi'i fiqh; عقيدة, تصوف, علوم القرآن and تفسير are
planned (see [ROADMAP.md](ROADMAP.md)). Books come from [shamela.ws](https://shamela.ws)
and [ketabonline.com](https://ketabonline.com).
Python + Flask + SQLite (FTS5).

**Live demo:** https://adsaadeddin.github.io/zawiyah-ulom/. This is a static export where
search runs in the browser.

| Book | Author |
|---|---|
| فتح القريب المجيب (شرح ابن قاسم على متن أبي شجاع) | ابن قاسم الغزي (ت 918 هـ) |
| كفاية الأخيار في حل غاية الإختصار | تقي الدين الحصني (ت 829 هـ) |
| بشرى الكريم بشرح مسائل التعليم | سعيد باعشن (ت 1270 هـ) |
| المقدمة الحضرمية (مسائل التعليم) — the matn of بشرى الكريم | عبد الله بافضل الحضرمي (ت 918 هـ) |
| المنهاج القويم — another sharh of المقدمة الحضرمية | ابن حجر الهيتمي (ت 974 هـ) |
| تحرير تنقيح اللباب (from ketabonline.com) | زكريا الأنصاري (ت 926 هـ) |

Search ignores tashkeel, letter variants (أ/إ/ا، ة/ه، ى/ي) and the definite article,
so «الوُضُوء», «والوضوء» and «وضوء» all match. Chapters are tagged by topic (طهارة، صلاة،
بيوع…) and category (عبادات، معاملات…).

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\zawiyah build       # data/books/*.json -> data/fiqh.db -> docs/
.\.venv\Scripts\zawiyah serve       # http://127.0.0.1:5000
```

The scraped books are committed in `data/books/`, so no scraping is needed. To
re-scrape: `zawiyah scrape`. It takes about 30 minutes, and pages are cached in
`data/raw/`.

**Full pipeline, project layout, how to add a book, and troubleshooting:
[DEVELOPER.md](DEVELOPER.md).**

## API (`zawiyah serve`)

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
