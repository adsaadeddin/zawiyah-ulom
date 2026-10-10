"""Rule-based chapter tagging.

Each rule maps keywords to tags: a topic tag plus (usually) a broad category
tag. A chapter gets a rule's tags when any keyword is a substring of the
chapter name. Both sides are compared after arabic.normalize(), so write
keywords in ordinary spelling (جنائز، أطعمة) without the definite article.

Rules are per science (RULES_BY_SCIENCE): a book's chapters are tagged only
with the rules of the sciences it belongs to. Fiqh keywords must not tag an
عقيدة book: «الإعتقاد» contains «عتق», and «الإيمان» (faith) normalizes like
«الأيمان» (oaths). Sciences without rules yet get no tags.

Edit the rules, then run `zawiyah tag` to retag every chapter without reloading.
"""
import sqlite3

from zawiyah_ulom.arabic import normalize

# Fiqh rules: (keywords, tags) — keywords in ordinary spelling, no ال.
RULES: list[tuple[list[str], list[str]]] = [
    (["طهار", "نجاس", "تيمم"],                   ["طهارة", "عبادات"]),
    (["صلا", "جمعة", "عيدين", "كسوف", "استسقاء"], ["صلاة", "عبادات"]),
    (["جنائز"],                                   ["جنائز", "عبادات"]),
    (["زكا"],                                     ["زكاة", "عبادات"]),
    (["صيام", "صوم", "اعتكاف"],                   ["صيام", "عبادات"]),
    (["حج", "نسك"],                               ["حج", "عبادات"]),
    (["أضحي", "ضحايا"],                           ["أضحية", "عبادات"]),
    (["بيوع", "معاملات"],                         ["بيوع", "معاملات"]),
    (["فرائض"],                                   ["مواريث", "معاملات"]),
    (["وصايا"],                                   ["وصايا", "معاملات"]),
    (["عتق"],                                     ["عتق", "معاملات"]),
    (["نكاح", "صداق"],                            ["نكاح", "أحوال شخصية"]),
    (["طلاق"],                                    ["طلاق", "أحوال شخصية"]),
    (["جنايات"],                                  ["جنايات", "عقوبات"]),
    (["حدود"],                                    ["حدود", "عقوبات"]),
    (["جهاد", "كتاب السير"],                      ["جهاد"]),
    (["صيد", "ذبائح"],                            ["صيد وذبائح"]),
    (["أطعمة"],                                   ["أطعمة"]),
    (["سبق", "رمي"],                              ["سبق ورمي"]),
    (["أيمان", "نذور"],                           ["أيمان ونذور"]),
    (["أقضية", "شهادات"],                         ["قضاء وشهادات"]),
]

# science slug -> rules. Add عقيدة / تصوف … topics here when they're defined.
RULES_BY_SCIENCE: dict[str, list[tuple[list[str], list[str]]]] = {
    "fiqh": RULES,
}
_NORMALIZED = {sci: [([normalize(k) for k in kws], tags) for kws, tags in rules]
               for sci, rules in RULES_BY_SCIENCE.items()}


def tags_for(chapter_name: str, sciences: list[str] | tuple[str, ...] = ("fiqh",)) -> list[str]:
    """Tags for a chapter of a book in `sciences` (default: فقه)."""
    name = normalize(chapter_name)
    tags: list[str] = []
    rules = [r for sci in sciences for r in _NORMALIZED.get(sci, [])]
    for keywords, rule_tags in rules:
        if any(k in name for k in keywords):
            tags += [t for t in rule_tags if t not in tags]
    return tags


def tag_chapters(conn: sqlite3.Connection) -> int:
    """Recompute ChapterTag for every chapter. Returns links created."""
    from zawiyah_ulom.sciences import book_sciences
    conn.execute("DELETE FROM ChapterTag")
    links = 0
    book_sci = book_sciences(conn)
    for ch in conn.execute("SELECT id, name, book_id FROM Chapter").fetchall():
        for tag in tags_for(ch["name"], book_sci.get(ch["book_id"], ["fiqh"])):
            conn.execute("INSERT OR IGNORE INTO Tag (name) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT id FROM Tag WHERE name = ?", (tag,)).fetchone()["id"]
            conn.execute("INSERT INTO ChapterTag (chapter_id, tag_id) VALUES (?, ?)",
                         (ch["id"], tag_id))
            links += 1
    # Drop tags no chapter uses any more (e.g. after editing RULES).
    conn.execute("DELETE FROM Tag WHERE id NOT IN (SELECT tag_id FROM ChapterTag)")
    return links
