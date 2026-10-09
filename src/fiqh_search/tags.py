"""Rule-based chapter tagging.

Each rule maps keywords to tags: a topic tag plus (usually) a broad category
tag. A chapter gets a rule's tags when any keyword is a substring of the
chapter name. Both sides are compared after arabic.normalize(), so write
keywords in ordinary spelling (جنائز، أطعمة) without the definite article.

Edit RULES, then run `fiqh tag` to retag every chapter without reloading.
"""
import sqlite3

from fiqh_search.arabic import normalize

# (keywords, tags) — keywords in ordinary spelling, no ال.
RULES: list[tuple[list[str], list[str]]] = [
    (["طهار", "نجاس", "تيمم"],                   ["طهارة", "عبادات"]),
    (["صلا", "جمعة", "عيدين", "كسوف", "استسقاء"], ["صلاة", "عبادات"]),
    (["جنائز"],                                   ["جنائز", "عبادات"]),
    (["زكا"],                                     ["زكاة", "عبادات"]),
    (["صيام", "صوم", "اعتكاف"],                   ["صيام", "عبادات"]),
    (["حج"],                                      ["حج", "عبادات"]),
    (["أضحي", "ضحايا"],                           ["أضحية", "عبادات"]),
    (["بيوع", "معاملات"],                         ["بيوع", "معاملات"]),
    (["فرائض"],                                   ["مواريث", "معاملات"]),
    (["وصايا"],                                   ["وصايا", "معاملات"]),
    (["عتق"],                                     ["عتق", "معاملات"]),
    (["نكاح"],                                    ["نكاح", "أحوال شخصية"]),
    (["طلاق"],                                    ["طلاق", "أحوال شخصية"]),
    (["جنايات"],                                  ["جنايات", "عقوبات"]),
    (["حدود"],                                    ["حدود", "عقوبات"]),
    (["جهاد"],                                    ["جهاد"]),
    (["صيد", "ذبائح"],                            ["صيد وذبائح"]),
    (["أطعمة"],                                   ["أطعمة"]),
    (["سبق", "رمي"],                              ["سبق ورمي"]),
    (["أيمان", "نذور"],                           ["أيمان ونذور"]),
    (["أقضية", "شهادات"],                         ["قضاء وشهادات"]),
]

_NORMALIZED_RULES = [([normalize(k) for k in keywords], tags) for keywords, tags in RULES]


def tags_for(chapter_name: str) -> list[str]:
    name = normalize(chapter_name)
    tags: list[str] = []
    for keywords, rule_tags in _NORMALIZED_RULES:
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
