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

CLI:  fiqh scrape [shamela_id ...]
"""
import json
import re
import time
from pathlib import Path

import requests
import truststore
from bs4 import BeautifulSoup

from fiqh_search.arabic import fuzzy_pattern, has_arabic, normalize, to_western_digits
from fiqh_search.config import BOOKS_DIR, OUTLINES_DIR, RAW_DIR, REQUEST_DELAY
from fiqh_search.config import SHAMELA_BASE as BASE

# Use the OS certificate store; certifi's bundle fails to verify shamela.ws.
truststore.inject_into_ssl()

_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (fiqh-search research scraper)"


def fetch(url: str, cache_file: Path) -> str:
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    for attempt in range(3):
        try:
            time.sleep(REQUEST_DELAY)
            resp = _session.get(url, timeout=30)
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


def build_outline(toc: list[dict]) -> list[dict]:
    """Top-level TOC entries become chapters; everything below is flattened
    into that chapter's sections. Text between a chapter's start and its
    first sub-entry becomes an intro section named after the chapter."""
    chapters = []
    for item in toc:
        sections = _flatten(item["children"])
        if not sections or sections[0]["page"] > item["page"]:
            sections.insert(0, {"title": item["title"], "page": item["page"]})
        chapters.append({"title": item["title"], "page": item["page"], "sections": sections})
    return chapters


# ---------------------------------------------------- outline overrides

def load_outline(book_id: int) -> dict | None:
    """data/outlines/<id>.json, for books whose Shamela TOC is flat (only فصول)
    while the كتاب/باب headings are printed inline in the text. Keys:
      chapters   [{"title", "page", "loose"?}] in reading order (required)
      ornaments  phrases Shamela rendered from decorative print ornaments around
                 headings; removed from page text only where dash-wrapped ("- … -")"""
    path = OUTLINES_DIR / f"{book_id}.json"
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


def merge_headings(book_id: int, toc: list[dict], headings: list[dict]) -> list[dict]:
    """Flatten the TOC and insert the outline's headings in text order, each
    marked heading=True. A heading that is already a TOC entry (same normalized
    title) marks that entry instead of being added twice."""
    flat = _flatten(toc)
    for h in headings:
        same = next((e for e in flat if normalize(e["title"]) == normalize(h["title"])), None)
        if same:
            same["heading"] = True
            continue
        text = page_text(book_id, h["page"])
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


def fill_content(book_id: int, chapters: list[dict], final_page: int) -> dict:
    """Attach `content` to every section. Returns locate stats."""
    sections = [s for ch in chapters for s in ch["sections"]]
    texts = {}

    def text(p):
        if p not in texts:
            texts[p] = page_text(book_id, p)
            if len(texts) % 25 == 0:
                print(f"  {len(texts)} pages...")
        return texts[p]

    # 1. Locate each section's start offset within its start page.
    found = 0
    prev_page, prev_off = None, -1
    for s in sections:
        search_from = prev_off + 1 if s["page"] == prev_page else 0
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
        found += s["located"]
        s["offset"] = off if off >= 0 else (prev_off if s["page"] == prev_page else 0)
        s["offset"] = max(s["offset"], 0)
        prev_page, prev_off = s["page"], s["offset"]

    # 2. Slice text between consecutive starts.
    for i, s in enumerate(sections):
        if i + 1 < len(sections):
            nxt = sections[i + 1]
            end_page = nxt["page"]
            end_off = nxt["offset"] if nxt["located"] else None  # None = whole page
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
            heading = re.match(r"[\[(•\s]*" + fuzzy_pattern(s["title"]) + r"[\])\s]*?(?:\n|$)", content)
            if heading:
                content = content[heading.end():]
        s["content"] = re.sub(r"\s*\*\s*\*\s*\*\s*$", "", content).strip()
        s["end_page"] = end_page

    return {"sections": len(sections), "located": found, "pages": len(texts)}


# -------------------------------------------------------------------- main

def scrape_book(book_id: int) -> Path:
    print(f"Book {book_id}")
    toc_html = fetch(f"{BASE}/book/{book_id}", RAW_DIR / str(book_id) / "toc.html")
    soup = BeautifulSoup(toc_html, "html.parser")
    meta = parse_card(soup)
    toc = _parse_toc_items(soup.select_one(".betaka-index > ul"), book_id)
    final = last_page(book_id)
    outline = load_outline(book_id)

    if outline:  # flat TOC: insert the outline's headings, locate everything, then group
        outline = outline["chapters"]
        flat = merge_headings(book_id, toc, outline)
        stats = fill_content(book_id, [{"sections": flat}], final)
        chapters = group_by_headings(flat)
        print(f"  {meta['title'][:60]} | outline: {len(outline)} headings | {final} pages")
    else:
        chapters = build_outline(toc)
        print(f"  {meta['title'][:60]} | {len(chapters)} chapters | {final} pages")
        stats = fill_content(book_id, chapters, final)
    print(f"  chapters: {len(chapters)}, sections: {sum(len(c['sections']) for c in chapters)}, "
          f"title located in text: {stats['located']} of {stats['sections']}")

    out = BOOKS_DIR / f"{book_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "shamela_id": book_id,
        "url": f"{BASE}/book/{book_id}",
        "last_page": final,
        **meta,
        "chapters": chapters,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  -> {out}")
    return out
