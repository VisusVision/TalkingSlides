from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import re
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from core.capabilities import intelligence_enabled, local_ollama_enabled
from core.intelligence_language import detect_lesson_language, resolve_output_language
from core.intelligence_progressive import (
    build_intelligence_run_identity,
    bounded_adaptive_background_timeout,
    enhancement_response_fields,
    first_provider_name,
    intelligence_hardware_profile,
    intelligence_runtime_profile_metadata,
    ollama_chunk_max_chars,
    ollama_chunk_concurrency,
    ollama_chunk_timeout_seconds,
    ollama_finalization_timeout_budget_details,
    ollama_no_progress_timeout_seconds,
    ollama_total_timeout_budget_seconds,
    ollama_workload_timeout_budget_details,
    provider_attempt as progressive_provider_attempt,
    provider_chain_contains_ollama,
    safe_response_preview,
)
from core.models import AnalyticsIntelligenceReport, LessonIntelligenceReport


logger = logging.getLogger(__name__)

ANALYTICS_INTELLIGENCE_PROMPT_VERSION = "analytics-intelligence-v2"
RISK_LEVELS = {"low", "medium", "high"}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
WHITESPACE_RE = re.compile(r"\s+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_.-]{2,}")
IDENTITY_LABEL_RE = re.compile(
    r"\b(?:viewer|learner|user)[\s_-]*(?:id|email|username|name)\b\s*[:=#-]?\s*[^\s,;]*",
    re.IGNORECASE,
)
PRIVATE_KEYS = {
    "avatar_url",
    "email",
    "full_name",
    "file_path",
    "path",
    "profile_url",
    "raw_path",
    "storage_path",
    "user_id",
    "username",
    "viewer_id",
    "viewer_username",
}


class AnalyticsIntelligenceProviderUnavailable(RuntimeError):
    """Provider cannot run with the current local configuration/runtime."""


class AnalyticsIntelligenceInputError(ValueError):
    """Input cannot be analyzed as an analytics intelligence request."""


class AnalyticsIntelligenceInputTooLarge(AnalyticsIntelligenceInputError):
    """Input exceeded the configured synchronous analysis limit."""


@dataclass(frozen=True)
class AnalyticsIntelligenceInput:
    requested_by_id: int
    scope: str
    analytics_payload: dict[str, Any]
    source_hash: str
    input_chars: int
    date_range: dict[str, Any]
    category_filter: str
    detected_language: str = "unknown"
    output_language: str = "en"
    language_confidence: float = 0.0
    input_truncated: bool = False
    compaction: dict[str, Any] | None = None

    def to_provider_payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "analytics": self.analytics_payload,
            "source_hash": self.source_hash,
            "input_chars": self.input_chars,
            "date_range": self.date_range,
            "category_filter": self.category_filter,
            "detected_language": self.detected_language,
            "output_language": self.output_language,
            "language_confidence": self.language_confidence,
            "input_truncated": self.input_truncated,
            "compaction": self.compaction or {},
        }


class AnalyticsIntelligenceProvider(Protocol):
    provider_name: str

    def analyze_analytics(self, input_payload: dict[str, Any], *, timeout_seconds_override: float | None = None) -> dict[str, Any]:
        ...


class HeuristicAnalyticsIntelligenceProvider:
    provider_name = "heuristic"

    def analyze_analytics(self, input_payload: dict[str, Any], *, timeout_seconds_override: float | None = None) -> dict[str, Any]:
        output_language = _output_language(input_payload)
        analytics = input_payload.get("analytics") if isinstance(input_payload.get("analytics"), dict) else {}
        summary = analytics.get("summary") if isinstance(analytics.get("summary"), dict) else {}
        tables = analytics.get("tables") if isinstance(analytics.get("tables"), dict) else {}
        charts = analytics.get("charts") if isinstance(analytics.get("charts"), dict) else {}
        meta = analytics.get("meta") if isinstance(analytics.get("meta"), dict) else {}
        feedback = analytics.get("qualitative_feedback") if isinstance(analytics.get("qualitative_feedback"), dict) else {}
        recent_comments = [item for item in _safe_list(feedback.get("recent_comments")) if isinstance(item, dict)]

        total_lessons = _safe_int(summary.get("total_lessons"), 0)
        published_lessons = _safe_int(summary.get("published_lessons"), 0)
        draft_lessons = _safe_int(summary.get("draft_lessons"), 0)
        total_views = _safe_int(summary.get("total_views") or summary.get("video_plays"), 0)
        unique_viewers = _safe_int(summary.get("unique_viewers"), 0)
        watch_minutes = _safe_float(summary.get("estimated_watch_time_minutes"), 0.0)
        completion_rate = _bounded_percent(summary.get("completion_rate"))
        average_progress = _bounded_percent(summary.get("average_progress"))
        engagement_events = _safe_int(summary.get("engagement_events"), 0)
        likes = _safe_int(summary.get("likes"), 0)
        comments = _safe_int(summary.get("comments"), 0)
        social_events = likes + comments
        has_activity = total_views > 0 or engagement_events > 0 or social_events > 0

        top_lessons = _safe_list(tables.get("top_lessons"))
        recent_lessons = _safe_list(tables.get("recent_lessons"))
        categories = _safe_list(tables.get("top_categories") or charts.get("category_popularity"))
        top_lessons = sorted(
            [item for item in top_lessons if isinstance(item, dict)],
            key=lambda item: (_safe_int(item.get("engagement_events"), 0), _safe_int(item.get("views"), 0)),
            reverse=True,
        )
        categories = sorted(
            [item for item in categories if isinstance(item, dict)],
            key=lambda item: _category_signal(item),
            reverse=True,
        )

        insights: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []
        lesson_actions: list[dict[str, Any]] = []
        category_actions: list[dict[str, Any]] = []
        limitations = _base_limitations(meta, output_language=output_language)
        if input_payload.get("input_truncated"):
            limitations.append(_analytics_text(output_language, "large_dataset_limitation"))
        if bool(feedback.get("truncated") or meta.get("comment_feedback_truncated")):
            limitations.append(_analytics_text(output_language, "comment_feedback_truncated"))

        if total_lessons <= 0:
            insights.append(
                _insight(
                    "no_lessons",
                    "medium",
                    _analytics_text(output_language, "no_lessons_insight"),
                    "total_lessons=0",
                )
            )
            recommendations.extend(
                [
                    _recommendation(
                        "publish_first_lesson",
                        "high",
                        _analytics_text(output_language, "publish_first_lesson"),
                    ),
                    _recommendation(
                        "define_category",
                        "medium",
                        _analytics_text(output_language, "define_category"),
                    ),
                ]
            )
        elif not has_activity:
            insights.append(
                _insight(
                    "no_activity",
                    "medium",
                    _analytics_text(output_language, "no_activity_insight"),
                    f"published_lessons={published_lessons}, views=0",
                )
            )
            recommendations.extend(
                [
                    _recommendation(
                        "share_lessons",
                        "high",
                        _analytics_text(output_language, "share_lessons"),
                    ),
                    _recommendation(
                        "improve_discovery",
                        "medium",
                        _analytics_text(output_language, "improve_discovery"),
                    ),
                ]
            )
            if draft_lessons > 0:
                recommendations.append(
                    _recommendation(
                        "publish_drafts",
                        "medium",
                        _analytics_text(output_language, "publish_drafts"),
                    )
                )
        else:
            insights.append(
                _insight(
                    "activity_summary",
                    "low",
                    _analytics_text(
                        output_language,
                        "activity_summary_insight",
                        total_views=total_views,
                        unique_viewers=unique_viewers,
                        engagement_events=engagement_events,
                    ),
                    _analytics_progress_sentence(
                        output_language,
                        completion_rate=completion_rate,
                        average_progress=average_progress,
                    ),
                )
            )

            if completion_rate > 0 and completion_rate < 35:
                insights.append(
                    _insight(
                        "low_completion",
                        "high",
                        _analytics_text(output_language, "low_completion_insight"),
                        _analytics_completion_sentence(output_language, completion_rate),
                    )
                )
                recommendations.append(
                    _recommendation(
                        "shorten_or_segment",
                        "high",
                        _analytics_text(output_language, "shorten_or_segment"),
                    )
                )

            if average_progress > 0 and average_progress < 45:
                insights.append(
                    _insight(
                        "views_low_progress",
                        "high",
                        _analytics_text(output_language, "views_low_progress_insight"),
                        _analytics_progress_only_sentence(output_language, average_progress),
                    )
                )
                recommendations.append(
                    _recommendation(
                        "clearer_intro",
                        "high",
                        _analytics_text(output_language, "clearer_intro"),
                    )
                )

            if total_views >= 3 and social_events == 0:
                insights.append(
                    _insight(
                        "low_engagement",
                        "medium",
                        _analytics_text(output_language, "low_engagement_insight"),
                        f"views={total_views}, likes=0, comments=0",
                    )
                )
                recommendations.append(
                    _recommendation(
                        "prompt_engagement",
                        "medium",
                        _analytics_text(output_language, "prompt_engagement"),
                    )
                )

            if social_events >= max(2, int(total_views * 0.3)) and total_views > 0:
                insights.append(
                    _insight(
                        "strong_social_engagement",
                        "low",
                        _analytics_text(output_language, "strong_social_engagement"),
                        f"likes={likes}, comments={comments}, views={total_views}",
                    )
                )
                if average_progress < 55:
                    recommendations.append(
                        _recommendation(
                            "match_interest_to_retention",
                            "medium",
                            _analytics_text(output_language, "match_interest_to_retention"),
                        )
                    )

            dominant_category = _dominant_category(categories)
            if dominant_category:
                category_name = _clean_text(
                    dominant_category.get("category_name") or dominant_category.get("name") or "one category",
                    max_chars=80,
                )
                share = dominant_category.get("_share", 0.0)
                insights.append(
                    _insight(
                        "category_dominance",
                        "medium",
                        _analytics_text(output_language, "category_dominance", category=category_name),
                        f"category_share={share:.0%}",
                    )
                )
                category_actions.append(
                    {
                        "type": "category_imbalance",
                        "category": category_name,
                        "message": _analytics_text(output_language, "category_imbalance"),
                        "evidence": _analytics_text(output_language, "category_share", category=category_name, share=share),
                    }
                )

            if not recommendations:
                recommendations.append(
                    _recommendation(
                        "review_top_lesson_style",
                        "medium",
                        _analytics_text(output_language, "review_top_lesson_style"),
                    )
                )

        if recent_comments:
            sample = _clean_text(recent_comments[0].get("text"), max_chars=120)
            insights.append(
                _insight(
                    "recent_comment_feedback",
                    "low",
                    _analytics_text(output_language, "recent_comment_feedback", count=len(recent_comments)),
                    sample,
                )
            )

        recommendations = _dedupe_by_message(recommendations)
        lesson_actions.extend(_lesson_actions(top_lessons, recent_lessons, output_language=output_language))
        category_actions.extend(_category_actions(categories, has_activity=has_activity, output_language=output_language))

        health_score = _health_score(
            total_lessons=total_lessons,
            published_lessons=published_lessons,
            total_views=total_views,
            completion_rate=completion_rate,
            average_progress=average_progress,
            social_events=social_events,
            has_activity=has_activity,
            category_count=len(categories),
        )
        risk_level = _risk_level(health_score)

        analytics_summary = _analytics_summary(
            total_lessons=total_lessons,
            published_lessons=published_lessons,
            total_views=total_views,
            unique_viewers=unique_viewers,
            watch_minutes=watch_minutes,
            completion_rate=completion_rate,
            average_progress=average_progress,
            likes=likes,
            comments=comments,
            has_activity=has_activity,
            output_language=output_language,
        )

        return {
            "provider": self.provider_name,
            "analytics_summary": analytics_summary,
            "health_score": health_score,
            "risk_level": risk_level,
            "insights": insights[:12],
            "recommendations": recommendations[:12],
            "lesson_actions": lesson_actions[:12],
            "category_actions": _dedupe_by_message(category_actions)[:12],
            "limitations": limitations,
            "metadata": {
                "input_char_count": int(input_payload.get("input_chars") or 0),
                "input_truncated": bool(input_payload.get("input_truncated")),
                "compaction": input_payload.get("compaction") if isinstance(input_payload.get("compaction"), dict) else {},
                "detected_language": str(input_payload.get("detected_language") or "unknown"),
                "output_language": output_language,
                "language_confidence": float(input_payload.get("language_confidence") or 0.0),
                "total_lessons": total_lessons,
                "published_lessons": published_lessons,
                "total_views": total_views,
                "estimated_watch_time_minutes": watch_minutes,
                "category_count": len(categories),
            },
        }


