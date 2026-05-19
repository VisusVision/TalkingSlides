from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from core.drafts import get_studio_transcript_pages
from core.intelligence_language import detect_lesson_language, resolve_output_language
from core.intelligence_progressive import (
    bounded_adaptive_background_timeout,
    enhancement_response_fields,
    first_provider_name,
    provider_attempt as progressive_provider_attempt,
    provider_chain_contains_ollama,
)
from core.models import LessonIntelligenceReport, Project


logger = logging.getLogger(__name__)

COMPLEXITY_LEVELS = {"beginner", "intermediate", "advanced"}
TECHNICAL_TERMS = {
    "algorithm",
    "api",
    "architecture",
    "asynchronous",
    "authentication",
    "backpropagation",
    "cache",
    "calculus",
    "classifier",
    "database",
    "derivative",
    "distribution",
    "embedding",
    "encryption",
    "framework",
    "gradient",
    "inference",
    "integration",
    "latency",
    "linear regression",
    "matrix",
    "metadata",
    "microservice",
    "neural",
    "optimization",
    "protocol",
    "regression",
    "runtime",
    "serialization",
    "tokenization",
    "transformer",
    "validation",
    "vector",
}
STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "between",
    "could",
    "every",
    "from",
    "have",
    "into",
    "lesson",
    "more",
    "page",
    "slide",
    "that",
    "their",
    "there",
    "these",
    "this",
    "through",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
WHITESPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü][A-Za-zÇĞİÖŞÜçğıöşü0-9_+-]*")


class LessonIntelligenceProviderUnavailable(RuntimeError):
    """Provider cannot run with the current local configuration/runtime."""


class LessonIntelligenceInputError(ValueError):
    """Input cannot be analyzed as a lesson intelligence request."""


class LessonIntelligenceInputTooLarge(LessonIntelligenceInputError):
    """Input exceeded the configured synchronous analysis limit."""


@dataclass(frozen=True)
class LessonPageInput:
    id: int | None
    order: int
    page_key: str
    original_text: str
    narration_text: str

    @property
    def analysis_text(self) -> str:
        return self.narration_text.strip() or self.original_text.strip()

    def to_payload(self, index: int, *, max_text_chars: int = -1) -> dict[str, Any]:
        original_full = self.original_text
        narration_full = self.narration_text
        original_text = _clean_text(original_full, max_chars=max_text_chars)
        narration_text = _clean_text(narration_full, max_chars=max_text_chars)
        text = narration_text.strip() or original_text.strip()
        full_text = self.analysis_text
        return {
            "id": self.id,
            "order": self.order,
            "page_number": index + 1,
            "page_key": self.page_key,
            "display_text": original_text,
            "narration_text": narration_text,
            "analysis_text": text,
            "word_count": len(_words(full_text)),
            "text_truncated": bool(
                max_text_chars >= 0
                and (len(original_full) > max_text_chars or len(narration_full) > max_text_chars)
            ),
        }


@dataclass(frozen=True)
class LessonIntelligenceInput:
    project_id: int
    title: str
    description: str
    pages: list[LessonPageInput]
    source_hash: str
    input_chars: int
    detected_language: str = "unknown"
    output_language: str = "en"
    language_confidence: float = 0.0
    input_truncated: bool = False
    source_chars: int = 0
    page_text_limit: int = -1

    def to_provider_payload(self) -> dict[str, Any]:
        pages = [page.to_payload(index, max_text_chars=self.page_text_limit) for index, page in enumerate(self.pages)]
        description = _clean_text(self.description, max_chars=1200) if self.input_truncated else self.description
        input_text = _compose_input_text(self.title, description, pages)
        return {
            "project": {
                "id": self.project_id,
                "title": self.title,
                "description": description,
            },
            "pages": pages,
            "source_hash": self.source_hash,
            "input_chars": self.input_chars,
            "source_chars": self.source_chars or self.input_chars,
            "input_truncated": self.input_truncated,
            "detected_language": self.detected_language,
            "output_language": self.output_language,
            "language_confidence": self.language_confidence,
            "input_text": input_text,
        }


