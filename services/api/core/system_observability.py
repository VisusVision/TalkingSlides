"""Read-only system observability report helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from core.render_recovery import DEFAULT_MAX_AGE_HOURS, build_render_recovery_report
from core.storage_retention import build_storage_report


ACTIVE_RENDER_STATUSES = ("pending", "running")
ACTIVE_INTENT_STATUSES = ("pending", "claimed", "dispatched")


@dataclass(frozen=True)
class ObservabilitySection:
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    available: bool = True


def build_system_observability_report(
    *,
    storage_root: str | Path | None = None,
    retention_older_than_days: int = 30,
    recovery_max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    generated_at = timezone.now().isoformat()
    render = _safe_section("render", _render_metrics)
    intents = _safe_section("follow_up_intents", _intent_metrics)
    storage = _safe_section(
        "storage",
        lambda: _storage_metrics(storage_root=storage_root, older_than_days=retention_older_than_days),
    )
    recovery = _safe_section(
        "recovery",
        lambda: _recovery_metrics(max_age_hours=recovery_max_age_hours),
    )
    environment = _environment_warnings(storage_root=storage_root)

    return {
        "generated_at": generated_at,
        "mode": "read-only/report-only",
        "render": _section_payload(render),
        "follow_up_intents": _section_payload(intents),
        "storage": _section_payload(storage),
        "recovery": _section_payload(recovery),
        "environment_warnings": environment,
        "warnings": _combined_warnings(render, intents, storage, recovery, environment),
    }


def _safe_section(name: str, builder) -> ObservabilitySection:
    try:
        return builder()
    except (OperationalError, ProgrammingError) as exc:
        return ObservabilitySection(available=False, warnings=[f"{name}_database_unavailable:{exc.__class__.__name__}"])
    except Exception as exc:  # noqa: BLE001
        return ObservabilitySection(available=False, warnings=[f"{name}_unavailable:{exc.__class__.__name__}:{exc}"])


def _section_payload(section: ObservabilitySection) -> dict[str, Any]:
    return {
        "available": section.available,
        "metrics": dict(section.metrics),
        "warnings": list(section.warnings),
    }


def _combined_warnings(*sections_or_warnings) -> list[str]:
    warnings: list[str] = []
    for item in sections_or_warnings:
        if isinstance(item, ObservabilitySection):
            warnings.extend(item.warnings)
        else:
            warnings.extend(list(item or []))
    return warnings


def _render_metrics() -> ObservabilitySection:
    Job, _RenderFollowUpIntent = _load_models()
    active = Job.objects.filter(job_type="video_export", status__in=ACTIVE_RENDER_STATUSES)
    return ObservabilitySection(
        metrics={
            "active_render_count": active.count(),
            "pending_render_count": Job.objects.filter(job_type="video_export", status="pending").count(),
            "running_render_count": Job.objects.filter(job_type="video_export", status="running").count(),
            "failed_render_count": Job.objects.filter(job_type="video_export", status="failed").count(),
            "oldest_active_render_age_seconds": _oldest_age_seconds(active),
        }
    )


def _intent_metrics() -> ObservabilitySection:
    _Job, RenderFollowUpIntent = _load_models()
    active = RenderFollowUpIntent.objects.filter(status__in=ACTIVE_INTENT_STATUSES)
    return ObservabilitySection(
        metrics={
            "pending_intent_count": RenderFollowUpIntent.objects.filter(status="pending").count(),
            "claimed_intent_count": RenderFollowUpIntent.objects.filter(status="claimed").count(),
            "dispatched_intent_count": RenderFollowUpIntent.objects.filter(status="dispatched").count(),
            "oldest_intent_age_seconds": _oldest_age_seconds(active),
        }
    )


def _storage_metrics(*, storage_root: str | Path | None, older_than_days: int) -> ObservabilitySection:
    report = build_storage_report(
        storage_root=storage_root,
        older_than_days=older_than_days,
    )
    retention_candidates = report.get("retention_candidates") or []
    orphan_candidates = report.get("orphan_candidates") or []
    reclaimable_bytes = sum(int(item.get("size_bytes") or 0) for item in [*retention_candidates, *orphan_candidates])
    return ObservabilitySection(
        available=bool(report.get("db_available", True)),
        warnings=list(report.get("warnings") or []),
        metrics={
            "total_storage_size_bytes": int((report.get("capacity") or {}).get("total_bytes") or 0),
            "orphan_candidate_count": len(orphan_candidates),
            "retention_candidate_count": len(retention_candidates),
            "reclaimable_bytes_estimate": reclaimable_bytes,
        },
    )


def _recovery_metrics(*, max_age_hours: float) -> ObservabilitySection:
    report = build_render_recovery_report(dry_run=True, max_age_hours=max_age_hours)
    summary = report.summary()
    return ObservabilitySection(
        warnings=list(report.warnings),
        metrics={
            "recovery_candidate_count": int(summary.get("total_findings") or 0),
            "stale_render_count": int(summary.get("stuck_render_count") or 0),
            "stale_intent_count": int(summary.get("stuck_intent_count") or 0),
        },
    )


def _environment_warnings(*, storage_root: str | Path | None) -> list[str]:
    warnings: list[str] = []
    if not str(getattr(settings, "PROMETHEUS_METRICS_TOKEN", "") or "").strip():
        warnings.append("prometheus_metrics_token_not_configured")
    broker_url = str(getattr(settings, "CELERY_BROKER_URL", "") or "").strip()
    if not broker_url:
        warnings.append("celery_broker_url_not_configured")
    db_engine = str((getattr(settings, "DATABASES", {}).get("default") or {}).get("ENGINE") or "")
    if db_engine.endswith("sqlite3") and not bool(getattr(settings, "DEBUG", False)):
        warnings.append("production_database_uses_sqlite")

    root = Path(storage_root or getattr(settings, "STORAGE_ROOT", "storage_local"))
    try:
        resolved = root.expanduser().resolve()
    except OSError as exc:
        warnings.append(f"storage_root_unresolvable:{exc.__class__.__name__}")
        return warnings
    if not resolved.exists():
        warnings.append(f"storage_root_missing:{resolved}")
    elif not resolved.is_dir():
        warnings.append(f"storage_root_not_directory:{resolved}")
    return warnings


def _oldest_age_seconds(queryset) -> int:
    oldest = queryset.order_by("updated_at", "created_at").first()
    if oldest is None:
        return 0
    changed_at = getattr(oldest, "updated_at", None) or getattr(oldest, "created_at", None)
    if changed_at is None:
        return 0
    return max(0, int((timezone.now() - changed_at).total_seconds()))


def _load_models():
    from core.models import Job, RenderFollowUpIntent

    return Job, RenderFollowUpIntent