class OllamaAnalyticsIntelligenceProvider:
    provider_name = "ollama"

    def __init__(self, *, background: bool = False) -> None:
        self.background = bool(background)
        self.base_url = _string_setting(
            "OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL",
            _string_setting("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        ).rstrip("/")
        self.model = _string_setting("OLLAMA_ANALYTICS_INTELLIGENCE_MODEL", "qwen2.5:7b-instruct")
        self.timeout_seconds = _float_setting(
            "ANALYTICS_INTELLIGENCE_TIMEOUT_SECONDS",
            30.0,
            minimum=0.5,
            maximum=180.0,
        )
        if background:
            self.timeout_seconds = _background_provider_timeout(
                cap_setting="ANALYTICS_INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS",
            )
        else:
            self.timeout_seconds = _effective_sync_provider_timeout(
                self.timeout_seconds,
                cap_setting="ANALYTICS_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS",
            )
        self.last_timeout_seconds = self.timeout_seconds

    def analyze_analytics(self, input_payload: dict[str, Any], *, timeout_seconds_override: float | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise AnalyticsIntelligenceProviderUnavailable("Ollama base URL is not configured")
        if not self.model:
            raise AnalyticsIntelligenceProviderUnavailable("Ollama analytics intelligence model is not configured")
        timeout_seconds = (
            float(timeout_seconds_override)
            if timeout_seconds_override is not None
            else (
                adaptive_analytics_intelligence_timeout(input_payload, base_seconds=self.timeout_seconds)
                if self.background
                else self.timeout_seconds
            )
        )
        self.last_timeout_seconds = timeout_seconds

        prompt = _ollama_prompt(input_payload)
        data, elapsed_seconds = self._generate(prompt, timeout_seconds, retry_count=0)
        response_text = data.get("response") if isinstance(data, dict) else None
        try:
            provider_json = data if response_text is None else _json_object_from_text(str(response_text or ""))
            normalized = _normalize_provider_result(provider_json, provider_name=self.provider_name)
            repaired = False
            repair_retry_count = 0
        except AnalyticsIntelligenceProviderUnavailable as exc:
            parse_stage = _ollama_parse_stage(exc)
            try:
                repair_timeout = min(30.0, max(10.0, timeout_seconds * 0.35))
                repair_prompt = _ollama_repair_prompt(str(response_text or data or ""), output_language=_output_language(input_payload))
                repair_data, repair_elapsed = self._generate(repair_prompt, repair_timeout, retry_count=1)
                repair_text = repair_data.get("response") if isinstance(repair_data, dict) else None
                repair_json = repair_data if repair_text is None else _json_object_from_text(str(repair_text or ""))
                normalized = _normalize_provider_result(repair_json, provider_name=self.provider_name)
                repaired = True
                repair_retry_count = 1
                elapsed_seconds = round(elapsed_seconds + repair_elapsed, 2)
            except Exception as repair_exc:  # noqa: BLE001
                failure = AnalyticsIntelligenceProviderUnavailable(str(exc))
                failure.diagnostic = _ollama_failure_diagnostic(
                    exc,
                    stage=parse_stage,
                    model=self.model,
                    elapsed_seconds=elapsed_seconds,
                    retry_count=1,
                    response_preview=safe_response_preview(response_text),
                    repair_error=repair_exc,
                )
                raise failure from exc

        normalized["metadata"] = {
            **dict(normalized.get("metadata") or {}),
            "model": self.model,
            "base_url_configured": bool(self.base_url),
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": elapsed_seconds,
            "repaired": repaired,
            "repair_retry_count": repair_retry_count,
        }
        return normalized

    def _generate(self, prompt: str, timeout_seconds: float, *, retry_count: int) -> tuple[dict[str, Any], float]:
        request_payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": _int_setting("OLLAMA_ANALYTICS_INTELLIGENCE_NUM_PREDICT", 700)},
        }
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        started_at = time.monotonic()
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
            data = json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            elapsed_seconds = round(time.monotonic() - started_at, 2)
            failure = AnalyticsIntelligenceProviderUnavailable(f"Ollama request failed: {exc.__class__.__name__}")
            failure.diagnostic = _ollama_failure_diagnostic(
                exc,
                stage=_ollama_stage_for_exception(exc),
                model=self.model,
                elapsed_seconds=elapsed_seconds,
                retry_count=retry_count,
            )
            raise failure from exc

        if not isinstance(data, dict):
            elapsed_seconds = round(time.monotonic() - started_at, 2)
            failure = AnalyticsIntelligenceProviderUnavailable("Ollama response must be a JSON object")
            failure.diagnostic = _ollama_failure_diagnostic(
                failure,
                stage="json",
                model=self.model,
                elapsed_seconds=elapsed_seconds,
                retry_count=retry_count,
                response_preview=safe_response_preview(data),
            )
            raise failure
        return data, round(time.monotonic() - started_at, 2)


class PaidAnalyticsIntelligenceProvider:
    """Placeholder for later paid-provider support. It never calls externally."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = str(provider_name or "external").strip().lower() or "external"

    def analyze_analytics(self, input_payload: dict[str, Any], *, timeout_seconds_override: float | None = None) -> dict[str, Any]:
        if not _bool_setting("ANALYTICS_INTELLIGENCE_ALLOW_EXTERNAL", False):
            raise AnalyticsIntelligenceProviderUnavailable("external analytics intelligence providers are disabled")
        raise AnalyticsIntelligenceProviderUnavailable(
            f"{self.provider_name} analytics intelligence provider is not implemented"
        )


def analytics_intelligence_enabled() -> bool:
    return bool(intelligence_enabled() and _bool_setting("ANALYTICS_INTELLIGENCE_ENABLED", True))


def analytics_provider_chain_from_settings() -> list[str]:
    raw = _string_setting("ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN", "")
    if not raw:
        raw = _string_setting("ANALYTICS_INTELLIGENCE_PROVIDER", "heuristic")
    providers = [item.strip().lower() for item in re.split(r"[\s,]+", raw) if item.strip()]
    if not providers:
        providers = ["heuristic"]
    return providers


def _provider_chain_contains(provider_chain: list[str] | tuple[str, ...] | None, provider: str) -> bool:
    expected = str(provider or "").strip().lower()
    return any(str(item or "").strip().lower() == expected for item in (provider_chain or []))


def get_analytics_intelligence_provider(provider_name: str) -> AnalyticsIntelligenceProvider:
    provider = str(provider_name or "heuristic").strip().lower()
    if provider == "heuristic":
        return HeuristicAnalyticsIntelligenceProvider()
    if provider == "ollama":
        return OllamaAnalyticsIntelligenceProvider()
    if provider in {"openai", "anthropic", "azure_openai", "external", "paid"}:
        return PaidAnalyticsIntelligenceProvider(provider)
    raise AnalyticsIntelligenceProviderUnavailable(f"unknown analytics intelligence provider: {provider}")


def _lesson_quality_report_hash(report: LessonIntelligenceReport) -> str:
    payload = {
        "report_id": int(report.id),
        "source_hash": str(report.source_hash or ""),
        "updated_at": report.updated_at.isoformat() if report.updated_at else "",
        "provider": str(report.provider or ""),
        "summary": str(report.summary or "")[:500],
        "complexity_level": str(report.complexity_level or ""),
        "complexity_score": int(report.complexity_score or 0),
        "warnings": _safe_json_list(report.clarity_warnings)[:3],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


def _lesson_quality_signal_blob(report: LessonIntelligenceReport) -> str:
    parts = [
        report.summary,
        report.short_description,
        report.complexity_level,
        report.complexity_reasons,
        report.clarity_warnings,
        report.page_suggestions,
        report.expanded_narration_suggestions,
    ]
    return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).lower()


def _lesson_quality_from_report(report: LessonIntelligenceReport) -> dict[str, Any]:
    warnings = []
    for item in _safe_json_list(report.clarity_warnings)[:3]:
        if not isinstance(item, dict):
            warnings.append({"message": _clean_text(item, max_chars=180)})
            continue
        warnings.append(
            {
                "type": _clean_text(item.get("type"), max_chars=80),
                "severity": _clean_text(item.get("severity"), max_chars=40),
                "message": _clean_text(item.get("message") or item.get("advice") or item.get("suggestion"), max_chars=220),
            }
        )
    blob = _lesson_quality_signal_blob(report)
    short_narration = ("narration" in blob or "anlat" in blob) and ("short" in blob or "brief" in blob or "kisa" in blob)
    missing_examples = "example" in blob or "ornek" in blob or "examples" in blob
    return _scrub_private(
        {
            "report_id": int(report.id),
            "report_hash": _lesson_quality_report_hash(report),
            "report_updated_at": report.updated_at.isoformat() if report.updated_at else "",
            "provider": _clean_text(report.provider, max_chars=40),
            "summary": _clean_text(report.summary, max_chars=500),
            "complexity_level": _clean_text(report.complexity_level, max_chars=40),
            "complexity_score": int(report.complexity_score or 0),
            "clarity_warnings": warnings,
            "short_narration_signal": bool(short_narration),
            "missing_examples_signal": bool(missing_examples),
        }
    )


def _latest_lesson_quality_reports(lesson_ids: list[int], *, requested_by_id: int) -> dict[int, dict[str, Any]]:
    if not lesson_ids or not requested_by_id:
        return {}
    lookup: dict[int, dict[str, Any]] = {}
    reports = (
        LessonIntelligenceReport.objects.filter(
            project_id__in=sorted(set(int(item) for item in lesson_ids if item)),
            project__user_id=int(requested_by_id),
        )
        .only(
            "id",
            "project_id",
            "provider",
            "source_hash",
            "summary",
            "short_description",
            "complexity_level",
            "complexity_score",
            "complexity_reasons",
            "clarity_warnings",
            "page_suggestions",
            "expanded_narration_suggestions",
            "updated_at",
        )
        .order_by("project_id", "-updated_at", "-id")
    )
    for report in reports:
        if report.project_id not in lookup:
            lookup[report.project_id] = _lesson_quality_from_report(report)
    return lookup


def _analytics_payload_with_lesson_intelligence(payload: dict[str, Any], *, requested_by_id: int) -> dict[str, Any]:
    safe_payload = dict(payload or {})
    lesson_quality = _safe_json_dict(safe_payload.get("lesson_quality"))
    weak_lessons = [dict(row) for row in _safe_list(lesson_quality.get("weak_lessons")) if isinstance(row, dict)]
    strong_lessons = [dict(row) for row in _safe_list(lesson_quality.get("strong_lessons")) if isinstance(row, dict)]
    if not weak_lessons and not strong_lessons:
        tables = _safe_json_dict(safe_payload.get("tables"))
        weak_lessons = [dict(row) for row in _safe_list(tables.get("top_lessons"))[:10] if isinstance(row, dict)]
        strong_lessons = [dict(row) for row in _safe_list(tables.get("recent_lessons"))[:5] if isinstance(row, dict)]
    lesson_ids = []
    for row in weak_lessons + strong_lessons:
        lesson_id = _safe_int(row.get("lesson_id") or row.get("id"), 0)
        if lesson_id:
            lesson_ids.append(lesson_id)
    report_lookup = _latest_lesson_quality_reports(lesson_ids, requested_by_id=requested_by_id)

    def enrich(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        enriched = []
        for row in rows[:limit]:
            lesson_id = _safe_int(row.get("lesson_id") or row.get("id"), 0)
            next_row = _compact_lesson_row(row, title_chars=140)
            if lesson_id in report_lookup:
                next_row["lesson_intelligence"] = report_lookup[lesson_id]
            enriched.append(next_row)
        return enriched

    enriched_weak = enrich(weak_lessons, limit=10)
    enriched_strong = enrich(strong_lessons, limit=5)
    report_hashes = [
        row.get("lesson_intelligence", {}).get("report_hash")
        for row in enriched_weak + enriched_strong
        if isinstance(row.get("lesson_intelligence"), dict)
    ]
    quality_payload = {
        **lesson_quality,
        "weak_lessons": enriched_weak,
        "strong_lessons": enriched_strong,
        "lesson_intelligence_report_count": len([item for item in report_hashes if item]),
        "lesson_intelligence_hash": hashlib.sha256(
            json.dumps(sorted(item for item in report_hashes if item), ensure_ascii=False).encode(
                "utf-8",
                errors="ignore",
            )
        ).hexdigest()
        if report_hashes
        else "",
    }
    safe_payload["lesson_quality"] = _scrub_private(quality_payload)
    return safe_payload


def build_analytics_intelligence_input(
    requested_by,
    analytics_payload: dict[str, Any],
    *,
    scope: str = "creator",
    max_chars: int | None = None,
    output_language: str = "auto",
    request_language: str = "",
) -> AnalyticsIntelligenceInput:
    if not isinstance(analytics_payload, dict):
        raise AnalyticsIntelligenceInputError("Analytics payload is empty.")

    limit = int(max_chars if max_chars is not None else _int_setting("ANALYTICS_INTELLIGENCE_MAX_INPUT_CHARS", 20000))
    safe_payload, compaction = _compact_analytics_payload(analytics_payload, max_chars=limit)
    safe_payload = _analytics_payload_with_lesson_intelligence(
        safe_payload,
        requested_by_id=int(getattr(requested_by, "id", 0) or 0),
    )
    detection_text = json.dumps(
        {
            "summary": safe_payload.get("summary"),
            "tables": safe_payload.get("tables"),
            "filters": safe_payload.get("filters"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    language = detect_lesson_language(detection_text)
    detected_language = str(language.get("language") or "unknown")
    resolved_output_language = resolve_output_language(
        requested=output_language,
        detected=detected_language,
        request_language=request_language,
    )
    source_payload = {
        **safe_payload,
        "output_language": resolved_output_language,
    }
    source_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    input_chars = len(source_json)
    lesson_quality = _safe_json_dict(safe_payload.get("lesson_quality"))
    compaction = {
        **(compaction or {}),
        "compact_char_count": input_chars,
        "lesson_quality_report_count": _safe_int(lesson_quality.get("lesson_intelligence_report_count"), 0),
    }
    filters = safe_payload.get("filters") if isinstance(safe_payload.get("filters"), dict) else {}
    date_range = {
        "from": _clean_text(filters.get("from"), max_chars=20),
        "to": _clean_text(filters.get("to"), max_chars=20),
        "range": _safe_int(filters.get("range"), 30),
    }
    category_filter = _clean_text(filters.get("category"), max_chars=120)
    return AnalyticsIntelligenceInput(
        requested_by_id=int(getattr(requested_by, "id", 0) or 0),
        scope=_clean_text(scope, max_chars=20) or "creator",
        analytics_payload=safe_payload,
        source_hash=hashlib.sha256(source_json.encode("utf-8", errors="ignore")).hexdigest(),
        input_chars=input_chars,
        date_range=date_range,
        category_filter=category_filter,
        detected_language=detected_language,
        output_language=resolved_output_language,
        language_confidence=float(language.get("confidence") or 0.0),
        input_truncated=bool(compaction.get("input_truncated")),
        compaction=compaction,
    )


def analyze_analytics_with_provider_chain(
    analytics_input: AnalyticsIntelligenceInput,
    *,
    chain: list[str] | None = None,
) -> dict[str, Any]:
    provider_chain = chain or analytics_provider_chain_from_settings()
    input_payload = analytics_input.to_provider_payload()
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
            provider = get_analytics_intelligence_provider(name)
            result = provider.analyze_analytics(input_payload)
            normalized = _normalize_provider_result(result, provider_name=getattr(provider, "provider_name", name))
        except AnalyticsIntelligenceProviderUnavailable as exc:
            attempts.append(_provider_attempt(name, "skipped", exc))
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analytics intelligence provider failed provider=%s error=%s", name, exc.__class__.__name__)
            attempts.append(_provider_attempt(name, "failed", exc))
            continue

        attempts.append(_provider_attempt(normalized["provider"], "success"))
        normalized["provider_chain"] = provider_chain
        normalized["fallback_used"] = bool(first_provider and normalized["provider"] != first_provider)
        normalized["metadata"] = {
            **dict(normalized.get("metadata") or {}),
            "provider_chain_attempts": attempts,
            "source_hash": analytics_input.source_hash,
            "detected_language": analytics_input.detected_language,
            "output_language": analytics_input.output_language,
            "language_confidence": analytics_input.language_confidence,
            "input_truncated": analytics_input.input_truncated,
            "compaction": analytics_input.compaction or {},
            "input_char_count": analytics_input.input_chars,
            "analytics_filters": _safe_json_dict(analytics_input.analytics_payload.get("filters")),
        }
        return normalized

    if _provider_chain_contains(provider_chain, "heuristic"):
        fallback_provider = HeuristicAnalyticsIntelligenceProvider()
        fallback = _normalize_provider_result(fallback_provider.analyze_analytics(input_payload), provider_name="heuristic")
        attempts.append(_provider_attempt("heuristic", "success"))
        fallback["provider_chain"] = provider_chain
        fallback["fallback_used"] = True
        fallback["metadata"] = {
            **dict(fallback.get("metadata") or {}),
            "provider_chain_attempts": attempts,
            "source_hash": analytics_input.source_hash,
            "detected_language": analytics_input.detected_language,
            "output_language": analytics_input.output_language,
            "language_confidence": analytics_input.language_confidence,
            "input_truncated": analytics_input.input_truncated,
            "compaction": analytics_input.compaction or {},
            "input_char_count": analytics_input.input_chars,
            "analytics_filters": _safe_json_dict(analytics_input.analytics_payload.get("filters")),
        }
        return fallback
    raise AnalyticsIntelligenceProviderUnavailable("No analytics intelligence provider completed.")


def progressive_analytics_ollama_enabled(chain: list[str] | None = None) -> bool:
    provider_chain = chain or analytics_provider_chain_from_settings()
    return (
        local_ollama_enabled()
        and _bool_setting("INTELLIGENCE_BACKGROUND_ENHANCEMENT_ENABLED", True)
        and provider_chain_contains_ollama(provider_chain)
        and _provider_chain_contains(provider_chain, "heuristic")
    )


def analytics_ollama_run_identity(analytics_input: AnalyticsIntelligenceInput) -> dict[str, str]:
    return build_intelligence_run_identity(
        kind="analytics",
        owner_id=analytics_input.requested_by_id,
        source_hash=analytics_input.source_hash,
        provider="ollama",
        model=_string_setting("OLLAMA_ANALYTICS_INTELLIGENCE_MODEL", "qwen2.5:7b-instruct"),
        output_language=analytics_input.output_language,
        prompt_version=ANALYTICS_INTELLIGENCE_PROMPT_VERSION,
        filters={
            "scope": analytics_input.scope,
            "date_range": analytics_input.date_range,
            "category_filter": analytics_input.category_filter,
        },
    )


def adaptive_analytics_intelligence_timeout(input_payload: dict[str, Any], *, base_seconds: float | None = None) -> float:
    payload = input_payload if isinstance(input_payload, dict) else {}
    analytics = payload.get("analytics") if isinstance(payload.get("analytics"), dict) else {}
    tables = analytics.get("tables") if isinstance(analytics.get("tables"), dict) else {}
    charts = analytics.get("charts") if isinstance(analytics.get("charts"), dict) else {}
    feedback = analytics.get("qualitative_feedback") if isinstance(analytics.get("qualitative_feedback"), dict) else {}
    recent_comments = feedback.get("recent_comments") if isinstance(feedback.get("recent_comments"), list) else []
    row_count = (
        len(_safe_list(tables.get("top_lessons")))
        + len(_safe_list(tables.get("recent_lessons")))
        + len(_safe_list(tables.get("top_categories") or charts.get("category_popularity")))
    )
    base = (
        float(base_seconds)
        if base_seconds is not None
        else _background_provider_timeout(cap_setting="ANALYTICS_INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS")
    )
    return bounded_adaptive_background_timeout(
        base_seconds=base,
        input_chars=_safe_int(payload.get("input_chars"), 0),
        page_count=row_count,
        comment_count=len(recent_comments),
    )


def analyze_analytics_heuristic_immediate(
    analytics_input: AnalyticsIntelligenceInput,
    *,
    chain: list[str] | None = None,
    enhancement_provider: str = "",
    enhancement_status: str = "",
) -> dict[str, Any]:
    provider_chain = chain or analytics_provider_chain_from_settings()
    input_payload = analytics_input.to_provider_payload()
    fallback_provider = HeuristicAnalyticsIntelligenceProvider()
    normalized = _normalize_provider_result(fallback_provider.analyze_analytics(input_payload), provider_name="heuristic")
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
        "source_hash": analytics_input.source_hash,
        "detected_language": analytics_input.detected_language,
        "output_language": analytics_input.output_language,
        "language_confidence": analytics_input.language_confidence,
        "input_truncated": analytics_input.input_truncated,
        "compaction": analytics_input.compaction or {},
        "input_char_count": analytics_input.input_chars,
        "analytics_filters": _safe_json_dict(analytics_input.analytics_payload.get("filters")),
    }
    return normalized


def _empty_analytics_payload_like(analytics: dict[str, Any]) -> dict[str, Any]:
    meta = _safe_json_dict(analytics.get("meta"))
    return {
        "summary": _safe_json_dict(analytics.get("summary")),
        "charts": {"engagement_trend": [], "category_popularity": []},
        "tables": {"top_lessons": [], "recent_lessons": [], "top_categories": []},
        "recent_activity": [],
        "qualitative_feedback": {
            "recent_comments": [],
            "truncated": bool(_safe_json_dict(analytics.get("qualitative_feedback")).get("truncated")),
            "limit": _safe_int(_safe_json_dict(analytics.get("qualitative_feedback")).get("limit"), 0),
            "max_comment_chars": _safe_int(_safe_json_dict(analytics.get("qualitative_feedback")).get("max_comment_chars"), 0),
        },
        "lesson_quality": _safe_json_dict(analytics.get("lesson_quality")),
        "filters": _safe_json_dict(analytics.get("filters")),
        "meta": {
            "contract": _clean_text(meta.get("contract"), max_chars=60),
            "scope": _clean_text(meta.get("scope"), max_chars=40),
            "estimated_metrics": bool(meta.get("estimated_metrics")),
            "comment_feedback_truncated": bool(meta.get("comment_feedback_truncated")),
            "estimated_fields": _scrub_private(_safe_list(meta.get("estimated_fields"))[:20]),
            "missing_metrics": _scrub_private(_safe_list(meta.get("missing_metrics"))[:20]),
        },
    }


def _analytics_chunk_payload(input_payload: dict[str, Any], *, section: str, rows: list[Any], total: int, index: int) -> dict[str, Any]:
    analytics = input_payload.get("analytics") if isinstance(input_payload.get("analytics"), dict) else {}
    chunk_analytics = _empty_analytics_payload_like(analytics)
    if section == "engagement_trend":
        chunk_analytics["charts"]["engagement_trend"] = rows
    elif section == "top_lessons":
        chunk_analytics["tables"]["top_lessons"] = rows
    elif section == "recent_lessons":
        chunk_analytics["tables"]["recent_lessons"] = rows
    elif section == "top_categories":
        chunk_analytics["tables"]["top_categories"] = rows
        chunk_analytics["charts"]["category_popularity"] = rows
    elif section == "recent_activity":
        chunk_analytics["recent_activity"] = rows
    elif section == "recent_comments":
        chunk_analytics["qualitative_feedback"]["recent_comments"] = rows
    elif section == "weak_lessons":
        chunk_analytics["lesson_quality"] = {
            **_safe_json_dict(chunk_analytics.get("lesson_quality")),
            "weak_lessons": rows,
            "strong_lessons": [],
        }
    elif section == "strong_lessons":
        chunk_analytics["lesson_quality"] = {
            **_safe_json_dict(chunk_analytics.get("lesson_quality")),
            "weak_lessons": [],
            "strong_lessons": rows,
        }
    else:
        chunk_analytics = analytics
    payload = {
        **dict(input_payload),
        "analytics": _scrub_private(chunk_analytics),
        "chunk": {
            "index": index,
            "count": total,
            "section": section,
            "item_count": len(rows),
        },
    }
    payload["input_chars"] = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    return payload


def _split_analytics_rows(rows: list[Any], *, target_chars: int) -> list[list[Any]]:
    chunks: list[list[Any]] = []
    current: list[Any] = []
    current_chars = 0
    max_items = max(1, _int_setting("INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS", 10))
    for row in rows:
        item = _scrub_private(row)
        item_chars = len(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
        if current and (len(current) >= max_items or current_chars + item_chars > target_chars):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)
    return chunks


def _analytics_chunk_payloads(input_payload: dict[str, Any]) -> list[dict[str, Any]]:
    serialized = json.dumps(input_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    max_chars = ollama_chunk_max_chars()
    analytics = input_payload.get("analytics") if isinstance(input_payload.get("analytics"), dict) else {}
    tables = analytics.get("tables") if isinstance(analytics.get("tables"), dict) else {}
    charts = analytics.get("charts") if isinstance(analytics.get("charts"), dict) else {}
    feedback = analytics.get("qualitative_feedback") if isinstance(analytics.get("qualitative_feedback"), dict) else {}
    lesson_quality = analytics.get("lesson_quality") if isinstance(analytics.get("lesson_quality"), dict) else {}
    sections = [
        ("engagement_trend", _safe_list(charts.get("engagement_trend"))),
        ("top_lessons", _safe_list(tables.get("top_lessons"))),
        ("recent_lessons", _safe_list(tables.get("recent_lessons"))),
        ("top_categories", _safe_list(tables.get("top_categories") or charts.get("category_popularity"))),
        ("recent_activity", _safe_list(analytics.get("recent_activity"))),
        ("recent_comments", _safe_list(feedback.get("recent_comments"))),
        ("weak_lessons", _safe_list(lesson_quality.get("weak_lessons"))),
        ("strong_lessons", _safe_list(lesson_quality.get("strong_lessons"))),
    ]
    total_rows = sum(len(rows) for _, rows in sections)
    row_threshold = max(
        _int_setting("INTELLIGENCE_OLLAMA_CHUNK_MAX_ITEMS", 10),
        _int_setting("INTELLIGENCE_OLLAMA_CHUNK_ROW_THRESHOLD", 40),
    )
    if len(serialized) <= max_chars and total_rows <= row_threshold:
        return [dict(input_payload)]

    prepared: list[tuple[str, list[Any]]] = []
    target_chars = max(600, int(max_chars * 0.7))
    for section, rows in sections:
        valid_rows = [row for row in rows if isinstance(row, (dict, str))]
        if not valid_rows:
            continue
        for part in _split_analytics_rows(valid_rows, target_chars=target_chars):
            prepared.append((section, part))
    if not prepared:
        return [dict(input_payload)]
    total = len(prepared)
    return [
        _analytics_chunk_payload(input_payload, section=section, rows=rows, total=total, index=index)
        for index, (section, rows) in enumerate(prepared, start=1)
    ]


def analytics_ollama_chunk_count(analytics_input: AnalyticsIntelligenceInput) -> int:
    return max(1, len(_analytics_chunk_payloads(analytics_input.to_provider_payload())))


def _analytics_total_timeout_budget_seconds() -> float:
    return _float_setting(
        "ANALYTICS_INTELLIGENCE_MAX_BACKGROUND_SECONDS",
        180.0,
        minimum=5.0,
        maximum=ollama_total_timeout_budget_seconds(),
    )


def _analytics_workload_timeout_budget_seconds(input_payload: dict[str, Any], *, chunk_count: int) -> float:
    return float(_analytics_workload_timeout_budget_details(input_payload, chunk_count=chunk_count)["timeout_budget_seconds"])


def _analytics_workload_timeout_budget_details(
    input_payload: dict[str, Any],
    *,
    chunk_count: int,
    model: str = "",
) -> dict[str, Any]:
    configured_budget = _analytics_total_timeout_budget_seconds()
    hard_max = configured_budget if configured_budget < 30.0 else ollama_total_timeout_budget_seconds()
    payload = input_payload if isinstance(input_payload, dict) else {}
    base_url = _string_setting(
        "OLLAMA_ANALYTICS_INTELLIGENCE_BASE_URL",
        _string_setting("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
    ).rstrip("/")
    return ollama_workload_timeout_budget_details(
        input_chars=_safe_int(payload.get("input_chars"), 0),
        chunk_count=chunk_count,
        base_seconds=configured_budget,
        hard_max_seconds=hard_max,
        kind="analytics",
        model=model,
        base_url=base_url,
    )


def _serialized_char_count(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _analytics_finalization_timeout_failure(*, chunk_count: int, completed_chunks: int, elapsed_seconds: float) -> AnalyticsIntelligenceProviderUnavailable:
    message = "Ollama timed out during final analytics summary after all chunks completed."
    failure = AnalyticsIntelligenceProviderUnavailable(message)
    failure.last_failure_reason = message
    failure.diagnostic = {
        "model": "",
        "error_class": "TimeoutError",
        "parse_stage": "final_aggregation_timeout",
        "reason": "final_aggregation_timeout",
        "safe_reason": message,
        "chunk_count": int(chunk_count),
        "chunks_completed": int(completed_chunks),
        "finalization_elapsed_seconds": round(float(elapsed_seconds or 0.0), 2),
    }
    return failure


def _dedupe_analytics_items(items: list[Any], *, limit: int) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            identity = str(item.get("message") or item.get("lesson_title") or item.get("category") or "") or json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        else:
            identity = str(item)
        if identity in seen:
            continue
        seen.add(identity)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def _analytics_chunk_limitation(language: str, key: str, *, failed: int = 0, total: int = 0) -> str:
    if language == "tr":
        if key == "partial":
            return f"Ollama {total} analiz parçasından {failed} tanesini tamamlayamadı; rapor kısmi geliştirme içeriyor."
        if key == "budget":
            return "Ollama toplam süre sınırına ulaştı; kalan analitik sinyaller hızlı analizle korundu."
        return "Büyük analitik yükü parçalara ayrılarak analiz edildi."
    if key == "partial":
        return f"Ollama could not complete {failed} of {total} analytics chunks; the report uses partial enhancement."
    if key == "budget":
        return "Ollama reached the total time budget; remaining analytics signals kept heuristic coverage."
    if key == "no_progress":
        return "Ollama enhancement timed out after partial progress; remaining analytics signals kept heuristic coverage."
    if key == "chunk_timeout":
        return "Ollama could not finish analytics chunks before timing out; heuristic coverage was kept."
    return "Large analytics workload was analyzed in chunks."


ANALYTICS_SUMMARY_MAX_SENTENCES = 4
ANALYTICS_SUMMARY_MAX_CHARS = 600
ANALYTICS_SUMMARY_BOILERPLATE_PATTERNS = (
    "ollama returned analytics guidance for the selected period",
    "based on aggregate creator analytics",
    "analytics guidance for the selected period",
)


def _analytics_sentence_parts(text: Any) -> list[str]:
    cleaned = _clean_text(text, max_chars=2000)
    if not cleaned:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]


def _analytics_sentence_identity(sentence: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(sentence or "").lower()).strip()


def _analytics_boilerplate_sentence(sentence: str) -> bool:
    identity = _analytics_sentence_identity(sentence)
    return any(pattern in identity for pattern in ANALYTICS_SUMMARY_BOILERPLATE_PATTERNS)


def _analytics_cap_sentences(
    sentences: list[str],
    *,
    max_sentences: int = ANALYTICS_SUMMARY_MAX_SENTENCES,
    max_chars: int = ANALYTICS_SUMMARY_MAX_CHARS,
) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for raw_sentence in sentences:
        sentence = _clean_text(raw_sentence, max_chars=max_chars).strip()
        if not sentence or _analytics_boilerplate_sentence(sentence):
            continue
        identity = _analytics_sentence_identity(sentence)
        if not identity or identity in seen:
            continue
        candidate = " ".join([*output, sentence]).strip()
        if output and len(candidate) > max_chars:
            break
        output.append(sentence)
        seen.add(identity)
        if len(output) >= max_sentences:
            break
    return _clean_text(" ".join(output), max_chars=max_chars)


def _analytics_format_range(date_range: dict[str, Any], *, output_language: str) -> str:
    start = str(date_range.get("from") or "").strip()
    end = str(date_range.get("to") or "").strip()
    if output_language == "tr":
        return f"{start}-{end}" if start and end else "secilen aralik"
    try:
        start_dt = datetime.fromisoformat(start).date()
        end_dt = datetime.fromisoformat(end).date()
    except (TypeError, ValueError):
        return "the selected range"
    if start_dt.year == end_dt.year and start_dt.month == end_dt.month:
        return f"{start_dt.strftime('%B')} {start_dt.day}-{end_dt.day}"
    if start_dt.year == end_dt.year:
        return f"{start_dt.strftime('%B')} {start_dt.day}-{end_dt.strftime('%B')} {end_dt.day}"
    return f"{start_dt.strftime('%B')} {start_dt.day}, {start_dt.year}-{end_dt.strftime('%B')} {end_dt.day}, {end_dt.year}"


def _analytics_count_phrase(value: int, singular: str, plural: str | None = None) -> str:
    word = singular if int(value) == 1 else (plural or f"{singular}s")
    return f"{int(value)} {word}"


def _analytics_top_category_name(payload: dict[str, Any]) -> str:
    charts = _safe_json_dict(payload.get("charts"))
    tables = _safe_json_dict(payload.get("tables"))
    categories = [
        item for item in _safe_list(tables.get("top_categories") or charts.get("category_popularity"))
        if isinstance(item, dict)
    ]
    if not categories:
        return ""
    dominant = _dominant_category(categories)
    if not dominant or _category_signal(dominant) <= 0:
        return ""
    return _clean_text(dominant.get("category_name") or dominant.get("name"), max_chars=80)


def _analytics_recommendation_sentence(
    *,
    output_language: str,
    total_lessons: int,
    published_lessons: int,
    total_views: int,
    unique_viewers: int,
    engagement_events: int,
    completion_rate: float,
    average_progress: float,
    top_category: str,
) -> str:
    if output_language == "tr":
        if total_lessons <= 0:
            return "Once olcumlenebilir ilerleme sinyalleri toplamak icin odakli bir ilk dersi yayinlayin."
        if published_lessons > 0 and total_views <= 0 and unique_viewers <= 0:
            return "Yayindaki dersleri tanitin ve kategori sayfalarina net eylem cagrilari ekleyin."
        if total_views > 0 and (completion_rate < 50 or average_progress < 50):
            return "Derslerin acilis vaadini, temposunu ve erken orneklerini iyilestirerek ilerlemeyi artirin."
        if engagement_events <= 0:
            return "Ders sonlarina kisa bir ozet sorusu veya pratik alistirma ekleyin."
        return "En guclu ders kaliplarini zayif derslere tasiyarak bir sonraki iyilestirmeyi planlayin."
    if total_lessons <= 0:
        return "Publish a focused first lesson so progress, completion, likes, and comments can be measured."
    if published_lessons > 0 and total_views <= 0 and unique_viewers <= 0:
        if top_category:
            return f"Prioritize promoting recently published {top_category} lessons and add clear calls to action."
        return "Prioritize promoting recently published lessons and add clear calls to action."
    if total_views > 0 and (completion_rate < 50 or average_progress < 50):
        return "Improve the opening promise, pacing, and early examples on active lessons to raise completion."
    if engagement_events <= 0:
        return "Add a short recap question or practical exercise near the end of each lesson to prompt engagement."
    if top_category:
        return f"Use {top_category} as the first category to review, then apply its strongest patterns to weaker lessons."
    return "Review the strongest lesson's title, opening, structure, and examples, then reuse those patterns in weaker lessons."


def _analytics_metric_summary_sentences(analytics_input: AnalyticsIntelligenceInput) -> list[str]:
    payload = analytics_input.analytics_payload if isinstance(analytics_input.analytics_payload, dict) else {}
    summary = _safe_json_dict(payload.get("summary"))
    output_language = analytics_input.output_language
    total_lessons = _safe_int(summary.get("total_lessons"), 0)
    published_lessons = _safe_int(summary.get("published_lessons"), 0)
    total_views = _safe_int(summary.get("total_views") or summary.get("video_plays"), 0)
    unique_viewers = _safe_int(summary.get("unique_viewers"), 0)
    engagement_events = _safe_int(summary.get("engagement_events"), 0)
    likes = _safe_int(summary.get("likes"), 0)
    comments = _safe_int(summary.get("comments"), 0)
    completion_rate = _bounded_percent(summary.get("completion_rate"))
    average_progress = _bounded_percent(summary.get("average_progress"))
    range_label = _analytics_format_range(analytics_input.date_range, output_language=output_language)
    top_category = _analytics_top_category_name(payload)
    published_phrase = _analytics_count_phrase(published_lessons, "published lesson")
    total_phrase = "1 total lesson" if total_lessons == 1 else f"{total_lessons} total"

    if output_language == "tr":
        if total_views <= 0 and unique_viewers <= 0:
            activity = "ancak video oynatma veya tekil izleyici kaydi yok."
        else:
            activity = f"{_analytics_count_phrase(total_views, 'video oynatma', 'video oynatma')} ve {_analytics_count_phrase(unique_viewers, 'tekil izleyici', 'tekil izleyici')} var."
        social_parts = [_analytics_count_phrase(engagement_events, "etkilesim", "etkilesim")]
        if likes:
            social_parts.append(_analytics_count_phrase(likes, "begeni", "begeni"))
        if comments:
            social_parts.append(_analytics_count_phrase(comments, "yorum", "yorum"))
        social_sentence = (
            "Etkilesim de sessiz; kayitli olay, begeni veya yorum yok."
            if len(social_parts) == 1 and engagement_events <= 0
            else f"Etkilesim dusuk; {', '.join(social_parts)} kaydedildi."
        )
        return [
            f"{range_label} araliginda katalogda {published_lessons} yayinda ders ve toplam {total_lessons} ders vardi, {activity}",
            social_sentence,
            _analytics_recommendation_sentence(
                output_language=output_language,
                total_lessons=total_lessons,
                published_lessons=published_lessons,
                total_views=total_views,
                unique_viewers=unique_viewers,
                engagement_events=engagement_events,
                completion_rate=completion_rate,
                average_progress=average_progress,
                top_category=top_category,
            ),
        ]

    if total_views <= 0 and unique_viewers <= 0:
        first = (
            f"During {range_label}, the catalog had {published_phrase} out of {total_phrase}, "
            "but there were no video plays or unique viewers."
        )
    else:
        first = (
            f"During {range_label}, the catalog had {published_phrase} out of {total_phrase}, "
            f"with {_analytics_count_phrase(total_views, 'video play')} from {_analytics_count_phrase(unique_viewers, 'unique viewer')}."
        )
    social_parts = [_analytics_count_phrase(engagement_events, "event")]
    if likes:
        social_parts.append(_analytics_count_phrase(likes, "like"))
    if comments:
        social_parts.append(_analytics_count_phrase(comments, "comment"))
    if len(social_parts) == 1 and engagement_events <= 0:
        second = "Engagement was also quiet, with no recorded events, likes, or comments."
    else:
        listed = " and ".join(social_parts) if len(social_parts) <= 2 else f"{', '.join(social_parts[:-1])}, and {social_parts[-1]}"
        second = f"Engagement was minimal, with {listed}."
    return [
        first,
        second,
        _analytics_recommendation_sentence(
            output_language=output_language,
            total_lessons=total_lessons,
            published_lessons=published_lessons,
            total_views=total_views,
            unique_viewers=unique_viewers,
            engagement_events=engagement_events,
            completion_rate=completion_rate,
            average_progress=average_progress,
            top_category=top_category,
        ),
    ]


def _analytics_compact_chunk_summary(analytics_input: AnalyticsIntelligenceInput, chunk_results: list[dict[str, Any]]) -> str:
    metric_sentences = _analytics_metric_summary_sentences(analytics_input)
    model_sentences: list[str] = []
    for result in chunk_results:
        for sentence in _analytics_sentence_parts(result.get("analytics_summary") or result.get("summary")):
            if not _analytics_boilerplate_sentence(sentence):
                model_sentences.append(sentence)
    summary = _analytics_cap_sentences(metric_sentences, max_sentences=3, max_chars=ANALYTICS_SUMMARY_MAX_CHARS)
    if summary:
        return summary
    return _analytics_cap_sentences(model_sentences, max_sentences=ANALYTICS_SUMMARY_MAX_SENTENCES, max_chars=ANALYTICS_SUMMARY_MAX_CHARS)


def _synthesize_analytics_chunk_results(
    analytics_input: AnalyticsIntelligenceInput,
    *,
    chunk_count: int,
    completed_chunks: int,
    failed_chunks: int,
    chunk_results: list[dict[str, Any]],
    chunk_limitations: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    language = analytics_input.output_language
    summary = _analytics_compact_chunk_summary(analytics_input, chunk_results)
    if not summary:
        summary = "Ollama analyzed the available analytics chunks." if language != "tr" else "Ollama mevcut analitik parçaları analiz etti."
    scores = [max(0, min(100, _safe_int(result.get("health_score"), 50))) for result in chunk_results]
    health_score = int(round(sum(scores) / max(1, len(scores))))
    insights: list[Any] = []
    recommendations: list[Any] = []
    lesson_actions: list[Any] = []
    category_actions: list[Any] = []
    limitations: list[Any] = [_analytics_chunk_limitation(language, "chunked")]
    for result in chunk_results:
        insights.extend(_safe_list(result.get("insights")))
        recommendations.extend(_safe_list(result.get("recommendations")))
        lesson_actions.extend(_safe_list(result.get("lesson_actions")))
        category_actions.extend(_safe_list(result.get("category_actions")))
        limitations.extend(_safe_list(result.get("limitations")))
    if failed_chunks:
        limitations.append(_analytics_chunk_limitation(language, "partial", failed=failed_chunks, total=chunk_count))
    limitations.extend(chunk_limitations)
    return {
        "provider": "ollama",
        "analytics_summary": summary,
        "health_score": health_score,
        "risk_level": _risk_level(health_score),
        "insights": _dedupe_analytics_items(insights, limit=16),
        "recommendations": _dedupe_analytics_items(recommendations, limit=16),
        "lesson_actions": _dedupe_analytics_items(lesson_actions, limit=16),
        "category_actions": _dedupe_analytics_items(category_actions, limit=16),
        "limitations": _dedupe_analytics_items(limitations, limit=12),
        "metadata": {
            "chunked": True,
            "chunk_count": chunk_count,
            "completed_chunks": completed_chunks,
            "failed_chunks": failed_chunks,
            "chunk_limitations": chunk_limitations,
            "partial_enhancement": bool(failed_chunks),
            "timeout_seconds": timeout_seconds,
        },
    }


def analyze_analytics_ollama_background(
    analytics_input: AnalyticsIntelligenceInput,
    *,
    chain: list[str] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    provider_chain = chain or analytics_provider_chain_from_settings()
    input_payload = analytics_input.to_provider_payload()
    provider = OllamaAnalyticsIntelligenceProvider(background=True)
    run_identity = analytics_ollama_run_identity(analytics_input)
    chunks = _analytics_chunk_payloads(input_payload)
    estimated_chunk_count = len(chunks) or 1
    estimated_workload = {
        "kind": "analytics",
        "input_chars": int(analytics_input.input_chars or 0),
        "estimated_input_chars": int(analytics_input.input_chars or 0),
        "estimated_tokens": int(((analytics_input.input_chars or 0) + 3) / 4),
        "chunk_count": estimated_chunk_count,
        "model": provider.model,
        "model_profile": intelligence_hardware_profile(),
        "hardware_profile": intelligence_hardware_profile(),
    }
    should_chunk = len(chunks) > 1
    if not should_chunk:
        if callable(progress_callback):
            progress_callback("chunk_processing", 1, 0, 0, {"index": 1, "count": 1})
        budget_details = _analytics_workload_timeout_budget_details(input_payload, chunk_count=1, model=provider.model)
        result = provider.analyze_analytics(input_payload, timeout_seconds_override=float(budget_details["timeout_budget_seconds"]))
        normalized = _normalize_provider_result(result, provider_name=provider.provider_name)
        provider_meta = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
        elapsed_seconds = provider_meta.get("elapsed_seconds")
        finalization_details = ollama_finalization_timeout_budget_details(
            input_chars=_safe_int(input_payload.get("input_chars"), 0),
            output_chars=_serialized_char_count(normalized),
            chunk_count=1,
            hard_max_seconds=budget_details.get("absolute_cap_seconds"),
            kind="analytics",
            model=provider.model,
            base_url=provider.base_url,
        )
        chunk_metadata = {
            "chunked": False,
            "chunk_count": 1,
            "completed_chunks": 1,
            "failed_chunks": 0,
            "chunks_attempted": 1,
            "chunks_completed": 1,
            "last_completed_chunk_index": 1,
            "partial_ollama_used": False,
            "fallback_used": False,
            "degraded_reason": "",
            "estimated_workload": estimated_workload,
            "elapsed_seconds": elapsed_seconds,
            "actual_elapsed_seconds": elapsed_seconds,
            "finalization_budget_seconds": finalization_details["timeout_budget_seconds"],
            "finalization_elapsed_seconds": 0.0,
            "finalization_timeout": False,
            "finalization_calculated_budget_seconds": finalization_details["calculated_budget_seconds"],
            "finalization_timeout_budget_seconds": finalization_details["timeout_budget_seconds"],
            "finalization_absolute_cap_seconds": finalization_details["absolute_cap_seconds"],
            **budget_details,
        }
        provider_attempt = progressive_provider_attempt("ollama", "success")
        timeout_seconds = getattr(provider, "last_timeout_seconds", None)
        if callable(progress_callback):
            progress_callback("final_synthesis", 1, 1, 0, {"index": 1, "count": 1})
    else:
        chunk_count = len(chunks)
        if callable(progress_callback):
            progress_callback("chunk_processing", chunk_count, 0, 0, {"index": 0, "count": chunk_count})
        started_at = time.monotonic()
        budget_details = _analytics_workload_timeout_budget_details(input_payload, chunk_count=chunk_count, model=provider.model)
        absolute_cap = float(budget_details.get("absolute_cap_seconds") or budget_details["timeout_budget_seconds"])
        completed_chunks = 0
        failed_chunks = 0
        attempted_chunks = 0
        last_completed_chunk_index = 0
        max_no_progress_timeout = float(budget_details.get("no_progress_timeout_seconds") or 0.0)
        budget_exceeded = False
        no_progress_exceeded = False
        chunk_results: list[dict[str, Any]] = []
        chunk_limitations: list[str] = []
        chunk_diagnostics: list[dict[str, Any]] = []
        if callable(progress_callback):
            progress_callback("chunk_processing", chunk_count, completed_chunks, failed_chunks, {"index": 0, "count": chunk_count})
        for position, chunk_payload in enumerate(chunks, start=1):
            chunk_meta = chunk_payload.get("chunk") if isinstance(chunk_payload.get("chunk"), dict) else {}
            current_chunk = {
                "index": int(chunk_meta.get("index") or position),
                "count": int(chunk_meta.get("count") or chunk_count),
                "section": _clean_text(chunk_meta.get("section"), max_chars=80),
                "item_count": _safe_int(chunk_meta.get("item_count"), 0),
            }
            if callable(progress_callback):
                progress_callback("chunk_processing", chunk_count, completed_chunks, failed_chunks, current_chunk)
            elapsed = time.monotonic() - started_at
            remaining_absolute = max(0.0, absolute_cap - elapsed)
            if remaining_absolute < 1.0:
                budget_exceeded = True
                remaining_chunks = max(1, chunk_count - position + 1)
                failed_chunks += remaining_chunks
                chunk_limitations.append(_analytics_chunk_limitation(analytics_input.output_language, "budget"))
                diagnostic = _chunk_failure_diagnostic(
                    current_chunk,
                    {
                        "model": provider.model,
                        "error_class": "TimeoutError",
                        "parse_stage": "timeout",
                        "reason": "total_budget_exceeded",
                        "safe_reason": "Ollama time budget was exceeded.",
                        "absolute_cap_seconds": round(absolute_cap, 2),
                        "elapsed_seconds": round(elapsed, 2),
                        "retry_count": 0,
                    },
                )
                chunk_diagnostics.append(diagnostic)
                if callable(progress_callback):
                    progress_callback(
                        "chunk_processing",
                        chunk_count,
                        completed_chunks,
                        failed_chunks,
                        {
                            **current_chunk,
                            "budget_exceeded": True,
                            "skipped_remaining_chunks": remaining_chunks,
                            "chunk_diagnostics": chunk_diagnostics[-10:],
                            "last_failure_reason": diagnostic.get("safe_reason") or diagnostic.get("reason"),
                        },
                )
                break
            chunk_input_chars = _safe_int(chunk_payload.get("input_chars"), 0)
            budget_chunk_timeout = float(
                budget_details.get("average_chunk_timeout_seconds")
                or ollama_chunk_timeout_seconds(chunk_input_chars)
            )
            chunk_no_progress_timeout = ollama_no_progress_timeout_seconds(
                chunk_input_chars,
                hard_max_seconds=remaining_absolute,
            )
            chunk_no_progress_timeout = min(remaining_absolute, max(chunk_no_progress_timeout, budget_chunk_timeout))
            max_no_progress_timeout = max(max_no_progress_timeout, chunk_no_progress_timeout)
            chunk_timeout = min(
                max(ollama_chunk_timeout_seconds(chunk_input_chars), budget_chunk_timeout),
                chunk_no_progress_timeout,
                remaining_absolute,
            )
            try:
                attempted_chunks += 1
                result = provider.analyze_analytics(chunk_payload, timeout_seconds_override=chunk_timeout)
                normalized_chunk = _normalize_provider_result(result, provider_name=provider.provider_name)
                completed_chunks += 1
                last_completed_chunk_index = int(current_chunk.get("index") or position)
            except Exception as exc:  # noqa: BLE001
                diagnostic = _chunk_failure_diagnostic(current_chunk, _diagnostic_from_exception(exc, model=provider.model))
                diagnostic["no_progress_timeout_seconds"] = round(chunk_no_progress_timeout, 2)
                chunk_diagnostics.append(diagnostic)
                if str(diagnostic.get("parse_stage") or "").lower() == "timeout" and completed_chunks > 0:
                    no_progress_exceeded = True
                    remaining_chunks = max(1, chunk_count - position + 1)
                    failed_chunks += remaining_chunks
                    chunk_limitations.append(_analytics_chunk_limitation(analytics_input.output_language, "no_progress"))
                    diagnostic["reason"] = "no_progress_timeout"
                    diagnostic["safe_reason"] = "Ollama enhancement timed out after partial progress."
                    diagnostic["skipped_remaining_chunks"] = remaining_chunks
                    if callable(progress_callback):
                        progress_callback(
                            "chunk_processing",
                            chunk_count,
                            completed_chunks,
                            failed_chunks,
                            {
                                **current_chunk,
                                "no_progress_timeout": True,
                                "skipped_remaining_chunks": remaining_chunks,
                                "chunk_diagnostics": chunk_diagnostics[-10:],
                                "last_failure_reason": diagnostic.get("safe_reason") or diagnostic.get("reason"),
                            },
                        )
                    break
                failed_chunks += 1
                chunk_limitations.append(diagnostic.get("safe_reason") or f"chunk_failed:{exc.__class__.__name__}")
                fallback = HeuristicAnalyticsIntelligenceProvider().analyze_analytics(chunk_payload)
                normalized_chunk = _normalize_provider_result(fallback, provider_name="heuristic")
            chunk_results.append(normalized_chunk)
            if callable(progress_callback):
                progress_payload = dict(current_chunk)
                if chunk_diagnostics:
                    progress_payload["chunk_diagnostics"] = chunk_diagnostics[-10:]
                    progress_payload["last_failure_reason"] = chunk_diagnostics[-1].get("safe_reason") or chunk_diagnostics[-1].get("reason")
                progress_callback("chunk_processing", chunk_count, completed_chunks, failed_chunks, progress_payload)
        all_ollama_chunks_failed = completed_chunks <= 0
        chunk_output_chars = _serialized_char_count(chunk_results)
        finalization_details = ollama_finalization_timeout_budget_details(
            input_chars=_safe_int(input_payload.get("input_chars"), 0),
            output_chars=chunk_output_chars,
            chunk_count=chunk_count,
            hard_max_seconds=absolute_cap,
            kind="analytics",
            model=provider.model,
            base_url=provider.base_url,
        )
        finalization_budget = float(finalization_details["timeout_budget_seconds"])
        finalization_elapsed = 0.0
        finalization_timeout = False
        finalization_message = "Ollama timed out during final analytics summary after all chunks completed."
        timeout_seconds = round(min(absolute_cap, time.monotonic() - started_at), 2)
        all_chunks_message = "Ollama chunk analysis timed out before any analytics chunk completed."
        all_chunk_failures_timed_out = bool(
            chunk_diagnostics
            and all(str(item.get("parse_stage") or "").lower() == "timeout" for item in chunk_diagnostics)
        )
        if all_ollama_chunks_failed:
            if not _provider_chain_contains(provider_chain, "heuristic"):
                failure = AnalyticsIntelligenceProviderUnavailable("Ollama chunk analysis failed for all chunks")
                failure.chunk_diagnostics = chunk_diagnostics[-20:]
                if chunk_diagnostics:
                    failure.last_failure_reason = chunk_diagnostics[-1].get("safe_reason") or chunk_diagnostics[-1].get("reason")
                raise failure
            fallback = HeuristicAnalyticsIntelligenceProvider().analyze_analytics(input_payload)
            normalized = _normalize_provider_result(fallback, provider_name="heuristic")
            provider_attempt = progressive_provider_attempt("ollama", "degraded", all_chunks_message)
            chunk_limitations.append(_analytics_chunk_limitation(analytics_input.output_language, "chunk_timeout"))
        elif callable(progress_callback):
            progress_callback(
                "final_synthesis",
                chunk_count,
                completed_chunks,
                failed_chunks,
                {
                    "index": chunk_count,
                    "count": chunk_count,
                    "finalization_budget_seconds": finalization_budget,
                    "chunks_completed": completed_chunks,
                },
            )
        if not all_ollama_chunks_failed:
            finalization_started = time.monotonic()
            finalization_error: Exception | None = None
            synthesized_payload: dict[str, Any] | None = None
            try:
                synthesized_payload = _synthesize_analytics_chunk_results(
                    analytics_input,
                    chunk_count=chunk_count,
                    completed_chunks=completed_chunks,
                    failed_chunks=failed_chunks,
                    chunk_results=chunk_results,
                    chunk_limitations=chunk_limitations,
                    timeout_seconds=round(min(absolute_cap, finalization_started - started_at), 2),
                )
            except TimeoutError as exc:
                finalization_error = exc
            finalization_elapsed_raw = max(0.0, time.monotonic() - finalization_started)
            finalization_elapsed = round(finalization_elapsed_raw, 2)
            finalization_timeout = bool(finalization_error or finalization_elapsed_raw > finalization_budget)
            timeout_seconds = round(min(absolute_cap, (finalization_started - started_at) + finalization_elapsed_raw), 2)
            if finalization_timeout:
                diagnostic = _chunk_failure_diagnostic(
                    {"index": chunk_count, "count": chunk_count, "section": "final_synthesis"},
                    {
                        "model": provider.model,
                        "error_class": (finalization_error.__class__.__name__ if finalization_error else "TimeoutError"),
                        "parse_stage": "final_aggregation_timeout",
                        "reason": "final_aggregation_timeout",
                        "safe_reason": finalization_message,
                        "finalization_budget_seconds": finalization_budget,
                        "finalization_elapsed_seconds": finalization_elapsed,
                        "chunks_completed": completed_chunks,
                        "chunk_count": chunk_count,
                        "retry_count": 0,
                    },
                )
                chunk_diagnostics.append(diagnostic)
                if not _provider_chain_contains(provider_chain, "heuristic"):
                    failure = _analytics_finalization_timeout_failure(
                        chunk_count=chunk_count,
                        completed_chunks=completed_chunks,
                        elapsed_seconds=finalization_elapsed,
                    )
                    failure.chunk_diagnostics = chunk_diagnostics[-20:]
                    raise failure
                fallback = HeuristicAnalyticsIntelligenceProvider().analyze_analytics(input_payload)
                normalized = _normalize_provider_result(fallback, provider_name="heuristic")
                provider_attempt = progressive_provider_attempt("ollama", "degraded", finalization_message)
            else:
                normalized = _normalize_provider_result(
                    synthesized_payload or {},
                    provider_name=provider.provider_name,
                )
                timeout_degraded = bool(budget_exceeded or no_progress_exceeded)
                provider_attempt = progressive_provider_attempt(
                    "ollama",
                    "degraded" if timeout_degraded else "partial" if failed_chunks else "success",
                    (
                        "Ollama time budget was exceeded."
                        if budget_exceeded
                        else "Ollama enhancement timed out after partial progress."
                        if no_progress_exceeded
                        else None
                    ),
                )
        degraded_reason = (
            "ollama_total_budget_exceeded"
            if budget_exceeded
            else "ollama_no_progress_timeout"
            if no_progress_exceeded
            else "chunk_timeout"
            if all_ollama_chunks_failed and all_chunk_failures_timed_out
            else "chunk_failure"
            if all_ollama_chunks_failed
            else "final_aggregation_timeout"
            if finalization_timeout
            else "partial_ollama_chunk_failure"
            if failed_chunks
            else ""
        )
        timeout_degraded = bool(all_ollama_chunks_failed or budget_exceeded or no_progress_exceeded or finalization_timeout)
        chunk_metadata = {
            "chunked": True,
            "chunk_count": chunk_count,
            "completed_chunks": completed_chunks,
            "failed_chunks": failed_chunks,
            "chunks_attempted": attempted_chunks,
            "chunks_completed": completed_chunks,
            "last_completed_chunk_index": last_completed_chunk_index,
            "chunk_limitations": chunk_limitations,
            "chunk_diagnostics": chunk_diagnostics[-20:],
            "partial_enhancement": bool(failed_chunks or finalization_timeout),
            "partial_ollama_used": bool(completed_chunks and (failed_chunks or finalization_timeout or budget_exceeded or no_progress_exceeded)),
            "fallback_used": bool(failed_chunks or finalization_timeout or all_ollama_chunks_failed or budget_exceeded or no_progress_exceeded),
            "degraded": timeout_degraded,
            "degraded_reason": degraded_reason,
            "estimated_workload": estimated_workload,
            "elapsed_seconds": timeout_seconds,
            "actual_elapsed_seconds": timeout_seconds,
            "phase": "chunk_processing" if all_ollama_chunks_failed else "final_synthesis" if finalization_timeout else "persistence",
            "finalization_budget_seconds": finalization_budget,
            "finalization_elapsed_seconds": finalization_elapsed,
            "finalization_timeout": finalization_timeout,
            "finalization_calculated_budget_seconds": finalization_details["calculated_budget_seconds"],
            "finalization_timeout_budget_seconds": finalization_details["timeout_budget_seconds"],
            "finalization_absolute_cap_seconds": finalization_details["absolute_cap_seconds"],
            **budget_details,
            "no_progress_timeout_seconds": round(max_no_progress_timeout, 2),
        }
    normalized["provider_chain"] = provider_chain
    normalized["fallback_used"] = bool(normalized.get("fallback_used") or chunk_metadata.get("fallback_used"))
    normalized["metadata"] = {
        **dict(normalized.get("metadata") or {}),
        "provider_chain_attempts": [provider_attempt],
        "source_hash": analytics_input.source_hash,
        "detected_language": analytics_input.detected_language,
        "output_language": analytics_input.output_language,
        "language_confidence": analytics_input.language_confidence,
        "input_truncated": analytics_input.input_truncated,
        "compaction": analytics_input.compaction or {},
        "input_char_count": analytics_input.input_chars,
        "analytics_filters": _safe_json_dict(analytics_input.analytics_payload.get("filters")),
        "prompt_version": ANALYTICS_INTELLIGENCE_PROMPT_VERSION,
        **run_identity,
        **chunk_metadata,
        **intelligence_runtime_profile_metadata(),
        "total_timeout_max_seconds": chunk_metadata.get("timeout_budget_seconds")
        or _analytics_workload_timeout_budget_seconds(input_payload, chunk_count=max(1, len(chunks))),
        "chunk_concurrency": ollama_chunk_concurrency(),
        "timeout_seconds": timeout_seconds,
    }
    return normalized


def apply_analytics_analysis_to_report(
    report: AnalyticsIntelligenceReport,
    analysis: dict[str, Any],
    *,
    source_hash: str,
) -> AnalyticsIntelligenceReport:
    report.status = "done"
    report.provider = str(analysis.get("provider") or "heuristic").strip().lower()
    report.provider_chain = list(analysis.get("provider_chain") or analytics_provider_chain_from_settings())
    report.fallback_used = bool(analysis.get("fallback_used"))
    report.source_hash = source_hash
    report.summary = str(analysis.get("analytics_summary") or analysis.get("summary") or "")
    report.health_score = max(0, min(100, _safe_int(analysis.get("health_score"), 0)))
    report.risk_level = _risk_level(report.health_score) if not analysis.get("risk_level") else str(analysis.get("risk_level")).lower()
    if report.risk_level not in RISK_LEVELS:
        report.risk_level = _risk_level(report.health_score)
    report.insights = _safe_json_list(analysis.get("insights"))
    report.recommendations = _safe_json_list(analysis.get("recommendations"))
    report.lesson_actions = _safe_json_list(analysis.get("lesson_actions"))
    report.category_actions = _safe_json_list(analysis.get("category_actions"))
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
            "health_score",
            "risk_level",
            "insights",
            "recommendations",
            "lesson_actions",
            "category_actions",
            "limitations",
            "metadata",
            "error_message",
            "updated_at",
        ]
    )
    return report


def analytics_report_response_payload(
    report: AnalyticsIntelligenceReport | None,
    *,
    enabled: bool = True,
    current_source_hash: str = "",
    current_run_key: str = "",
) -> dict[str, Any]:
    current_hash = str(current_source_hash or "")
    current_key = str(current_run_key or "")
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
            "run_key": "",
            "report_run_key": "",
            "current_run_key": current_key,
            "run_key_matches": False,
            "force": False,
            "is_stale": bool(enabled),
            "insight_stale": bool(enabled),
            "refresh_status": "idle" if enabled else "disabled",
            "latest_refresh_failed": False,
            "last_analyzed_at": "",
            "active_report_id": None,
            "pending_report_id": None,
            "latest_refresh_report_id": None,
            "detected_language": "unknown",
            "output_language": "en",
            "language_confidence": 0.0,
            "summary": "",
            "health_score": 0,
            "risk_level": "",
            "insights": [],
            "recommendations": [],
            "lesson_actions": [],
            "category_actions": [],
            "limitations": [],
        }
    report_metadata = report.metadata if isinstance(report.metadata, dict) else {}
    report_hash = str(report.source_hash or "")
    enhancement = report_metadata.get("progressive_enhancement") if isinstance(report_metadata.get("progressive_enhancement"), dict) else {}
    report_run_key = str(enhancement.get("run_key") or report_metadata.get("run_key") or "")
    provider_chain_attempts = report_metadata.get("provider_chain_attempts")
    if not isinstance(provider_chain_attempts, list):
        provider_chain_attempts = []
    refresh_status = str(report.status or "").strip().lower() or "idle"
    enhancement_status = str(enhancement.get("status") or "").strip().lower()
    if enhancement_status:
        refresh_status = enhancement_status
    is_stale = bool(enabled and current_hash and report_hash != current_hash)
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
        "output_language": str(report_metadata.get("output_language") or "en"),
        "language_confidence": float(report_metadata.get("language_confidence") or 0.0),
        "source_hash": report_hash,
        "report_source_hash": report_hash,
        "current_source_hash": current_hash,
        "run_key": report_run_key,
        "report_run_key": report_run_key,
        "current_run_key": current_key,
        "run_key_matches": bool(current_key and report_run_key and current_key == report_run_key),
        "force": bool(report_metadata.get("force") or enhancement.get("force")),
        "is_stale": is_stale,
        "insight_stale": is_stale,
        "refresh_status": refresh_status,
        "latest_refresh_failed": False,
        "last_analyzed_at": report.updated_at.isoformat() if report.status == "done" and report.updated_at else "",
        "active_report_id": report.id,
        "pending_report_id": None,
        "latest_refresh_report_id": report.id,
        "date_range": report.date_range if isinstance(report.date_range, dict) else {},
        "category_filter": report.category_filter,
        "summary": report.summary,
        "health_score": int(report.health_score or 0),
        "risk_level": report.risk_level,
        "insights": report.insights if isinstance(report.insights, list) else [],
        "recommendations": report.recommendations if isinstance(report.recommendations, list) else [],
        "lesson_actions": report.lesson_actions if isinstance(report.lesson_actions, list) else [],
        "category_actions": report.category_actions if isinstance(report.category_actions, list) else [],
        "limitations": report.limitations if isinstance(report.limitations, list) else [],
        "metadata": {
            key: value
            for key, value in (report.metadata if isinstance(report.metadata, dict) else {}).items()
            if key in {
                "provider_chain_attempts",
                "progressive_enhancement",
                "input_char_count",
                "total_lessons",
                "published_lessons",
                "total_views",
                "category_count",
                "detected_language",
                "output_language",
                "language_confidence",
                "input_truncated",
                "compaction",
                "run_key",
                "model",
                "prompt_version",
                "hardware_profile",
                "input_fingerprint",
                "chunk_max_chars",
                "chunk_concurrency",
                "chunk_timeout_min_seconds",
                "chunk_timeout_max_seconds",
                "total_timeout_max_seconds",
                "chunked",
                "estimated_workload",
                "input_chars",
                "estimated_input_chars",
                "estimated_input_tokens",
                "estimated_output_tokens",
                "estimated_tokens",
                "chunk_count",
                "completed_chunks",
                "failed_chunks",
                "chunks_attempted",
                "chunks_completed",
                "last_completed_chunk_index",
                "chunk_limitations",
                "chunk_diagnostics",
                "partial_enhancement",
                "partial_ollama_used",
                "degraded_reason",
                "calculated_budget_seconds",
                "calibrated_estimate_seconds",
                "configured_safety_margin_seconds",
                "safety_margin_seconds",
                "hard_max_seconds",
                "timeout_budget_seconds",
                "absolute_cap_seconds",
                "average_chunk_timeout_seconds",
                "no_progress_timeout_seconds",
                "elapsed_seconds",
                "actual_elapsed_seconds",
                "finalization_budget_seconds",
                "finalization_elapsed_seconds",
                "finalization_timeout",
                "finalization_calculated_budget_seconds",
                "finalization_timeout_budget_seconds",
                "finalization_absolute_cap_seconds",
                "degraded",
                "enhancement_failed",
                "fallback_used",
                "model_profile",
                "workload_kind",
                "calibration_enabled",
                "calibration_used",
                "calibration_cache_hit",
                "calibration_failed",
                "calibration_elapsed_seconds",
                "calibration_error",
                "measured_chars_per_second",
                "measured_tokens_per_second",
                "fallback_chars_per_second",
                "fallback_tokens_per_second",
                "throughput_chars_per_second",
                "throughput_tokens_per_second",
                "force",
                "forced_at",
                "manual_retry",
                "retry_count",
                "retry_requested_at",
                "retry_bypassed_cooldown",
                "last_ollama_failure_at",
                "retry_available_at",
                "retry_cooldown_seconds",
                "last_failure_reason",
                "repaired",
                "repair_retry_count",
            }
        },
        "error_message": report.error_message,
        "created_at": report.created_at.isoformat() if report.created_at else "",
        "updated_at": report.updated_at.isoformat() if report.updated_at else "",
    }


def _compact_analytics_payload(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = _safe_json_dict(payload.get("summary"))
    charts = _safe_json_dict(payload.get("charts"))
    tables = _safe_json_dict(payload.get("tables"))
    filters = _safe_json_dict(payload.get("filters"))
    meta = _safe_json_dict(payload.get("meta"))
    feedback = _safe_json_dict(payload.get("qualitative_feedback"))
    lesson_quality = _safe_json_dict(payload.get("lesson_quality"))
    source_counts = {
        "engagement_trend": len(_safe_list(charts.get("engagement_trend"))),
        "top_lessons": len(_safe_list(tables.get("top_lessons"))),
        "recent_lessons": len(_safe_list(tables.get("recent_lessons"))),
        "top_categories": len(_safe_list(tables.get("top_categories") or charts.get("category_popularity"))),
        "recent_activity": len(_safe_list(payload.get("recent_activity"))),
        "recent_comments": len(_safe_list(feedback.get("recent_comments"))),
        "weak_lessons": len(_safe_list(lesson_quality.get("weak_lessons"))),
        "strong_lessons": len(_safe_list(lesson_quality.get("strong_lessons"))),
    }

    def build_payload(limits: dict[str, int], *, title_chars: int = 160, include_activity_messages: bool = False) -> dict[str, Any]:
        trend_limit = limits["engagement_trend"]
        trend_source = _safe_list(charts.get("engagement_trend"))
        trend_rows = trend_source[-trend_limit:] if trend_limit > 0 else []
        trend = [
            {
                "date": _clean_text(row.get("date"), max_chars=20),
                "views": _safe_int(row.get("views") or row.get("video_plays"), 0),
                "engagement": _safe_int(row.get("engagement"), 0),
                "completions": _safe_int(row.get("completions"), 0),
            }
            for row in trend_rows
            if isinstance(row, dict)
        ]
        top_lessons = [
            _compact_lesson_row(row, title_chars=title_chars)
            for row in _safe_list(tables.get("top_lessons"))[: limits["top_lessons"]]
            if isinstance(row, dict)
        ]
        recent_lessons = [
            _compact_lesson_row(row, title_chars=title_chars)
            for row in _safe_list(tables.get("recent_lessons"))[: limits["recent_lessons"]]
            if isinstance(row, dict)
        ]
        categories_source = _safe_list(tables.get("top_categories") or charts.get("category_popularity"))
        categories = [
            {
                "category_slug": _clean_text(row.get("category_slug") or row.get("slug"), max_chars=80),
                "category_name": _clean_text(row.get("category_name") or row.get("name") or "Uncategorized", max_chars=title_chars),
                "lesson_count": _safe_int(row.get("lesson_count") or row.get("lessons"), 0),
                "views": _safe_int(row.get("views") or row.get("video_plays"), 0),
                "average_progress": _safe_float(row.get("average_progress"), 0.0),
                "completion_rate": _safe_float(row.get("completion_rate"), 0.0),
                "likes": _safe_int(row.get("likes"), 0),
                "comments": _safe_int(row.get("comments"), 0),
                "engagement_events": _safe_int(row.get("engagement_events") or row.get("engagement"), 0),
                "estimated_watch_minutes": _safe_float(row.get("estimated_watch_minutes"), 0.0),
            }
            for row in categories_source[: limits["top_categories"]]
            if isinstance(row, dict)
        ]
        activity = []
        for row in _safe_list(payload.get("recent_activity"))[: limits["recent_activity"]]:
            if not isinstance(row, dict):
                continue
            item = {
                "type": _clean_text(row.get("type"), max_chars=40),
                "label": _clean_text(row.get("label"), max_chars=60),
                "timestamp": _clean_text(row.get("timestamp"), max_chars=40),
                "lesson_id": _safe_int(row.get("lesson_id"), 0),
                "lesson_title": _clean_text(row.get("lesson_title") or row.get("title"), max_chars=title_chars),
                "value": row.get("value") if isinstance(row.get("value"), (int, float, str, bool)) else None,
            }
            if include_activity_messages:
                item["message"] = _generic_activity_message(item)
            activity.append(item)
        comment_limit = limits.get("recent_comments", 0)
        comments = []
        for row in _safe_list(feedback.get("recent_comments"))[:comment_limit]:
            if not isinstance(row, dict):
                continue
            comments.append(
                {
                    "lesson_title": _clean_text(row.get("lesson_title") or row.get("title"), max_chars=title_chars),
                    "text": _sanitize_feedback_text(row.get("text"), max_chars=320),
                }
            )
        return _scrub_private(
            {
                "summary": summary,
                "charts": {
                    "engagement_trend": trend,
                    "category_popularity": categories,
                },
                "tables": {
                    "top_lessons": top_lessons,
                    "recent_lessons": recent_lessons,
                    "top_categories": categories,
                },
                "recent_activity": activity,
                "qualitative_feedback": {
                    "recent_comments": comments,
                    "truncated": bool(feedback.get("truncated")),
                    "limit": _safe_int(feedback.get("limit"), len(comments)),
                    "max_comment_chars": _safe_int(feedback.get("max_comment_chars"), 0),
                },
                "lesson_quality": _compact_lesson_quality(lesson_quality, title_chars=title_chars),
                "filters": filters,
                "meta": {
                    "contract": _clean_text(meta.get("contract"), max_chars=60),
                    "scope": _clean_text(meta.get("scope"), max_chars=40),
                    "estimated_metrics": bool(meta.get("estimated_metrics")),
                    "comment_feedback_truncated": bool(meta.get("comment_feedback_truncated")),
                    "estimated_fields": _scrub_private(_safe_list(meta.get("estimated_fields"))[:12]),
                    "missing_metrics": _scrub_private(_safe_list(meta.get("missing_metrics"))[:12]),
                },
            }
        )

    original_safe = _safe_analytics_payload(payload)
    original_chars = len(json.dumps(original_safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    candidates = [
        ({"engagement_trend": 90, "top_lessons": 60, "recent_lessons": 30, "top_categories": 30, "recent_activity": 60, "recent_comments": 50, "weak_lessons": 10, "strong_lessons": 5}, 160),
        ({"engagement_trend": 45, "top_lessons": 30, "recent_lessons": 20, "top_categories": 20, "recent_activity": 30, "recent_comments": 30, "weak_lessons": 10, "strong_lessons": 5}, 140),
        ({"engagement_trend": 30, "top_lessons": 15, "recent_lessons": 10, "top_categories": 10, "recent_activity": 20, "recent_comments": 20, "weak_lessons": 10, "strong_lessons": 5}, 120),
        ({"engagement_trend": 14, "top_lessons": 8, "recent_lessons": 6, "top_categories": 8, "recent_activity": 12, "recent_comments": 12, "weak_lessons": 8, "strong_lessons": 5}, 100),
        ({"engagement_trend": 7, "top_lessons": 5, "recent_lessons": 3, "top_categories": 5, "recent_activity": 6, "recent_comments": 6, "weak_lessons": 5, "strong_lessons": 3}, 90),
        ({"engagement_trend": 0, "top_lessons": 3, "recent_lessons": 0, "top_categories": 3, "recent_activity": 0, "recent_comments": 3, "weak_lessons": 3, "strong_lessons": 2}, 80),
    ]
    chosen = build_payload(candidates[0][0], title_chars=candidates[0][1])
    chosen_limits = candidates[0][0]
    chosen_chars = len(json.dumps(chosen, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    for limits, title_chars in candidates:
        candidate = build_payload(limits, title_chars=title_chars)
        candidate_chars = len(json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        chosen = candidate
        chosen_limits = limits
        chosen_chars = candidate_chars
        if candidate_chars <= max(1, max_chars):
            break

    omitted_counts = {
        key: max(0, source_counts.get(key, 0) - chosen_limits.get(key, 0))
        for key in source_counts
    }
    input_truncated = original_chars > max_chars or any(value > 0 for value in omitted_counts.values())
    return chosen, {
        "input_truncated": input_truncated,
        "original_char_count": original_chars,
        "compact_char_count": chosen_chars,
        "max_chars": max_chars,
        "omitted_counts": omitted_counts,
    }


def _compact_lesson_row(row: dict[str, Any], *, title_chars: int) -> dict[str, Any]:
    payload = {
        "lesson_id": _safe_int(row.get("lesson_id") or row.get("id"), 0),
        "id": _safe_int(row.get("id") or row.get("lesson_id"), 0),
        "title": _clean_text(row.get("title") or row.get("name") or "Untitled lesson", max_chars=title_chars),
        "category_slug": _clean_text(row.get("category_slug"), max_chars=80),
        "category_name": _clean_text(row.get("category_name") or "Uncategorized", max_chars=title_chars),
        "status": _clean_text(row.get("status"), max_chars=30),
        "is_published": bool(row.get("is_published")),
        "created_at": _clean_text(row.get("created_at"), max_chars=40),
        "latest_activity_at": _clean_text(row.get("latest_activity_at"), max_chars=40),
        "views": _safe_int(row.get("views") or row.get("video_plays"), 0),
        "video_plays": _safe_int(row.get("video_plays") or row.get("views"), 0),
        "unique_viewers": _safe_int(row.get("unique_viewers"), 0),
        "average_progress": _safe_float(row.get("average_progress") or row.get("progress_pct"), 0.0),
        "progress_pct": _safe_float(row.get("progress_pct") or row.get("average_progress"), 0.0),
        "completion_rate": _safe_float(row.get("completion_rate") or row.get("completion_pct"), 0.0),
        "completion_pct": _safe_float(row.get("completion_pct") or row.get("completion_rate"), 0.0),
        "likes": _safe_int(row.get("likes"), 0),
        "comments": _safe_int(row.get("comments"), 0),
        "engagement_events": _safe_int(row.get("engagement_events"), 0),
        "estimated_watch_minutes": _safe_float(row.get("estimated_watch_minutes"), 0.0),
        "has_cover": bool(row.get("has_cover")),
        "missing_cover": bool(row.get("missing_cover")),
    }
    if isinstance(row.get("lesson_intelligence"), dict):
        payload["lesson_intelligence"] = _scrub_private(row.get("lesson_intelligence"))
    return payload


def _compact_lesson_quality(lesson_quality: dict[str, Any], *, title_chars: int) -> dict[str, Any]:
    weak_lessons = [
        _compact_lesson_row(row, title_chars=title_chars)
        for row in _safe_list(lesson_quality.get("weak_lessons"))[:10]
        if isinstance(row, dict)
    ]
    strong_lessons = [
        _compact_lesson_row(row, title_chars=title_chars)
        for row in _safe_list(lesson_quality.get("strong_lessons"))[:5]
        if isinstance(row, dict)
    ]
    return _scrub_private(
        {
            "weak_lessons": weak_lessons,
            "strong_lessons": strong_lessons,
            "missing_cover_count": _safe_int(lesson_quality.get("missing_cover_count"), 0),
            "with_cover_count": _safe_int(lesson_quality.get("with_cover_count"), 0),
            "lesson_intelligence_report_count": _safe_int(lesson_quality.get("lesson_intelligence_report_count"), 0),
            "lesson_intelligence_hash": _clean_text(lesson_quality.get("lesson_intelligence_hash"), max_chars=80),
            "limitations": _scrub_private(_safe_list(lesson_quality.get("limitations"))[:4]),
        }
    )


def _safe_analytics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = _safe_json_dict(payload.get("summary"))
    charts = _safe_json_dict(payload.get("charts"))
    tables = _safe_json_dict(payload.get("tables"))
    filters = _safe_json_dict(payload.get("filters"))
    meta = _safe_json_dict(payload.get("meta"))
    feedback = _safe_json_dict(payload.get("qualitative_feedback"))
    lesson_quality = _safe_json_dict(payload.get("lesson_quality"))
    return {
        "summary": _scrub_private(summary),
        "charts": {
            "engagement_trend": _scrub_private(_safe_list(charts.get("engagement_trend"))[-120:]),
            "category_popularity": _scrub_private(_safe_list(charts.get("category_popularity"))[:30]),
        },
        "tables": {
            "top_lessons": _scrub_private(_safe_list(tables.get("top_lessons"))[:60]),
            "recent_lessons": _scrub_private(_safe_list(tables.get("recent_lessons"))[:30]),
            "top_categories": _scrub_private(_safe_list(tables.get("top_categories"))[:30]),
        },
        "recent_activity": _scrub_private(_safe_list(payload.get("recent_activity"))[:60]),
        "qualitative_feedback": {
            "recent_comments": [
                _scrub_private(
                    {
                        **row,
                        "text": _sanitize_feedback_text(row.get("text"), max_chars=320),
                    }
                )
                for row in _safe_list(feedback.get("recent_comments"))[:100]
                if isinstance(row, dict)
            ],
            "truncated": bool(feedback.get("truncated")),
            "limit": _safe_int(feedback.get("limit"), 0),
            "max_comment_chars": _safe_int(feedback.get("max_comment_chars"), 0),
        },
        "lesson_quality": _compact_lesson_quality(lesson_quality, title_chars=160),
        "filters": _scrub_private(filters),
        "meta": {
            "contract": _clean_text(meta.get("contract"), max_chars=60),
            "scope": _clean_text(meta.get("scope"), max_chars=40),
            "estimated_metrics": bool(meta.get("estimated_metrics")),
            "comment_feedback_truncated": bool(meta.get("comment_feedback_truncated")),
            "estimated_fields": _scrub_private(_safe_list(meta.get("estimated_fields"))[:20]),
            "missing_metrics": _scrub_private(_safe_list(meta.get("missing_metrics"))[:20]),
        },
    }


def _normalize_provider_result(raw: Any, *, provider_name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AnalyticsIntelligenceProviderUnavailable("provider result must be a JSON object")
    summary = _clean_text(raw.get("analytics_summary") or raw.get("summary"), max_chars=1600)
    if not summary:
        fallback_messages = []
        for key in ("insights", "recommendations", "lesson_actions", "category_actions"):
            for item in _safe_json_list(raw.get(key)):
                if isinstance(item, dict):
                    message = _clean_text(item.get("message") or item.get("summary") or item.get("evidence"), max_chars=220)
                else:
                    message = _clean_text(item, max_chars=220)
                if message:
                    fallback_messages.append(message)
                    break
            if fallback_messages:
                break
        summary = fallback_messages[0] if fallback_messages else "Analytics guidance is available for the selected period."
    health_score = max(0, min(100, _safe_int(raw.get("health_score"), 50)))
    risk_level = str(raw.get("risk_level") or _risk_level(health_score)).strip().lower()
    if risk_level not in RISK_LEVELS:
        risk_level = _risk_level(health_score)
    return {
        "provider": str(raw.get("provider") or provider_name or "heuristic").strip().lower(),
        "analytics_summary": summary,
        "health_score": health_score,
        "risk_level": risk_level,
        "insights": _safe_json_list(raw.get("insights")),
        "recommendations": _safe_json_list(raw.get("recommendations")),
        "lesson_actions": _safe_json_list(raw.get("lesson_actions")),
        "category_actions": _safe_json_list(raw.get("category_actions")),
        "limitations": _safe_json_list(raw.get("limitations")),
        "metadata": _safe_json_dict(raw.get("metadata")),
    }


def _ollama_prompt(input_payload: dict[str, Any]) -> str:
    output_language = _output_language(input_payload)
    safe_payload = {
        "scope": input_payload.get("scope") or "creator",
        "date_range": input_payload.get("date_range") or {},
        "category_filter": input_payload.get("category_filter") or "",
        "analytics": input_payload.get("analytics") or {},
        "source_hash": input_payload.get("source_hash") or "",
        "detected_language": input_payload.get("detected_language") or "unknown",
        "output_language": output_language,
        "input_truncated": bool(input_payload.get("input_truncated")),
        "prompt_version": ANALYTICS_INTELLIGENCE_PROMPT_VERSION,
        "chunk": input_payload.get("chunk") if isinstance(input_payload.get("chunk"), dict) else {},
    }
    language_instruction = (
        "Respond in Turkish. Keep JSON keys in English, but all user-facing text values in Turkish. "
        if output_language == "tr"
        else "Respond in English. "
    )
    return (
        f"You are a publisher analytics analyst. Return JSON only. {language_instruction}"
        "No markdown. No code fences. No prose outside JSON. Keep keys exactly as provided. "
        "If unsure, use empty arrays while keeping the schema. "
        "Use only the analytics data provided. Do not invent trends, viewer identities, or private data. "
        "Treat this as aggregate strategy work, not transcript analysis: connect performance drops to lesson quality "
        "summaries, complexity, clarity warnings, missing examples or short narration signals, cover-image signals, "
        "and sanitized learner comments when those fields are present. "
        "Do not edit lessons, trigger rendering, or perform hidden actions. "
        "Return keys: analytics_summary, health_score, risk_level, insights, recommendations, "
        "lesson_actions, category_actions, limitations. "
        "health_score must be an integer 0-100. risk_level must be low, medium, or high. "
        "Keep analytics_summary under 150 words. Return max 4 insights, max 4 recommendations, "
        "max 4 lesson actions, and max 3 category actions. Each action should be a concise JSON object with type, message, and evidence when useful. "
        f"Analytics payload: {json.dumps(safe_payload, ensure_ascii=False)}"
    )


def _output_language(input_payload: dict[str, Any]) -> str:
    language = str(input_payload.get("output_language") or "").strip().lower()
    return language if language in {"tr", "en"} else "en"


def _ollama_repair_prompt(raw_response: str, *, output_language: str) -> str:
    language_instruction = (
        "Use Turkish for user-facing values. "
        if output_language == "tr"
        else "Use English for user-facing values. "
    )
    schema = (
        '{"analytics_summary":"short paragraph","health_score":50,"risk_level":"low|medium|high",'
        '"insights":[],"recommendations":[],"lesson_actions":[],"category_actions":[],"limitations":[]}'
    )
    return (
        "Convert the following response to valid JSON matching this schema. "
        "Return JSON only. No markdown. No code fences. Keep keys exactly as provided. "
        f"{language_instruction}If information is missing, use empty arrays or safe defaults.\n"
        f"Schema: {schema}\n"
        f"Response: {str(raw_response or '')[:4000]}"
    )


def _analytics_text(language: str, key: str, **kwargs: Any) -> str:
    if language == "tr":
        messages = {
            "comment_feedback_truncated": "Recent learner comments were capped or shortened before analysis.",
            "recent_comment_feedback": f"The latest {kwargs.get('count', 0)} comments add qualitative learner feedback to this analysis.",
            "large_dataset_limitation": "Veri seti büyük olduğu için bazı analiz satırları özetlendi veya çıkarıldı.",
            "no_lessons_insight": "Bu analiz kapsamında içerik üretici dersi bulunamadı.",
            "publish_first_lesson": "İlerleme, tamamlama, beğeni ve yorum sinyallerini ölçebilmek için odaklı bir ilk ders yayınlayın.",
            "define_category": "Gelecekte kategori analitiğini karşılaştırabilmek için ilk ders için net bir kategori seçin.",
            "no_activity_insight": "Bu tarih aralığında kayıtlı izleyici etkinliği yok.",
            "share_lessons": "Analitiğe göre içerik değiştirmeden önce yayınlanan dersleri paylaşın ve küçük bir test kitlesi davet edin.",
            "improve_discovery": "Öğrencilerin ders vaadini hızlı anlaması için başlıkları, kapakları ve kategorileri gözden geçirin.",
            "publish_drafts": "Gerçek etkileşim sinyalleri toplamak için güçlü taslakları yayına alın.",
            "activity_summary_insight": f"{kwargs.get('unique_viewers', 0)} tekil izleyiciden {kwargs.get('total_views', 0)} görüntüleme, {kwargs.get('engagement_events', 0)} kayıtlı etkileşim üretti.",
            "low_completion_insight": "Kayıtlı görüntülemeler için tamamlama oranı düşük.",
            "shorten_or_segment": "Uzun dersleri kısaltın veya net kontrol noktalarıyla daha küçük bölümlere ayırın.",
            "views_low_progress_insight": "Öğrenciler derslere başlıyor, ancak ortalama ilerleme zayıf.",
            "clearer_intro": "Ders ilerlemesini artırmak için daha net bir açılış vaadi ve erken bir somut örnek ekleyin.",
            "low_engagement_insight": "Görüntüleme var, ancak beğeni ve yorum henüz yok.",
            "prompt_engagement": "Dersin sonuna kısa bir özet sorusu veya pratik alıştırma ekleyin.",
            "strong_social_engagement": "Beğeni ve yorumlar mevcut görüntülemelere göre güçlü.",
            "match_interest_to_retention": "Öğrenciler tepki veriyor, ancak ilerleme daha düşük; en çok izlenen derslerde tempo ve örnekleri gözden geçirin.",
            "category_dominance": f"{kwargs.get('category', 'Bir kategori')} bu aralıkta içerik üretici analitiğine hakim.",
            "category_imbalance": "Kataloğu genişletmeden önce bu kategorinin ders stilini daha zayıf kategorilerle karşılaştırın.",
            "category_share": f"{kwargs.get('category', 'Bu kategori')} kategori etkinliğinin %{float(kwargs.get('share') or 0) * 100:.0f} kadarını oluşturuyor.",
            "review_top_lesson_style": "En güçlü dersin başlığını, açılışını, yapısını ve örneklerini gözden geçirip bu kalıpları zayıf derslerde yeniden kullanın.",
            "lesson_views_low_progress": "Bu ders görüntüleme alıyor ancak ilerleme veya tamamlama zayıf; giriş, tempo, örnekler ve kapanışı gözden geçirin.",
            "lesson_no_engagement": "Bu ders görüntüleme alıyor ancak beğeni veya yorum yok; bir özet sorusu veya pratik soru ekleyin.",
            "lesson_top_style": "Bu üst dersi, daha zayıf dersler için başlık, tempo ve yapı referansı olarak inceleyin.",
            "missing_category_breakdown": "Analitiğin daha net içerik fırsatları gösterebilmesi için ders kategorilerini ekleyin veya gözden geçirin.",
            "category_retention": "Bu kategoride etkinlik var ancak ilerleme zayıf; ders temposunu ve örnekleri gözden geçirin.",
            "privacy_limitation": "Analytics Intelligence çıktıları izleyici kimliklerini içermez.",
            "dropoff_limitation": "Ek izleme eklenmedikçe slayt veya sayfa bazlı düşüş verisi yoktur.",
            "heuristic_limitation": "Heuristik analiz deterministik toplu sinyalleri kullanır ve alan nüanslarını kaçırabilir.",
            "estimated_limitation": "İzleme süresi ve görüntüleme sayıları ilerleme kayıtlarından tahmin edilir.",
            "missing_metrics_limitation": f"Bazı metrikler mevcut değil: {kwargs.get('missing', '')}.",
        }
        return messages.get(key, key)
    messages = {
        "comment_feedback_truncated": "Recent learner comments were capped or shortened before analysis.",
        "recent_comment_feedback": f"The latest {kwargs.get('count', 0)} comments add qualitative learner feedback to this analysis.",
        "large_dataset_limitation": "Some analytics rows were omitted because the dataset was large.",
        "no_lessons_insight": "No creator lessons were found in this analytics scope.",
        "publish_first_lesson": "Publish a focused first lesson so progress, completion, likes, and comments can be measured.",
        "define_category": "Choose a clear category for the first lesson so future category analytics are easier to compare.",
        "no_activity_insight": "No viewer activity was recorded for this date range.",
        "share_lessons": "Share published lessons and invite a small test audience before changing content based on analytics.",
        "improve_discovery": "Review lesson titles, thumbnails, and categories so learners can quickly understand the lesson promise.",
        "publish_drafts": "Convert strong drafts into published lessons to start collecting real engagement signals.",
        "activity_summary_insight": f"{kwargs.get('total_views', 0)} views from {kwargs.get('unique_viewers', 0)} unique viewers produced {kwargs.get('engagement_events', 0)} recorded engagement events.",
        "low_completion_insight": "Completion rate is low for the recorded views.",
        "shorten_or_segment": "Shorten long lessons or split them into clearer segments with checkpoints.",
        "views_low_progress_insight": "Learners are starting lessons but average progress is weak.",
        "clearer_intro": "Add a clearer opening promise and an early concrete example to improve lesson progress.",
        "low_engagement_insight": "Views are present but likes and comments are still absent.",
        "prompt_engagement": "Add a short recap question or practical exercise near the end of the lesson.",
        "strong_social_engagement": "Likes and comments are strong relative to current views.",
        "match_interest_to_retention": "Learners are reacting, but progress is lower; review pacing and examples in the most-viewed lessons.",
        "category_dominance": f"{kwargs.get('category', 'One category')} dominates creator analytics in this range.",
        "category_imbalance": "Compare this category's lesson style with weaker categories before expanding the catalog.",
        "category_share": f"{kwargs.get('category', 'This category')} accounts for {float(kwargs.get('share') or 0):.0%} of category activity.",
        "review_top_lesson_style": "Review the strongest lesson's title, opening, structure, and examples, then reuse the patterns in weaker lessons.",
        "lesson_views_low_progress": "This lesson has views but weak progress or completion; review its intro, pacing, examples, and ending.",
        "lesson_no_engagement": "This lesson has views but no likes or comments; add a recap prompt or practical question.",
        "lesson_top_style": "Review this top lesson's title, pacing, and structure as a reference for weaker lessons.",
        "missing_category_breakdown": "Add or review lesson categories so analytics can show clearer content opportunities.",
        "category_retention": "Review lesson pacing and examples in this category; activity exists but progress is weak.",
        "privacy_limitation": "No viewer identities are included in Analytics Intelligence outputs.",
        "dropoff_limitation": "No per-slide or per-page drop-off is available unless that tracking is added later.",
        "heuristic_limitation": "Heuristic analysis uses deterministic aggregate signals and may miss domain nuance.",
        "estimated_limitation": "Watch time and view counts are estimated from progress records.",
        "missing_metrics_limitation": f"Some metrics are unavailable: {kwargs.get('missing', '')}.",
    }
    return messages.get(key, key)


def _format_percent_metric(value: Any) -> str:
    return f"{_bounded_percent(value):.0f}%"


def _analytics_completion_sentence(language: str, completion_rate: Any) -> str:
    completion = _format_percent_metric(completion_rate)
    if language == "tr":
        return f"Bu aralikta ders tamamlama orani {completion}."
    return f"About {completion} of learners completed these lessons."


def _analytics_progress_only_sentence(language: str, average_progress: Any) -> str:
    progress = _format_percent_metric(average_progress)
    if language == "tr":
        return f"Ortalama izleyici dersin {progress} kadarina ulasti."
    return f"The average viewer reached {progress} of the lesson."


def _analytics_progress_sentence(language: str, *, completion_rate: Any, average_progress: Any) -> str:
    completion = _format_percent_metric(completion_rate)
    progress = _format_percent_metric(average_progress)
    if language == "tr":
        return f"Dersleri tamamlama orani {completion}; ortalama izleyici dersin {progress} kadarina ulasti."
    return f"About {completion} of learners completed these lessons, while the average viewer reached {progress} of the lesson."


def _analytics_summary(**kwargs) -> str:
    total_lessons = kwargs["total_lessons"]
    published_lessons = kwargs["published_lessons"]
    total_views = kwargs["total_views"]
    unique_viewers = kwargs["unique_viewers"]
    watch_minutes = kwargs["watch_minutes"]
    completion_rate = kwargs["completion_rate"]
    average_progress = kwargs["average_progress"]
    likes = kwargs["likes"]
    comments = kwargs["comments"]
    has_activity = kwargs["has_activity"]
    output_language = kwargs.get("output_language") or "en"
    if output_language == "tr":
        if total_lessons <= 0:
            return "Bu kapsamda henüz içerik üretici dersi yok; bu nedenle analiz başlangıç önerileriyle sınırlı."
        if not has_activity:
            return (
                f"{total_lessons} dersin {published_lessons} tanesi yayında, ancak bu aralıkta kayıtlı izleyici "
                "etkinliği yok."
            )
        return (
            f"{total_lessons} dersin {published_lessons} tanesi yayında. Bu aralıkta {unique_viewers} tekil izleyiciden "
            f"{total_views} görüntüleme, yaklaşık {watch_minutes:.1f} dakika tahmini izleme, "
            f"{_analytics_progress_sentence(output_language, completion_rate=completion_rate, average_progress=average_progress)} "
            f"{likes} beğeni ve {comments} yorum var."
        )
    if total_lessons <= 0:
        return "No creator lessons are available in this scope yet, so analytics intelligence is limited to onboarding guidance."
    if not has_activity:
        return (
            f"{published_lessons} of {total_lessons} lessons are published, but this range has no recorded viewer "
            "activity yet."
        )
    return (
        f"{published_lessons} of {total_lessons} lessons are published. This range has {total_views} views from "
        f"{unique_viewers} unique viewers and about {watch_minutes:.1f} estimated watch minutes. "
        f"{_analytics_progress_sentence(output_language, completion_rate=completion_rate, average_progress=average_progress)} "
        f"{likes} likes, and {comments} comments."
    )


def _health_score(**kwargs) -> int:
    total_lessons = kwargs["total_lessons"]
    published_lessons = kwargs["published_lessons"]
    total_views = kwargs["total_views"]
    completion_rate = kwargs["completion_rate"]
    average_progress = kwargs["average_progress"]
    social_events = kwargs["social_events"]
    has_activity = kwargs["has_activity"]
    category_count = kwargs["category_count"]

    if total_lessons <= 0:
        return 24

    score = 35
    score += min(15, published_lessons * 5)
    if total_views > 0:
        score += 10
    score += min(20, int(round(completion_rate * 0.2)))
    score += min(15, int(round(average_progress * 0.15)))
    if social_events > 0:
        score += min(10, 3 + social_events)
    if category_count >= 2:
        score += 5
    if not has_activity:
        score = min(score, 40)
    if total_views >= 3 and completion_rate < 35:
        score -= 12
    if total_views >= 3 and average_progress < 45:
        score -= 8
    return max(0, min(100, int(score)))


def _risk_level(score: int) -> str:
    score = max(0, min(100, int(score or 0)))
    if score >= 70:
        return "low"
    if score >= 45:
        return "medium"
    return "high"


def _lesson_actions(
    top_lessons: list[dict[str, Any]],
    recent_lessons: list[dict[str, Any]],
    *,
    output_language: str,
) -> list[dict[str, Any]]:
    lesson_ids = [_safe_int(lesson.get("lesson_id") or lesson.get("id"), 0) for lesson in top_lessons + recent_lessons]
    report_lookup = _lesson_report_lookup([lesson_id for lesson_id in lesson_ids if lesson_id])
    actions: list[dict[str, Any]] = []

    for lesson in top_lessons:
        views = _safe_int(lesson.get("views") or lesson.get("video_plays"), 0)
        if views <= 0:
            continue
        progress = _bounded_percent(lesson.get("average_progress") or lesson.get("progress_pct"))
        completion = _bounded_percent(lesson.get("completion_rate") or lesson.get("completion_pct"))
        if progress >= 50 and (completion >= 35 or completion == 0):
            continue
        actions.append(
            _lesson_action(
                lesson,
                report_lookup,
                "views_low_progress",
                _analytics_text(output_language, "lesson_views_low_progress"),
                output_language=output_language,
            )
        )
        if len(actions) >= 6:
            break

    if len(actions) < 6:
        for lesson in top_lessons:
            views = _safe_int(lesson.get("views") or lesson.get("video_plays"), 0)
            social = _safe_int(lesson.get("likes"), 0) + _safe_int(lesson.get("comments"), 0)
            if views > 0 and social == 0:
                action = _lesson_action(
                    lesson,
                    report_lookup,
                    "no_engagement",
                    _analytics_text(output_language, "lesson_no_engagement"),
                    output_language=output_language,
                )
                if action not in actions:
                    actions.append(action)
            if len(actions) >= 6:
                break

    if top_lessons:
        top = top_lessons[0]
        if _safe_int(top.get("views") or top.get("video_plays"), 0) > 0:
            action = _lesson_action(
                top,
                report_lookup,
                "review_top_lesson_style",
                _analytics_text(output_language, "lesson_top_style"),
                output_language=output_language,
            )
            if action not in actions:
                actions.append(action)
    return actions[:8]


def _lesson_action(
    lesson: dict[str, Any],
    report_lookup: dict[int, int],
    action_type: str,
    message: str,
    *,
    output_language: str = "en",
) -> dict[str, Any]:
    lesson_id = _safe_int(lesson.get("lesson_id") or lesson.get("id"), 0)
    report_id = report_lookup.get(lesson_id)
    return {
        "type": action_type,
        "lesson_id": lesson_id,
        "lesson_title": _clean_text(lesson.get("title"), max_chars=160) or "Untitled lesson",
        "message": message,
        "metrics": {
            "views": _safe_int(lesson.get("views") or lesson.get("video_plays"), 0),
            "average_progress": _bounded_percent(lesson.get("average_progress") or lesson.get("progress_pct")),
            "completion_rate": _bounded_percent(lesson.get("completion_rate") or lesson.get("completion_pct")),
            "likes": _safe_int(lesson.get("likes"), 0),
            "comments": _safe_int(lesson.get("comments"), 0),
        },
        "has_lesson_intelligence_report": bool(report_id),
        "lesson_intelligence_report_id": report_id,
        "action_label": (
            "Ders önerilerini gözden geçir." if report_id else "Studio'da dersi analiz et."
        ) if output_language == "tr" else (
            "Review lesson suggestions." if report_id else "Analyze lesson in Studio."
        ),
    }


def _lesson_report_lookup(lesson_ids: list[int]) -> dict[int, int]:
    if not lesson_ids:
        return {}
    lookup: dict[int, int] = {}
    for report in (
        LessonIntelligenceReport.objects.filter(project_id__in=sorted(set(lesson_ids)))
        .only("id", "project_id")
        .order_by("project_id", "-created_at", "-id")
    ):
        if report.project_id not in lookup:
            lookup[report.project_id] = report.id
    return lookup


def _category_actions(
    categories: list[dict[str, Any]],
    *,
    has_activity: bool,
    output_language: str,
) -> list[dict[str, Any]]:
    if not categories:
        if has_activity:
            return [
                {
                    "type": "missing_category_breakdown",
                    "message": _analytics_text(output_language, "missing_category_breakdown"),
                }
            ]
        return []

    actions: list[dict[str, Any]] = []
    for category in categories[:4]:
        completion = _bounded_percent(category.get("completion_rate"))
        progress = _bounded_percent(category.get("average_progress"))
        signal = _category_signal(category)
        if signal > 0 and (completion < 35 or progress < 45):
            name = _clean_text(category.get("category_name") or category.get("name"), max_chars=120) or "Uncategorized"
            actions.append(
                {
                    "type": "category_retention",
                    "category": name,
                    "message": _analytics_text(output_language, "category_retention"),
                    "evidence": _analytics_progress_sentence(
                        output_language,
                        completion_rate=completion,
                        average_progress=progress,
                    ),
                }
            )
    return actions


def _dominant_category(categories: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(categories) < 2:
        return None
    signals = [_category_signal(category) for category in categories]
    total = sum(signals)
    if total <= 0:
        return None
    share = signals[0] / total
    if share < 0.7:
        return None
    dominant = dict(categories[0])
    dominant["_share"] = share
    return dominant


def _category_signal(category: dict[str, Any]) -> int:
    return max(
        _safe_int(category.get("views") or category.get("total_views") or category.get("video_plays"), 0),
        _safe_int(category.get("engagement_events") or category.get("engagement"), 0),
        _safe_int(category.get("lesson_count") or category.get("lessons"), 0),
    )


def _base_limitations(meta: dict[str, Any], *, output_language: str = "en") -> list[str]:
    limitations = [
        _analytics_text(output_language, "privacy_limitation"),
        _analytics_text(output_language, "dropoff_limitation"),
        _analytics_text(output_language, "heuristic_limitation"),
    ]
    if meta.get("estimated_metrics"):
        limitations.insert(0, _analytics_text(output_language, "estimated_limitation"))
    missing_metrics = _safe_list(meta.get("missing_metrics"))
    if missing_metrics:
        missing = ", ".join(_clean_text(item, max_chars=60) for item in missing_metrics[:6])
        limitations.append(_analytics_text(output_language, "missing_metrics_limitation", missing=missing))
    return limitations


def _insight(insight_type: str, severity: str, message: str, evidence: str = "") -> dict[str, str]:
    return {
        "type": insight_type,
        "severity": severity,
        "message": message,
        "evidence": evidence,
    }


def _recommendation(recommendation_type: str, priority: str, message: str) -> dict[str, str]:
    return {
        "type": recommendation_type,
        "priority": priority,
        "message": message,
    }


def _dedupe_by_message(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _clean_text(item.get("message") or item.get("type"), max_chars=300).lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _json_object_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned:
        raise AnalyticsIntelligenceProviderUnavailable("empty provider response")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        for start in (index for index, char in enumerate(cleaned) if char == "{"):
            try:
                data, _ = decoder.raw_decode(cleaned[start:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise AnalyticsIntelligenceProviderUnavailable("provider response was not JSON") from exc
    if not isinstance(data, dict):
        raise AnalyticsIntelligenceProviderUnavailable("provider JSON must be an object")
    return data


def _ollama_stage_for_exception(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, HTTPError):
        return "http"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
            return "timeout"
        return "http"
    if isinstance(exc, (OSError, json.JSONDecodeError)):
        text = str(exc).lower()
        if "timed out" in text or "timeout" in text:
            return "timeout"
        return "json" if isinstance(exc, json.JSONDecodeError) else "http"
    return "validation"


def _ollama_parse_stage(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "invalid json" in text or "not json" in text or "was not json" in text or "json response" in text:
        return "json"
    if "missing" in text or "must be" in text:
        return "validation"
    return _ollama_stage_for_exception(exc)


def _ollama_safe_reason(stage: str, exc: Exception | str | None = None) -> str:
    if stage == "timeout":
        return "Ollama timed out."
    if stage == "json":
        return "Ollama returned invalid JSON."
    if stage == "validation":
        return "Ollama response missed required fields."
    if stage == "http":
        return "Ollama request failed."
    return safe_response_preview(exc, limit=120) or "Ollama enhancement failed."


def _ollama_failure_diagnostic(
    exc: Exception | str,
    *,
    stage: str,
    model: str,
    elapsed_seconds: float,
    retry_count: int,
    response_preview: str = "",
    repair_error: Exception | None = None,
) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "model": str(model or ""),
        "error_class": exc.__class__.__name__ if isinstance(exc, Exception) else "ProviderError",
        "parse_stage": stage,
        "reason": safe_response_preview(exc, limit=120),
        "safe_reason": _ollama_safe_reason(stage, exc),
        "elapsed_seconds": round(float(elapsed_seconds or 0.0), 2),
        "retry_count": int(retry_count or 0),
    }
    if isinstance(exc, HTTPError):
        diagnostic["error_code"] = int(exc.code)
    if response_preview:
        diagnostic["response_preview"] = safe_response_preview(response_preview)
    if repair_error is not None:
        diagnostic["repair_error_class"] = repair_error.__class__.__name__
        diagnostic["repair_reason"] = safe_response_preview(repair_error, limit=120)
    return {key: value for key, value in diagnostic.items() if value not in {"", None}}


def _diagnostic_from_exception(exc: Exception, *, model: str) -> dict[str, Any]:
    diagnostic = getattr(exc, "diagnostic", None)
    if isinstance(diagnostic, dict):
        return dict(diagnostic)
    stage = _ollama_parse_stage(exc)
    return _ollama_failure_diagnostic(
        exc,
        stage=stage,
        model=model,
        elapsed_seconds=0.0,
        retry_count=0,
    )


def _chunk_failure_diagnostic(current_chunk: dict[str, Any], diagnostic: dict[str, Any]) -> dict[str, Any]:
    payload = {
        **{key: value for key, value in diagnostic.items() if value not in {"", None}},
        "chunk_index": int(current_chunk.get("index") or 0),
        "chunk_count": int(current_chunk.get("count") or 0),
        "section": _clean_text(current_chunk.get("section"), max_chars=80),
        "item_count": _safe_int(current_chunk.get("item_count"), 0),
    }
    return payload


def _scrub_private(value: Any) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in PRIVATE_KEYS:
                continue
            if key_lower.endswith("_path") or key_lower.endswith("_url"):
                continue
            output[key_text] = _scrub_private(child)
        return output
    if isinstance(value, list):
        return [_scrub_private(item) for item in value]
    if isinstance(value, str):
        cleaned = _sanitize_feedback_text(value, max_chars=1200)
        lower = cleaned.lower()
        if "storage_local" in lower or "node_modules" in lower or ".vite" in lower or "c:/" in lower:
            return ""
        return cleaned
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean_text(value, max_chars=300)


def _clean_text(value: Any, *, max_chars: int = 500) -> str:
    if value is None:
        return ""
    text = str(value)
    text = CONTROL_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    if max_chars >= 0:
        text = text[:max_chars].strip()
    return text


def _sanitize_feedback_text(value: Any, *, max_chars: int = 500) -> str:
    text = _clean_text(value, max_chars=max_chars)
    if not text:
        return ""
    text = EMAIL_RE.sub("[email]", text)
    text = HANDLE_RE.sub("@[handle]", text)
    text = IDENTITY_LABEL_RE.sub("learner", text)
    return _clean_text(text, max_chars=max_chars)


def _generic_activity_message(item: dict[str, Any]) -> str:
    activity_type = _clean_text(item.get("type"), max_chars=40).lower()
    if activity_type == "progress":
        return f"A learner reached {int(_bounded_percent(item.get('value')))}% progress."
    if activity_type == "like":
        return "A learner liked a lesson."
    if activity_type == "comment":
        return "A learner commented."
    return "Learner activity was recorded."


def _bounded_percent(value: Any) -> float:
    numeric = _safe_float(value, 0.0)
    if 0 < numeric < 1:
        numeric *= 100.0
    return max(0.0, min(100.0, numeric))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return _scrub_private(value)
    return []


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _scrub_private(value)
    return {}


def _provider_attempt(provider: str, status_value: str, error: Any = "") -> dict[str, str]:
    payload = {"provider": str(provider), "status": str(status_value)}
    if error:
        payload["error"] = str(error)[:240]
    return payload


def _bool_setting(name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _string_setting(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or "").strip()


def _int_setting(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _float_setting(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _effective_sync_provider_timeout(configured_timeout: float, *, cap_setting: str) -> float:
    global_cap = _float_setting("INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS", 20.0, minimum=0.5, maximum=60.0)
    cap = _float_setting(cap_setting, global_cap, minimum=0.5, maximum=60.0)
    return min(float(configured_timeout), cap)


def _background_provider_timeout(*, cap_setting: str) -> float:
    global_timeout = _float_setting("INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS", 120.0, minimum=1.0, maximum=600.0)
    return _float_setting(cap_setting, global_timeout, minimum=1.0, maximum=600.0)
