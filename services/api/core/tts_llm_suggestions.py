from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = {"abbreviation", "technical", "mixed_word"}
ALLOWED_CONFIDENCES = {"low", "medium", "high"}
MAX_TERM_CHARS = 120
MAX_SPOKEN_CHARS = 160
MAX_REASON_CHARS = 240
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class TtsLlmSuggestionConfig:
    enabled: bool
    provider: str
    ollama_base_url: str
    ollama_model: str
    timeout_seconds: float
    max_terms: int
    context_max_chars: int


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def get_config() -> TtsLlmSuggestionConfig:
    return TtsLlmSuggestionConfig(
        enabled=bool(getattr(settings, "TTS_LLM_SUGGESTIONS_ENABLED", False)),
        provider=str(getattr(settings, "TTS_LLM_PROVIDER", "ollama") or "ollama").strip().lower(),
        ollama_base_url=str(getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434") or "").strip().rstrip("/"),
        ollama_model=str(getattr(settings, "OLLAMA_PRONUNCIATION_MODEL", "llama3.1:8b") or "").strip(),
        timeout_seconds=_bounded_float(
            getattr(settings, "TTS_LLM_SUGGESTION_TIMEOUT_SECONDS", 8),
            default=8.0,
            minimum=0.5,
            maximum=60.0,
        ),
        max_terms=_bounded_int(
            getattr(settings, "TTS_LLM_MAX_TERMS", 20),
            default=20,
            minimum=1,
            maximum=50,
        ),
        context_max_chars=_bounded_int(
            getattr(settings, "TTS_LLM_CONTEXT_MAX_CHARS", 1000),
            default=1000,
            minimum=0,
            maximum=4000,
        ),
    )


def _normalize_language(language: Any) -> str:
    value = str(language or "").strip().lower().replace("_", "-")
    if value.startswith("en"):
        return "en"
    return "tr"


def _clean_plain_text(value: Any, max_chars: int) -> str:
    text = _CONTROL_RE.sub("", str(value or ""))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _looks_unsafe_suggestion(value: str) -> bool:
    lowered = value.lower()
    return (
        "http://" in lowered
        or "https://" in lowered
        or "```" in value
        or "{" in value
        or "}" in value
        or "[" in value
        or "]" in value
    )


def _clean_terms(raw_terms: Any, max_terms: int) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    if not isinstance(raw_terms, list):
        return [], ["terms_must_be_a_list"]

    terms: list[str] = []
    seen: set[str] = set()
    for raw in raw_terms:
        term = _clean_plain_text(raw, MAX_TERM_CHARS)
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)

    if len(terms) > max_terms:
        terms = terms[:max_terms]
        warnings.append(f"terms_truncated_to_{max_terms}")
    return terms, warnings


def _clean_context(raw_context: Any, max_chars: int) -> tuple[str, list[str]]:
    context = _clean_plain_text(raw_context, max_chars)
    original = _clean_plain_text(raw_context, -1)
    if max_chars >= 0 and len(original) > len(context):
        return context, [f"context_truncated_to_{max_chars}"]
    return context, []


def build_prompt(language: str, terms: list[str], context: str) -> str:
    terms_json = json.dumps(terms, ensure_ascii=False)
    return (
        "You suggest spoken pronunciations for Turkish text-to-speech review.\n\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "suggestions": [\n'
        "    {\n"
        '      "term": "original term",\n'
        '      "suggested_spoken": "how a Turkish TTS should speak the term",\n'
        '      "category": "abbreviation | technical | mixed_word",\n'
        '      "confidence": "low | medium | high",\n'
        '      "reason": "short reason"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Do not translate the meaning.\n"
        "- Do not rewrite the full sentence.\n"
        "- Do not add terms not present in the input.\n"
        "- Suggest only a spoken spelling for each provided term.\n"
        '- Use Turkish-friendly phonetic spelling when language is "tr".\n'
        "- category must be one of: abbreviation, technical, mixed_word.\n"
        "- confidence must be one of: low, medium, high.\n"
        "- Keep suggested_spoken short.\n"
        "- If unsure, use low confidence and explain briefly.\n\n"
        f"Language: {language}\n"
        f"Context: {context}\n"
        f"Terms: {terms_json}\n"
    )


