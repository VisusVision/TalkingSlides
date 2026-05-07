from __future__ import annotations

import re
import unicodedata

from .config import TTSPreprocessConfig, get_preprocess_config
from .deterministic_resolver import resolve_deterministic_terms
from .glossary import apply_glossary_with_rules, apply_outside_protected, load_glossary_for_lang
from .schemas import TTSPreparedText
from .segmenter import split_text_to_chunks


# ---------------------------------------------------------------------------
# English number word tables
# ---------------------------------------------------------------------------

_ONES = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}
_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}
_DIGITS = {str(key): value for key, value in _ONES.items() if key < 10}
_UNIT_WORDS = {
    "kb": "kilobytes",
    "mb": "megabytes",
    "gb": "gigabytes",
    "tb": "terabytes",
}


def number_to_words(value: int) -> str:
    value = int(value)
    if value < 0:
        return f"minus {number_to_words(abs(value))}"
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens = (value // 10) * 10
        ones = value % 10
        return _TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ONES[ones]}"
    if value < 1000:
        hundreds = value // 100
        rest = value % 100
        return f"{_ONES[hundreds]} hundred" if rest == 0 else f"{_ONES[hundreds]} hundred {number_to_words(rest)}"
    if value < 10000:
        thousands = value // 1000
        rest = value % 1000
        return f"{number_to_words(thousands)} thousand" if rest == 0 else f"{number_to_words(thousands)} thousand {number_to_words(rest)}"
    return str(value)


# ---------------------------------------------------------------------------
# Unicode / whitespace cleaning  (language-independent)
# ---------------------------------------------------------------------------

