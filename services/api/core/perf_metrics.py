from __future__ import annotations

import bisect
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.conf import settings

from core import system_observability

RECENT_WINDOW_SECONDS = 30 * 60
MAX_RECENT_SAMPLES = 5000

_QUEUE_WAIT_RECENT_KEY = "perf:queue_wait_recent"
_ENQUEUE_LATENCY_RECENT_KEY = "perf:enqueue_latency_recent"
_WORKER_DURATION_RECENT_KEY = "perf:worker_duration_recent"
_WORKER_RETRY_TOTAL_KEY = "worker_task_retries_total"
_WORKER_FAILURE_TOTAL_KEY = "worker_task_failures_total"
_RETRY_STORM_PREVENTED_KEY = "retry_storm_prevented_total"
_API_5XX_TOTAL_KEY = "api_5xx_total"
_DEFERRED_RENDER_REQUESTS_KEY = "deferred_render_requests_total"
_REDIS_HIT_TOTAL_KEY = "redis_cache_hits_total"
_REDIS_MISS_TOTAL_KEY = "redis_cache_misses_total"


@dataclass(frozen=True)
class SummarySnapshot:
    p50: float
    p95: float
    p99: float
    count: int
    sum_value: float


def _now_ts() -> float:
    return time.time()


def _pruned_samples(key: str) -> deque[tuple[float, float]]:
    raw = cache.get(key) or []
    now = _now_ts()
    floor = now - RECENT_WINDOW_SECONDS
    dq: deque[tuple[float, float]] = deque(maxlen=MAX_RECENT_SAMPLES)
    for item in raw[-MAX_RECENT_SAMPLES:]:
        try:
            ts, value = float(item[0]), float(item[1])
        except Exception:
            continue
        if ts >= floor and math.isfinite(value):
            dq.append((ts, value))
    return dq


def _store_samples(key: str, dq: deque[tuple[float, float]]) -> None:
    cache.set(key, list(dq), timeout=60 * 60 * 24)


def observe_recent_value(key: str, value: float) -> None:
    try:
        numeric = max(float(value), 0.0)
    except Exception:
        return
    dq = _pruned_samples(key)
    dq.append((_now_ts(), numeric))
    _store_samples(key, dq)


def summary_from_recent(key: str) -> SummarySnapshot:
    dq = _pruned_samples(key)
    values = sorted(v for _, v in dq)
    if not values:
        return SummarySnapshot(p50=0.0, p95=0.0, p99=0.0, count=0, sum_value=0.0)
    return SummarySnapshot(
        p50=_quantile(values, 0.50),
        p95=_quantile(values, 0.95),
        p99=_quantile(values, 0.99),
        count=len(values),
        sum_value=float(sum(values)),
    )


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(max(int(math.ceil(q * len(sorted_values))) - 1, 0), len(sorted_values) - 1)
    return float(sorted_values[idx])


def histogram_buckets(values: Iterable[float], bucket_edges: list[float]) -> list[tuple[float, int]]:
    ordered = sorted(max(float(v), 0.0) for v in values)
    out: list[tuple[float, int]] = []
    for edge in bucket_edges:
        count = bisect.bisect_right(ordered, edge)
        out.append((edge, count))
    out.append((math.inf, len(ordered)))
    return out


def increment_counter(key: str, amount: int = 1) -> int:
    try:
        if not cache.add(key, int(amount), timeout=60 * 60 * 24 * 30):
            return int(cache.incr(key, int(amount)))
        return int(cache.get(key) or amount)
    except Exception:
        return int(cache.get(key) or 0)


def get_counter(key: str) -> int:
    try:
        return int(cache.get(key) or 0)
    except Exception:
        return 0


def observe_queue_wait_seconds(value: float) -> None:
    observe_recent_value(_QUEUE_WAIT_RECENT_KEY, value)


def observe_render_enqueue_latency_seconds(value: float) -> None:
    observe_recent_value(_ENQUEUE_LATENCY_RECENT_KEY, value)


def observe_worker_task_duration_seconds(value: float) -> None:
    observe_recent_value(_WORKER_DURATION_RECENT_KEY, value)


def queue_wait_summary() -> SummarySnapshot:
    return summary_from_recent(_QUEUE_WAIT_RECENT_KEY)


def render_enqueue_summary() -> SummarySnapshot:
    return summary_from_recent(_ENQUEUE_LATENCY_RECENT_KEY)


def worker_duration_summary() -> SummarySnapshot:
    return summary_from_recent(_WORKER_DURATION_RECENT_KEY)


def increment_worker_retries() -> int:
    return increment_counter(_WORKER_RETRY_TOTAL_KEY, 1)


def increment_worker_failures() -> int:
    return increment_counter(_WORKER_FAILURE_TOTAL_KEY, 1)


def increment_retry_storm_prevented() -> int:
    return increment_counter(_RETRY_STORM_PREVENTED_KEY, 1)


def increment_api_5xx_total() -> int:
    return increment_counter(_API_5XX_TOTAL_KEY, 1)


def increment_deferred_render_requests_total() -> int:
    return increment_counter(_DEFERRED_RENDER_REQUESTS_KEY, 1)


def increment_redis_cache_hit_total() -> int:
    return increment_counter(_REDIS_HIT_TOTAL_KEY, 1)


def increment_redis_cache_miss_total() -> int:
    return increment_counter(_REDIS_MISS_TOTAL_KEY, 1)


def _prometheus_line(name: str, value: int | float) -> str:
    numeric = float(value)
    if math.isfinite(numeric):
        return f"{name} {numeric:g}"
    return f"{name} 0"


def _prometheus_bool_line(name: str, value: bool) -> str:
    return _prometheus_line(name, 1 if value else 0)


def _metric_value(report: dict, section_name: str, metric_name: str) -> int | float:
    section = report.get(section_name) or {}
    metrics = section.get("metrics") or {}
    return metrics.get(metric_name) or 0


def _section_available(report: dict, section_name: str) -> bool:
    return bool((report.get(section_name) or {}).get("available"))


def _skipped_storage_payload() -> dict:
    return {
        "available": False,
        "metrics": {
            "total_storage_size_bytes": 0,
            "orphan_candidate_count": 0,
            "retention_candidate_count": 0,
            "reclaimable_bytes_estimate": 0,
        },
        "warnings": ["storage_scan_skipped_for_prometheus_scrape"],
    }


def _scrape_safe_system_observability_report() -> dict:
    render = system_observability._safe_section("render", system_observability._render_metrics)
    intents = system_observability._safe_section("follow_up_intents", system_observability._intent_metrics)
    recovery = system_observability._safe_section(
        "recovery",
        lambda: system_observability._recovery_metrics(max_age_hours=system_observability.DEFAULT_MAX_AGE_HOURS),
    )
    return {
        "render": system_observability._section_payload(render),
        "follow_up_intents": system_observability._section_payload(intents),
        "storage": _skipped_storage_payload(),
        "recovery": system_observability._section_payload(recovery),
    }


