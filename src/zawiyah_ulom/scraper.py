"""Scrape books from shamela.ws into data/books/{shamela_id}.json.

Shamela layout (as of 2026-10):
  /book/{id}                        book card + top-level TOC (ul > li)
  /ajax/titlechilds/{id}/{title_id} nested TOC entries for one title
  /book/{id}/{page_id}              one page; text in div.nass > p

A TOC entry only gives the page a section starts on. Several sections can
start on the same page, so each section's start is located inside the page
text by searching for its title. If the title isn't found, the boundary
page is given to both neighbouring sections (overlap beats losing text).

Every HTTP response is cached under data/raw/, so re-runs are offline and
an interrupted scrape resumes where it stopped.

CLI:  zawiyah scrape [shamela_id ...]
"""
import json
import re
import time
from pathlib import Path

import requests
import truststore
from bs4 import BeautifulSoup

from zawiyah_ulom.arabic import fuzzy_pattern, has_arabic, normalize, to_western_digits, tokens
from zawiyah_ulom.config import BOOKS_DIR, OUTLINES_DIR, RAW_DIR, REQUEST_DELAY
from zawiyah_ulom.config import SHAMELA_BASE as BASE

# Use the OS certificate store; certifi's bundle fails to verify shamela.ws.
truststore.inject_into_ssl()

_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (zawiyah-ulom research scraper)"


def fetch(url: str, cache_file: Path, headers: dict | None = None) -> str:
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    for attempt in range(3):
        try:
            time.sleep(REQUEST_DELAY)
            resp = _session.get(url, timeout=30, headers=headers)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt == 2:
                raise
            print(f"  retry {url}: {exc}")
            time.sleep(5 * (attempt + 1))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(resp.text, encoding="utf-8")
    return resp.text


# ---------------------------------------------------------------- book card

def parse_card(soup: BeautifulSoup) -> dict:
    """Read the 'بطاقة الكتاب' block: 'الكتاب: ...', 'المؤلف: ...', etc."""
    card = soup.select_one("div.nass div[style*='line-height']")
    fields = {}
    for line in card.get_text("\n", strip=True).split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()

    author_full = fields.get("المؤلف", "")
    death = re.search(r"\(ت\s*([٠-٩0-9]+)\s*هـ?\)", author_full)
    author_link = soup.select_one("a[href*='/author/']")

    return {
        "title": fields.get("الكتاب"),
        "author": {
            "name": author_link.get_text(strip=True) if author_link else author_full,
            "full_arabic_name": re.sub(r"\s*\(ت[^)]*\)\s*$", "", author_full),
            "year_death_hijri": int(to_western_digits(death.group(1))) if death else None,
            "url": author_link["href"] if author_link else None,
        },
        "publisher": fields.get("الناشر"),
        "card": fields,
    }


# --------------------------------------------------------------------- TOC

def _parse_toc_items(ul, book_id: int) -> list[dict]:
    items = []
    for li in ul.find_all("li", recursive=False):
        link = li.find("a", href=re.compile(rf"/book/{book_id}/\d+"))
        if not link:
            continue
        expand = li.select_one("a.exp_bu")
        items.append({
            "title": link.get_text(strip=True).lstrip("•").strip(),
            "page": int(link["href"].rstrip("/").rsplit("/", 1)[-1].split("#")[0]),
            "children": _fetch_children(book_id, expand["data-id"]) if expand else [],
        })
    return items


def _fetch_children(book_id: int, title_id: str) -> list[dict]:
    html = fetch(f"{BASE}/ajax/titlechilds/{book_id}/{title_id}",
                 RAW_DIR / str(book_id) / f"toc_{title_id}.html")
    ul = BeautifulSoup(html, "html.parser").find("ul")
    return _parse_toc_items(ul, book_id) if ul else []


