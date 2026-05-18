from __future__ import annotations

import re
from typing import Any


TURKISH_CHARS_RE = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
WORD_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü][A-Za-zÇĞİÖŞÜçğıöşü0-9_+-]*")

TURKISH_WORDS = {
    "ama",
    "bir",
    "bu",
    "çünkü",
    "ders",
    "için",
    "ile",
    "konu",
    "nasıl",
    "neden",
    "olarak",
    "öğrenci",
    "örnek",
    "ve",
}

ENGLISH_WORDS = {
    "about",
    "and",
    "because",
    "example",
    "for",
    "how",
    "lesson",
    "learn",
    "student",
    "summary",
    "the",
    "this",
    "topic",
    "what",
    "why",
    "with",
}


def detect_lesson_language(text: Any) -> dict[str, Any]:
    clean = str(text or "")
    words = [word.lower() for word in WORD_RE.findall(clean)]
    if not clean.strip() or not words:
        return {"language": "unknown", "confidence": 0.0}

    tr_char_hits = len(TURKISH_CHARS_RE.findall(clean))
    tr_word_hits = sum(1 for word in words if word in TURKISH_WORDS)
    en_word_hits = sum(1 for word in words if word in ENGLISH_WORDS)
    tr_score = tr_word_hits * 2.0 + min(8.0, tr_char_hits * 0.9)
    en_score = en_word_hits * 1.5

    if tr_score >= max(3.0, en_score + 1.5):
        total = tr_score + en_score + 1.0
        return {"language": "tr", "confidence": round(min(0.99, tr_score / total), 2)}
    if en_score >= max(3.0, tr_score + 1.5):
        total = tr_score + en_score + 1.0
        return {"language": "en", "confidence": round(min(0.99, en_score / total), 2)}
    return {"language": "unknown", "confidence": 0.35 if tr_score or en_score else 0.0}


def normalize_output_language(value: Any) -> str:
    language = str(value or "auto").strip().lower()
    if language in {"tr", "turkish", "türkçe", "turkce"}:
        return "tr"
    if language in {"en", "english"}:
        return "en"
    return "auto"


def resolve_output_language(
    *,
    requested: Any = "auto",
    detected: str = "unknown",
    request_language: Any = "",
) -> str:
    normalized = normalize_output_language(requested)
    if normalized in {"tr", "en"}:
        return normalized
    detected = str(detected or "unknown").lower()
    if detected in {"tr", "en"}:
        return detected
    request_normalized = normalize_output_language(request_language)
    if request_normalized in {"tr", "en"}:
        return request_normalized
    return "en"


def language_display_label(language: Any) -> str:
    value = str(language or "").lower()
    if value == "tr":
        return "Turkish analysis"
    if value == "en":
        return "English analysis"
    return "Language uncertain"
