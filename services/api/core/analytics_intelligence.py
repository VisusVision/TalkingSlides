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

from core.models import AnalyticsIntelligenceReport, LessonIntelligenceReport


logger = logging.getLogger(__name__)

RISK_LEVELS = {"low", "medium", "high"}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
WHITESPACE_RE = re.compile(r"\s+")
PRIVATE_KEYS = {
    "avatar_url",
    "email",
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

    def to_provider_payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "analytics": self.analytics_payload,
            "source_hash": self.source_hash,
            "input_chars": self.input_chars,
            "date_range": self.date_range,
            "category_filter": self.category_filter,
        }


class AnalyticsIntelligenceProvider(Protocol):
    provider_name: str

    def analyze_analytics(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class HeuristicAnalyticsIntelligenceProvider:
    provider_name = "heuristic"

    def analyze_analytics(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        analytics = input_payload.get("analytics") if isinstance(input_payload.get("analytics"), dict) else {}
        summary = analytics.get("summary") if isinstance(analytics.get("summary"), dict) else {}
        tables = analytics.get("tables") if isinstance(analytics.get("tables"), dict) else {}
        charts = analytics.get("charts") if isinstance(analytics.get("charts"), dict) else {}
        meta = analytics.get("meta") if isinstance(analytics.get("meta"), dict) else {}

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
        limitations = _base_limitations(meta)

        if total_lessons <= 0:
            insights.append(
                _insight(
                    "no_lessons",
                    "medium",
                    "No creator lessons were found in this analytics scope.",
                    "total_lessons=0",
                )
            )
            recommendations.extend(
                [
                    _recommendation(
                        "publish_first_lesson",
                        "high",
                        "Publish a focused first lesson so progress, completion, likes, and comments can be measured.",
                    ),
                    _recommendation(
                        "define_category",
                        "medium",
                        "Choose a clear category for the first lesson so future category analytics are easier to compare.",
                    ),
                ]
            )
        elif not has_activity:
            insights.append(
                _insight(
                    "no_activity",
                    "medium",
                    "No viewer activity was recorded for this date range.",
                    f"published_lessons={published_lessons}, views=0",
                )
            )
            recommendations.extend(
                [
                    _recommendation(
                        "share_lessons",
                        "high",
                        "Share published lessons and invite a small test audience before changing content based on analytics.",
                    ),
                    _recommendation(
                        "improve_discovery",
                        "medium",
                        "Review lesson titles, thumbnails, and categories so learners can quickly understand the lesson promise.",
                    ),
                ]
            )
            if draft_lessons > 0:
                recommendations.append(
                    _recommendation(
                        "publish_drafts",
                        "medium",
                        "Convert strong drafts into published lessons to start collecting real engagement signals.",
                    )
                )
        else:
            insights.append(
                _insight(
                    "activity_summary",
                    "low",
                    (
                        f"{total_views} views from {unique_viewers} unique viewers produced "
                        f"{engagement_events} recorded engagement events."
                    ),
                    f"completion_rate={completion_rate:.0f}%, average_progress={average_progress:.0f}%",
                )
            )

            if completion_rate > 0 and completion_rate < 35:
                insights.append(
                    _insight(
                        "low_completion",
                        "high",
                        "Completion rate is low for the recorded views.",
                        f"completion_rate={completion_rate:.0f}%",
                    )
                )
                recommendations.append(
                    _recommendation(
                        "shorten_or_segment",
                        "high",
                        "Shorten long lessons or split them into clearer segments with checkpoints.",
                    )
                )

            if average_progress > 0 and average_progress < 45:
                insights.append(
                    _insight(
                        "views_low_progress",
                        "high",
                        "Learners are starting lessons but average progress is weak.",
                        f"average_progress={average_progress:.0f}%",
                    )
                )
                recommendations.append(
                    _recommendation(
                        "clearer_intro",
                        "high",
                        "Add a clearer opening promise and an early concrete example to improve lesson progress.",
                    )
                )

            if total_views >= 3 and social_events == 0:
                insights.append(
                    _insight(
                        "low_engagement",
                        "medium",
                        "Views are present but likes and comments are still absent.",
                        f"views={total_views}, likes=0, comments=0",
                    )
                )
                recommendations.append(
                    _recommendation(
                        "prompt_engagement",
                        "medium",
                        "Add a short recap question or practical exercise near the end of the lesson.",
                    )
                )

            if social_events >= max(2, int(total_views * 0.3)) and total_views > 0:
                insights.append(
                    _insight(
                        "strong_social_engagement",
                        "low",
                        "Likes and comments are strong relative to current views.",
                        f"likes={likes}, comments={comments}, views={total_views}",
                    )
                )
                if average_progress < 55:
                    recommendations.append(
                        _recommendation(
                            "match_interest_to_retention",
                            "medium",
                            "Learners are reacting, but progress is lower; review pacing and examples in the most-viewed lessons.",
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
                        f"{category_name} dominates creator analytics in this range.",
                        f"category_share={share:.0%}",
                    )
                )
                category_actions.append(
                    {
                        "type": "category_imbalance",
                        "category": category_name,
                        "message": "Compare this category's lesson style with weaker categories before expanding the catalog.",
                        "evidence": f"{category_name} accounts for {share:.0%} of category activity.",
                    }
                )

            if not recommendations:
                recommendations.append(
                    _recommendation(
                        "review_top_lesson_style",
                        "medium",
                        "Review the strongest lesson's title, opening, structure, and examples, then reuse the patterns in weaker lessons.",
                    )
                )

        recommendations = _dedupe_by_message(recommendations)
        lesson_actions.extend(_lesson_actions(top_lessons, recent_lessons))
        category_actions.extend(_category_actions(categories, has_activity=has_activity))

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
                "total_lessons": total_lessons,
                "published_lessons": published_lessons,
                "total_views": total_views,
                "estimated_watch_time_minutes": watch_minutes,
                "category_count": len(categories),
            },
        }