def _flatten(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        out.append({"title": item["title"], "page": item["page"]})
        out.extend(_flatten(item["children"]))
    return out


PREAMBLE_TITLE = "مقدمة الكتاب"
PREAMBLE_MIN_CHARS = 100  # shorter text before the first TOC entry is just a title page


def build_outline(toc: list[dict]) -> list[dict]:
    """Top-level TOC entries become chapters; everything below is flattened
    into that chapter's sections. Text between a chapter's start and its
    first sub-entry becomes an intro section named after the chapter:
      - first sub-entry on a later page: always (as before)
      - on the same page: an "intro" entry, kept only if the chapter heading
        is found in the text and something stands between it and the first
        sub-entry (fill_content drops it otherwise)
    A "preamble" chapter, «مقدمة الكتاب», holds any text before the first TOC
    entry (e.g. the author's introduction when the TOC starts at page 3); it
    is dropped when that text is only a title page (see fill_content)."""
    chapters = [{"title": PREAMBLE_TITLE, "page": 1,
                 "sections": [{"title": PREAMBLE_TITLE, "page": 1, "preamble": True}]}]
    for item in toc:
        sections = _flatten(item["children"])
        if not sections or sections[0]["page"] > item["page"]:
            sections.insert(0, {"title": item["title"], "page": item["page"]})
        else:
            sections.insert(0, {"title": item["title"], "page": item["page"], "intro": True})
        chapters.append({"title": item["title"], "page": item["page"], "sections": sections})
    return chapters


# ---------------------------------------------------- outline overrides

def load_outline(book_key: int | str) -> dict | None:
    """data/outlines/<id>.json, for books whose Shamela TOC is flat (only فصول)
    while the كتاب/باب headings are printed inline in the text. Keys:
      chapters   [{"title", "page", "loose"?}] in reading order (required)
      ornaments  phrases Shamela rendered from decorative print ornaments around
                 headings; removed from page text only where dash-wrapped ("- … -")"""
    path = OUTLINES_DIR / f"{book_key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


_ornament_cache: dict[int, re.Pattern | None] = {}


def _ornament_re(book_id: int) -> re.Pattern | None:
    if book_id not in _ornament_cache:
        phrases = (load_outline(book_id) or {}).get("ornaments", [])
        _ornament_cache[book_id] = re.compile(
            r"-\s*(?:" + "|".join(fuzzy_pattern(p) for p in phrases) + r")\s*َ?\s*-"
        ) if phrases else None
    return _ornament_cache[book_id]


def heading_pattern(title: str, loose: bool = False) -> str:
    """An inline heading: the title, then a separator ("-", "="), end of line,
    or end of page. With loose=True (outline "loose": true), the title alone,
    for headings that run straight into the text (مُقَدّمَة بِسم الله ...)."""
    return fuzzy_pattern(title) + ("" if loose else r"\s*(?:[-=]|$)")


def merge_headings(read, toc: list[dict], headings: list[dict]) -> list[dict]:
    """Flatten the TOC and insert the outline's headings in text order, each
    marked heading=True. A heading that is already a TOC entry (same normalized
    title) marks that entry instead of being added twice."""
    flat = _flatten(toc)
    for h in headings:
        same = next((e for e in flat if normalize(e["title"]) == normalize(h["title"])), None)
        if same:
            same["heading"] = True
            continue
        text = read(h["page"])
        loose = bool(h.get("loose"))
        m = re.compile(heading_pattern(h["title"], loose), re.M).search(text)
        if not m:
            raise ValueError(f"outline heading {h['title']!r} not found on page {h['page']}")
        # Insert before the first TOC entry that starts after the heading.
        pos = len(flat)
        for i, e in enumerate(flat):
            if e.get("heading"):
                continue
            if e["page"] > h["page"] or (e["page"] == h["page"] and find_title(text, e["title"], 0) > m.start()):
                pos = i
                break
        flat.insert(pos, {"title": h["title"], "page": h["page"], "heading": True, "loose": loose})
    return flat


def group_by_headings(flat: list[dict]) -> list[dict]:
    """Turn a located, filled flat list into chapters: each heading starts a
    chapter, and its own text (the bab's opening, before its first فصل) becomes
    the chapter's intro section, titled "مقدمة <chapter>" so it reads as a
    section of its own (e.g. مقدمة كتاب الطهارة). A chapter that is itself a
    مقدمة keeps its title."""
    chapters: list[dict] = []
    for s in flat:
        s.pop("loose", None)
        if s.pop("heading", False):
            chapters.append({"title": s["title"], "page": s["page"], "sections": []})
            if not s["content"]:
                continue
            if not s["title"].startswith("مقدمة"):
                s["title"] = f"مقدمة {s['title']}"
        elif not chapters:  # text before the first heading
            chapters.append({"title": s["title"], "page": s["page"], "sections": []})
        chapters[-1]["sections"].append(s)
    return chapters


# ------------------------------------------------------------------- pages

def page_text(book_id: int, page: int) -> str:
    html = fetch(f"{BASE}/book/{book_id}/{page}", RAW_DIR / str(book_id) / f"p{page}.html")
    nass = BeautifulSoup(html, "html.parser").select_one("div.nass")
    if not nass:
        return ""
    ornament = _ornament_re(book_id)
    paragraphs = []
    for p in nass.find_all("p"):
        for btn in p.select("a.btn_tag"):
            btn.decompose()
        text = p.get_text()
        if ornament:  # "- ﷺ - باب X - ﷺ -" -> " - باب X - " (keeps the heading separator)
            text = ornament.sub(" - ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def last_page(book_id: int) -> int:
    html = fetch(f"{BASE}/book/{book_id}/1", RAW_DIR / str(book_id) / "p1.html")
    soup = BeautifulSoup(html, "html.parser")
    last = soup.find("a", string=">>")
    return int(last["href"].split("#")[0].rsplit("/", 1)[-1]) if last else 1


HEADING_WORDS = ("فصل", "باب", "كتاب", "فرع", "تنبيه", "خاتمة", "مقدمة")


def find_title(text: str, title: str, start: int) -> int:
    """Offset of `title` in page `text` at or after `start`, or -1.
    Matching ignores tashkeel and letter variants (see arabic.fuzzy_pattern):
    TOC titles are plain while some books' text is fully voweled."""
    if not has_arabic(title):
        return text.find(title, start)
    m = re.compile(fuzzy_pattern(title)).search(text, start)
    if m:
        return m.start()
    # Fallback: some books (e.g. بشرى الكريم) head a section with the matn,
    # "(فصل: يكره) ..." rather than the TOC title "فصل: في الماء المكروه".
    # Accept a paragraph that opens with "(" + the title's first heading word.
    first = title.split(":")[0].split()[0] if title.split() else ""
    if first in HEADING_WORDS:
        m = re.compile(r"(?m)^\(\s*" + fuzzy_pattern(first)).search(text, start)
        if m:
            return m.start()
    return -1


def fill_content(read, chapters: list[dict], final_page: int, progress=None,
                 skip_pages: frozenset[int] = frozenset()) -> dict:
    """Attach `content` to every section, reading pages with read(page) -> text.
    Returns locate stats.
    progress(pages_read, final_page), if given, is called after each new page.
    skip_pages are read as empty (heading-only pages, see fix_heading_pages)."""
    sections = [s for ch in chapters for s in ch["sections"]]
    texts = {}

    def text(p):
        if p not in texts:
            texts[p] = "" if p in skip_pages else read(p)
            if progress:
                progress(len(texts), final_page)
            if len(texts) % 25 == 0:
                print(f"  {len(texts)} pages...")
        return texts[p]

    # 1. Locate each section's start offset within its start page.
    prev_page, prev_off = None, -1
    for s in sections:
        search_from = prev_off + 1 if s["page"] == prev_page else 0
        s["_prev"] = (prev_page, prev_off)
        if s.get("preamble"):  # everything from the very start of the book
            s["located"], s["offset"] = True, 0
            prev_page, prev_off = s["page"], 0
            continue
        if s.get("heading"):  # outline heading: stricter inline-heading match
            m = re.compile(heading_pattern(s["title"], s.get("loose", False)), re.M).search(text(s["page"]), search_from)
            off = m.start() if m else -1
        else:
            off = find_title(text(s["page"]), s["title"], search_from)
        if off >= 0:  # split before a "[• title]", "(title" or "=title" opener, not inside it
            opener = r"[\[(•=\-][\[(•=\-\s]*$" if s.get("heading") else r"[\[(•][\[(•\s]*$"
            marker = re.search(opener, text(s["page"])[:off])
            off = marker.start() if marker else off
        s["located"] = off >= 0
        s["offset"] = off if off >= 0 else (prev_off if s["page"] == prev_page else 0)
        s["offset"] = max(s["offset"], 0)
        prev_page, prev_off = s["page"], s["offset"]
        if s.get("intro") and not s["located"]:
            prev_page, prev_off = s.get("_prev", (prev_page, prev_off))

    # A chapter intro whose heading isn't in the text can't be placed: drop it
    # (its text stays with the previous section, as before intros existed).
    sections = [s for s in sections if not (s.get("intro") and not s["located"])]
    for ch in chapters:
        ch["sections"] = [s for s in ch["sections"] if not (s.get("intro") and not s["located"])]

    # 2. Slice text between consecutive starts.
    for i, s in enumerate(sections):
        if i + 1 < len(sections):
            nxt = sections[i + 1]
            end_page = nxt["page"]
            end_off = nxt["offset"] if nxt["located"] else None  # None = whole page
            if s.get("preamble") and not nxt["located"]:
                end_off = 0  # never share a page: the preamble is only what precedes it
        else:
            end_page, end_off = final_page, None

        if end_page == s["page"]:
            body = text(s["page"])[s["offset"]:end_off]
            if not body.strip():  # unlocated neighbour on same page: share the page
                body = text(s["page"])[s["offset"]:]
            parts = [body]
        else:
            parts = [text(s["page"])[s["offset"]:]]
            parts += [text(p) for p in range(s["page"] + 1, end_page)]
            if end_off != 0:
                parts.append(text(end_page)[:end_off])
        content = "\n".join(p for p in parts if p).strip()
        # The title is stored separately: drop a leading line that is only the
        # heading (e.g. "[• السواك]", "• السَلَم"), and the "* * *" separator
        # that precedes the next section's heading.
        if s.get("heading"):  # inline heading: drop "كتاب الطهارة =" / "باب التيمم -"
            heading = re.match(r"[=\-\s]*" + heading_pattern(s["title"], s.get("loose", False)) + r"\s*", content, re.M)
            if heading:
                content = content[heading.end():]
        elif has_arabic(s["title"]):
            heading = re.match(r"[\[(•\"«\s]*" + fuzzy_pattern(s["title"]) + r"[\])\"»\s]*?(?:\n|$)", content)
            if heading:
                content = content[heading.end():]
        content = re.sub(r"\s*\*\s*\*\s*\*\s*$", "", content).strip()
        s["content"] = "" if s.get("missing_text") else content  # only its heading was there
        s["end_page"] = end_page

    for ch in chapters:
        ch["sections"] = [
            s for s in ch["sections"]
            if not (s.get("intro") and not s["content"])
            and not (s.get("preamble") and len(s["content"]) < PREAMBLE_MIN_CHARS)]
        for s in ch["sections"]:
            for flag in ("intro", "preamble", "_prev"):
                s.pop(flag, None)
    chapters[:] = [ch for ch in chapters if ch["sections"] or "sections" not in ch]
    kept = [s for ch in chapters for s in ch["sections"]]
    return {"sections": len(kept), "located": sum(s["located"] for s in kept), "pages": len(texts)}


# -------------------------------------------------------------------- main

HEADING_PAGE_LOOKAHEAD = 5  # pages to search forward for a heading-only page's real text


def _heading_only(text: str, title: str) -> bool:
    """True if a page holds nothing but this section's heading. Shamela inserts
    such pages (printed page number 0) before some فصول."""
    return has_arabic(title) and re.fullmatch(
        r"[\s\[(•\"«]*" + fuzzy_pattern(title) + r"[\s\])\"»]*", text) is not None


def fix_heading_pages(read, chapters: list[dict]) -> set[int]:
    """Repair TOC entries that point at a heading-only page:
      - the next entry has the same title (a duplicate heading page): drop it
      - the heading opens a paragraph on one of the next pages (the page was
        out of order): move the entry there, then re-sort the chapter's sections
        into text order
      - otherwise the فصل's text is missing from Shamela's copy: keep the entry,
        flagged missing_text, so it's visible instead of silently empty
    Returns the heading-only pages that were dropped or moved away from: pass
    them to fill_content as skip_pages so their heading text doesn't end up in
    the neighbouring section. Books without such pages are unaffected."""
    skip: set[int] = set()
    for ch in chapters:
        secs = ch["sections"]
        kept = []
        for i, s in enumerate(secs):
            if s.get("preamble") or s.get("intro") or not _heading_only(read(s["page"]), s["title"]):
                kept.append(s)
                continue
            nxt = secs[i + 1] if i + 1 < len(secs) else None
            if nxt and tokens(nxt["title"]) == tokens(s["title"]):
                skip.add(s["page"])
                continue  # duplicate heading page
            opens = re.compile(r"(?m)^[\[(•\"«\s]*" + fuzzy_pattern(s["title"]))
            for q in range(s["page"] + 1, s["page"] + 1 + HEADING_PAGE_LOOKAHEAD):
                if opens.search(read(q)):
                    break
            else:
                s["missing_text"] = True
                kept.append(s)
                continue
            skip.add(s["page"])
            if any(o is not s and o["page"] == q and tokens(o["title"]) == tokens(s["title"]) for o in secs):
                continue  # another TOC entry already starts there: this one is a duplicate
            s["relocated_from"], s["page"] = s["page"], q
            kept.append(s)

        def text_position(s):
            off = find_title(read(s["page"]), s["title"], 0)
            return (s["page"], off if off >= 0 else 0)
        ch["sections"] = sorted(kept, key=text_position) if any("relocated_from" in s for s in kept) else kept
    return skip


def drop_repeated_sections(chapters: list[dict]) -> int:
    """Drop a section whose title and text are identical to an earlier one in
    the same chapter: Shamela sometimes repeats a whole page (e.g. 9973 has
    pages 101 and 103 identical, both listed in its TOC). Only exact repeats."""
    dropped = 0
    for ch in chapters:
        seen, kept = set(), []
        for s in ch["sections"]:
            key = (tuple(tokens(s["title"])), s["content"])
            if s["content"] and key in seen:
                dropped += 1
                continue
            seen.add(key)
            kept.append(s)
        ch["sections"] = kept
    return dropped


FLAT_TOC_MIN_ENTRIES = 15  # more top-level entries than this, none nested -> likely flat


# ------------------------------------------------------------------ sources
#
# A source knows one website. It provides the book card, the nested TOC, the
# page count and the text of a page; everything else (locating headings,
# slicing sections, the heading-page repairs) is shared. `key` names the
# book's files: data/books/<key>.json, data/raw/<key>/, data/outlines/<key>.json.
# For Shamela the key is the plain id, as it always was (note keys depend on it).

class ShamelaSource:
    name, label = "shamela", "المكتبة الشاملة"

    def __init__(self, book_id: int):
        self.id = book_id
        self.key = str(book_id)
        self.url = f"{BASE}/book/{book_id}"

    def _soup(self) -> BeautifulSoup:
        return BeautifulSoup(fetch(self.url, RAW_DIR / self.key / "toc.html"), "html.parser")

    def card_and_toc(self) -> tuple[dict, list[dict]]:
        soup = self._soup()
        return parse_card(soup), _parse_toc_items(soup.select_one(".betaka-index > ul"), self.id)

    def last_page(self) -> int:
        return last_page(self.id)

    def page_text(self, page: int) -> str:
        return page_text(self.id, page)

    def top_level(self) -> tuple[dict, list[dict]]:
        """Card and top-level TOC without fetching nested entries (for previews)."""
        soup = self._soup()
        if not soup.select_one(".betaka-index > ul"):
            raise ValueError(f"no book {self.id} on Shamela (no table of contents found)")
        chapters = []
        for li in soup.select(".betaka-index > ul > li"):
            link = li.find("a", href=re.compile(rf"/book/{self.id}/\d+"))
            if link:
                chapters.append({"title": link.get_text(strip=True).lstrip("•").strip(),
                                 "page": int(link["href"].split("#")[0].rsplit("/", 1)[-1]),
                                 "nested": li.select_one("a.exp_bu") is not None})
        return parse_card(soup), chapters

    def header(self) -> dict:
        """The book JSON's leading fields (kept exactly as before for Shamela books)."""
        return {"shamela_id": self.id, "url": self.url}


def get_source(name: str, book_id: int):
    if name == "shamela":
        return ShamelaSource(book_id)
    if name == "ketabonline":
        from zawiyah_ulom.ketabonline import KetabonlineSource
        return KetabonlineSource(book_id)
    raise ValueError(f"unknown source {name!r}")


def preview(src) -> dict:
    """Book card and top-level contents, from a couple of requests. Nested
    entries are not fetched; each chapter just says whether it has sub-entries."""
    meta, chapters = src.top_level()
    flat = len(chapters) > FLAT_TOC_MIN_ENTRIES and not any(c["nested"] for c in chapters)
    return {"source": src.name, "source_label": src.label, "source_id": src.id, "key": src.key,
            "url": src.url, **meta, "last_page": src.last_page(), "chapters": chapters,
            "flat_toc": flat, "has_outline": load_outline(src.key) is not None}


def scrape(src, out_dir: Path = BOOKS_DIR, progress=None) -> Path:
    """Scrape a book from any source into <out_dir>/<key>.json and return that path.
    progress(pages_read, total_pages) is called as pages are read."""
    print(f"Book {src.key}")
    meta, toc = src.card_and_toc()
    final = src.last_page()
    outline = load_outline(src.key)

    seen: set[int] = set()

    def read(page: int) -> str:  # every page read counts toward progress, in any pass
        if page not in seen:
            seen.add(page)
            if progress:
                progress(len(seen), final)
        return src.page_text(page)

    if outline:  # flat TOC: insert the outline's headings, locate everything, then group
        outline = outline["chapters"]
        flat = merge_headings(read, toc, outline)
        stats = fill_content(read, [{"sections": flat}], final)
        chapters = group_by_headings(flat)
        print(f"  {meta['title'][:60]} | outline: {len(outline)} headings | {final} pages")
    else:
        chapters = build_outline(toc)
        print(f"  {meta['title'][:60]} | {len(chapters)} chapters | {final} pages")
        skip = fix_heading_pages(read, chapters)
        if skip:
            print(f"  heading-only pages skipped: {sorted(skip)}")
        stats = fill_content(read, chapters, final, skip_pages=frozenset(skip))
        if dropped := drop_repeated_sections(chapters):
            print(f"  repeated sections dropped: {dropped}")
    print(f"  chapters: {len(chapters)}, sections: {sum(len(c['sections']) for c in chapters)}, "
          f"title located in text: {stats['located']} of {stats['sections']}")

    out = out_dir / f"{src.key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        **src.header(),
        "last_page": final,
        **meta,
        "chapters": chapters,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  -> {out}")
    return out


def scrape_book(book_id: int, out_dir: Path = BOOKS_DIR, progress=None) -> Path:
    """Scrape a Shamela book (kept for the CLI and existing callers)."""
    return scrape(ShamelaSource(book_id), out_dir, progress)


def preview_book(book_id: int) -> dict:
    return preview(ShamelaSource(book_id))
