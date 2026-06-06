"""
services/tts_service/preprocess/tr_normalizer.py
=================================================
Turkish-specific TTS text normalization.

All rules here are *conservative* and *deterministic*:
- Correct common patterns (numbers, currency, percentages, storage sizes, ranges).
- Leave ambiguous or complex cases unchanged to avoid mangling valid Turkish text.
- Never import from normalizer.py (no circular imports); only from glossary.py.
"""

from __future__ import annotations

import logging
import re

from .glossary import apply_outside_protected

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Turkish number words
# ---------------------------------------------------------------------------

_TR_ONES: dict[int, str] = {
    0: "sıfır",
    1: "bir",
    2: "iki",
    3: "üç",
    4: "dört",
    5: "beş",
    6: "altı",
    7: "yedi",
    8: "sekiz",
    9: "dokuz",
    10: "on",
    11: "on bir",
    12: "on iki",
    13: "on üç",
    14: "on dört",
    15: "on beş",
    16: "on altı",
    17: "on yedi",
    18: "on sekiz",
    19: "on dokuz",
}

_TR_TENS: dict[int, str] = {
    20: "yirmi",
    30: "otuz",
    40: "kırk",
    50: "elli",
    60: "altmış",
    70: "yetmiş",
    80: "seksen",
    90: "doksan",
}

_TR_HUNDREDS: dict[int, str] = {
    1: "yüz",
    2: "iki yüz",
    3: "üç yüz",
    4: "dört yüz",
    5: "beş yüz",
    6: "altı yüz",
    7: "yedi yüz",
    8: "sekiz yüz",
    9: "dokuz yüz",
}

_TR_DIGITS: dict[str, str] = {str(k): v for k, v in _TR_ONES.items() if k < 10}

_TR_UNIT_WORDS: dict[str, str] = {
    "kb": "kilobayt",
    "mb": "megabayt",
    "gb": "gigabayt",
    "tb": "terabayt",
}

# ---------------------------------------------------------------------------
# Turkish abbreviations to protect from bad sentence splitting
# ---------------------------------------------------------------------------
# These are *added* to the segmenter's COMMON_ABBREVIATIONS set so the
# split_sentences() function in segmenter.py won't break after them.
# We do not expand them here — the segmenter already handles protection.
TR_ABBREVIATION_SAFE_LIST: frozenset[str] = frozenset({
    "vb.",
    "vs.",
    "dr.",
    "prof.",
    "doç.",
    "sn.",
    "örn.",
    "yrd.",
    "orn.",
})


# ---------------------------------------------------------------------------
# Integer → Turkish words  (0 – 999 999)
# ---------------------------------------------------------------------------