class OllamaAnalyticsIntelligenceProvider:
    provider_name = "ollama"

    def __init__(self) -> None:
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

    def analyze_analytics(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise AnalyticsIntelligenceProviderUnavailable("Ollama base URL is not configured")
        if not self.model:
            raise AnalyticsIntelligenceProviderUnavailable("Ollama analytics intelligence model is not configured")

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
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
            data = json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise AnalyticsIntelligenceProviderUnavailable(f"Ollama request failed: {exc.__class__.__name__}") from exc

        if not isinstance(data, dict):
            raise AnalyticsIntelligenceProviderUnavailable("Ollama response must be a JSON object")
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
        }
        return normalized


class PaidAnalyticsIntelligenceProvider:
    """Placeholder for later paid-provider support. It never calls externally."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = str(provider_name or "external").strip().lower() or "external"

    def analyze_analytics(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not _bool_setting("ANALYTICS_INTELLIGENCE_ALLOW_EXTERNAL", False):
            raise AnalyticsIntelligenceProviderUnavailable("external analytics intelligence providers are disabled")
        raise AnalyticsIntelligenceProviderUnavailable(
            f"{self.provider_name} analytics intelligence provider is not implemented"
        )


def analytics_intelligence_enabled() -> bool:
    return _bool_setting("ANALYTICS_INTELLIGENCE_ENABLED", True)


def analytics_provider_chain_from_settings() -> list[str]:
    raw = _string_setting("ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN", "")
    if not raw:
        raw = _string_setting("ANALYTICS_INTELLIGENCE_PROVIDER", "heuristic")
    providers = [item.strip().lower() for item in re.split(r"[\s,]+", raw) if item.strip()]
    if not providers:
        providers = ["heuristic"]
    if "heuristic" not in providers:
        providers.append("heuristic")
    return providers


def get_analytics_intelligence_provider(provider_name: str) -> AnalyticsIntelligenceProvider:
    provider = str(provider_name or "heuristic").strip().lower()
    if provider == "heuristic":
        return HeuristicAnalyticsIntelligenceProvider()
    if provider == "ollama":
        return OllamaAnalyticsIntelligenceProvider()
    if provider in {"openai", "anthropic", "azure_openai", "external", "paid"}:
        return PaidAnalyticsIntelligenceProvider(provider)
    raise AnalyticsIntelligenceProviderUnavailable(f"unknown analytics intelligence provider: {provider}")


def build_analytics_intelligence_input(
    requested_by,
    analytics_payload: dict[str, Any],
    *,
    scope: str = "creator",
    max_chars: int | None = None,
) -> AnalyticsIntelligenceInput:
    if not isinstance(analytics_payload, dict):
        raise AnalyticsIntelligenceInputError("Analytics payload is empty.")

    limit = int(max_chars if max_chars is not None else _int_setting("ANALYTICS_INTELLIGENCE_MAX_INPUT_CHARS", 20000))
    safe_payload = _safe_analytics_payload(analytics_payload)
    source_json = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    input_chars = len(source_json)
    if input_chars > limit:
        raise AnalyticsIntelligenceInputTooLarge(
            f"Analytics payload is too large for synchronous analysis ({input_chars} chars, limit {limit})."
        )
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
        }
        return normalized

    fallback_provider = HeuristicAnalyticsIntelligenceProvider()
    fallback = _normalize_provider_result(fallback_provider.analyze_analytics(input_payload), provider_name="heuristic")
    attempts.append(_provider_attempt("heuristic", "success"))
    fallback["provider_chain"] = provider_chain
    fallback["fallback_used"] = True
    fallback["metadata"] = {
        **dict(fallback.get("metadata") or {}),
        "provider_chain_attempts": attempts,
        "source_hash": analytics_input.source_hash,
    }
    return fallback


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
) -> dict[str, Any]:
    if report is None:
        return {
            "enabled": enabled,
            "status": "empty" if enabled else "disabled",
            "provider": "",
            "fallback_used": False,
            "summary": "",
            "health_score": 0,
            "risk_level": "",
            "insights": [],
            "recommendations": [],
            "lesson_actions": [],
            "category_actions": [],
            "limitations": [],
        }
    return {
        "enabled": enabled,
        "id": report.id,
        "status": report.status,
        "provider": report.provider,
        "provider_chain": report.provider_chain if isinstance(report.provider_chain, list) else [],
        "fallback_used": bool(report.fallback_used),
        "source_hash": report.source_hash,
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
                "input_char_count",
                "total_lessons",
                "published_lessons",
                "total_views",
                "category_count",
            }
        },
        "error_message": report.error_message,
        "created_at": report.created_at.isoformat() if report.created_at else "",
        "updated_at": report.updated_at.isoformat() if report.updated_at else "",
    }


def _safe_analytics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = _safe_json_dict(payload.get("summary"))
    charts = _safe_json_dict(payload.get("charts"))
    tables = _safe_json_dict(payload.get("tables"))
    filters = _safe_json_dict(payload.get("filters"))
    meta = _safe_json_dict(payload.get("meta"))
    return {
        "summary": _scrub_private(summary),
        "charts": {
            "engagement_trend": _scrub_private(_safe_list(charts.get("engagement_trend"))[-90:]),
            "category_popularity": _scrub_private(_safe_list(charts.get("category_popularity"))[:12]),
        },
        "tables": {
            "top_lessons": _scrub_private(_safe_list(tables.get("top_lessons"))[:20]),
            "recent_lessons": _scrub_private(_safe_list(tables.get("recent_lessons"))[:20]),
            "top_categories": _scrub_private(_safe_list(tables.get("top_categories"))[:12]),
        },
        "recent_activity": _scrub_private(_safe_list(payload.get("recent_activity"))[:30]),
        "filters": _scrub_private(filters),
        "meta": {
            "contract": _clean_text(meta.get("contract"), max_chars=60),
            "scope": _clean_text(meta.get("scope"), max_chars=40),
            "estimated_metrics": bool(meta.get("estimated_metrics")),
            "estimated_fields": _scrub_private(_safe_list(meta.get("estimated_fields"))[:20]),
            "missing_metrics": _scrub_private(_safe_list(meta.get("missing_metrics"))[:20]),
        },
    }


def _normalize_provider_result(raw: Any, *, provider_name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AnalyticsIntelligenceProviderUnavailable("provider result must be a JSON object")
    summary = _clean_text(raw.get("analytics_summary") or raw.get("summary"), max_chars=1600)
    if not summary:
        raise AnalyticsIntelligenceProviderUnavailable("provider result missing analytics summary")
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
    safe_payload = {
        "scope": input_payload.get("scope") or "creator",
        "date_range": input_payload.get("date_range") or {},
        "category_filter": input_payload.get("category_filter") or "",
        "analytics": input_payload.get("analytics") or {},
        "source_hash": input_payload.get("source_hash") or "",
    }
    return (
        "You are a publisher analytics analyst. Return JSON only. "
        "Use only the analytics data provided. Do not invent trends, viewer identities, or private data. "
        "Do not edit lessons, trigger rendering, or perform hidden actions. "
        "Return keys: analytics_summary, health_score, risk_level, insights, recommendations, "
        "lesson_actions, category_actions, limitations. "
        "health_score must be an integer 0-100. risk_level must be low, medium, or high. "
        "Each insight/recommendation/action should be a concise JSON object with type, message, and evidence when useful. "
        f"Analytics payload: {json.dumps(safe_payload, ensure_ascii=False)}"
    )


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
    if total_lessons <= 0:
        return "No creator lessons are available in this scope yet, so analytics intelligence is limited to onboarding guidance."
    if not has_activity:
        return (
            f"{published_lessons} of {total_lessons} lessons are published, but this range has no recorded viewer "
            "activity yet."
        )
    return (
        f"{published_lessons} of {total_lessons} lessons are published. This range has {total_views} views from "
        f"{unique_viewers} unique viewers, about {watch_minutes:.1f} estimated watch minutes, "
        f"{completion_rate:.0f}% completion, {average_progress:.0f}% average progress, "
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


def _lesson_actions(top_lessons: list[dict[str, Any]], recent_lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                "This lesson has views but weak progress or completion; review its intro, pacing, examples, and ending.",
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
                    "This lesson has views but no likes or comments; add a recap prompt or practical question.",
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
                "Review this top lesson's title, pacing, and structure as a reference for weaker lessons.",
            )
            if action not in actions:
                actions.append(action)
    return actions[:8]


def _lesson_action(
    lesson: dict[str, Any],
    report_lookup: dict[int, int],
    action_type: str,
    message: str,
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
        "action_label": "Review lesson suggestions." if report_id else "Analyze lesson in Studio.",
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


def _category_actions(categories: list[dict[str, Any]], *, has_activity: bool) -> list[dict[str, Any]]:
    if not categories:
        if has_activity:
            return [
                {
                    "type": "missing_category_breakdown",
                    "message": "Add or review lesson categories so analytics can show clearer content opportunities.",
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
                    "message": "Review lesson pacing and examples in this category; activity exists but progress is weak.",
                    "evidence": f"completion={completion:.0f}%, progress={progress:.0f}%",
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


def _base_limitations(meta: dict[str, Any]) -> list[str]:
    limitations = [
        "No viewer identities are included in Analytics Intelligence outputs.",
        "No per-slide or per-page drop-off is available unless that tracking is added later.",
        "Heuristic analysis uses deterministic aggregate signals and may miss domain nuance.",
    ]
    if meta.get("estimated_metrics"):
        limitations.insert(0, "Watch time and view counts are estimated from progress records.")
    missing_metrics = _safe_list(meta.get("missing_metrics"))
    if missing_metrics:
        limitations.append("Some metrics are unavailable: " + ", ".join(_clean_text(item, max_chars=60) for item in missing_metrics[:6]) + ".")
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
    if not cleaned:
        raise AnalyticsIntelligenceProviderUnavailable("empty provider response")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AnalyticsIntelligenceProviderUnavailable("provider response was not JSON")
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise AnalyticsIntelligenceProviderUnavailable("provider JSON must be an object")
    return data


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
        cleaned = _clean_text(value, max_chars=1200)
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