def _system_observability_prometheus_lines() -> list[str]:
    report = _scrape_safe_system_observability_report()
    storage_warnings = set((report.get("storage") or {}).get("warnings") or [])
    storage_scan_skipped = "storage_scan_skipped_for_prometheus_scrape" in storage_warnings
    return [
        "# HELP system_observability_render_available Whether render observability metrics were available on this scrape.",
        "# TYPE system_observability_render_available gauge",
        _prometheus_bool_line("system_observability_render_available", _section_available(report, "render")),
        "# HELP system_observability_render_active_count Active render jobs in pending or running status.",
        "# TYPE system_observability_render_active_count gauge",
        _prometheus_line("system_observability_render_active_count", _metric_value(report, "render", "active_render_count")),
        "# HELP system_observability_render_pending_count Render jobs currently pending.",
        "# TYPE system_observability_render_pending_count gauge",
        _prometheus_line("system_observability_render_pending_count", _metric_value(report, "render", "pending_render_count")),
        "# HELP system_observability_render_running_count Render jobs currently running.",
        "# TYPE system_observability_render_running_count gauge",
        _prometheus_line("system_observability_render_running_count", _metric_value(report, "render", "running_render_count")),
        "# HELP system_observability_render_failed_count Render jobs currently failed.",
        "# TYPE system_observability_render_failed_count gauge",
        _prometheus_line("system_observability_render_failed_count", _metric_value(report, "render", "failed_render_count")),
        "# HELP system_observability_render_oldest_active_age_seconds Age in seconds of the oldest active render.",
        "# TYPE system_observability_render_oldest_active_age_seconds gauge",
        _prometheus_line(
            "system_observability_render_oldest_active_age_seconds",
            _metric_value(report, "render", "oldest_active_render_age_seconds"),
        ),
        "# HELP system_observability_followup_available Whether follow-up intent observability metrics were available on this scrape.",
        "# TYPE system_observability_followup_available gauge",
        _prometheus_bool_line(
            "system_observability_followup_available",
            _section_available(report, "follow_up_intents"),
        ),
        "# HELP system_observability_followup_pending_count Render follow-up intents currently pending.",
        "# TYPE system_observability_followup_pending_count gauge",
        _prometheus_line(
            "system_observability_followup_pending_count",
            _metric_value(report, "follow_up_intents", "pending_intent_count"),
        ),
        "# HELP system_observability_followup_claimed_count Render follow-up intents currently claimed.",
        "# TYPE system_observability_followup_claimed_count gauge",
        _prometheus_line(
            "system_observability_followup_claimed_count",
            _metric_value(report, "follow_up_intents", "claimed_intent_count"),
        ),
        "# HELP system_observability_followup_dispatched_count Render follow-up intents currently dispatched.",
        "# TYPE system_observability_followup_dispatched_count gauge",
        _prometheus_line(
            "system_observability_followup_dispatched_count",
            _metric_value(report, "follow_up_intents", "dispatched_intent_count"),
        ),
        "# HELP system_observability_followup_oldest_age_seconds Age in seconds of the oldest active follow-up intent.",
        "# TYPE system_observability_followup_oldest_age_seconds gauge",
        _prometheus_line(
            "system_observability_followup_oldest_age_seconds",
            _metric_value(report, "follow_up_intents", "oldest_intent_age_seconds"),
        ),
        "# HELP system_observability_storage_available Whether storage observability metrics were available on this scrape.",
        "# TYPE system_observability_storage_available gauge",
        _prometheus_bool_line("system_observability_storage_available", _section_available(report, "storage")),
        "# HELP system_observability_storage_scan_skipped Whether expensive storage scanning was skipped for this scrape.",
        "# TYPE system_observability_storage_scan_skipped gauge",
        _prometheus_bool_line("system_observability_storage_scan_skipped", storage_scan_skipped),
        "# HELP system_observability_storage_total_bytes Total bytes reported by the safe storage snapshot.",
        "# TYPE system_observability_storage_total_bytes gauge",
        _prometheus_line("system_observability_storage_total_bytes", _metric_value(report, "storage", "total_storage_size_bytes")),
        "# HELP system_observability_storage_retention_candidate_count Storage retention candidate count from the safe snapshot.",
        "# TYPE system_observability_storage_retention_candidate_count gauge",
        _prometheus_line(
            "system_observability_storage_retention_candidate_count",
            _metric_value(report, "storage", "retention_candidate_count"),
        ),
        "# HELP system_observability_storage_orphan_candidate_count Storage orphan candidate count from the safe snapshot.",
        "# TYPE system_observability_storage_orphan_candidate_count gauge",
        _prometheus_line(
            "system_observability_storage_orphan_candidate_count",
            _metric_value(report, "storage", "orphan_candidate_count"),
        ),
        "# HELP system_observability_storage_reclaimable_bytes_estimate Reclaimable bytes estimate from the safe storage snapshot.",
        "# TYPE system_observability_storage_reclaimable_bytes_estimate gauge",
        _prometheus_line(
            "system_observability_storage_reclaimable_bytes_estimate",
            _metric_value(report, "storage", "reclaimable_bytes_estimate"),
        ),
        "# HELP system_observability_recovery_available Whether recovery observability metrics were available on this scrape.",
        "# TYPE system_observability_recovery_available gauge",
        _prometheus_bool_line("system_observability_recovery_available", _section_available(report, "recovery")),
        "# HELP system_observability_recovery_candidate_count Render recovery candidate count.",
        "# TYPE system_observability_recovery_candidate_count gauge",
        _prometheus_line("system_observability_recovery_candidate_count", _metric_value(report, "recovery", "recovery_candidate_count")),
        "# HELP system_observability_recovery_stale_render_count Stale render count from recovery inspection.",
        "# TYPE system_observability_recovery_stale_render_count gauge",
        _prometheus_line("system_observability_recovery_stale_render_count", _metric_value(report, "recovery", "stale_render_count")),
        "# HELP system_observability_recovery_stale_intent_count Stale follow-up intent count from recovery inspection.",
        "# TYPE system_observability_recovery_stale_intent_count gauge",
        _prometheus_line("system_observability_recovery_stale_intent_count", _metric_value(report, "recovery", "stale_intent_count")),
    ]


