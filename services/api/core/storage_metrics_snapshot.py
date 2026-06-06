"""Cached storage metrics snapshot helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.storage_adapter import get_storage_adapter
from core.storage_json import write_json_metadata_file
from core.storage_retention import build_storage_report


SNAPSHOT_REL_PATH = Path("observability") / "storage_metrics_snapshot.json"
SNAPSHOT_KEYS = (
    "total_storage_bytes",
    "retention_candidate_count",
    "orphan_candidate_count",
    "reclaimable_bytes_estimate",
    "generated_at",
)


@dataclass(frozen=True)
class StorageMetricsSnapshot:
    available: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    path: str = ""


def storage_metrics_snapshot_path(storage_root: str | Path | None = None) -> Path:
    return get_storage_adapter(storage_root).resolve_path(SNAPSHOT_REL_PATH)


def build_storage_metrics_snapshot(
    *,
    storage_root: str | Path | None = None,
    older_than_days: int = 30,
) -> dict[str, Any]:
    report = build_storage_report(storage_root=storage_root, older_than_days=older_than_days)
    retention_candidates = report.get("retention_candidates") or []
    orphan_candidates = report.get("orphan_candidates") or []
    reclaimable_bytes = sum(int(item.get("size_bytes") or 0) for item in [*retention_candidates, *orphan_candidates])
    return {
        "total_storage_bytes": int((report.get("capacity") or {}).get("total_bytes") or 0),
        "retention_candidate_count": len(retention_candidates),
        "orphan_candidate_count": len(orphan_candidates),
        "reclaimable_bytes_estimate": reclaimable_bytes,
        "generated_at": timezone.now().isoformat(),
    }


def write_storage_metrics_snapshot(
    *,
    storage_root: str | Path | None = None,
    older_than_days: int = 30,
) -> dict[str, Any]:
    snapshot = build_storage_metrics_snapshot(storage_root=storage_root, older_than_days=older_than_days)
    write_json_metadata_file(
        storage_root=storage_root,
        relative_path=SNAPSHOT_REL_PATH,
        payload=snapshot,
        sort_keys=True,
        trailing_newline=True,
    )
    return snapshot


def load_storage_metrics_snapshot(*, storage_root: str | Path | None = None) -> StorageMetricsSnapshot:
    adapter = get_storage_adapter(storage_root)
    path = adapter.resolve_path(SNAPSHOT_REL_PATH)
    try:
        raw = json.loads(adapter.read_text(SNAPSHOT_REL_PATH))
    except FileNotFoundError:
        return StorageMetricsSnapshot(available=False, warnings=["storage_metrics_snapshot_missing"], path=str(path))
    except (OSError, json.JSONDecodeError) as exc:
        return StorageMetricsSnapshot(
            available=False,
            warnings=[f"storage_metrics_snapshot_unavailable:{exc.__class__.__name__}"],
            path=str(path),
        )

    warnings = _snapshot_warnings(raw)
    if warnings:
        return StorageMetricsSnapshot(available=False, metrics=_zero_metrics(), warnings=warnings, path=str(path))
    return StorageMetricsSnapshot(available=True, metrics=_coerce_metrics(raw), path=str(path))


def _snapshot_warnings(raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        return ["storage_metrics_snapshot_invalid:payload_not_object"]
    missing = [key for key in SNAPSHOT_KEYS if key not in raw]
    if missing:
        return [f"storage_metrics_snapshot_invalid:missing_{','.join(missing)}"]
    invalid_numeric = []
    for key in ("total_storage_bytes", "retention_candidate_count", "orphan_candidate_count", "reclaimable_bytes_estimate"):
        try:
            int(raw.get(key) or 0)
        except (TypeError, ValueError):
            invalid_numeric.append(key)
    if invalid_numeric:
        return [f"storage_metrics_snapshot_invalid:nonnumeric_{','.join(invalid_numeric)}"]
    return []


def _coerce_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    metrics = _zero_metrics()
    for key in ("total_storage_bytes", "retention_candidate_count", "orphan_candidate_count", "reclaimable_bytes_estimate"):
        metrics[key] = max(int(raw.get(key) or 0), 0)
    generated_at = _parse_generated_at(raw.get("generated_at"))
    metrics["generated_at"] = generated_at.isoformat() if generated_at else ""
    metrics["generated_timestamp"] = generated_at.timestamp() if generated_at else 0
    metrics["age_seconds"] = max(0, int((timezone.now() - generated_at).total_seconds())) if generated_at else 0
    return metrics


def _zero_metrics() -> dict[str, Any]:
    return {
        "total_storage_bytes": 0,
        "retention_candidate_count": 0,
        "orphan_candidate_count": 0,
        "reclaimable_bytes_estimate": 0,
        "generated_at": "",
        "generated_timestamp": 0,
        "age_seconds": 0,
    }


def _parse_generated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = parse_datetime(value.strip())
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed
