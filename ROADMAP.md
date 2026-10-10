# Roadmap

A personal tool for studying and teaching the Islamic sciences, within an Ash'ari, Shafi'i
and Sufi framework. It began with Shafi'i fiqh. Pace: about 2 hours a week.
Status markers: ✅ done · 🔄 in progress · ⬜ planned. Technical details for each part
are in [DEVELOPER.md](DEVELOPER.md).

## Done

| | Stage | What exists |
|---|---|---|
| ✅ | Foundation | Shamela scraper with a page cache. Search that ignores tashkeel, ال and letter variants. Topic tags. Flask JSON API. Public demo site on GitHub Pages |
| ✅ | Refactor, phase 1 | `src/` package, the `zawiyah` CLI, one Arabic normalization shared by Python and the browser, DEVELOPER.md |
| ✅ | Study notes | A note on every section: searchable, marked in each table of contents, safe across rebuilds |
| ✅ | قراءة مقارنة (study a matn with two commentaries) | Each matn paragraph with what each commentary says about it, side by side. Matn paragraphs are placed automatically in each commentary (tolerant of glosses and recension differences). Sets: متن أبي شجاع with فتح القريب (90% placed) and كفاية الأخيار (86%); المقدمة الحضرمية with بشرى الكريم (71%) and المنهاج القويم (98%) |
| ✅ | Public site, part 2 | The live site also shows the tarajem (read-only) and قراءة مقارنة (matn + commentaries side by side, without notes). Notes stay private: the export attaches an empty notes database |
| ✅ | Study view, part 2 | Notes inside the study view: on the matn and the commentaries, plus a narrow «ملاحظاتي» column with notes on the whole paragraph. A printable A4 lesson (commentaries in full, notes numbered, filter by type). Study sets managed from the app (add a third commentary column in one click) |
| ✅ | Notes on a passage | Select a sentence, then write a note on it, typed فائدة / سؤال / إشكال / للتدريس with a highlight colour per type. Survives rebuilds, can be filtered by type on the notes page, and is searchable |
| ✅ | Tarajem, part 1 | التراجم tab: add, edit and search scholars, teacher↔student links, authors linked from their books. Kept across rebuilds, and included in `zawiyah backup` |
| ✅ | Library tab | Add a Shamela book from the app: preview, background download, quality report, then add or discard. Details below |
| ✅ | Two sources | Books from shamela.ws and ketabonline.com (جامع الكتب الإسلامية), through the same tab |
| ✅ | Science tabs | الفقه، العقيدة، التصوف، علوم القرآن، تفاسير القرآن, plus «الكل». Every page is filtered to the current science. The library's «يُضاف إلى» picker chooses a new book's sciences (several allowed). Tags are per science |
| ✅ | علوم القرآن (1 book) | مناهل العرفان في علوم القرآن (الزرقاني), 19 chapters (17 مباحث), 204 sections. Footnotes shown as footnotes, which also improves المنهاج القويم and فتح القريب |
| ✅ | العقيدة (4 books) | عقيدة العوام (المرزوقي), جوهرة التوحيد (اللقاني), الخريدة البهية (الدردير), قواعد العقائد (الغزالي) |
| ✅ | Library | 6 books. The matn المقدمة الحضرمية with two of its commentaries (بشرى الكريم، المنهاج القويم), plus فتح القريب and كفاية الأخيار |

## ⬜ Next: several sciences, each with its own workspace

Agreed on 2026-10-09, before building. Each science (فن) is its own workspace: when you
work in فقه, you see no عقيدة or تصوف books, results, tags or notes, and the reverse.
It is still one project underneath, so the tarajem, notes and backups stay in one place.

```
                              المكتبة العلمية
  ┌────────┬─────────┬─────────┬────────────┬─────────────┐
  │  فقه   │  عقيدة  │  تصوف   │ علوم القرآن │ تفاسير القرآن │  + «الكل», only
  │ /fiqh  │/aqeedah │/tasawwuf│ /ulum-quran │   /tafsir    │    on purpose
  └───┬────┴────┬────┴────┬────┴─────┬──────┴──────┬──────┘
      │ everything below is filtered to the current science
  search · books & TOC · topic tags · notes · library tab · tarajem (filtered view)
  + a per-science structure type that adds navigation, e.g. by ayah (تفسير)
```

**Sciences and their structure type**

| Science | Slug | Structure | Extra navigation | When |
|---|---|---|---|---|
| فقه | `/fiqh` | book (كتاب/باب/فصل) | — | first build (existing books) |
| عقيدة | `/aqeedah` | book | — | first build |
| تصوف | `/tasawwuf` | book | — | first build |
| علوم القرآن | `/ulum-quran` | book | — | first build |
| تفاسير القرآن | `/tafsir` | **ayah**: commentary keyed to surah:ayah | Go to an ayah (e.g. البقرة ٢٥٥) and see every tafsir in the library on it, side by side | own phase |
| حديث | `/hadith` | **hadith**: numbered, isnad + matn | By hadith number or narrator, linked to the tarajem chains | own phase |

The first build creates all the workspaces, so books of any science can be added and searched
as text straight away. The ayah and hadith navigation are added in their own phases, as each
needs its books' structure parsed (ayah references, isnad).

تفسير الأحلام was considered and left out (2026-10-09): Adnan plans a separate project for it.

**Decisions**

