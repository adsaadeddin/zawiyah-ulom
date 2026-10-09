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

Usage:  python scraper.py [shamela_id ...]
"""
import json
import re
import sys
import time
from pathlib import Path

import requests
import truststore
from bs4 import BeautifulSoup

from arabic import to_western_digits

# Use the OS certificate store; certifi's bundle fails to verify shamela.ws.
truststore.inject_into_ssl()

BASE = "https://shamela.ws"
BOOK_IDS = [35120, 6140, 26535]
DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "books"
REQUEST_DELAY = 1.0  # seconds between live requests; be polite

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


# ------------------------------------------------------------------- pages

def page_text(book_id: int, page: int) -> str:
    html = fetch(f"{BASE}/book/{book_id}/{page}", RAW_DIR / str(book_id) / f"p{page}.html")
    nass = BeautifulSoup(html, "html.parser").select_one("div.nass")
    if not nass:
        return ""
    paragraphs = []
    for p in nass.find_all("p"):
        for btn in p.select("a.btn_tag"):
            btn.decompose()
        text = re.sub(r"\s+", " ", p.get_text()).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def last_page(book_id: int) -> int:
    html = fetch(f"{BASE}/book/{book_id}/1", RAW_DIR / str(book_id) / "p1.html")
    soup = BeautifulSoup(html, "html.parser")
    last = soup.find("a", string=">>")
    return int(last["href"].split("#")[0].rsplit("/", 1)[-1]) if last else 1


_MARKS = "[ؐ-ًؚ-ٰٟۖ-ۭـ]*"
_VARIANTS = {"ا": "[اأإآٱ]", "أ": "[اأإآٱ]", "إ": "[اأإآٱ]", "آ": "[اأإآٱ]",
             "ي": "[يى]", "ى": "[يى]", "ة": "[ةه]", "ه": "[ةه]"}


def title_pattern(title: str) -> re.Pattern:
    """Match a TOC title inside page text regardless of tashkeel, letter
    variants, or punctuation/brackets between words — TOC titles are plain
    while some books' text is fully voweled (e.g. الْوضُوء)."""
    words = re.findall(r"[ء-ي]+", title)
    word_res = [_MARKS.join(_VARIANTS.get(ch, re.escape(ch)) for ch in w) for w in words]
    return re.compile(r"[^ء-ي]+".join(w + _MARKS for w in word_res))


HEADING_WORDS = ("فصل", "باب", "كتاب", "فرع", "تنبيه", "خاتمة", "مقدمة")


def find_title(text: str, title: str, start: int) -> int:
    if not re.search(r"[ء-ي]", title):
        return text.find(title, start)
    m = title_pattern(title).search(text, start)
    if m:
        return m.start()
    # Fallback: some books (e.g. بشرى الكريم) head a section with the matn,
    # "(فصل: يكره) ..." rather than the TOC title "فصل: في الماء المكروه".
    # Accept a paragraph that opens with "(" + the title's first heading word.
    first = title.split(":")[0].split()[0] if title.split() else ""
    if first in HEADING_WORDS:
        m = re.compile(r"(?m)^\(\s*" + title_pattern(first).pattern).search(text, start)
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
        off = find_title(text(s["page"]), s["title"], search_from)
        if off >= 0:  # split before a "[• title]" or "(title" opener, not inside it
            marker = re.search(r"[\[(•][\[(•\s]*$", text(s["page"])[:off])
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
        if re.search(r"[ء-ي]", s["title"]):
            heading = re.match(r"[\[(•\s]*" + title_pattern(s["title"]).pattern + r"[\])\s]*?(?:\n|$)", content)
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
    chapters = build_outline(toc)
    final = last_page(book_id)
    print(f"  {meta['title'][:60]} | {len(chapters)} chapters | {final} pages")

    stats = fill_content(book_id, chapters, final)
    print(f"  sections: {stats['sections']}, title located in text: {stats['located']}")

    out = OUT_DIR / f"{book_id}.json"
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ids = [int(a) for a in sys.argv[1:]] or BOOK_IDS
    for bid in ids:
        scrape_book(bid)