def prometheus_metrics_text() -> str:
    duration = worker_duration_summary()
    queue_wait = queue_wait_summary()
    enqueue = render_enqueue_summary()
    lines = [
        "# HELP worker_task_failures_total Total worker task failures observed by the application.",
        "# TYPE worker_task_failures_total counter",
        _prometheus_line("worker_task_failures_total", get_counter(_WORKER_FAILURE_TOTAL_KEY)),
        "# HELP worker_task_retries_total Total worker task retries observed by the application.",
        "# TYPE worker_task_retries_total counter",
        _prometheus_line("worker_task_retries_total", get_counter(_WORKER_RETRY_TOTAL_KEY)),
        "# HELP retry_storm_prevented_total Total worker retries suppressed by retry-storm guards.",
        "# TYPE retry_storm_prevented_total counter",
        _prometheus_line("retry_storm_prevented_total", get_counter(_RETRY_STORM_PREVENTED_KEY)),
        "# HELP worker_task_duration_seconds Recent worker task duration summary.",
        "# TYPE worker_task_duration_seconds summary",
        _prometheus_line('worker_task_duration_seconds{quantile="0.50"}', duration.p50),
        _prometheus_line('worker_task_duration_seconds{quantile="0.95"}', duration.p95),
        _prometheus_line('worker_task_duration_seconds{quantile="0.99"}', duration.p99),
        _prometheus_line("worker_task_duration_seconds_count", duration.count),
        _prometheus_line("worker_task_duration_seconds_sum", duration.sum_value),
        "# HELP render_queue_wait_seconds Recent render queue wait summary.",
        "# TYPE render_queue_wait_seconds summary",
        _prometheus_line('render_queue_wait_seconds{quantile="0.95"}', queue_wait.p95),
        _prometheus_line("render_queue_wait_seconds_count", queue_wait.count),
        _prometheus_line("render_queue_wait_seconds_sum", queue_wait.sum_value),
        "# HELP render_enqueue_latency_seconds Recent render enqueue latency summary.",
        "# TYPE render_enqueue_latency_seconds summary",
        _prometheus_line('render_enqueue_latency_seconds{quantile="0.95"}', enqueue.p95),
        _prometheus_line("render_enqueue_latency_seconds_count", enqueue.count),
        _prometheus_line("render_enqueue_latency_seconds_sum", enqueue.sum_value),
    ]
    lines.extend(_system_observability_prometheus_lines())
    return "\n".join(lines) + "\n"


def prometheus_metrics_response(request: HttpRequest) -> HttpResponse:
    token = str(getattr(settings, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()
    if token and request.headers.get("X-Metrics-Token", "") != token:
        return HttpResponse("unauthorized\n", status=401, content_type="text/plain; charset=utf-8")
    return HttpResponse(prometheus_metrics_text(), content_type="text/plain; version=0.0.4; charset=utf-8")
