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
    return "\n".join(lines) + "\n"


def prometheus_metrics_response(request: HttpRequest) -> HttpResponse:
    token = str(getattr(settings, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()
    if token and request.headers.get("X-Metrics-Token", "") != token:
        return HttpResponse("unauthorized\n", status=401, content_type="text/plain; charset=utf-8")
    return HttpResponse(prometheus_metrics_text(), content_type="text/plain; version=0.0.4; charset=utf-8")