def _call_ollama(config: TtsLlmSuggestionConfig, prompt: str) -> str:
    if not config.ollama_base_url:
        raise ValueError("ollama_base_url_missing")
    if not config.ollama_model:
        raise ValueError("ollama_model_missing")

    payload = {
        "model": config.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    request = Request(
        f"{config.ollama_base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=config.timeout_seconds) as response:
        body = response.read().decode("utf-8")

    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("ollama_invalid_response")
    generated = data.get("response")
    if not isinstance(generated, str):
        raise ValueError("ollama_missing_response")
    return generated


def _provider_text_to_json(provider_text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(provider_text or "").strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sanitize_suggestions(raw: Any, requested_terms: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return [], ["provider_malformed_json"]

    raw_suggestions = raw.get("suggestions")
    if not isinstance(raw_suggestions, list):
        return [], ["provider_malformed_json"]

    requested = {term: term for term in requested_terms}
    seen: set[str] = set()
    suggestions: list[dict[str, str]] = []
    dropped = 0

    for item in raw_suggestions:
        if not isinstance(item, dict):
            dropped += 1
            continue
        term = _clean_plain_text(item.get("term"), MAX_TERM_CHARS)
        if term not in requested or term in seen:
            dropped += 1
            continue
        suggested = _clean_plain_text(item.get("suggested_spoken"), MAX_SPOKEN_CHARS)
        category = _clean_plain_text(item.get("category"), 32).lower()
        confidence = _clean_plain_text(item.get("confidence"), 32).lower()
        reason = _clean_plain_text(item.get("reason"), MAX_REASON_CHARS)
        if (
            not suggested
            or _looks_unsafe_suggestion(suggested)
            or category not in ALLOWED_CATEGORIES
            or confidence not in ALLOWED_CONFIDENCES
        ):
            dropped += 1
            continue
        seen.add(term)
        suggestions.append(
            {
                "term": requested[term],
                "suggested_spoken": suggested,
                "category": category,
                "confidence": confidence,
                "reason": reason,
            }
        )

    if dropped:
        warnings.append("invalid_provider_suggestions_dropped")
    return suggestions, warnings


def pronunciation_suggestion_response(language: Any, raw_terms: Any, raw_context: Any) -> dict[str, Any]:
    config = get_config()
    warnings: list[str] = []
    terms, term_warnings = _clean_terms(raw_terms, config.max_terms)
    context, context_warnings = _clean_context(raw_context, config.context_max_chars)
    warnings.extend(term_warnings)
    warnings.extend(context_warnings)

    if not config.enabled:
        return {
            "enabled": False,
            "suggestions": [],
            "fallback_used": True,
            "provider": "",
            "warnings": [*warnings, "LLM pronunciation suggestions are disabled."],
        }

    provider = config.provider
    if provider != "ollama":
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, f"Unsupported LLM pronunciation suggestion provider: {provider}."],
        }

    if not terms:
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, "No pronunciation terms were provided."],
        }

    prompt = build_prompt(_normalize_language(language), terms, context)
    try:
        provider_text = _call_ollama(config, prompt)
    except TimeoutError:
        logger.warning("LLM pronunciation suggestion provider timed out")
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, "LLM pronunciation suggestion provider timed out."],
        }
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("LLM pronunciation suggestion provider unavailable: %s", exc.__class__.__name__)
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, "LLM pronunciation suggestion provider unavailable."],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM pronunciation suggestion provider failed: %s", exc.__class__.__name__)
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, "LLM pronunciation suggestion provider failed."],
        }

    provider_json = _provider_text_to_json(provider_text)
    if provider_json is None:
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, "LLM pronunciation suggestion provider returned malformed JSON."],
        }

    suggestions, validation_warnings = _sanitize_suggestions(provider_json, terms)
    if not suggestions:
        return {
            "enabled": True,
            "suggestions": [],
            "fallback_used": True,
            "provider": provider,
            "warnings": [*warnings, *validation_warnings, "No valid pronunciation suggestions were returned."],
        }

    return {
        "enabled": True,
        "suggestions": suggestions,
        "fallback_used": False,
        "provider": provider,
        "warnings": [*warnings, *validation_warnings],
    }