def number_to_words_tr(value: int) -> str:
    """Convert a non-negative integer (0–999 999) to Turkish words.

    Returns the original string representation for values outside range.
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return str(value)

    if value < 0:
        return f"eksi {number_to_words_tr(abs(value))}"
    if value <= 19:
        return _TR_ONES[value]
    if value < 100:
        tens = (value // 10) * 10
        ones = value % 10
        if ones == 0:
            return _TR_TENS[tens]
        return f"{_TR_TENS[tens]} {_TR_ONES[ones]}"
    if value < 1000:
        hundreds = value // 100
        rest = value % 100
        h_word = _TR_HUNDREDS[hundreds]
        if rest == 0:
            return h_word
        return f"{h_word} {number_to_words_tr(rest)}"
    if value < 1_000_000:
        thousands = value // 1000
        rest = value % 1000
        # "bin" alone for 1000, "iki bin" for 2000, etc.
        t_word = "bin" if thousands == 1 else f"{number_to_words_tr(thousands)} bin"
        if rest == 0:
            return t_word
        return f"{t_word} {number_to_words_tr(rest)}"

    # Unsupported range — return as-is and warn
    logger.warning("tr_normalizer: number_to_words_tr: value %d out of supported range (0-999999)", value)
    return str(value)


# ---------------------------------------------------------------------------
# Decimal helper
# ---------------------------------------------------------------------------

def _tr_decimal_to_words(match: re.Match[str]) -> str:
    """'3.5' → 'üç nokta beş'."""
    whole_part = number_to_words_tr(int(match.group(1)))
    fraction_digits = " ".join(_TR_DIGITS.get(d, d) for d in match.group(2))
    return f"{whole_part} nokta {fraction_digits}"


# ---------------------------------------------------------------------------
# Core Turkish number/symbol normalization
# ---------------------------------------------------------------------------

def _normalize_numbers_in_segment_tr(segment: str) -> str:
    """Apply all Turkish number/symbol substitutions to one unprotected segment."""
    updated = segment

    # 1. Percent: %10  →  yüzde on  (prefix form)
    updated = re.sub(
        r"%(\d{1,6})\b",
        lambda m: f"yüzde {number_to_words_tr(int(m.group(1)))}",
        updated,
    )
    # 2. Percent: 10%  →  yüzde on  (suffix form)
    updated = re.sub(
        r"\b(\d{1,6})%",
        lambda m: f"yüzde {number_to_words_tr(int(m.group(1)))}",
        updated,
    )

    # 3. Currency ₺ prefix: ₺10  →  on lira
    updated = re.sub(
        r"₺(\d{1,6})\b",
        lambda m: f"{number_to_words_tr(int(m.group(1)))} lira",
        updated,
    )
    # 4. Currency ₺ suffix: 10₺  →  on lira
    updated = re.sub(
        r"\b(\d{1,6})₺",
        lambda m: f"{number_to_words_tr(int(m.group(1)))} lira",
        updated,
    )

    # 5. Currency $ prefix: $10  →  on dolar
    updated = re.sub(
        r"\$(\d{1,6})\b",
        lambda m: f"{number_to_words_tr(int(m.group(1)))} dolar",
        updated,
    )
    # 6. Currency $ suffix: 10$  →  on dolar
    updated = re.sub(
        r"\b(\d{1,6})\$",
        lambda m: f"{number_to_words_tr(int(m.group(1)))} dolar",
        updated,
    )

    # 7. Storage: 5GB → beş gigabayt  (case-insensitive)
    updated = re.sub(
        r"\b(\d{1,6})\s*(KB|MB|GB|TB)\b",
        lambda m: f"{number_to_words_tr(int(m.group(1)))} {_TR_UNIT_WORDS[m.group(2).lower()]}",
        updated,
        flags=re.IGNORECASE,
    )

    # 8. Decimal: 3.5  →  üç nokta beş
    #    (must come BEFORE bare integer rule to avoid double processing)
    updated = re.sub(r"\b(\d{1,6})\.(\d+)\b", _tr_decimal_to_words, updated)

    # 9. Range: 3-4 dakika  →  üç ila dört dakika
    #    Only when followed by a space + Turkish/Latin word (guards against IP/version ranges)
    updated = re.sub(
        r"\b(\d{1,4})\s*-\s*(\d{1,4})(?=\s+[A-Za-zÇçĞğİıÖöŞşÜü])",
        lambda m: f"{number_to_words_tr(int(m.group(1)))} ila {number_to_words_tr(int(m.group(2)))}",
        updated,
    )

    # 10. Version: v2  →  versiyon iki
    updated = re.sub(
        r"\bv(\d{1,3})\b",
        lambda m: f"versiyon {number_to_words_tr(int(m.group(1)))}",
        updated,
        flags=re.IGNORECASE,
    )

    # 11. Bare integer: 2026  →  iki bin yirmi altı
    #     Only standalone integers not already handled above.
    #     Exclude years embedded in decimal/range context (already converted above).
    updated = re.sub(
        r"\b(\d{1,6})\b",
        lambda m: number_to_words_tr(int(m.group(1))),
        updated,
    )

    return updated


def normalize_numbers_and_symbols_tr(text: str) -> str:
    """Apply Turkish number/symbol normalization outside URL/path/filename spans."""
    return apply_outside_protected(text, _normalize_numbers_in_segment_tr)


# ---------------------------------------------------------------------------
# Turkish abbreviation protection
# (segmenter.py already has vb./vs./dr./etc in COMMON_ABBREVIATIONS;
#  this function is a no-op guard used by _build_spoken_text_tr to document
#  the intent — actual protection happens in split_sentences via segmenter)
# ---------------------------------------------------------------------------

def protect_turkish_abbreviations(text: str) -> str:
    """Return *text* unchanged; documents that TR abbrev protection is in segmenter.py."""
    return text


# ---------------------------------------------------------------------------
# Public builder (called from normalizer.py)
# ---------------------------------------------------------------------------

def build_spoken_text_tr(
    normalized_text: str,
    *,
    glossary: dict[str, str],
    warnings: list[str],
    rules_applied: list[dict] | None = None,
) -> str:
    """Build Turkish spoken text from structure-normalized text.

    Order:
    1. Apply Turkish glossary entries (longest-first, outside protected spans).
    2. Apply Turkish number/symbol normalization (outside protected spans).
    3. Clean up spacing.

    English number/symbol rules and English abbreviation expansion are
    deliberately NOT applied here.
    """
    from .glossary import apply_glossary_with_rules  # local import avoids any circularity

    if not normalized_text:
        return ""

    spoken, glossary_rules = apply_glossary_with_rules(normalized_text, glossary, language="tr")
    if rules_applied is not None:
        rules_applied.extend(glossary_rules)
    spoken = normalize_numbers_and_symbols_tr(spoken)

    # Compact spacing (mirror of normalizer._cleanup_spacing logic)
    import re as _re
    paragraphs: list[str] = []
    for paragraph in _re.split(r"\n\s*\n+", spoken or ""):
        compact = _re.sub(r"[ \t]+", " ", paragraph).strip()
        compact = _re.sub(r"\s+([,.!?;:])", r"\1", compact)
        compact = _re.sub(r"([([{])\s+", r"\1", compact)
        if compact:
            paragraphs.append(compact)
    return "\n\n".join(paragraphs).strip()
