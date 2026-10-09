"""Arabic text normalization shared by indexing and search.

Shamela texts are heavily voweled (مُشَكَّل) while users type without
diacritics, so both the indexed text and the search query go through
the same normalize() before hitting FTS5.
"""
import re

# Harakat, tanween, shadda, sukun, superscript alef, Quranic marks, tatweel.
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")

_LETTER_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي",
    "ة": "ه",
    "ؤ": "و",
    "ئ": "ي",
})

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# Attached prefixes: و/ف + ب/ك/ل + ال, e.g. والبيع، فبالماء، للصلاة.
# Keeps at least 2 letters (الحج -> حج) and leaves الله intact (not له).
_PREFIX = re.compile(r"\b(?:[وف]?(?:[بكل]?ال|لل))(?=\w{2,})(?!له\b)")


def normalize(text: str) -> str:
    """Strip diacritics, unify letter variants and drop the definite-article
    prefix so that البيع / والبيع / بيع all index and match as بيع."""
    if not text:
        return ""
    text = _DIACRITICS.sub("", text)
    text = text.translate(_LETTER_MAP)
    return _PREFIX.sub("", text)


def to_western_digits(text: str) -> str:
    return text.translate(_ARABIC_DIGITS)
