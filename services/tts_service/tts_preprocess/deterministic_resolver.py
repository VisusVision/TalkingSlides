from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from .glossary import protected_spans as default_protected_spans


_PACKAGE_DIR = Path(__file__).resolve().parent
_MAX_REPORTED_TERMS = 20
_PLACEHOLDER_RE = re.compile(r"__[A-Za-z0-9_]+__")
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]*)(?![A-Za-z0-9_])")


@dataclass(frozen=True)
class DeterministicResolverData:
    acronyms: dict[str, dict[str, str]]
    tr_known_words: set[str]
    en_technical_terms: dict[str, str]


@dataclass
class DeterministicResolverResult:
    spoken_text: str
    rules_applied: list[dict[str, Any]] = field(default_factory=list)
    unknown_terms: list[str] = field(default_factory=list)
    ambiguous_terms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalize_word_key(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def _normalize_acronym_key(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).upper()


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_resolver_data() -> DeterministicResolverData:
    raw_acronyms = _read_json(_PACKAGE_DIR / "acronym_pronunciations.json")
    acronyms: dict[str, dict[str, str]] = {"en": {}, "tr": {}}
    for lang in ("en", "tr"):
        values = raw_acronyms.get(lang)
        if not isinstance(values, dict):
            continue
        acronyms[lang] = {
            _normalize_acronym_key(term): str(spoken).strip()
            for term, spoken in values.items()
            if str(term or "").strip() and str(spoken or "").strip()
        }

    raw_terms = _read_json(_PACKAGE_DIR / "en_technical_terms.json")
    en_technical_terms = {
        _normalize_word_key(term): str(spoken).strip()
        for term, spoken in raw_terms.items()
        if str(term or "").strip() and str(spoken or "").strip()
    }

    tr_known_words: set[str] = set()
    with open(_PACKAGE_DIR / "tr_known_words.txt", "r", encoding="utf-8") as handle:
        for line in handle:
            word = line.strip()
            if word and not word.startswith("#"):
                tr_known_words.add(_normalize_word_key(word))

    return DeterministicResolverData(
        acronyms=acronyms,
        tr_known_words=tr_known_words,
        en_technical_terms=en_technical_terms,
    )


def _normalize_language(language: str | None) -> str:
    lang = str(language or "").strip().lower().replace("_", "-")
    if lang.startswith("tr"):
        return "tr"
    return "en"


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    spans = sorted((max(0, start), max(0, end)) for start, end in spans if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _combined_protected_spans(text: str, extra_spans: list[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    spans = list(extra_spans or [])
    spans.extend(default_protected_spans(text))
    spans.extend((match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text or ""))
    return _merge_spans(spans)


def _apply_outside_spans(
    text: str,
    spans: list[tuple[int, int]],
    transform: Callable[[str], str],
) -> str:
    if not spans:
        return transform(text)

    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if cursor < start:
            parts.append(transform(text[cursor:start]))
        parts.append(text[start:end])
        cursor = end
    if cursor < len(text):
        parts.append(transform(text[cursor:]))
    return "".join(parts)


def _append_unique(values: list[str], seen: set[str], term: str) -> None:
    key = _normalize_word_key(term)
    if key in seen or len(values) >= _MAX_REPORTED_TERMS:
        return
    seen.add(key)
    values.append(term)


def _add_rule(
    rules: list[dict[str, Any]],
    *,
    rule: str,
    language: str,
    term: str,
    replacement: str,
) -> None:
    for existing in rules:
        if (
            existing.get("rule") == rule
            and existing.get("language") == language
            and existing.get("term") == term
            and existing.get("replacement") == replacement
        ):
            existing["count"] = int(existing.get("count") or 0) + 1
            return
    rules.append(
        {
            "rule": rule,
            "language": language,
            "term": term,
            "replacement": replacement,
            "count": 1,
        }
    )


def _looks_like_acronym(surface: str) -> bool:
    return len(surface) >= 2 and surface.upper() == surface and any(ch.isalpha() for ch in surface)


def _looks_suspicious_technical(surface: str) -> bool:
    if len(surface) < 3 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", surface):
        return False
    if surface.upper() == surface:
        return True
    if any(ch.isdigit() for ch in surface):
        return True
    return any(ch.isupper() for ch in surface[1:])


def resolve_deterministic_terms(
    text: str,
    language: str,
    protected_spans: list[tuple[int, int]] | None = None,
) -> DeterministicResolverResult:
    value = str(text or "")
    if not value:
        return DeterministicResolverResult(spoken_text="")

    lang = _normalize_language(language)
    data = load_resolver_data()
    acronym_map = data.acronyms.get(lang) or data.acronyms.get("en", {})
    rules: list[dict[str, Any]] = []
    unknown_terms: list[str] = []
    ambiguous_terms: list[str] = []
    seen_unknown: set[str] = set()
    seen_ambiguous: set[str] = set()

    def transform(segment: str) -> str:
        def replace(match: re.Match[str]) -> str:
            surface = match.group(1)
            acronym_key = _normalize_acronym_key(surface)
            word_key = _normalize_word_key(surface)

            if _looks_like_acronym(surface) and acronym_key in acronym_map:
                replacement = acronym_map[acronym_key]
                _add_rule(rules, rule="acronym", language=lang, term=surface, replacement=replacement)
                return replacement

            if lang != "tr":
                return surface

            is_tr_known = word_key in data.tr_known_words
            is_en_technical = word_key in data.en_technical_terms
            if is_tr_known and is_en_technical:
                _append_unique(ambiguous_terms, seen_ambiguous, surface)
                return surface
            if is_tr_known:
                return surface
            if is_en_technical:
                replacement = data.en_technical_terms[word_key]
                _add_rule(
                    rules,
                    rule="english_technical_fallback",
                    language=lang,
                    term=surface,
                    replacement=replacement,
                )
                return replacement
            if _looks_suspicious_technical(surface):
                _append_unique(unknown_terms, seen_unknown, surface)
            return surface

        return _TOKEN_RE.sub(replace, segment)

    resolved = _apply_outside_spans(value, _combined_protected_spans(value, protected_spans), transform)
    warnings: list[str] = []
    if unknown_terms:
        warnings.append("deterministic_resolver_unknown_terms")
    if ambiguous_terms:
        warnings.append("deterministic_resolver_ambiguous_terms")
    return DeterministicResolverResult(
        spoken_text=resolved,
        rules_applied=rules,
        unknown_terms=unknown_terms,
        ambiguous_terms=ambiguous_terms,
        warnings=warnings,
    )
