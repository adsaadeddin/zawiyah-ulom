"""Rule-based chapter tagging.

Each rule maps keywords (matched as substrings of the *normalized* chapter
name) to tags: a topic tag plus a broad category tag. Edit RULES to adjust;
re-run `python tags.py` to retag every chapter without re-loading books.
"""
import sqlite3
import sys

from arabic import normalize
from database import get_connection, init_db

# (keywords, tags). Keywords are normalized forms: no ال, ة->ه, ى->ي, ئ->ي.
RULES = [
    (["طهار", "نجاس", "تيمم"],                   ["طهارة", "عبادات"]),
    (["صلا", "جمعه", "عيدين", "كسوف", "استسقاء"], ["صلاة", "عبادات"]),
    (["جنايز"],                                   ["جنائز", "عبادات"]),
    (["زكا"],                                     ["زكاة", "عبادات"]),
    (["صيام", "صوم", "اعتكاف"],                   ["صيام", "عبادات"]),
    (["حج"],                                      ["حج", "عبادات"]),
    (["اضحي", "ضحايا"],                           ["أضحية", "عبادات"]),
    (["بيوع", "معاملات"],                         ["بيوع", "معاملات"]),
    (["فرايض"],                                   ["مواريث", "معاملات"]),
    (["وصايا"],                                   ["وصايا", "معاملات"]),
    (["عتق"],                                     ["عتق", "معاملات"]),
    (["نكاح"],                                    ["نكاح", "أحوال شخصية"]),
    (["طلاق"],                                    ["طلاق", "أحوال شخصية"]),
    (["جنايات"],                                  ["جنايات", "عقوبات"]),
    (["حدود"],                                    ["حدود", "عقوبات"]),
    (["جهاد"],                                    ["جهاد"]),
    (["صيد", "ذبايح"],                            ["صيد وذبائح"]),
    (["اطعمه"],                                   ["أطعمة"]),
    (["سبق", "رمي"],                              ["سبق ورمي"]),
    (["ايمان", "نذور"],                           ["أيمان ونذور"]),
    (["اقضيه", "شهادات"],                         ["قضاء وشهادات"]),
]


def tags_for(chapter_name: str) -> list[str]:
    name = normalize(chapter_name)
    tags = []
    for keywords, rule_tags in RULES:
        if any(k in name for k in keywords):
            tags += [t for t in rule_tags if t not in tags]
    return tags


def tag_chapters(conn: sqlite3.Connection) -> int:
    """Recompute ChapterTag for every chapter. Returns links created."""
    conn.execute("DELETE FROM ChapterTag")
    links = 0
    for ch in conn.execute("SELECT id, name FROM Chapter").fetchall():
        for tag in tags_for(ch["name"]):
            conn.execute("INSERT OR IGNORE INTO Tag (name) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT id FROM Tag WHERE name = ?", (tag,)).fetchone()["id"]
            conn.execute("INSERT INTO ChapterTag (chapter_id, tag_id) VALUES (?, ?)",
                         (ch["id"], tag_id))
            links += 1
    # Drop tags no chapter uses any more (e.g. after editing RULES).
    conn.execute("DELETE FROM Tag WHERE id NOT IN (SELECT tag_id FROM ChapterTag)")
    return links


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    conn = get_connection()
    init_db(conn)
    with conn:
        print(f"{tag_chapters(conn)} chapter-tag links")
        for ch in conn.execute("SELECT name FROM Chapter ORDER BY book_id, chapter_order"):
            print(f"  {ch['name'][:50]:50} -> {', '.join(tags_for(ch['name'])) or '—'}")
    conn.close()