class LessonIntelligenceProvider(Protocol):
    provider_name: str

    def analyze_lesson(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class HeuristicLessonIntelligenceProvider:
    provider_name = "heuristic"

    def analyze_lesson(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        output_language = _output_language(input_payload)
        pages = [page for page in input_payload.get("pages", []) if isinstance(page, dict)]
        all_text = _clean_text(input_payload.get("input_text"), max_chars=-1)
        sentences = _sentences(all_text)
        words = _words(all_text)
        avg_sentence_words = (len(words) / len(sentences)) if sentences else 0.0
        technical_hits = _technical_hits(all_text)
        page_word_counts = [int(page.get("word_count") or 0) for page in pages]
        max_page_words = max(page_word_counts or [0])
        example_present = _has_example_signal(all_text)
        intro_present = _has_intro_signal(pages[:2], input_payload)
        conclusion_present = _has_conclusion_signal(pages[-2:])

        clarity_warnings: list[dict[str, Any]] = []
        page_suggestions: list[dict[str, Any]] = []
        expanded_suggestions: list[dict[str, Any]] = []

        if avg_sentence_words > 24:
            clarity_warnings.append(
                {
                    "type": "long_sentences",
                    "severity": "medium",
                    "message": _lesson_message(output_language, "long_sentences", value=avg_sentence_words),
                }
            )
        if max_page_words > 95:
            clarity_warnings.append(
                {
                    "type": "dense_slide",
                    "severity": "medium",
                    "message": _lesson_message(output_language, "dense_slide"),
                }
            )
        empty_pages = [page for page in pages if not _page_text(page)]
        if len(empty_pages) >= 2:
            clarity_warnings.append(
                {
                    "type": "empty_pages",
                    "severity": "medium",
                    "message": _lesson_message(output_language, "empty_pages"),
                }
            )
        if not example_present:
            clarity_warnings.append(
                {
                    "type": "missing_examples",
                    "severity": "low",
                    "message": _lesson_message(output_language, "missing_examples"),
                }
            )
        if not intro_present:
            clarity_warnings.append(
                {
                    "type": "missing_intro",
                    "severity": "low",
                    "message": _lesson_message(output_language, "missing_intro"),
                }
            )
        if not conclusion_present and len(pages) >= 2:
            clarity_warnings.append(
                {
                    "type": "missing_conclusion",
                    "severity": "low",
                    "message": _lesson_message(output_language, "missing_conclusion"),
                }
            )

        for page in pages:
            page_number = int(page.get("page_number") or 0)
            page_key = str(page.get("page_key") or "")
            display_text = _clean_text(page.get("display_text"), max_chars=-1)
            narration_text = _clean_text(page.get("narration_text"), max_chars=-1)
            analysis_text = _page_text(page)
            page_words = len(_words(analysis_text))
            display_words = len(_words(display_text))
            narration_words = len(_words(narration_text))
            bullet_lines = _bullet_line_count(display_text or narration_text)

            if page_words == 0:
                page_suggestions.append(
                    _page_suggestion(
                        page_number,
                        page_key,
                        _lesson_message(output_language, "empty_page_suggestion"),
                        "empty_page",
                    )
                )
                expanded_suggestions.append(
                    _expanded_suggestion(
                        page_number,
                        page_key,
                        _lesson_message(output_language, "empty_page_advice"),
                        _draft_narration_for_page(
                            output_language=output_language,
                            suggestion_type="empty_page",
                            display_text=display_text,
                            narration_text=narration_text,
                        ),
                        "empty_page",
                        generated_by=self.provider_name,
                    )
                )
                continue

            if page_words > 95:
                page_suggestions.append(
                    _page_suggestion(
                        page_number,
                        page_key,
                        _lesson_message(output_language, "reduce_density_suggestion"),
                        "reduce_density",
                    )
                )

            if bullet_lines >= 2 and narration_words <= max(display_words + 8, 45):
                clarity_warnings.append(
                    {
                        "type": "bullets_without_explanation",
                        "severity": "low",
                        "page_number": page_number,
                        "page_key": page_key,
                        "message": _lesson_message(output_language, "bullets_without_explanation"),
                    }
                )
                page_suggestions.append(
                    _page_suggestion(
                        page_number,
                        page_key,
                        _lesson_message(output_language, "explain_bullets_suggestion"),
                        "explain_bullets",
                    )
                )
                expanded_suggestions.append(
                    _expanded_suggestion(
                        page_number,
                        page_key,
                        _lesson_message(output_language, "bullet_expansion"),
                        _draft_narration_for_page(
                            output_language=output_language,
                            suggestion_type="bullet_expansion",
                            display_text=display_text,
                            narration_text=narration_text,
                        ),
                        "bullet_expansion",
                        generated_by=self.provider_name,
                    )
                )

            if narration_words < 24 and display_words >= 8:
                expanded_suggestions.append(
                    _expanded_suggestion(
                        page_number,
                        page_key,
                        _lesson_message(output_language, "short_narration"),
                        _draft_narration_for_page(
                            output_language=output_language,
                            suggestion_type="short_narration",
                            display_text=display_text,
                            narration_text=narration_text,
                        ),
                        "short_narration",
                        generated_by=self.provider_name,
                    )
                )

        complexity_score, complexity_level, complexity_reasons = _complexity_assessment(
            avg_sentence_words=avg_sentence_words,
            technical_hits=technical_hits,
            max_page_words=max_page_words,
            words=words,
            output_language=output_language,
        )

        summary = _summary_from_text(input_payload.get("project", {}).get("title"), all_text, output_language=output_language)
        limitations = [
            _lesson_message(output_language, "heuristic_limitation"),
            _lesson_message(output_language, "advisory_limitation"),
        ]
        if input_payload.get("input_truncated"):
            limitations.append(_lesson_message(output_language, "lesson_truncated_limitation"))
        detected_language = str(input_payload.get("detected_language") or "unknown")
        if detected_language == "unknown":
            limitations.append(_lesson_message(output_language, "language_uncertain_limitation"))
        return {
            "provider": self.provider_name,
            "lesson_summary": summary,
            "short_description": _short_description(summary),
            "complexity_level": complexity_level,
            "complexity_score": complexity_score,
            "complexity_reasons": complexity_reasons,
            "clarity_warnings": clarity_warnings,
            "page_suggestions": page_suggestions[:12],
            "expanded_narration_suggestions": expanded_suggestions[:12],
            "suggested_tags": _suggested_tags(all_text, technical_hits),
            "limitations": limitations,
            "metadata": {
                "page_count": len(pages),
                "input_char_count": int(input_payload.get("input_chars") or len(all_text)),
                "source_char_count": int(input_payload.get("source_chars") or len(all_text)),
                "input_truncated": bool(input_payload.get("input_truncated")),
                "detected_language": detected_language,
                "output_language": output_language,
                "language_confidence": float(input_payload.get("language_confidence") or 0.0),
                "average_sentence_words": round(avg_sentence_words, 2),
                "technical_terms_detected": technical_hits,
            },
        }


class OllamaLessonIntelligenceProvider:
    provider_name = "ollama"

    def __init__(self, *, background: bool = False) -> None:
        self.background = bool(background)
        self.base_url = _string_setting(
            "OLLAMA_LESSON_INTELLIGENCE_BASE_URL",
            _string_setting("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        ).rstrip("/")
        self.model = _string_setting("OLLAMA_LESSON_INTELLIGENCE_MODEL", "qwen2.5:7b-instruct")
        configured_timeout = _float_setting("LESSON_INTELLIGENCE_TIMEOUT_SECONDS", 30.0, minimum=0.5, maximum=180.0)
        if background:
            self.timeout_seconds = _background_provider_timeout(
                cap_setting="LESSON_INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS",
            )
        else:
            self.timeout_seconds = _effective_sync_provider_timeout(
                configured_timeout,
                cap_setting="LESSON_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS",
            )
        self.last_timeout_seconds = self.timeout_seconds

    def analyze_lesson(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise LessonIntelligenceProviderUnavailable("Ollama base URL is not configured")
        if not self.model:
            raise LessonIntelligenceProviderUnavailable("Ollama lesson intelligence model is not configured")
        timeout_seconds = (
            adaptive_lesson_intelligence_timeout(input_payload, base_seconds=self.timeout_seconds)
            if self.background
            else self.timeout_seconds
        )
        self.last_timeout_seconds = timeout_seconds

        request_payload = {
            "model": self.model,
            "prompt": _ollama_prompt(input_payload),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
            data = json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LessonIntelligenceProviderUnavailable(f"Ollama request failed: {exc.__class__.__name__}") from exc

        if not isinstance(data, dict):
            raise LessonIntelligenceProviderUnavailable("Ollama response must be a JSON object")
        response_text = data.get("response")
        if response_text is None:
            provider_json = data
        else:
            provider_json = _json_object_from_text(str(response_text or ""))
        normalized = _normalize_provider_result(provider_json, provider_name=self.provider_name)
        normalized["metadata"] = {
            **dict(normalized.get("metadata") or {}),
            "model": self.model,
            "base_url_configured": bool(self.base_url),
            "timeout_seconds": timeout_seconds,
        }
        return normalized


class PaidLessonIntelligenceProvider:
    """Placeholder for later paid-provider support. It never calls externally."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = str(provider_name or "external").strip().lower() or "external"

    def analyze_lesson(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not _bool_setting("LESSON_INTELLIGENCE_ALLOW_EXTERNAL", False):
            raise LessonIntelligenceProviderUnavailable("external lesson intelligence providers are disabled")
        raise LessonIntelligenceProviderUnavailable(
            f"{self.provider_name} lesson intelligence provider is not implemented"
        )


def lesson_intelligence_enabled() -> bool:
    return _bool_setting("LESSON_INTELLIGENCE_ENABLED", True)


def provider_chain_from_settings() -> list[str]:
    raw = _string_setting("LESSON_INTELLIGENCE_PROVIDER_CHAIN", "")
    if not raw:
        raw = _string_setting("LESSON_INTELLIGENCE_PROVIDER", "heuristic")
    providers = [item.strip().lower() for item in re.split(r"[\s,]+", raw) if item.strip()]
    if not providers:
        providers = ["heuristic"]
    if "heuristic" not in providers:
        providers.append("heuristic")
    return providers


def get_lesson_intelligence_provider(provider_name: str) -> LessonIntelligenceProvider:
    provider = str(provider_name or "heuristic").strip().lower()
    if provider == "heuristic":
        return HeuristicLessonIntelligenceProvider()
    if provider == "ollama":
        return OllamaLessonIntelligenceProvider()
    if provider in {"openai", "anthropic", "azure_openai", "external", "paid"}:
        return PaidLessonIntelligenceProvider(provider)
    raise LessonIntelligenceProviderUnavailable(f"unknown lesson intelligence provider: {provider}")


def build_lesson_intelligence_input(
    project: Project,
    *,
    max_chars: int | None = None,
    output_language: str = "auto",
    request_language: str = "",
) -> LessonIntelligenceInput:
    limit = int(max_chars if max_chars is not None else _int_setting("LESSON_INTELLIGENCE_MAX_INPUT_CHARS", 20000))
    title = _clean_text(getattr(project, "title", ""), max_chars=500)
    description = _clean_text(getattr(project, "description", ""), max_chars=4000)
    pages: list[LessonPageInput] = []
    for index, raw_page in enumerate(get_studio_transcript_pages(project)):
        if not isinstance(raw_page, dict):
            continue
        page_id = _optional_int(raw_page.get("id"))
        pages.append(
            LessonPageInput(
                id=page_id,
                order=_safe_int(raw_page.get("order"), index),
                page_key=_clean_text(raw_page.get("page_key"), max_chars=64) or f"page-{index + 1}",
                original_text=_clean_text(raw_page.get("original_text"), max_chars=-1),
                narration_text=_clean_text(raw_page.get("narration_text"), max_chars=-1),
            )
        )

    if not any(page.analysis_text for page in pages):
        raise LessonIntelligenceInputError("Lesson transcript is empty.")

    full_detection_text = _compose_input_text(title, description, [page.to_payload(index) for index, page in enumerate(pages)])
    language = detect_lesson_language(full_detection_text)
    detected_language = str(language.get("language") or "unknown")
    resolved_output_language = resolve_output_language(
        requested=output_language,
        detected=detected_language,
        request_language=request_language,
    )
    source_payload = {
        "project": {"title": title, "description": description},
        "output_language": resolved_output_language,
        "pages": [
            {
                "order": page.order,
                "page_key": page.page_key,
                "original_text": page.original_text,
                "narration_text": page.narration_text,
            }
            for page in pages
        ],
    }
    source_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_chars = len(full_detection_text)
    page_text_limit, input_chars, input_truncated = _lesson_compaction_for_limit(title, description, pages, limit)

    return LessonIntelligenceInput(
        project_id=int(project.id),
        title=title,
        description=description,
        pages=pages,
        source_hash=hashlib.sha256(source_json.encode("utf-8", errors="ignore")).hexdigest(),
        input_chars=input_chars,
        detected_language=detected_language,
        output_language=resolved_output_language,
        language_confidence=float(language.get("confidence") or 0.0),
        input_truncated=input_truncated,
        source_chars=source_chars,
        page_text_limit=page_text_limit,
    )


def analyze_with_provider_chain(
    lesson_input: LessonIntelligenceInput,
    *,
    chain: list[str] | None = None,
) -> dict[str, Any]:
    provider_chain = chain or provider_chain_from_settings()
    input_payload = lesson_input.to_provider_payload()
    attempts: list[dict[str, str]] = []
    first_provider = ""

    for provider_name in provider_chain:
        name = str(provider_name or "").strip().lower()
        if not name:
            continue
        if not first_provider:
            first_provider = name
        if name == "auto":
            attempts.append(_provider_attempt(name, "skipped", "auto cannot include itself"))
            continue
        try:
            provider = get_lesson_intelligence_provider(name)
            result = provider.analyze_lesson(input_payload)
            normalized = _normalize_provider_result(result, provider_name=getattr(provider, "provider_name", name))
        except LessonIntelligenceProviderUnavailable as exc:
            attempts.append(_provider_attempt(name, "skipped", exc))
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lesson intelligence provider failed provider=%s error=%s", name, exc.__class__.__name__)
            attempts.append(_provider_attempt(name, "failed", exc))
            continue

        attempts.append(_provider_attempt(normalized["provider"], "success"))
        normalized["provider_chain"] = provider_chain
        normalized["fallback_used"] = bool(first_provider and normalized["provider"] != first_provider)
        normalized["metadata"] = {
            **dict(normalized.get("metadata") or {}),
            "provider_chain_attempts": attempts,
            "source_hash": lesson_input.source_hash,
            "detected_language": lesson_input.detected_language,
            "output_language": lesson_input.output_language,
            "language_confidence": lesson_input.language_confidence,
            "input_truncated": lesson_input.input_truncated,
            "source_char_count": lesson_input.source_chars,
            "input_char_count": lesson_input.input_chars,
        }
        return normalized

    fallback_provider = HeuristicLessonIntelligenceProvider()
    fallback = _normalize_provider_result(fallback_provider.analyze_lesson(input_payload), provider_name="heuristic")
    attempts.append(_provider_attempt("heuristic", "success"))
    fallback["provider_chain"] = provider_chain
    fallback["fallback_used"] = True
    fallback["metadata"] = {
        **dict(fallback.get("metadata") or {}),
        "provider_chain_attempts": attempts,
        "source_hash": lesson_input.source_hash,
        "detected_language": lesson_input.detected_language,
        "output_language": lesson_input.output_language,
        "language_confidence": lesson_input.language_confidence,
        "input_truncated": lesson_input.input_truncated,
        "source_char_count": lesson_input.source_chars,
        "input_char_count": lesson_input.input_chars,
    }
    return fallback


def progressive_ollama_enabled(chain: list[str] | None = None) -> bool:
    provider_chain = chain or provider_chain_from_settings()
    return _bool_setting("INTELLIGENCE_BACKGROUND_ENHANCEMENT_ENABLED", True) and provider_chain_contains_ollama(provider_chain)


def adaptive_lesson_intelligence_timeout(input_payload: dict[str, Any], *, base_seconds: float | None = None) -> float:
    payload = input_payload if isinstance(input_payload, dict) else {}
    pages = [page for page in payload.get("pages", []) if isinstance(page, dict)]
    base = (
        float(base_seconds)
        if base_seconds is not None
        else _background_provider_timeout(cap_setting="LESSON_INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS")
    )
    return bounded_adaptive_background_timeout(
        base_seconds=base,
        input_chars=_safe_int(payload.get("input_chars") or payload.get("source_chars"), 0),
        page_count=len(pages),
    )


def analyze_lesson_heuristic_immediate(
    lesson_input: LessonIntelligenceInput,
    *,
    chain: list[str] | None = None,
    enhancement_provider: str = "",
    enhancement_status: str = "",
) -> dict[str, Any]:
    provider_chain = chain or provider_chain_from_settings()
    input_payload = lesson_input.to_provider_payload()
    fallback_provider = HeuristicLessonIntelligenceProvider()
    normalized = _normalize_provider_result(fallback_provider.analyze_lesson(input_payload), provider_name="heuristic")
    attempts: list[dict[str, str]] = []
    enhancement_name = str(enhancement_provider or "").strip().lower()
    for provider_name in provider_chain:
        name = str(provider_name or "").strip().lower()
        if not name or name == "auto":
            continue
        if name == "heuristic":
            attempts.append(_provider_attempt("heuristic", "success"))
        elif enhancement_name and name == enhancement_name:
            attempts.append(progressive_provider_attempt(name, enhancement_status or "queued"))
        else:
            attempts.append(_provider_attempt(name, "skipped", "provider deferred to heuristic fallback"))
    if not any(item.get("provider") == "heuristic" for item in attempts):
        attempts.append(_provider_attempt("heuristic", "success"))

    first_provider = first_provider_name(provider_chain)
    normalized["provider_chain"] = provider_chain
    normalized["fallback_used"] = bool(first_provider and first_provider != "heuristic")
    normalized["metadata"] = {
        **dict(normalized.get("metadata") or {}),
        "provider_chain_attempts": attempts,
        "source_hash": lesson_input.source_hash,
        "detected_language": lesson_input.detected_language,
        "output_language": lesson_input.output_language,
        "language_confidence": lesson_input.language_confidence,
        "input_truncated": lesson_input.input_truncated,
        "source_char_count": lesson_input.source_chars,
        "input_char_count": lesson_input.input_chars,
    }
    return normalized


def analyze_lesson_ollama_background(
    lesson_input: LessonIntelligenceInput,
    *,
    chain: list[str] | None = None,
) -> dict[str, Any]:
    provider_chain = chain or provider_chain_from_settings()
    input_payload = lesson_input.to_provider_payload()
    provider = OllamaLessonIntelligenceProvider(background=True)
    result = provider.analyze_lesson(input_payload)
    normalized = _normalize_provider_result(result, provider_name=provider.provider_name)
    normalized["provider_chain"] = provider_chain
    normalized["fallback_used"] = False
    normalized["metadata"] = {
        **dict(normalized.get("metadata") or {}),
        "provider_chain_attempts": [progressive_provider_attempt("ollama", "success")],
        "source_hash": lesson_input.source_hash,
        "detected_language": lesson_input.detected_language,
        "output_language": lesson_input.output_language,
        "language_confidence": lesson_input.language_confidence,
        "input_truncated": lesson_input.input_truncated,
        "source_char_count": lesson_input.source_chars,
        "input_char_count": lesson_input.input_chars,
        "timeout_seconds": getattr(provider, "last_timeout_seconds", None),
    }
    return normalized


def apply_analysis_to_report(
    report: LessonIntelligenceReport,
    analysis: dict[str, Any],
    *,
    source_hash: str,
) -> LessonIntelligenceReport:
    report.status = "done"
    report.provider = str(analysis.get("provider") or "heuristic").strip().lower()
    report.provider_chain = list(analysis.get("provider_chain") or provider_chain_from_settings())
    report.fallback_used = bool(analysis.get("fallback_used"))
    report.source_hash = source_hash
    report.summary = str(analysis.get("lesson_summary") or analysis.get("summary") or "")
    report.short_description = str(analysis.get("short_description") or "")
    report.complexity_level = str(analysis.get("complexity_level") or "beginner")
    report.complexity_score = max(0, min(100, _safe_int(analysis.get("complexity_score"), 0)))
    report.complexity_reasons = _safe_json_list(analysis.get("complexity_reasons"))
    report.clarity_warnings = _safe_json_list(analysis.get("clarity_warnings"))
    report.page_suggestions = _safe_json_list(analysis.get("page_suggestions"))
    report.expanded_narration_suggestions = _safe_json_list(analysis.get("expanded_narration_suggestions"))
    report.suggested_tags = _safe_json_list(analysis.get("suggested_tags"))
    report.limitations = _safe_json_list(analysis.get("limitations"))
    report.metadata = _safe_json_dict(analysis.get("metadata"))
    report.error_message = ""
    report.save(
        update_fields=[
            "status",
            "provider",
            "provider_chain",
            "fallback_used",
            "source_hash",
            "summary",
            "short_description",
            "complexity_level",
            "complexity_score",
            "complexity_reasons",
            "clarity_warnings",
            "page_suggestions",
            "expanded_narration_suggestions",
            "suggested_tags",
            "limitations",
            "metadata",
            "error_message",
            "updated_at",
        ]
    )
    return report


def report_response_payload(
    report: LessonIntelligenceReport | None,
    *,
    enabled: bool = True,
    current_source_hash: str = "",
) -> dict[str, Any]:
    current_hash = str(current_source_hash or "")
    if report is None:
        return {
            "enabled": enabled,
            "status": "empty" if enabled else "disabled",
            "provider": "",
            "fallback_used": False,
            "provider_chain_attempts": [],
            "enhancement_available": False,
            "enhancement_pending": False,
            "enhancement_status": "",
            "enhancement_provider": "",
            "enhancement_error_safe": "",
            "source_hash": "",
            "report_source_hash": "",
            "current_source_hash": current_hash,
            "is_stale": bool(enabled),
            "detected_language": "unknown",
            "output_language": "en",
            "language_confidence": 0.0,
            "summary": "",
            "short_description": "",
            "complexity": {"level": "", "display_label": "", "score": 0, "reasons": []},
            "clarity_warnings": [],
            "page_suggestions": [],
            "expanded_narration_suggestions": [],
            "suggested_tags": [],
            "limitations": [],
        }
    report_metadata = report.metadata if isinstance(report.metadata, dict) else {}
    output_language = str(report_metadata.get("output_language") or "en")
    report_hash = str(report.source_hash or "")
    provider_chain_attempts = report_metadata.get("provider_chain_attempts")
    if not isinstance(provider_chain_attempts, list):
        provider_chain_attempts = []
    return {
        "enabled": enabled,
        "id": report.id,
        "status": report.status,
        "provider": report.provider,
        "provider_chain": report.provider_chain if isinstance(report.provider_chain, list) else [],
        "fallback_used": bool(report.fallback_used),
        "provider_chain_attempts": provider_chain_attempts,
        **enhancement_response_fields(report_metadata),
        "detected_language": str(report_metadata.get("detected_language") or "unknown"),
        "output_language": output_language,
        "language_confidence": float(report_metadata.get("language_confidence") or 0.0),
        "source_hash": report_hash,
        "report_source_hash": report_hash,
        "current_source_hash": current_hash,
        "is_stale": bool(enabled and current_hash and report_hash != current_hash),
        "summary": report.summary,
        "short_description": report.short_description,
        "complexity": {
            "level": report.complexity_level,
            "display_label": _complexity_display_label(report.complexity_level, output_language),
            "score": int(report.complexity_score or 0),
            "reasons": report.complexity_reasons if isinstance(report.complexity_reasons, list) else [],
        },
        "clarity_warnings": report.clarity_warnings if isinstance(report.clarity_warnings, list) else [],
        "page_suggestions": report.page_suggestions if isinstance(report.page_suggestions, list) else [],
        "expanded_narration_suggestions": (
            report.expanded_narration_suggestions
            if isinstance(report.expanded_narration_suggestions, list)
            else []
        ),
        "suggested_tags": report.suggested_tags if isinstance(report.suggested_tags, list) else [],
        "limitations": report.limitations if isinstance(report.limitations, list) else [],
        "metadata": {
            key: value
            for key, value in (report.metadata if isinstance(report.metadata, dict) else {}).items()
            if key in {
                "provider_chain_attempts",
                "page_count",
                "input_char_count",
                "source_char_count",
                "average_sentence_words",
                "detected_language",
                "output_language",
                "language_confidence",
                "input_truncated",
            }
        },
        "error_message": report.error_message,
        "created_at": report.created_at.isoformat() if report.created_at else "",
        "updated_at": report.updated_at.isoformat() if report.updated_at else "",
    }


def _normalize_provider_result(raw: Any, *, provider_name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LessonIntelligenceProviderUnavailable("provider result must be a JSON object")
    complexity = raw.get("complexity") if isinstance(raw.get("complexity"), dict) else {}
    level = str(raw.get("complexity_level") or complexity.get("level") or "beginner").strip().lower()
    if level not in COMPLEXITY_LEVELS:
        level = "intermediate"
    score = max(0, min(100, _safe_int(raw.get("complexity_score", complexity.get("score")), 50)))
    summary = _clean_text(raw.get("lesson_summary") or raw.get("summary"), max_chars=1200)
    short_description = _clean_text(raw.get("short_description"), max_chars=260)
    if not summary:
        raise LessonIntelligenceProviderUnavailable("provider result missing lesson summary")
    if not short_description:
        short_description = _short_description(summary)
    return {
        "provider": str(raw.get("provider") or provider_name or "heuristic").strip().lower(),
        "lesson_summary": summary,
        "short_description": short_description,
        "complexity_level": level,
        "complexity_score": score,
        "complexity_reasons": _safe_json_list(raw.get("complexity_reasons") or complexity.get("reasons")),
        "clarity_warnings": _safe_json_list(raw.get("clarity_warnings")),
        "page_suggestions": _safe_json_list(raw.get("page_suggestions")),
        "expanded_narration_suggestions": _normalize_expanded_narration_suggestions(
            raw.get("expanded_narration_suggestions"),
            provider_name=provider_name,
        ),
        "suggested_tags": _safe_json_list(raw.get("suggested_tags")),
        "limitations": _safe_json_list(raw.get("limitations")),
        "metadata": _safe_json_dict(raw.get("metadata")),
    }


def _normalize_expanded_narration_suggestions(raw: Any, *, provider_name: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in _safe_json_list(raw):
        if isinstance(item, str):
            normalized.append(
                {
                    "page_number": 0,
                    "page_key": "",
                    "type": "expanded_narration",
                    "title": "Expand narration",
                    "advice": item,
                    "suggestion": item,
                    "draft_narration": "",
                    "copy_text": "",
                    "generated_by": provider_name,
                    "ai_generated": True,
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        suggestion_type = str(item.get("type") or "expanded_narration").strip() or "expanded_narration"
        title = _clean_text(item.get("title"), max_chars=120) or _expanded_suggestion_title(suggestion_type)
        advice = _clean_text(item.get("advice") or item.get("suggestion") or item.get("message"), max_chars=700)
        draft = _clean_text(item.get("draft_narration") or item.get("copy_text"), max_chars=1200)
        normalized.append(
            {
                "page_number": _safe_int(item.get("page_number"), 0),
                "page_key": _clean_text(item.get("page_key"), max_chars=80),
                "type": suggestion_type,
                "title": title,
                "advice": advice,
                "suggestion": advice,
                "draft_narration": draft,
                "copy_text": draft,
                "generated_by": _clean_text(item.get("generated_by"), max_chars=40) or provider_name,
                "ai_generated": bool(item.get("ai_generated", True)),
            }
        )
    return normalized


def _ollama_prompt(input_payload: dict[str, Any]) -> str:
    output_language = _output_language(input_payload)
    safe_payload = {
        "project": input_payload.get("project") or {},
        "pages": input_payload.get("pages") or [],
        "source_hash": input_payload.get("source_hash") or "",
        "detected_language": input_payload.get("detected_language") or "unknown",
        "output_language": output_language,
        "input_truncated": bool(input_payload.get("input_truncated")),
    }
    language_instruction = (
        "Respond in Turkish. Keep JSON keys in English, but all user-facing text values in Turkish. "
        if output_language == "tr"
        else "Respond in English. "
    )
    return (
        f"You are a lesson quality analyst for publisher Studio. Return JSON only. {language_instruction}"
        "Do not edit lesson text, do not trigger rendering, and do not suggest hidden actions. "
        "Focus on clarity, structure, narration quality, and learner comprehension.\n"
        "Required JSON shape: {"
        "\"lesson_summary\":\"short paragraph\","
        "\"short_description\":\"one sentence\","
        "\"complexity_level\":\"beginner|intermediate|advanced\","
        "\"complexity_score\":0,"
        "\"complexity_reasons\":[\"reason\"],"
        "\"clarity_warnings\":[{\"type\":\"warning_type\",\"severity\":\"low|medium|high\",\"message\":\"text\"}],"
        "\"page_suggestions\":[{\"page_number\":1,\"page_key\":\"key\",\"type\":\"suggestion_type\",\"suggestion\":\"text\"}],"
        "\"expanded_narration_suggestions\":[{\"page_number\":1,\"page_key\":\"key\",\"type\":\"short_narration\",\"title\":\"Expand narration\",\"advice\":\"why this helps\",\"draft_narration\":\"actual narration to apply\",\"copy_text\":\"same useful narration text\",\"generated_by\":\"ollama\",\"ai_generated\":true}],"
        "\"suggested_tags\":[\"tag\"],"
        "\"limitations\":[\"limitation\"]"
        "}.\n"
        f"Lesson JSON:\n{json.dumps(safe_payload, ensure_ascii=False)}"
    )


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                raise LessonIntelligenceProviderUnavailable("provider returned invalid JSON") from exc
        else:
            raise LessonIntelligenceProviderUnavailable("provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise LessonIntelligenceProviderUnavailable("provider JSON response must be an object")
    return data


def _output_language(input_payload: dict[str, Any]) -> str:
    language = str(input_payload.get("output_language") or "").strip().lower()
    return language if language in {"tr", "en"} else "en"


def _complexity_display_label(level: Any, output_language: str) -> str:
    normalized = str(level or "").strip().lower()
    labels = {
        "tr": {
            "beginner": "başlangıç",
            "intermediate": "orta",
            "advanced": "ileri",
        },
        "en": {
            "beginner": "beginner",
            "intermediate": "intermediate",
            "advanced": "advanced",
        },
    }
    return labels.get(output_language, labels["en"]).get(normalized, normalized)


def _lesson_message(language: str, key: str, **kwargs: Any) -> str:
    if language == "tr":
        messages = {
            "long_sentences": f"Ortalama cümle uzunluğu {float(kwargs.get('value') or 0):.0f} kelime; daha kısa anlatım cümleleri açıklığı artırabilir.",
            "dense_slide": "En az bir slayt/sayfa çok yoğun metin içeriyor; görsel yoğunluğu azaltın veya fikri bölün.",
            "empty_pages": "Birden fazla slayt/sayfada transkript veya anlatım metni yok.",
            "missing_examples": "Belirgin örnek veya vaka anlatımı tespit edilmedi.",
            "missing_intro": "Açılış, hedefleri veya öğrencinin ne öğreneceğini yeterince net tanıtmıyor.",
            "missing_conclusion": "Kapanış, dersi özetlemiyor veya sonraki adımları net vermiyor.",
            "empty_page_suggestion": "Bu slayta öğrenme bağlamı kazandırmak için kısa bir anlatım notu ekleyin.",
            "empty_page_advice": "Bu slaytta kullanılabilir anlatım yok; öğrenciye bağlam, hedef ve geçiş veren kısa bir anlatım gerekir.",
            "reduce_density_suggestion": "Görsel yoğunluğu azaltmak için bu sayfayı bölün veya ayrıntıları anlatıma taşıyın.",
            "bullets_without_explanation": "Madde ağırlıklı slayt metni daha açıklayıcı anlatım gerektirebilir.",
            "explain_bullets_suggestion": "Öğrencinin yalnızca listeyi değil, gerekçeyi de duyması için maddeler arasına açıklayıcı anlatım ekleyin.",
            "bullet_expansion": "Önce ana fikri tanıtın, her maddenin neden önemli olduğunu açıklayın ve sonraki slayta bağlantı kurarak kapatın.",
            "short_narration": "Ana fikri tanımlayan, somut bir örnek veren ve sonraki kavrama geçiş yapan 2-3 cümlelik anlatım ekleyin.",
            "heuristic_limitation": "Heuristik analiz deterministik metin sinyallerini kullanır ve alan nüanslarını kaçırabilir.",
            "advisory_limitation": "Öneriler danışma amaçlıdır ve derse otomatik olarak uygulanmadı.",
            "lesson_truncated_limitation": "Ders çok uzun olduğu için bazı metinler analiz öncesinde güvenli şekilde kısaltıldı.",
            "language_uncertain_limitation": "Ders dili belirsiz olduğu için çıktı dili güvenli varsayımla seçildi.",
        }
        return messages.get(key, key)
    messages = {
        "long_sentences": f"Average sentence length is {float(kwargs.get('value') or 0):.0f} words; shorter narration sentences may improve clarity.",
        "dense_slide": "At least one slide/page has a high word count; reduce on-slide density or split the idea.",
        "empty_pages": "Multiple slides/pages have no transcript or narration text.",
        "missing_examples": "No clear example or case-study language was detected.",
        "missing_intro": "The opening does not clearly introduce goals or what the learner will learn.",
        "missing_conclusion": "The ending does not clearly recap the lesson or provide next steps.",
        "empty_page_suggestion": "Add a short narration note so this slide has learning context.",
        "empty_page_advice": "This slide has no usable narration; add a short explanation that gives learners context, the goal, and a transition.",
        "reduce_density_suggestion": "Split this page or move detail into narration to reduce visual density.",
        "bullets_without_explanation": "Bullet-heavy slide text may need more explanatory narration.",
        "explain_bullets_suggestion": "Add explanatory narration between bullet points so learners hear the reasoning, not just the list.",
        "bullet_expansion": "Introduce the point, explain why each bullet matters, and close with how the bullets connect to the next slide.",
        "short_narration": "Add a 2-3 sentence narration that defines the key idea, gives one concrete example, and transitions to the next concept.",
        "heuristic_limitation": "Heuristic analysis uses deterministic text signals and may miss domain nuance.",
        "advisory_limitation": "Suggestions are advisory and were not applied to the lesson.",
        "lesson_truncated_limitation": "Some lesson text was safely shortened before analysis because the lesson is large.",
        "language_uncertain_limitation": "Lesson language was uncertain, so the output language was chosen by fallback.",
    }
    return messages.get(key, key)


def _complexity_assessment(
    *,
    avg_sentence_words: float,
    technical_hits: list[str],
    max_page_words: int,
    words: list[str],
    output_language: str = "en",
) -> tuple[int, str, list[str]]:
    long_word_ratio = 0.0
    if words:
        long_word_ratio = len([word for word in words if len(word) >= 12]) / len(words)
    score = 25
    score += min(30, int(avg_sentence_words * 1.15))
    score += min(25, len(technical_hits) * 4)
    score += 10 if max_page_words > 95 else 0
    score += 10 if long_word_ratio > 0.18 else 0
    score = max(0, min(100, score))
    if score < 40:
        level = "beginner"
    elif score < 70:
        level = "intermediate"
    else:
        level = "advanced"
    if output_language == "tr":
        reasons = [
            f"Ortalama cümle uzunluğu {avg_sentence_words:.1f} kelime.",
            f"{len(technical_hits)} teknik terim sinyali algılandı.",
        ]
    else:
        reasons = [
            f"Average sentence length is {avg_sentence_words:.1f} words.",
            f"Detected {len(technical_hits)} technical term signals.",
        ]
    if max_page_words > 95:
        reasons.append(
            f"En yoğun sayfada {max_page_words} kelime var."
            if output_language == "tr"
            else f"Densest page has {max_page_words} words."
        )
    if long_word_ratio > 0.18:
        reasons.append(
            "Uzun kelime yoğunluğu yüksek."
            if output_language == "tr"
            else "Long-word density is elevated."
        )
    return score, level, reasons


def _summary_from_text(title: Any, text: str, *, output_language: str = "en") -> str:
    clean_title = _clean_text(title, max_chars=180)
    sentences = _sentences(text)
    body = " ".join(sentences[:2]).strip()
    if output_language == "tr":
        if clean_title and body:
            return _truncate(f"{clean_title}: Bu ders {body}", 700)
        if body:
            return _truncate(f"Bu ders {body}", 700)
        if clean_title:
            return f"{clean_title}: Güvenilir bir özet için ders metninde daha fazla ayrıntı gerekiyor."
        return "Güvenilir bir özet için ders metninde daha fazla ayrıntı gerekiyor."
    if clean_title and body:
        return _truncate(f"{clean_title}: This lesson covers {body}", 700)
    if body:
        return _truncate(f"This lesson covers {body}", 700)
    if clean_title:
        return f"{clean_title}: lesson transcript needs more detail before a reliable summary can be generated."
    return "Lesson transcript needs more detail before a reliable summary can be generated."


def _short_description(summary: str) -> str:
    first = _sentences(summary)
    return _truncate(first[0] if first else summary, 180)


def _suggested_tags(text: str, technical_hits: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for term in technical_hits:
        key = term.lower()
        if key not in seen:
            tags.append(term)
            seen.add(key)
    frequencies: dict[str, int] = {}
    for word in _words(text):
        key = word.lower()
        if len(key) < 5 or key in STOPWORDS:
            continue
        frequencies[key] = frequencies.get(key, 0) + 1
    for word, _count in sorted(frequencies.items(), key=lambda item: (-item[1], item[0])):
        if word not in seen:
            tags.append(word)
            seen.add(word)
        if len(tags) >= 8:
            break
    return tags[:8]


def _technical_hits(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    hits = []
    for term in sorted(TECHNICAL_TERMS):
        pattern = r"(?<![a-z0-9_+-])" + re.escape(term.lower()) + r"(?![a-z0-9_+-])"
        if re.search(pattern, lowered):
            hits.append(term)
    return hits


def _page_suggestion(page_number: int, page_key: str, suggestion: str, suggestion_type: str) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "page_key": page_key,
        "type": suggestion_type,
        "suggestion": suggestion,
    }


def _expanded_suggestion(
    page_number: int,
    page_key: str,
    advice: str,
    draft_narration: str,
    suggestion_type: str,
    *,
    generated_by: str = "heuristic",
) -> dict[str, Any]:
    clean_draft = _clean_text(draft_narration, max_chars=900)
    return {
        "page_number": page_number,
        "page_key": page_key,
        "type": suggestion_type,
        "title": _expanded_suggestion_title(suggestion_type),
        "advice": advice,
        "suggestion": advice,
        "draft_narration": clean_draft,
        "copy_text": clean_draft,
        "generated_by": generated_by,
        "ai_generated": True,
    }


def _expanded_suggestion_title(suggestion_type: str) -> str:
    normalized = str(suggestion_type or "").strip().lower()
    if normalized == "empty_page":
        return "Add narration"
    return "Expand narration"


def _draft_narration_for_page(
    *,
    output_language: str,
    suggestion_type: str,
    display_text: str,
    narration_text: str,
) -> str:
    source = _clean_text(display_text or narration_text, max_chars=650)
    bullet_items = _bullet_items(display_text or narration_text)
    if output_language == "tr":
        if not source:
            return (
                "Bu bölümde slaydın ana fikrini kısa ve net biçimde tanıtıyoruz. "
                "Öğrenciye önce konunun neden önemli olduğunu söylüyor, ardından basit bir örnekle bağlantı kuruyoruz. "
                "Son olarak bir sonraki adıma geçmeden önce öğrenilmesi gereken noktayı özetliyoruz."
            )
        if bullet_items:
            items = ", ".join(bullet_items[:3])
            return _truncate(
                "Bu bölümde listedeki ana noktaları birlikte anlamlandırıyoruz: "
                f"{items}. Her madde, dersin ana fikrine nasıl katkı verdiğini gösteren kısa bir açıklamayla ele alınır. "
                "Böylece öğrenci yalnızca listeyi okumaz, maddeler arasındaki ilişkiyi de duyar.",
                900,
            )
        return _truncate(
            f"Bu bölümde ana fikri şöyle açıklıyoruz: {source}. "
            "Bunu somut bir örnekle düşünürsek, öğrenci kavramın nerede kullanılacağını daha kolay görür. "
            "Bir sonraki adımda bu fikri dersin devamındaki uygulamayla ilişkilendiriyoruz.",
            900,
        )
    if not source:
        return (
            "In this part, we introduce the slide's main idea in clear learner-friendly language. "
            "First, we explain why the point matters, then we connect it to a simple example. "
            "Before moving on, we summarize the takeaway learners should remember."
        )
    if bullet_items:
        items = ", ".join(bullet_items[:3])
        return _truncate(
            "In this part, we connect the bullet points into a clear explanation: "
            f"{items}. Each point should be described in terms of why it matters and how it supports the main idea. "
            "That gives learners the reasoning behind the list instead of only reading the items.",
            900,
        )
    return _truncate(
        f"In this part, we explain the key idea: {source}. "
        "A simple example can make the concept easier to place in a real learning context. "
        "Then we transition to the next step by showing how this idea will be used in the rest of the lesson.",
        900,
    )


def _bullet_items(text: str) -> list[str]:
    items: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not re.match(r"^[-*•]\s+", stripped):
            continue
        item = re.sub(r"^[-*•]\s+", "", stripped).strip()
        if item:
            items.append(_truncate(item, 120))
    return items


def _compose_input_text(title: str, description: str, pages: list[dict[str, Any]]) -> str:
    chunks = []
    if title:
        chunks.append(f"Title: {title}")
    if description:
        chunks.append(f"Description: {description}")
    for page in pages:
        page_number = page.get("page_number") or ""
        display_text = _clean_text(page.get("display_text"), max_chars=-1)
        narration_text = _clean_text(page.get("narration_text"), max_chars=-1)
        page_chunks = []
        if display_text:
            page_chunks.append(f"Display: {display_text}")
        if narration_text and narration_text != display_text:
            page_chunks.append(f"Narration: {narration_text}")
        if page_chunks:
            chunks.append(f"Page {page_number}\n" + "\n".join(page_chunks))
    return "\n\n".join(chunks).strip()


def _lesson_compaction_for_limit(
    title: str,
    description: str,
    pages: list[LessonPageInput],
    limit: int,
) -> tuple[int, int, bool]:
    source_pages = [page.to_payload(index) for index, page in enumerate(pages)]
    source_chars = len(_compose_input_text(title, description, source_pages))
    if source_chars <= max(1, limit):
        return -1, source_chars, False

    for page_limit in (1800, 1200, 800, 500, 280, 160, 80, 0):
        compact_pages = [page.to_payload(index, max_text_chars=page_limit) for index, page in enumerate(pages)]
        input_chars = len(_compose_input_text(title, _clean_text(description, max_chars=1200), compact_pages))
        if input_chars <= max(1, limit) or page_limit == 0:
            return page_limit, input_chars, True
    return 0, source_chars, True


def _has_example_signal(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\b(example|for instance|case study|scenario|e\.g\.|ornek|örnek|mesela)\b", lowered))


def _has_intro_signal(pages: list[dict[str, Any]], input_payload: dict[str, Any]) -> bool:
    text = " ".join([str(input_payload.get("project", {}).get("description") or ""), *[_page_text(page) for page in pages]])
    lowered = text.lower()
    return bool(re.search(r"\b(introduction|overview|objective|goal|today we|we will|learn|giris|giriş|hedef|amac|amaç)\b", lowered))


def _has_conclusion_signal(pages: list[dict[str, Any]]) -> bool:
    lowered = " ".join(_page_text(page) for page in pages).lower()
    return bool(re.search(r"\b(conclusion|summary|recap|next step|wrap up|finally|sonuc|sonuç|ozet|özet)\b", lowered))


def _bullet_line_count(text: str) -> int:
    raw = str(text or "")
    line_count = len([line for line in raw.splitlines() if re.match(r"^\s*(?:[-*]|[0-9]+[.)])\s+", line)])
    inline_count = len(re.findall(r"(?:^|\s)(?:[-*]|[0-9]+[.)])\s+[A-Za-z0-9]", raw))
    return max(line_count, inline_count)


def _page_text(page: dict[str, Any]) -> str:
    return _clean_text(page.get("analysis_text") or page.get("narration_text") or page.get("display_text"), max_chars=-1)


def _sentences(text: str) -> list[str]:
    clean = _clean_text(text, max_chars=-1)
    if not clean:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", clean) if part.strip()]
    if len(parts) <= 1:
        parts = [part.strip() for part in re.split(r";\s+|\s+-\s+", clean) if part.strip()]
    return parts or [clean]


def _words(text: str) -> list[str]:
    return WORD_RE.findall(_clean_text(text, max_chars=-1))


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = CONTROL_RE.sub("", str(value or ""))
    text = WHITESPACE_RE.sub(" ", text).strip()
    if max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _safe_json_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    safe = []
    for item in value[:50]:
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe.append(item)
        elif isinstance(item, dict):
            safe.append(_safe_json_dict(item))
    return safe


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = _clean_text(key, max_chars=80)
        if not clean_key:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[clean_key] = item
        elif isinstance(item, list):
            safe[clean_key] = _safe_json_list(item)
        elif isinstance(item, dict):
            safe[clean_key] = _safe_json_dict(item)
    return safe


def _provider_attempt(provider: str, status_value: str, error: Exception | str = "") -> dict[str, str]:
    payload = {"provider": provider, "status": status_value}
    if error:
        payload["error"] = _truncate(re.sub(r"\s+", " ", str(error).strip()), 240)
    return payload


def _truncate(text: str, limit: int) -> str:
    clean = _clean_text(text, max_chars=-1)
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "..."


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_setting(name: str, default: bool) -> bool:
    return bool(getattr(settings, name, default))


def _string_setting(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or default).strip()


def _int_setting(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return int(default)


def _float_setting(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(getattr(settings, name, default))
    except (TypeError, ValueError):
        parsed = float(default)
    return min(maximum, max(minimum, parsed))


def _effective_sync_provider_timeout(configured_timeout: float, *, cap_setting: str) -> float:
    cap = _float_setting(
        cap_setting,
        _float_setting("INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS", 20.0, minimum=0.5, maximum=60.0),
        minimum=0.5,
        maximum=60.0,
    )
    return min(float(configured_timeout), cap)


def _background_provider_timeout(*, cap_setting: str) -> float:
    global_timeout = _float_setting("INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS", 120.0, minimum=1.0, maximum=600.0)
    return _float_setting(cap_setting, global_timeout, minimum=1.0, maximum=600.0)