| Question | Decision |
|---|---|
| Separate per science? | Yes, as workspaces. One database underneath, with everything filtered by science |
| Tarajem | **Shared**, one record per scholar. Inside a science it shows that science's scholars by default, with a switch to show all |
| Cross-science search | **Yes, only on request**: a separate «الكل» page. Never mixed in otherwise |
| Book ↔ science | **A book can belong to several sciences** (e.g. إحياء علوم الدين in both تصوف and فقه). It appears in each of those workspaces |
| Framework | عقيدة: Ash'ari books and terms (الإلهيات، النبوات، السمعيات…). فقه: Shafi'i. تصوف: its own science and topics. Book suggestions for every science come from these traditions |

**What each part does**

- **Sciences.** A `Science` table (slug, Arabic name, order, **structure type**:
  book / ayah / hadith), extendable from the app, and a `BookScience` link
  table, since a book can have several.
- **Opening the app.** It opens in the last science you used. Tabs in the header switch
  between sciences, and URLs carry the science (`/fiqh/…`, `/aqeedah/…`).
- **Search, books, tags and notes** are all limited to the current science's books.
- **A book in several sciences** shows up in each of their workspaces. Its notes appear
  wherever the book does.
- **Tags** are defined per science. A chapter of a book in several sciences can get tags
  from each of those sciences' rules, but each workspace shows only its own.
- **Synonyms** (later) are defined per science, in that science's terms.
- **Reading a section.** Its address stays `/read/section/<id>`, and the workspace you came
  from is remembered, so the header and links stay in your current science.
- **Library tab.** Adding a book assigns it to the current science. You can add more
  sciences, and Shamela's own category can be suggested.
- **Existing books.** The six current books become فقه automatically. Nothing changes
  for them, and your notes and tarajem are untouched.
- **Public demo site.** It stays the fiqh library for now.

**Later phases with their own navigation:**
- تفاسير القرآن: by surah and ayah
- حديث: by hadith number or narrator, which also builds on the tarajem chains

## ✅ «المكتبة» tab (add books from the app)

Add a book from shamela.ws or ketabonline.com from the app, without the command line or code changes.

1. **Library list.** Every book with its author, number of chapters and sections,
   section-matching score, and how many of your notes it has.
2. **Add a book.** Paste a Shamela link (`https://shamela.ws/book/9973`) or just the
   number. Anything other than a shamela.ws book link is rejected, as is a book
   already in the library.
3. **Preview, before downloading.** Title, author, death year, publisher, page count
   and the contents (chapters and sections). Includes a warning when the contents
   are **flat** (only فصول, no كتاب/باب), which needs a chapter-headings file like
   المقدمة الحضرمية.
4. **Background download.** A progress bar (pages fetched / total). You can keep using the
   app meanwhile. One download at a time, and interrupted downloads resume, thanks to
   the page cache.
5. **Quality report** when the download finishes:
   - sections matched to their heading in the text, e.g. 118/124, with the
     unmatched ones listed
   - chapters with no tag (they may need tag rules)
   - stray markers at the start or end of sections
   - a sample of sections to look at
6. **Add or discard.** The downloaded book waits in a staging area (`data/staging/`).
   - **أضف إلى المكتبة** loads it. Your notes, tarajem and the existing books' IDs
     are untouched.
   - **تجاهل** discards it.
7. **No delete button** (your choice, 2026-10-09). Removing a book is a manual step:
   delete `data/books/<id>.json`, then run `zawiyah build`. Notes on it are kept and shown as
   unlinked.
8. **No more book list in the code.** The library is whatever is in `data/books/`, so
   `config.BOOK_IDS` goes away.

Limits: the page detects flat contents but can't fix them. Writing the
chapter-headings file stays a manual step. Like notes and tarajem, the tab runs only
locally, and the public site changes only when you choose to push.

## 🔄 Next: Phase 2, grow the tarajem

- **You:** enter the scholars you meet while studying, starting with the four
  authors and the scholars they cite most (النووي، الرافعي، ابن حجر، الرملي…).
- **Several names per scholar.** E.g. النووي is also «الشيخ» or «المصنف», depending on
  the book.
- **«العلماء المذكورون» on each section page.** Names in the text are linked to
  profiles, and ambiguous names are offered for you to confirm, not guessed.

## ⬜ Phase 3: chain visualization

- A graph of a scholar's teachers and students, a few generations in each direction.
- The chain between two scholars (e.g. you → … → الشافعي).

## ⬜ Phase 4: teaching tools

- Matn beside its commentaries, فصل by فصل: المقدمة الحضرمية ↔ بشرى الكريم ↔ المنهاج
  القويم, with your note on the same page.
- Lesson sheets: a chapter with your notes, printable or as a PDF.
- One topic across all the books, side by side.
- A synonym list (فرائض ↔ فروض …).

## ⬜ Phase 5: scale

- More books, through the «المكتبة» tab.
- Mobile layout, and faster search as the library grows.

## ⬜ Phase 6: question answering with citations

- Answers that quote and link the exact passages from your books, notes and tarajem.
- Built with retrieval, not fine-tuning. A study aid, not a fatwa tool.

## Ongoing: quality

- Tests for what can't be rebuilt: notes and tarajem surviving rebuilds, and
  normalization staying identical in Python and the browser.
- Clearer scraper errors, and validation of book JSON files.
- Type hints, completed gradually.

## Decisions

- The code is public. Notes and tarajem stay local, in git-ignored databases.
- Nothing is pushed without asking first.