def clean_text_for_tts(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")

    chars: list[str] = []
    for char in value:
        if char in {"\n", "\t"}:
            chars.append(char)
            continue
        if unicodedata.category(char) in {"Cc", "Cs"}:
            continue
        chars.append(char)

    cleaned = "".join(chars)
    cleaned = (
        cleaned.replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00a0", " ")
    )
    cleaned = re.sub(r"[\u200b-\u200d\ufeff]", "", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _ensure_sentence_punctuation(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    if value.endswith((".", "!", "?")):
        return value
    if value.endswith((",", ";", ":")):
        value = value[:-1].strip()
    return f"{value}."


def _cleanup_spacing(text: str) -> str:
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", text or ""):
        compact = re.sub(r"[ \t]+", " ", paragraph).strip()
        compact = re.sub(r"\s+([,.!?;:])", r"\1", compact)
        compact = re.sub(r"([([{])\s+", r"\1", compact)
        if compact:
            paragraphs.append(compact)
    return "\n\n".join(paragraphs).strip()


def normalize_structure(text: str) -> str:
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        return ""

    paragraphs = [p for p in re.split(r"\n\s*\n+", cleaned) if p.strip()]
    normalized: list[str] = []

    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        sentences: list[str] = []
        buffer: list[str] = []

        def flush_buffer() -> None:
            if not buffer:
                return
            sentences.append(_ensure_sentence_punctuation(" ".join(buffer)))
            buffer.clear()

        for line in lines:
            compact = re.sub(r"\s+", " ", line).strip()
            bullet = re.match(r"^(?:[-*]|\u2022|\d+[.)])\s+(.+)$", compact)
            if bullet:
                flush_buffer()
                sentences.append(_ensure_sentence_punctuation(bullet.group(1)))
                continue
            if compact.endswith(":"):
                flush_buffer()
                sentences.append(_ensure_sentence_punctuation(compact[:-1]))
                continue
            buffer.append(compact)

        flush_buffer()
        paragraph_text = " ".join(sentence for sentence in sentences if sentence)
        paragraph_text = _ensure_sentence_punctuation(paragraph_text) if paragraph_text else ""
        if paragraph_text:
            normalized.append(paragraph_text)

    return _cleanup_spacing("\n\n".join(normalized))


# ---------------------------------------------------------------------------
# Language tag normalizer
# ---------------------------------------------------------------------------

def _normalize_language_tag(lang: str | None) -> str:
    """Map tr/tr-TR/tr_TR → 'tr'; everything else → 'en'."""
    if not lang:
        return "en"
    normalized = lang.strip().lower().replace("-", "_")
    if normalized == "tr" or normalized.startswith("tr_"):
        return "tr"
    return "en"


# ---------------------------------------------------------------------------
# English-specific helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _expand_abbreviations(text: str) -> str:
    replacements = [
        (r"\be\.g\.", "for example"),
        (r"\bi\.e\.", "that is"),
        (r"\bDr\.", "Doctor"),
        (r"\bMr\.", "Mister"),
        (r"\bMrs\.", "Misses"),
        (r"\bMs\.", "Miss"),
        (r"\bProf\.", "Professor"),
    ]

    def transform(segment: str) -> str:
        updated = segment
        for pattern, replacement in replacements:
            updated = re.sub(pattern, replacement, updated, flags=re.IGNORECASE)
        return updated

    return apply_outside_protected(text, transform)


def _decimal_to_words(match: re.Match[str]) -> str:
    whole = number_to_words(int(match.group(1)))
    fraction = " ".join(_DIGITS[digit] for digit in match.group(2))
    return f"{whole} point {fraction}"


def _normalize_numbers_in_segment(segment: str) -> str:
    updated = segment
    updated = re.sub(
        r"\b(\d{1,3})\s*-\s*(\d{1,3})(?=\s+[A-Za-z])",
        lambda match: f"{number_to_words(int(match.group(1)))} to {number_to_words(int(match.group(2)))}",
        updated,
    )
    updated = re.sub(
        r"\$(\d{1,4})(?![\d.])",
        lambda match: f"{number_to_words(int(match.group(1)))} {'dollar' if int(match.group(1)) == 1 else 'dollars'}",
        updated,
    )
    updated = re.sub(
        r"\b(\d{1,4})%",
        lambda match: f"{number_to_words(int(match.group(1)))} percent",
        updated,
    )
    updated = re.sub(
        r"\b(\d{1,4})\s*(KB|MB|GB|TB)\b",
        lambda match: f"{number_to_words(int(match.group(1)))} {_UNIT_WORDS[match.group(2).lower()]}",
        updated,
        flags=re.IGNORECASE,
    )
    updated = re.sub(r"\b(\d{1,4})\.(\d+)\b", _decimal_to_words, updated)
    updated = re.sub(
        r"\bv(\d{1,3})\b",
        lambda match: f"version {number_to_words(int(match.group(1)))}",
        updated,
        flags=re.IGNORECASE,
    )
    return updated


def normalize_numbers_and_symbols(text: str) -> str:
    return apply_outside_protected(text, _normalize_numbers_in_segment)


# ---------------------------------------------------------------------------
# English spoken-text builder
# ---------------------------------------------------------------------------

def _build_spoken_text_en(
    normalized_text: str,
    *,
    config: TTSPreprocessConfig,
    warnings: list[str],
    rules_applied: list[dict],
    unknown_terms: list[str],
    ambiguous_terms: list[str],
) -> str:
    if not normalized_text:
        return ""

    spoken = _expand_abbreviations(normalized_text)
    glossary, glossary_warnings = load_glossary_for_lang(config.glossary_path, "en")
    warnings.extend(glossary_warnings)
    spoken, glossary_rules = apply_glossary_with_rules(spoken, glossary, language="en")
    rules_applied.extend(glossary_rules)
    spoken = normalize_numbers_and_symbols(spoken)
    resolved = resolve_deterministic_terms(spoken, "en")
    rules_applied.extend(resolved.rules_applied)
    warnings.extend(w for w in resolved.warnings if w not in warnings)
    unknown_terms.extend(resolved.unknown_terms)
    ambiguous_terms.extend(resolved.ambiguous_terms)
    spoken = resolved.spoken_text
    return _cleanup_spacing(spoken)


# ---------------------------------------------------------------------------
# Turkish spoken-text builder
# ---------------------------------------------------------------------------

def _build_spoken_text_tr(
    normalized_text: str,
    *,
    config: TTSPreprocessConfig,
    warnings: list[str],
    rules_applied: list[dict],
    unknown_terms: list[str],
    ambiguous_terms: list[str],
) -> str:
    """Turkish normalization branch.

    - Applies the Turkish glossary (language-specific entries only).
    - Applies Turkish number/symbol/currency rules.
    - Does NOT apply English abbreviation expansion, English number words,
      or English glossary entries.
    """
    if not normalized_text:
        return ""

    from .tr_normalizer import build_spoken_text_tr  # deferred to avoid circular import

    glossary, glossary_warnings = load_glossary_for_lang(config.glossary_path, "tr")
    warnings.extend(glossary_warnings)
    spoken = build_spoken_text_tr(
        normalized_text,
        glossary=glossary,
        warnings=warnings,
        rules_applied=rules_applied,
    )
    resolved = resolve_deterministic_terms(spoken, "tr")
    rules_applied.extend(resolved.rules_applied)
    warnings.extend(w for w in resolved.warnings if w not in warnings)
    unknown_terms.extend(resolved.unknown_terms)
    ambiguous_terms.extend(resolved.ambiguous_terms)
    return _cleanup_spacing(resolved.spoken_text)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def prepare_text_for_tts(
    raw_text: str,
    language: str = "en",
    already_prepared: bool = False,
    max_chars_per_chunk: int | None = None,
    target_chars_per_chunk: int | None = None,
) -> TTSPreparedText:
    config = get_preprocess_config()
    raw_value = str(raw_text or "")
    warnings: list[str] = []
    rules_applied: list[dict] = []
    unknown_terms: list[str] = []
    ambiguous_terms: list[str] = []

    normalized_text = normalize_structure(raw_value)
    if not normalized_text:
        warnings.append("empty_input")

    lang = _normalize_language_tag(language)

    if already_prepared:
        spoken_text = normalized_text
    elif not config.preprocessing_enabled:
        warnings.append("preprocessing_disabled")
        spoken_text = normalized_text
    elif lang == "tr":
        spoken_text = _build_spoken_text_tr(
            normalized_text,
            config=config,
            warnings=warnings,
            rules_applied=rules_applied,
            unknown_terms=unknown_terms,
            ambiguous_terms=ambiguous_terms,
        )
    else:
        spoken_text = _build_spoken_text_en(
            normalized_text,
            config=config,
            warnings=warnings,
            rules_applied=rules_applied,
            unknown_terms=unknown_terms,
            ambiguous_terms=ambiguous_terms,
        )

    max_chars = max_chars_per_chunk or config.max_chars_per_chunk
    target_chars = target_chars_per_chunk or config.target_chars_per_chunk
    chunks, chunk_pause_ms, chunk_warnings = split_text_to_chunks(
        spoken_text,
        max_chars=max_chars,
        target_chars=target_chars,
        sentence_pause_ms=config.sentence_pause_ms,
        paragraph_pause_ms=config.paragraph_pause_ms,
    )
    warnings.extend(w for w in chunk_warnings if w not in warnings)

    if spoken_text and not any(char.isalpha() for char in spoken_text):
        warnings.append("no_alphabetic_content")

    return TTSPreparedText(
        raw_text=raw_value,
        normalized_text=normalized_text,
        spoken_text=spoken_text,
        chunks=chunks,
        warnings=warnings,
        chunk_pause_ms=chunk_pause_ms,
        original_text=raw_value,
        tts_normalization_language=lang,
        tts_normalization_rules_applied=rules_applied,
        unknown_terms=unknown_terms,
        ambiguous_terms=ambiguous_terms,
    )
