# Developer Guide

How the fiqh search engine is built, how to run each stage, and how to extend it.
For a project overview, see [README.md](README.md).

## Contents

1. [Architecture](#1-architecture)
2. [Project layout](#2-project-layout)
3. [Setup](#3-setup)
4. [Commands](#4-commands)
5. [Pipeline stages](#5-pipeline-stages): scrape, section boundaries, load, normalization, tagging, search, static site
6. [Recipe: adding a new book](#6-recipe-adding-a-new-book)
7. [Recipe: changing normalization or tags](#7-recipe-changing-normalization-or-tags)
8. [Troubleshooting (Windows)](#8-troubleshooting-windows)
9. [Known limitations](#9-known-limitations)

---

## 1. Architecture

```
 shamela.ws ──scrape──▶ data/raw/<id>/*.html   (HTTP cache, git-ignored)
                              │
                              ▼
                       data/books/<id>.json   (committed — the source of truth for the corpus)
                              │ load (+ tag)
                              ▼
                       data/fiqh.db           (SQLite + FTS5, git-ignored, rebuildable)
                         │            │
                 serve   │            │ export
                         ▼            ▼
              Flask API + UI      docs/  ──▶ GitHub Pages (static; search runs in the browser)
              (local only)
```

Each stage reads only the previous stage's output, so you can rerun any stage alone:

| Stage | Input | Output | Needs network |
|---|---|---|---|
| `fiqh scrape` | Shamela, or the `data/raw/` cache | `data/books/*.json` | Only for uncached pages |
| `fiqh load` | `data/books/*.json` | `data/fiqh.db` | No |
| `fiqh tag` | `data/fiqh.db` | `data/fiqh.db` (ChapterTag) | No |
| `fiqh export` | `data/fiqh.db` | `docs/` | No |

`data/books/*.json` is committed, so a fresh clone can rebuild the database and site
with `fiqh build` without scraping.

## 2. Project layout

```
pyproject.toml              package metadata, dependencies, `fiqh` console script
src/fiqh_search/
  config.py                 paths (data/, docs/), BOOK_IDS, Shamela settings
  cli.py                    `fiqh` subcommands, the only entry point
  arabic.py                 normalization rules and functions (single source of truth)
  scraper.py                Shamela → book JSON (TOC, page text, section boundaries)
  db.py                     SQLite schema (8 tables + SectionFTS) and connection helper
  loader.py                 book JSON → SQLite; reindexes FTS; runs tagging
  tags.py                   rule-based chapter tags
  search.py                 FTS5 query building, ranking, snippets
  web/app.py, templates/    Flask JSON API + server-rendered UI
  static_site/export.py     SQLite → docs/ (HTML pages + search index)
  static_site/templates/    Jinja templates for the static pages
  static_site/assets/       style.css, search.js (copied into docs/assets/)
data/books/                 scraped books (committed)
docs/                       generated static site (committed; served by GitHub Pages)
```

Generated files: don't edit anything in `docs/` by hand. `fiqh export` deletes and
recreates the whole folder.

## 3. Setup

Requires Python 3.10+ (developed on 3.13).

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\fiqh --help
```

`-e` (editable) installs the package from `src/`, so code changes take effect without
reinstalling. In VS Code, select `.venv` as the interpreter (*Python: Select
Interpreter*). Otherwise the editor flags the imports as missing.

## 4. Commands

| Command | What it does |
|---|---|
| `fiqh scrape [ID ...]` | Scrape books (default: `config.BOOK_IDS`) into `data/books/<id>.json` |
| `fiqh load [--fresh] [FILE ...]` | Load book JSON into SQLite, then retag. `--fresh` deletes the DB first |
| `fiqh tag` | Re-run tag rules on every chapter and print the result |
| `fiqh export` | Generate the static site in `docs/` |
| `fiqh build` | `load --fresh` followed by `export`. Use this after any data change |
| `fiqh search QUERY [--tag T] [--limit N]` | Search from the terminal (FTS5) |
| `fiqh serve [--port N] [--no-debug]` | Run the Flask app at http://127.0.0.1:5000 |

`python -m fiqh_search …` works as well as `fiqh …`.

## 5. Pipeline stages

### 5.1 Scraping (`scraper.py`)

Shamela endpoints used (layout as of 2026-10):

| URL | Contains |
|---|---|
| `/book/{id}` | Book card ("الكتاب: … / المؤلف: … / الناشر: …") and the top-level TOC (`.betaka-index > ul > li`) |
| `/ajax/titlechilds/{id}/{title_id}` | Child TOC entries of one entry. `title_id` is the `data-id` of its `a.exp_bu` button |
| `/book/{id}/{page}` | One page. The text is in `div.nass > p`, and the `>>` link gives the last page |

Behaviour:

- **Caching.** Every response is cached in `data/raw/<id>/`: `toc.html`, `toc_<title_id>.html`
  and `p<page>.html`. Cached pages are never re-requested, so an interrupted scrape resumes
  where it stopped, and changes to the parsing code can be re-run offline in seconds.
  Delete a book's folder to force a fresh download.
- **Politeness.** One live request per `REQUEST_DELAY` (1 s), with 3 retries and backoff.
  A full book takes 5–15 minutes.
- **Outline.** Top-level TOC entries become chapters. All nested entries, at any depth,
  are flattened into that chapter's sections. If a chapter has text before its first
  sub-entry, or has no sub-entries, that text becomes a section named after the chapter.

Book JSON format (`data/books/<id>.json`):

```jsonc
{
  "shamela_id": 35120,
  "url": "https://shamela.ws/book/35120",
  "last_page": 329,
  "title": "…", "publisher": "…",
  "author": {"name": "…", "full_arabic_name": "…", "year_death_hijri": 918, "url": "https://shamela.ws/author/…",
             "nickname": "ابن قاسم"},            // optional; not produced by the scraper, add by hand
  "card": {"الكتاب": "…", "المؤلف": "…", …},       // raw book-card fields
  "chapters": [{
    "title": "كتاب أحكام الطهارة", "page": 6,
    "sections": [{
      "title": "فروض الوضوء",
      "page": 13, "offset": 412, "end_page": 15,   // where the text was cut
      "located": true,                             // was the title found in the page text?
      "content": "…"                               // paragraphs separated by \n
    }]
  }]
}
```

### 5.2 Section boundaries (`scraper.fill_content`)

A TOC entry gives only the **page** where a section starts, and several sections often
start on the same page. To cut the text precisely, the scraper finds each section's
title inside its start page (`find_title`):

1. **Fuzzy title match.** `arabic.fuzzy_pattern(title)` matches the title while ignoring
   tashkeel and letter variants and allowing any punctuation between words. It is needed
   because TOC titles are plain while some books' text is fully voweled (كفاية الأخيار).
2. **Heading fallback.** If that fails, the scraper takes the first paragraph that opens
   with `(` + the title's first word, when that word is فصل/باب/كتاب/فرع/تنبيه/خاتمة/مقدمة.
   This is needed because بشرى الكريم heads sections with the matn ("(فصل: يكره) …")
   rather than the TOC title.
3. **Back up over openers.** The cut moves back over a preceding `[`, `(` or `•`, so
   heading markers stay with their own section.
4. **Unmatched titles.** If the title still isn't found (`located: false`), the boundary
   page is included in **both** neighbouring sections. Duplicated text is better than
   lost text.

Content cleanup: a first line consisting only of the heading (e.g. `[• السواك]`) is removed,
because the UI shows the title separately. A trailing `* * *` separator is also removed.

`fiqh scrape` prints `title located in text: X` for each book. **Check this number when
adding a book.** Current results: 204/204 (35120), 145/145 (6140), 118/124 (26535),
118/118 (6125).

**Outline files (`data/outlines/<id>.json`), for flat TOCs.** Some Shamela books list only
their فصول, with no كتاب/باب level, while the headings are printed inline in the text
("… بَاب التَّيَمُّم - يتيمم …"). Without help, every فصل becomes its own chapter and gets
no tags. An outline file lists the real chapter headings:

```jsonc
{
  "about": "why this file exists and how the headings were found",
  "chapters": [
    {"title": "مقدمة", "page": 1, "loose": true},  // loose: heading runs into the text, no "-"/"=" after it
    {"title": "كتاب الطهارة", "page": 2},
    …
  ],
  "ornaments": ["صلى الله عليه وسلم"]           // optional, see below
}
```

How it is applied:
- **Splitting.** The scraper inserts each heading among the TOC entries in text order and
  locates it as title + `-`/`=`/end of line, ignoring tashkeel. It then splits the text
  there and groups the following فصول under it. The bab's opening text, before its first
  فصل, becomes the chapter's intro section, titled "مقدمة <chapter>" (e.g. «مقدمة كتاب
  الطهارة») so it reads, and takes notes, as a section of its own. A heading that is
  already a TOC entry is not duplicated.
- **Wrong pages fail loudly.** If a heading can't be found on its page, the scrape stops
  with an error.
- **Ornaments.** Phrases that Shamela produced from decorative print ornaments around
  headings are removed only in their dash-wrapped form (`- … -`). In المقدمة الحضرمية
  (6125), every باب
  heading is wrapped in "- صَلَّى اللَّهُ عَلَيْهِ وَسَلَّم َ -", while the 14 genuine
  occurrences in sentences are kept.
- **Pages before the TOC.** An outline also recovers pages before the first TOC entry. In
  6125, for example, the TOC starts at page 3, but the مقدمة and the start of كتاب الطهارة are on
  pages 1–2.

To find the headings, search the cached pages for كتاب/باب with tashkeel ignored, e.g. a
regex built with `arabic.fuzzy_pattern("باب")`, then drop false positives such as
"بَاب مغلق".

### 5.3 Loading and the database (`loader.py`, `db.py`)

- Books are matched by their Shamela URL (`URL` table, `entity_type='book'`). Reloading a
  book deletes it and inserts it again, and its chapters and sections cascade, so nothing
  is duplicated.
- The whole load runs in one transaction, and tagging runs at the end.
- **IDs and URLs.** SQLite assigns new IDs on every reload, and the static site's URLs
  (`section/<id>.html`) come from these IDs. Use `fiqh load --fresh` (or `fiqh build`) so
  that IDs start at 1 and stay stable for the same set of books.

Schema: `Book`, `Scholar`, `Publisher`, `Chapter`, `Section`, `Tag`, `ChapterTag`,
`TeacherStudent` and `URL`, plus `SectionFTS`, an FTS5 table holding the **normalized**
section name and content. `SectionFTS` is filled in Python (`loader.index_section`),
because normalization runs in Python and can't run in an SQL trigger. A delete trigger
keeps it in sync when sections are removed.

**Scholars**
- `Scholar.nickname` (laqab/kunya) is loaded from the optional `author.nickname` in book
  JSON. On reload, a non-blank value replaces the stored one, and a missing or blank
  value leaves it unchanged. A non-string value stops the load with a `ValueError`.
- `TeacherStudent(teacher_id, student_id)` links two scholars, with a `CHECK` that they
  differ.
  - **Keys:** the primary key is `(teacher_id, student_id)`, which also serves "students
    of X" lookups. `idx_teacherstudent_student` serves "teachers of X".
  - **Deletes:** removing a scholar removes their links (`ON DELETE CASCADE`).
  - **Types:** the ids are `INTEGER`, like `Scholar.id`. SQLite treats `'5'` and `5` as
    different values, so text ids would fail the foreign-key checks.
  - **Loading:** nothing fills this table yet.

**Schema changes.** `init_db()` runs `CREATE TABLE IF NOT EXISTS`, which never alters an
existing table. To add a column to an existing table, also append it to `db.MIGRATIONS`.
`init_db()` then adds it with `ALTER TABLE` to databases created before the change (it
runs on every `fiqh load`/`tag`). New tables and indexes need only the `SCHEMA` entry.
Foreign keys are enforced per connection (`PRAGMA foreign_keys = ON` in `get_connection()`).

### 5.4 Arabic normalization (`arabic.py`): single source of truth

Indexed text and queries are normalized the same way, so searches match regardless of
spelling:

| Rule | Example |
|---|---|
| Remove diacritics (`DIACRITIC_RANGES`: harakat, tanween, shadda, sukun, dagger alef, Quranic marks, tatweel) | الوُضُوءِ → الوضوء |
| Unify letter variants (`LETTER_MAP`) | أ إ آ ٱ → ا · ى → ي · ة → ه · ؤ → و · ئ → ي |
| Strip the article and attached particles (`ARTICLE_PREFIXES`, longest first), keeping at least `MIN_STEM` letters | البيع، والبيع، بالبيع → بيع · للصلاة → صلاه |
| …except `PROTECTED_STEMS` | الله stays الله (not له) |

The rules are plain data, and three places use them:

| Consumer | How |
|---|---|
| Index and Flask search | `normalize()`, `tokens()` |
| Scraper title matching | `fuzzy_pattern()` builds a regex from the same ranges and letter map |
| Browser search (`search.js`) | `rules_for_js()` is written into `docs/data/search.json` as `normalize`, and `makeNormalizer()` in `search.js` applies the same algorithm |

`search.js` contains **no rules of its own**. Its only copy is the small algorithm, which
must match `normalize()` in `arabic.py`: remove diacritics → map letters → per token, strip
the longest valid prefix. If you change the algorithm (not just the rule data), change
both and run the parity check in §7.

### 5.5 Tagging (`tags.py`)

`RULES` is a list of `(keywords, tags)`. A chapter receives a rule's tags when any
keyword is a substring of its name. Both are compared after `normalize()`, so write
keywords in ordinary spelling (جنائز، أطعمة) without ال. Each rule normally assigns a
topic tag plus a broad category (عبادات، معاملات، أحوال شخصية، عقوبات).

Tags are recomputed from scratch on every `fiqh load` and `fiqh tag`, and tags that no
chapter uses any more are deleted. Run `fiqh tag` to see every chapter with its tags.

### 5.6 Search

- **Flask / CLI (`search.py`).** Every normalized word is quoted and joined with AND for
  FTS5, so user input can't inject FTS5 syntax. Results are ranked by
  `bm25(SectionFTS, 5.0, 1.0)`, which weighs title matches 5×. Snippets come back
  HTML-escaped, with matches wrapped in `<mark>`.
- **Static site (`search.js`).** Search loads `data/search.json` once (about 3.6 MB, served
  gzipped at about 1 MB). It applies the same every-word-must-match rule and ranks with
  tf-idf, also weighting title matches 5×. Result **sets** match FTS5 exactly. The
  **order** can differ slightly.

### 5.7 Personal notes (`notes.py`)

There is one note per section, written in the Flask UI (`fiqh serve`). The note box is
at the bottom of each section page, and Ctrl+Enter saves. `/notes` lists every note in
reading order. Home-page search shows matching notes first, under «في ملاحظاتي».

- **Storage.** Notes live in `data/notes.db`, separate from `fiqh.db`, because
  `fiqh build` deletes `fiqh.db`. The web app attaches it to every connection as schema
  `notes` (`attach_notes`).
- **Linking.** A note is linked by `Section.source_key` = `"<shamela_id>:<page>:<title>"`
  (`loader.section_keys`), never by `Section.id`. Keys depend only on the book's TOC, so
  notes survive rebuilds and the addition of new books.
- **Unlinked notes.** If a section's page or title changes (for example, after a scraper
  fix), its note is **not** deleted. `/notes` lists it under «ملاحظات غير مرتبطة» with its
  old key, so you can copy it to the right section.
- **Search.** `notes.NoteFTS` stores normalized note text, using the same rules as the books.
- **Deleting.** Saving an empty note deletes it.
- **Privacy.** `notes.db` and `data/backups/` are git-ignored, and `fiqh export` never
  reads notes, so they can't reach the public repo or the static site. The JSON API
  doesn't expose them either.
- **Backups.** `fiqh backup-notes` writes `data/backups/notes-<timestamp>.db`, and it is
  safe while the app runs. Copy that folder somewhere outside this machine regularly
  (cloud drive or USB), because your notes exist nowhere else.
- **Local only.** The UI has no login. Run it only on `127.0.0.1`, which is the
  default, and never expose it to a network as it is.

### 5.8 Static site and deployment (`static_site/`)

`fiqh export` writes `docs/`:

- `index.html`, plus one page per book and one per section (`book/<id>.html`,
  `section/<id>.html`). All links are relative, so the site works under
  `/fiqh-search/`.
- `data/search.json`: the normalization rules and the books, chapters (with tags) and
  sections. Section text is normalized, so snippets show it without tashkeel.
- `assets/`, copied from `static_site/assets/`.
- `.nojekyll`, which tells GitHub Pages to serve the files without Jekyll processing.

GitHub Pages serves `main:/docs` at https://adsaadeddin.github.io/fiqh-search/. To deploy,
commit `docs/` and push. The site updates within about a minute.

## 6. Recipe: adding a new book

1. **Find the Shamela id.** For `https://shamela.ws/book/12345`, the id is `12345`.
2. **Add it to `BOOK_IDS`** in `src/fiqh_search/config.py`.
3. **Scrape it:** `fiqh scrape 12345`. Takes 5–15 minutes, and it is safe to interrupt
   and rerun.
4. **Check the boundary stats** in the output (`title located in text: X`).
   - If X is close to the number of sections, continue.
   - If the book loads as dozens of one-section chapters, its TOC is flat. Add an
     outline file (§5.2) and rescrape.
   - If X is low, open `data/raw/12345/p<page>.html` for a few unmatched sections
     (`"located": false` in the JSON). Compare the in-text heading with the TOC title,
     extend `find_title` (for example, add a heading word to `HEADING_WORDS`), and rerun
     the scrape. It's offline now, so it takes seconds.
   - After any scraper change, rerun **all** books and confirm the other books' JSON is
     unchanged: `git diff --stat data/books`.
5. **Spot-check the content.** Section text should start at its heading and end just
   before the next one, with no stray `(`, `[•` or `* * *`.
6. **Check the tags:** `fiqh load`, then `fiqh tag`. Any chapter showing `—` has no tags.
   Add keywords to `RULES` where needed (see §7).
7. **Rebuild and test:** `fiqh build`, then `fiqh search <something from the new book>`
   and `fiqh serve` to browse it.
8. **Publish:** commit `data/books/12345.json`, `docs/` and any code changes, then push.
   Also update the book table in `README.md`.

Note that adding a book changes the IDs of books loaded after it, and so their section
URLs.

## 7. Recipe: changing normalization or tags

**A normalization rule** (e.g. a new letter variant or prefix):

1. Edit the data in `arabic.py` (`LETTER_MAP`, `ARTICLE_PREFIXES`, …).
2. Run `fiqh build`. The FTS index and `search.json` both store normalized text, so
   everything must be regenerated.
3. Tag keywords and scraper title matching pick up the change automatically. Rerun
   `fiqh scrape` (offline) if title matching could be affected, and check
   `git diff --stat data/books`.
4. Check that the browser and Python still agree. A parity check: for a set of sample
   texts, compare `arabic.tokens(text)` in Python with `makeNormalizer(index.normalize)(text)`
   from `docs/assets/search.js` in Node. They must be identical. (Phase 3 turns this into
   an automated test.)

**A tag rule:** edit `RULES` in `tags.py`, run `fiqh tag` to review, then
`fiqh export`. IDs don't change, so `--fresh` isn't needed.

## 8. Troubleshooting (Windows)

| Symptom | Cause | Fix |
|---|---|---|
| `SSLError: CERTIFICATE_VERIFY_FAILED` when scraping | `requests` uses certifi's CA bundle, which can't verify shamela.ws on this machine | Already handled: `scraper.py` calls `truststore.inject_into_ssl()` to use the Windows certificate store |
| `git push`: *SSL certificate problem: unable to get local issuer certificate* | Git's OpenSSL backend has the same problem | `git config --local http.sslBackend schannel` |
| `pip` installs into the wrong Python | `pip` on PATH belongs to another environment (e.g. Anaconda) | Always use `.\.venv\Scripts\python -m pip …` |
| Arabic prints as `?` or raises `UnicodeEncodeError` | The console code page is cp1252 | The CLI reconfigures stdout to UTF-8. In ad-hoc scripts, call `sys.stdout.reconfigure(encoding="utf-8")`. `python -I` ignores `PYTHONIOENCODING` |
| `git commit -m` with quotes fails in PowerShell 5.1 (*pathspec … did not match*) | PowerShell 5.1 mangles embedded `"` when passing arguments to programs | Write the message to a file and use `git commit -F file` |
| VS Code: *Package `flask` is not installed* | The editor uses a different interpreter | *Python: Select Interpreter* → `.venv` |

## 9. Known limitations

- **Whole-word matching only.** «فرائض» doesn't find «فروض», and there is no stemming
  beyond the article. Next step: a synonym list in `arabic.py`, exported to the
  browser like the other rules.
- **Shared boundary pages.** 6 sections in بشرى الكريم share a boundary page with a
  neighbour (§5.2).
- **IDs come from load order**, so static URLs change when the book set changes (§5.3).
- **Normalized snippets.** Static-site snippets show normalized text (no tashkeel, ال
  removed). The reading pages show the original.
- **Planned (refactor phases 2–3):** structured logging and clearer scraper errors,
  validation of book JSON and DB integrity, unit tests (normalization parity, boundary
  detection, tagging), and complete type hints.
