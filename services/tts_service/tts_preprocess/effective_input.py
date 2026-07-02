from __future__ import annotations

from typing import Any

from .glossary import apply_glossary_with_rules
from .normalizer import prepare_text_for_tts


def _canonical_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return text


def _canonical_language(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return "tr" if normalized == "tr" or normalized.startswith("tr_") else "en"


def _canonical_tts_settings(tts_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(tts_settings, dict):
        return None
    overrides = tts_settings.get("overrides") if isinstance(tts_settings.get("overrides"), dict) else {}

    def _override_map(name: str) -> dict[str, str]:
        value = overrides.get(name)
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, str] = {}
        for term, replacement in value.items():
            if isinstance(term, str) and isinstance(replacement, str):
                t = term.strip()
                r = replacement.strip()
                if t and r:
                    cleaned[t] = r
        return cleaned

    provider_preference = str(tts_settings.get("provider_preference") or "auto").strip().lower()
    if provider_preference not in {"auto", "xtts_v2", "gtts"}:
        provider_preference = "auto"
    normalization_mode = str(tts_settings.get("normalization_mode") or "loose").strip().lower()
    if normalization_mode not in {"loose", "strict"}:
        normalization_mode = "loose"
    unknown_word_strategy = str(tts_settings.get("unknown_word_strategy") or "keep").strip().lower()
    if unknown_word_strategy not in {"keep", "phonetic"}:
        unknown_word_strategy = "keep"

    return {
        "provider_preference": provider_preference,
        "normalization_enabled": bool(tts_settings.get("normalization_enabled", True)),
        "normalization_mode": normalization_mode,
        "unknown_word_strategy": unknown_word_strategy,
        "overrides": {
            "technical": _override_map("technical"),
            "abbreviation": _override_map("abbreviation"),
            "mixed_word": _override_map("mixed_word"),
        },
        "speech_speed": tts_settings.get("speech_speed", 1.0),
        "volume_gain_db": tts_settings.get("volume_gain_db", 0),
        "pause_seconds": tts_settings.get("pause_seconds"),
    }


def _override_summary(settings: dict[str, Any] | None) -> dict[str, int]:
    if not settings:
        return {}
    overrides = settings.get("overrides") if isinstance(settings.get("overrides"), dict) else {}
    technical = overrides.get("technical") if isinstance(overrides.get("technical"), dict) else {}
    abbreviation = overrides.get("abbreviation") if isinstance(overrides.get("abbreviation"), dict) else {}
    mixed_word = overrides.get("mixed_word") if isinstance(overrides.get("mixed_word"), dict) else {}
    return {
        "technical_count": len(technical),
        "abbreviation_count": len(abbreviation),
        "mixed_word_count": len(mixed_word),
        "merged_override_count": len({**technical, **abbreviation, **mixed_word}),
    }


def _merged_override_glossary(settings: dict[str, Any]) -> dict[str, str]:
    overrides = settings.get("overrides") if isinstance(settings.get("overrides"), dict) else {}
    merged: dict[str, str] = {}
    for category in ("technical", "abbreviation", "mixed_word"):
        value = overrides.get(category)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _apply_generation_overrides(
    text: str,
    override_glossary: dict[str, str],
    language: str,
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    if not text or not override_glossary:
        return text, [], {}

    placeholder_glossary: dict[str, str] = {}
    replacement_map: dict[str, str] = {}
    for index, (term, replacement) in enumerate(override_glossary.items()):
        placeholder = f"__PROJECT_TTS_OVERRIDE_{index}__"
        placeholder_glossary[term] = placeholder
        replacement_map[placeholder] = replacement

    substituted, rules = apply_glossary_with_rules(text, placeholder_glossary, language=language)
    for rule in rules:
        rule["source"] = "project_tts_override"
        replacement = rule.get("replacement")
        if isinstance(replacement, str) and replacement in replacement_map:
            rule["actual_replacement"] = replacement_map[replacement]
    return substituted, rules, replacement_map


def _restore_overrides(text: str, replacement_map: dict[str, str]) -> str:
    restored = str(text or "")
    for placeholder, replacement in replacement_map.items():
        restored = restored.replace(placeholder, replacement)
    return restored


def prepare_effective_tts_input(
    text: Any,
    language: Any = "en",
    tts_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare the deterministic text identity sent to the TTS engine.

    This mirrors the worker TTS client preparation path: project overrides are
    applied before deterministic normalization, and the resulting spoken text is
    sent to the service with ``already_prepared=True``. It performs no network,
    model, filesystem, or runtime work.
    """

    raw_text = str(text or "")
    lang = str(language or "en")
    settings = _canonical_tts_settings(tts_settings)

    if settings and not settings["normalization_enabled"]:
        prepared = prepare_text_for_tts(raw_text, language=lang, already_prepared=True)
        warnings = list(prepared.warnings or [])
        if "normalization_disabled" not in warnings:
            warnings.append("normalization_disabled")
        return {
            "settings": settings,
            "original_text": prepared.original_text or prepared.raw_text,
            "normalized_text": prepared.normalized_text,
            "spoken_text": prepared.spoken_text,
            "chunks": list(prepared.chunks or []),
            "chunk_pause_ms": list(prepared.chunk_pause_ms or []),
            "tts_normalization_language": prepared.tts_normalization_language,
            "tts_normalization_rules_applied": [],
            "unknown_terms": list(prepared.unknown_terms or []),
            "ambiguous_terms": list(prepared.ambiguous_terms or []),
            "warnings": warnings,
            "applied_overrides": _override_summary(settings),
        }

    replacement_map: dict[str, str] = {}
    pre_rules: list[dict[str, Any]] = []
    source_text = raw_text
    if settings:
        override_glossary = _merged_override_glossary(settings)
        if override_glossary:
            source_text, pre_rules, replacement_map = _apply_generation_overrides(
                raw_text,
                override_glossary,
                lang,
            )

    prepared = prepare_text_for_tts(source_text, language=lang)
    spoken_text = _restore_overrides(prepared.spoken_text, replacement_map)
    normalized_text = _restore_overrides(prepared.normalized_text, replacement_map)
    chunks = [_restore_overrides(chunk, replacement_map) for chunk in list(prepared.chunks or [])]
    rules_applied = pre_rules + list(prepared.tts_normalization_rules_applied or [])
    return {
        "settings": settings,
        "original_text": raw_text,
        "normalized_text": normalized_text,
        "spoken_text": spoken_text,
        "chunks": chunks,
        "chunk_pause_ms": list(prepared.chunk_pause_ms or []),
        "tts_normalization_language": prepared.tts_normalization_language,
        "tts_normalization_rules_applied": rules_applied,
        "unknown_terms": list(prepared.unknown_terms or []),
        "ambiguous_terms": list(prepared.ambiguous_terms or []),
        "warnings": list(prepared.warnings or []),
        "applied_overrides": _override_summary(settings),
    }


def canonicalize_tts_input(
    narration_text: Any,
    *,
    tts_settings: dict[str, Any] | None = None,
    effective_language: Any = None,
    spoken_text: Any = None,
) -> dict[str, str]:
    """Return the semantic TTS input identity used by partial-render hashes.

    ``narration_text`` is the authored text and remains hashed separately by
    the partial-render manifest. This identity is only the effective language
    plus the deterministic spoken text actually supplied to the TTS engine.

    If a finalized result already supplies ``spoken_text``, it is treated as the
    already-prepared effective value to avoid double-normalization.
    """

    supplied_spoken_text = "" if spoken_text is None else _canonical_text(spoken_text)
    if supplied_spoken_text:
        return {
            "language": _canonical_language(effective_language),
            "spoken_text": supplied_spoken_text,
        }

    prepared = prepare_effective_tts_input(
        narration_text,
        language=effective_language or "en",
        tts_settings=tts_settings,
    )
    return {
        "language": _canonical_language(prepared.get("tts_normalization_language")),
        "spoken_text": _canonical_text(prepared.get("spoken_text")),
    }
