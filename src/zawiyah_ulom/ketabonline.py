"""ketabonline.com (جامع الكتب الإسلامية) as a book source.

It has books that aren't on shamela.ws's public site, e.g. تحرير تنقيح اللباب
(92681), taken from the desktop "الشاملة الذهبية". The site's reader is backed by
a JSON API, so pages are read from there (~10 KB per page, not the 400 KB HTML):

  GET {API}/books/{id}                       card: title, description, authors, pages_count
  GET {API}/books/{id}/index?is_recursive=1  nested TOC: title, page_id, children
  GET {API}/books/{id}/page/{page_id}        one page: content = <p class="g-paragraph">…

Responses are cached in data/raw/ketabonline-<id>/ like Shamela pages, and the
book is saved as data/books/ketabonline-<id>.json, so ids never clash with Shamela.
"""
import json
import re

from bs4 import BeautifulSoup

from zawiyah_ulom.arabic import to_western_digits
from zawiyah_ulom.config import RAW_DIR
from zawiyah_ulom.scraper import fetch

API = "https://backend.ketabonline.com/api/v2"
SITE = "https://ketabonline.com/ar"
_HEADERS = {"Accept": "application/json", "Accept-Language": "ar"}
_DEATH = re.compile(r"\((?:ت|المتوفى)\s*:?\s*([٠-٩0-9]+)\s*هـ?\s*\)")


class KetabonlineSource:
    name, label = "ketabonline", "جامع الكتب الإسلامية (ketabonline)"

    def __init__(self, book_id: int):
        self.id = book_id
        self.key = f"ketabonline-{book_id}"
        self.url = f"{SITE}/books/{book_id}"
        self._raw = RAW_DIR / self.key

    def _get(self, path: str, cache_name: str) -> dict:
        return json.loads(fetch(f"{API}{path}", self._raw / cache_name, headers=_HEADERS))

    def _book(self) -> dict:
        data = self._get(f"/books/{self.id}", "book.json").get("data")
        if not data or not data.get("title"):
            raise ValueError(f"no book {self.id} on ketabonline")
        return data

    # ------------------------------------------------------------- card
    def _card(self) -> dict:
        """Same shape as scraper.parse_card. The description holds Shamela-style
        lines: 'الكتاب: …', 'المؤلف: … (المتوفى: 926 هـ)', 'المصدر: …'."""
        book = self._book()
        fields = {}
        for line in (book.get("description") or "").splitlines():
            key, sep, value = line.partition(":")
            if sep and value.strip():
                fields[key.strip()] = value.strip()
        author_full = fields.get("المؤلف") or fields.get("الناظم", "")  # poems: «الناظم»
        death = _DEATH.search(author_full)
        author = (book.get("authors") or [{}])[0]
        publisher = book.get("publisher")
        return {
            "title": fields.get("الكتاب") or re.sub(r"\s*\(\d+\)\s*$", "", book["title"]),
            "author": {
                "name": author.get("name") or author_full,
                "full_arabic_name": _DEATH.sub("", author_full).strip(" ،,"),
                "year_death_hijri": int(to_western_digits(death.group(1))) if death else None,
                "url": f"{SITE}/authors/{author['id']}" if author.get("id") else None,
            },
            "publisher": (publisher.get("name") if isinstance(publisher, dict) else publisher)
                         or fields.get("الناشر"),
            "card": {**fields, "المصدر الإلكتروني": book.get("source") or "ketabonline"},
        }

    # -------------------------------------------------------------- TOC
    def _index(self) -> list[dict]:
        return self._get(f"/books/{self.id}/index?is_recursive=1", "index.json")["data"]

    @staticmethod
    def _toc(items: list[dict]) -> list[dict]:
        return [{"title": it["title"].strip(), "page": it["page_id"],
                 "children": KetabonlineSource._toc(it.get("children") or [])} for it in items]

    def card_and_toc(self) -> tuple[dict, list[dict]]:
        return self._card(), self._toc(self._index())

    def top_level(self) -> tuple[dict, list[dict]]:
        return self._card(), [{"title": it["title"].strip(), "page": it["page_id"],
                               "nested": bool(it.get("children"))} for it in self._index()]

    def last_page(self) -> int:
        book = self._book()
        return book.get("pages_count") or max(
            (it["to_page_id"] for it in self._index()), default=1)

    # ------------------------------------------------------------ pages
    def page_text(self, page: int) -> str:
        """One line per block, in page order: paragraphs (<p class="g-paragraph">)
        and headings (<div class="g-title">, e.g. "باب الوضوء"), which the
        section boundaries depend on. Copy-link icons are removed."""
        content = self._get(f"/books/{self.id}/page/{page}", f"p{page}.json").get("content") or ""
        soup = BeautifulSoup(content, "html.parser")
        for a in soup.select("a.g-copy"):
            a.decompose()
        blocks = soup.find_all(lambda tag: tag.name == "p" or "g-title" in (tag.get("class") or []))
        lines = [re.sub(r"\s+", " ", b.get_text()).strip() for b in blocks] or [soup.get_text(" ", strip=True)]
        return "\n".join(t for t in lines if t)

    def header(self) -> dict:
        return {"source": self.name, "source_id": self.id, "key": self.key, "url": self.url}
