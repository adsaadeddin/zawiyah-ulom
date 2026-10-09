# Developer Guide

How زاوية العلوم (Zawiyah Ulom) is built, how to run each stage, and how to extend it.
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
                       data/fiqh.db           (SQLite + FTS5, git-ignored; book tables rebuildable, tarajem kept)
                         │            │
                 serve   │            │ export
                         ▼            ▼
              Flask API + UI      docs/  ──▶ GitHub Pages (static; search runs in the browser)
              (local only)
```

Each stage reads only the previous stage's output, so you can rerun any stage alone:

| Stage | Input | Output | Needs network |
|---|---|---|---|
| `zawiyah scrape` | Shamela, or the `data/raw/` cache | `data/books/*.json` | Only for uncached pages |
| `zawiyah load` | `data/books/*.json` | `data/fiqh.db` | No |
| `zawiyah tag` | `data/fiqh.db` | `data/fiqh.db` (ChapterTag) | No |
| `zawiyah export` | `data/fiqh.db` | `docs/` | No |

`data/books/*.json` is committed, so a fresh clone can rebuild the database and site
with `zawiyah build` without scraping.

## 2. Project layout

```
pyproject.toml              package metadata, dependencies, `zawiyah` console script
src/zawiyah_ulom/
  config.py                 paths (data/, docs/), book_ids() (= files in data/books/), Shamela settings
  library.py                «المكتبة» tab: preview, background download, quality report, add
  ketabonline.py            ketabonline.com source (JSON API), alongside the Shamela source in scraper.py
  cli.py                    `zawiyah` subcommands, the only entry point
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

Generated files: don't edit anything in `docs/` by hand. `zawiyah export` deletes and
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
| `zawiyah scrape [ID ...]` | Scrape books into `data/books/<id>.json` (default: re-scrape every book already in `data/books/`) |
| `zawiyah load [--fresh] [FILE ...]` | Load book JSON into SQLite, then retag. `--fresh` empties the book tables first (scholars are kept) |
| `zawiyah tag` | Re-run tag rules on every chapter and print the result |
| `zawiyah export` | Generate the static site in `docs/` |
| `zawiyah build` | `load --fresh` followed by `export`. Use this after any data change |
| `zawiyah search QUERY [--tag T] [--limit N]` | Search from the terminal (FTS5) |
| `zawiyah serve [--port N] [--no-debug]` | Run the Flask app at http://127.0.0.1:5000 |

`python -m zawiyah_ulom …` works as well as `fiqh …`.

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

`zawiyah scrape` prints `title located in text: X` for each book. **Check this number when
adding a book.** Current results: 209/209 (35120), 149/149 (6140), 118/124 (26535),
118/118 (6125), 113/113 (9973), 179/179 (ketabonline-92681).

**Openings (`build_outline`).** Two kinds of text sat outside every section and were
lost or misfiled until 2026-10-09:
- **Text before the first TOC entry**, such as the author's introduction (كفاية الأخيار's
  TOC starts at page 3) or a khutba before the first كتاب. It now goes to a first
  chapter «مقدمة الكتاب», which is dropped when it's only a title page (under 100
  characters). It never shares a boundary page.
- **A كتاب's opening text when its first باب starts on the same page.** It now becomes
  the كتاب's own first section, just as when the first باب starts on a later page. It is
  kept only if the كتاب heading is found in the text and the section isn't empty;
  otherwise the text stays with the previous section, as before. This also stops a
  chapter heading from leaking onto the end of the previous section.

A check across all books found no other text outside a section.

**Heading-only and repeated pages (`fix_heading_pages`, `drop_repeated_sections`).**
Shamela sometimes inserts a page that holds only a فصل heading (printed page number 0),
and its TOC links to it. In المنهاج القويم (9973) there are three cases:

| Case | Page | Fix |
|---|---|---|
| Duplicate heading page | p171 (the real فصل starts on p172 under the same title) | The entry is dropped |
| Out-of-order heading page | p102 «سنن الاعتدال» (its text is on p104, after سنن الركوع) | Moved to where the heading opens a paragraph within the next 5 pages. Dropped if another entry with that title already starts there |
| Text missing from Shamela's copy | p11 «في الماء المكروه»: not even the matn wording appears anywhere | The entry is kept with `missing_text: true` and empty content. The reading page shows a notice instead of a blank page |

- **Skipped pages.** Pages whose entry was dropped or moved are read as empty
  (`skip_pages`), so their heading doesn't leak into the previous section.
- **Repeated pages.** Shamela also repeats whole pages: 9973's p103 is a copy of p101.
  After filling, a section with the same title and identical text as an earlier one in
  its chapter is dropped. Only exact repeats are affected.
- **Other books** were unaffected by both fixes (byte-identical JSON).

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
  (`section/<id>.html`) come from these IDs. Use `zawiyah load --fresh` (or `zawiyah build`) so
  that IDs start at 1 and stay stable for the same set of books.
- **What `--fresh` keeps.** It empties only the book tables (`loader.CORPUS_TABLES`:
  Book, Chapter, Section, Tag, ChapterTag, plus SectionFTS and the book URLs). Because
  they are empty, their IDs restart at 1. `Scholar`, `TeacherStudent`, `Publisher` and
  scholar links are **kept**, because they hold the tarajem you enter by hand. So
  `fiqh.db` is no longer disposable. Back it up with `zawiyah backup`, and don't delete it
  to "start clean".
- **Book authors** are matched to existing scholars by Shamela author URL first, then by
  name. Renaming an author on the tarajem page therefore doesn't create a duplicate on
  the next rebuild. Their stored fields are never overwritten by a load (except
  `nickname`, when the book JSON provides one).

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
runs on every `zawiyah load`/`tag`). New tables and indexes need only the `SCHEMA` entry.
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

Tags are recomputed from scratch on every `zawiyah load` and `zawiyah tag`, and tags that no
chapter uses any more are deleted. Run `zawiyah tag` to see every chapter with its tags.

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

There is one note per section, written in the Flask UI (`zawiyah serve`). The note box is
at the bottom of each section page, and Ctrl+Enter saves. `/notes` lists every note in
reading order. Home-page search shows matching notes first, under «في ملاحظاتي».

- **Storage.** Notes live in `data/notes.db`, separate from `fiqh.db`, because
  `zawiyah build` empties and reloads the book tables there, and section ids can change. The web app attaches it to every connection as schema
  `notes` (`attach_notes`).
- **Linking.** A note is linked by `Section.source_key` = `"<shamela_id>:<page>:<title>"`
  (`loader.section_keys`), never by `Section.id`. Keys depend only on the book's TOC, so
  notes survive rebuilds and the addition of new books.
- **Unlinked notes.** If a section's page or title changes (for example, after a scraper
  fix), its note is **not** deleted. `/notes` lists it under «ملاحظات غير مرتبطة» with its
  old key, so you can copy it to the right section.
- **Search.** `notes.NoteFTS` stores normalized note text, using the same rules as the books.
- **Deleting.** Saving an empty note deletes it.
- **Privacy.** `notes.db` and `data/backups/` are git-ignored, and `zawiyah export` never
  reads notes, so they can't reach the public repo or the static site. The JSON API
  doesn't expose them either.
- **Backups.** `zawiyah backup` writes `data/backups/notes-<timestamp>.db` (and `fiqh-<timestamp>.db`, for tarajem), and it is
  safe while the app runs. Copy that folder somewhere outside this machine regularly
  (cloud drive or USB), because your notes exist nowhere else.
- **Local only.** The UI has no login. Run it only on `127.0.0.1`, which is the
  default, and never expose it to a network as it is.

**Notes on a passage (`notes.Annotation`).** Select any words inside one paragraph of a
section and a ✎ button appears. The note gets a kind:

| Kind | Label | Highlight |
|---|---|---|
| `faida` | فائدة | yellow |
| `question` | سؤال | blue |
| `ishkal` | إشكال | red |
| `teaching` | للتدريس | green |

- **Display.** The passage stays highlighted. Clicking it opens the note under the
  paragraph, where it can be edited or deleted. A section can have any number of these
  notes, plus the one general note.
- **Anchoring.** A note stores the selected text (`quote`), up to 32 characters before
  and after it (`prefix`/`suffix`), and the paragraph index. `anchor_annotations()`
  places it again on every page view:
  1. an exact match, picking among repeated phrases by the surrounding context
  2. otherwise, a match that ignores tashkeel and letter variants (`fuzzy_pattern`)
  3. otherwise, it is listed under the section as «لم يُعثر على نصها», with its quote,
     and never deleted

  So notes survive rebuilds and small text changes, and overlapping notes each keep
  their highlight.
- **Where else they appear.** `/notes` lists them in reading order, with a filter by kind
  (`?kind=teaching`). Search covers both the note and the quoted passage
  (`AnnotationFTS`). The table of contents shows a ✎ count per section.
- **Limitation.** A selection must stay within one paragraph.

### 5.7b قراءة مقارنة (`align.py`, `study.py`)

`/study` shows a matn paragraph by paragraph, with each commentary's explanation of
that paragraph beside it. Sets are configured in `data/commentaries.json` (committed),
as `{"<matn key>": ["<sharh key>", ...]}`. Keys are file keys: a Shamela id, or
`ketabonline-<id>`.

**Alignment (`align.align`).** A commentary quotes the matn, often splitting it with
glosses («(والذي ينقض) أي يبطل (الوضوء…)»), and may follow a different recension
(كفاية الأخيار: «خمسة أشياء» where the matn has «ستة»). Each matn paragraph is placed
in two steps:
1. **Candidates.** The paragraph's first 6 normalized words are seeded from the 2 rarest
   in the commentary. A candidate needs at least 60% of those words in order, with at
   most 8 commentary words between matches; missing words are skipped.
2. **Global order.** A weighted longest-increasing-subsequence picks one candidate per
   paragraph, so that positions follow the matn's order. A stray match far ahead
   can't derail the rest.

Measured with random samples, all 12 anchors were correct in each pair:

| Matn | Commentary | Paragraphs placed |
|---|---|---|
| 11370 | 35120 | 90% |
| 11370 | 6140 | 86% |
| 6125 | 9973 | 98% |
| 6125 | 26535 | 71% (it paraphrases more) |

Unplaced paragraphs are mostly page-break fragments, the author's introduction, or
paraphrased فصول. They show as «مشروحة مع الفقرة السابقة», and the previous block runs
on to the next placed anchor, so no commentary text is skipped.

**Notes in the study view.** The matn box and the commentary passages are annotatable,
like the reading page. `notes.annotated_slice()` renders a slice of a section (a matn
paragraph, or a commentary passage between two anchors) with its highlights, and puts
each note's card where its highlight starts. The shared pieces are
`templates/_ann_macros.html` and `static/annotate.js`, used by both pages. Note forms
carry `next`, so saving returns to the study view.

**Notes column (narrow, toggle «ملاحظاتي»).** For each row, the column shows:
- notes on the **whole matn paragraph**: `Annotation.scope = 'paragraph'`, quote = the
  paragraph's text, found again by `notes.paragraph_of()`, not highlighted. The reading
  page shows them open under the paragraph.
- a compact list of the row's passage notes (matn and commentaries). Clicking one opens
  its card in the text.

The `scope` column was added to existing `notes.db` files by `notes.NOTES_MIGRATIONS`.

**Print (`/study/<id>/print`).** One كتاب on A4: each matn paragraph, the commentaries in
full, and your notes numbered under each paragraph. Options: notes on or off, and which
types (e.g. only للتدريس). Use the browser's print, or save as PDF.

**Sets (`/study/sets`).** Add or remove commentaries in a set (each one is a column; a
third works as is), or create a set from any two library books. This writes
`data/commentaries.json`.

**Display (`study.study_chapter`).** One matn chapter per page. Each row shows the matn
paragraph and one column per commentary, with its text from the anchor up to the next
anchor and links to the commentary sections, for writing notes there. Alignments are
cached in memory per pair, and recomputed when either book's text changes (about 1–2
seconds the first time).

### 5.8 Tarajem (`tarajem.py`)

The **التراجم** tab (`/tarajem`) is where you enter scholar biographies and teacher→student
links, in the Flask UI only.

| Page | What it does |
|---|---|
| `/tarajem` | Every scholar, ordered by death year (طبقات order). Search covers name, nickname and full name, ignoring tashkeel and ال |
| `/tarajem/new` | Add a scholar |
| `/tarajem/<id>` | Profile: names, dates, country, biography, teachers, students, books in the library, links |
| `/tarajem/<id>/edit` | Edit a scholar, including their teachers, students and links |

- **Storage.** Data goes in `Scholar`, `TeacherStudent` and `URL` (`entity_type='scholar_bio'`)
  in `fiqh.db`. A rebuild keeps them (§5.3). Saving a profile updates the scholar, their
  teacher and student links, and their links (`source='user'`) in one transaction.
  Shamela author links (`source='shamela'`) are read-only, because the loader uses them to
  find a book's author.
- **Validation (`parse_form`).**
  - The name is required.
  - Years accept Arabic digits; hijri must be 1–1500 and gregorian 500–2100, and death
    can't come before birth.
  - Links must start with `http`.
  - A scholar can't be their own teacher (also a `CHECK` constraint).
  - On an error, the form comes back with your input kept.
- **Deleting** removes the scholar, their teacher/student links and their URLs. It is
  refused for the author of a book in the library, whose page has no delete button.
- **Book authors** (`Book.author_id`) appear automatically, and their name links to
  their profile from the home and book pages.
- **Published, read-only.** Since 2026-10-09, `zawiyah export` publishes the tarajem
  to the public site as `docs/tarajem/` (the list, plus one page per scholar), at Adnan's
  request. Every biography written here becomes public at the next export and push.
  Editing stays local.

### 5.9 Library tab (`library.py`)

The **المكتبة** tab (`/library`) adds books from the app.

1. **Paste a link** (`https://shamela.ws/book/9973`, `shamela.ws/book/9973/12`, or
   `9973`). Anything that isn't a shamela.ws book link is rejected
   (`library.parse_book_ref`).
2. **Preview** (`scraper.preview_book`): the book card, page count and top-level
   contents, from 2 requests. It warns about a flat TOC and refuses books already in the
   library.
3. **Background download**: `scrape_book(out_dir=STAGING_DIR, progress=…)` runs in a
   thread, one job at a time, and the page polls `/library/job` for a progress bar.
   Jobs live in memory, so an app restart loses the job, but rerunning it resumes from
   the page cache.
4. **Quality report** (`library.quality_report`):
   - located vs total sections, with the unmatched ones listed
   - untagged chapters
   - stray markers and empty sections
   - a flat-TOC warning
   - sample sections
5. **Add or discard.** `accept` moves `data/staging/<id>.json` into `data/books/` and loads
   it **without** `--fresh`. The new book is appended, so existing ids, static URLs and
   note links don't change. If the load fails, the file is moved back. `discard` deletes
   the staged file but keeps the page cache, so a second download is instant.
6. **No removing from the app.** The page deliberately has no delete button. To remove a
   book, delete `data/books/<id>.json` and run `zawiyah build`.
   - Notes on its sections are kept and shown as unlinked on `/notes`.
   - Its author stays in the tarajem.

After adding or removing books, run `zawiyah export` (and commit `docs/`) to update the
public site. `zawiyah build` also renumbers everything from 1, which keeps the static URLs
compact.

### 5.10 Static site and deployment (`static_site/`)

`zawiyah export` writes `docs/`:

- `index.html`, plus one page per book and one per section (`book/<id>.html`,
  `section/<id>.html`). All links are relative, so the site works under
  `/zawiyah-ulom/`.
- `data/search.json`: the normalization rules and the books, chapters (with tags) and
  sections. Section text is normalized, so snippets show it without tashkeel.
- `assets/`, copied from `static_site/assets/`.
- `.nojekyll`, which tells GitHub Pages to serve the files without Jekyll processing.

GitHub Pages serves `main:/docs` at https://adsaadeddin.github.io/zawiyah-ulom/. To deploy,
commit `docs/` and push. The site updates within about a minute.

## 6. Recipe: adding a new book

**From the app (usual way):** open the **المكتبة** tab (`zawiyah serve`) and paste the
Shamela link. It previews the book, downloads it in the background, and shows a quality
report covering steps 4–6 below. Then press أضف إلى المكتبة, or تجاهل. Afterwards, do
steps 7–8. The steps below are the same process by hand, which you still need for a
flat-TOC book (§5.2).

1. **Find the Shamela id.** For `https://shamela.ws/book/12345`, the id is `12345`.
2. **No list to edit.** The library is simply the files in `data/books/`
   (`config.book_ids()`).
3. **Scrape it:** `zawiyah scrape 12345`. Takes 5–15 minutes, and it is safe to interrupt
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
6. **Check the tags:** `zawiyah load`, then `zawiyah tag`. Any chapter showing `—` has no tags.
   Add keywords to `RULES` where needed (see §7).
7. **Rebuild and test:** `zawiyah build`, then `zawiyah search <something from the new book>`
   and `zawiyah serve` to browse it.
8. **Publish:** commit `data/books/12345.json`, `docs/` and any code changes, then push.
   Also update the book table in `README.md`.

Note that adding a book changes the IDs of books loaded after it, and so their section
URLs.

## 7. Recipe: changing normalization or tags

**A normalization rule** (e.g. a new letter variant or prefix):

1. Edit the data in `arabic.py` (`LETTER_MAP`, `ARTICLE_PREFIXES`, …).
2. Run `zawiyah build`. The FTS index and `search.json` both store normalized text, so
   everything must be regenerated.
3. Tag keywords and scraper title matching pick up the change automatically. Rerun
   `zawiyah scrape` (offline) if title matching could be affected, and check
   `git diff --stat data/books`.
4. Check that the browser and Python still agree. A parity check: for a set of sample
   texts, compare `arabic.tokens(text)` in Python with `makeNormalizer(index.normalize)(text)`
   from `docs/assets/search.js` in Node. They must be identical. (Phase 3 turns this into
   an automated test.)

**A tag rule:** edit `RULES` in `tags.py`, run `zawiyah tag` to review, then
`zawiyah export`. IDs don't change, so `--fresh` isn't needed.

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
