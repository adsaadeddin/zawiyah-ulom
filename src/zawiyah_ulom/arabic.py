"""Arabic text normalization — the single source of truth.

Shamela texts are heavily voweled (مُشَكَّل) while users type without
diacritics, so the indexed text and every query go through normalize().

The rules are plain data (RULES below). Python applies them here; the
static site's search.js receives the same data via `rules_for_js()`
(exported into docs/data/search.json) and applies it with the same
algorithm, so changing a rule here changes both. The algorithm:

  1. remove every character in DIACRITIC_RANGES
  2. map letter variants via LETTER_MAP
  3. for each token (run of letters/digits/_), strip the longest prefix in
     ARTICLE_PREFIXES, provided at least MIN_STEM characters remain and the
     remainder is not in PROTECTED_STEMS (so الله is not reduced to له)
"""
import re

# Harakat, tanween, shadda, sukun, superscript alef, Quranic marks, tatweel.
DIACRITIC_RANGES: list[tuple[int, int]] = [
    (0x0610, 0x061A),
    (0x064B, 0x065F),
    (0x0670, 0x0670),
    (0x06D6, 0x06ED),
    (0x0640, 0x0640),
]

LETTER_MAP: dict[str, str] = {
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي",
    "ة": "ه",
    "ؤ": "و",
    "ئ": "ي",
}

# Definite article with optional attached particles: (و|ف)? + (ب|ك|ل)? + ال, and (و|ف)? + لل.
# e.g. البيع، والبيع، بالماء، فالصلاة، للصلاة، وللصلاة -> بيع، ماء، صلاه
ARTICLE_PREFIXES: list[str] = sorted(
    {conj + base for conj in ("", "و", "ف") for base in ("ال", "بال", "كال", "لال", "لل")},
    key=lambda p: (-len(p), p),  # longest first; ties sorted so exports are deterministic
)
MIN_STEM = 2
PROTECTED_STEMS: set[str] = {"له"}  # الله, بالله, والله stay intact

ARABIC_LETTER = "[ء-ي]"  # base letters ء..ي (regex class)

_DIACRITIC_CLASS = "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in DIACRITIC_RANGES) + "]"
_DIACRITICS = re.compile(_DIACRITIC_CLASS)
_LETTERS = str.maketrans(LETTER_MAP)
# Each canonical letter with all variants that normalize to it, e.g. ا -> [اأإآٱ].
_VARIANT_CLASS = {
    target: "[" + target + "".join(k for k, v in LETTER_MAP.items() if v == target) + "]"
    for target in set(LETTER_MAP.values())
}
_TOKEN = re.compile(r"\w+")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def strip_article(token: str) -> str:
    for prefix in ARTICLE_PREFIXES:
        if token.startswith(prefix):
            stem = token[len(prefix):]
            if len(stem) >= MIN_STEM and stem not in PROTECTED_STEMS:
                return stem
    return token


def normalize(text: str) -> str:
    """Strip diacritics, unify letter variants and drop the definite-article
    prefix, so that البيع / والبيع / بيع all index and match as بيع.
    Non-word characters (spaces, punctuation, newlines) are preserved."""
    if not text:
        return ""
    text = _DIACRITICS.sub("", text).translate(_LETTERS)
    return _TOKEN.sub(lambda m: strip_article(m.group()), text)


def fuzzy_pattern(text: str) -> str:
    """Regex source matching the Arabic words of `text` inside *unnormalized*
    text: diacritics may appear after any letter, letter variants are
    interchangeable (per LETTER_MAP), and any non-letters may separate words.
    Used to find plain TOC titles in fully voweled page text (الْوضُوء)."""
    marks = _DIACRITIC_CLASS + "*"
    words = re.findall(ARABIC_LETTER + "+", text)
    word_res = [
        marks.join(_VARIANT_CLASS.get(LETTER_MAP.get(ch, ch), re.escape(ch)) for ch in word) + marks
        for word in words
    ]
    return ("[^" + ARABIC_LETTER[1:-1] + "]+").join(word_res)


def has_arabic(text: str) -> bool:
    return re.search(ARABIC_LETTER, text) is not None


def tokens(text: str) -> list[str]:
    """Normalized search terms of `text`."""
    return _TOKEN.findall(normalize(text))


def rules_for_js() -> dict:
    """The normalization rules as JSON-serializable data for search.js."""
    return {
        "diacritic_ranges": DIACRITIC_RANGES,
        "letter_map": LETTER_MAP,
        "article_prefixes": ARTICLE_PREFIXES,
        "min_stem": MIN_STEM,
        "protected_stems": sorted(PROTECTED_STEMS),
    }


def to_western_digits(text: str) -> str:
    return text.translate(_ARABIC_DIGITS)
