from __future__ import annotations

import os
import random
from dataclasses import dataclass

from django.core.cache import cache
from core.perf_metrics import increment_retry_storm_prevented, increment_worker_retries


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter_ratio: float
    storm_ttl_seconds: int


def load_retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_retries=int(os.environ.get("WORKER_RETRY_MAX_RETRIES", "2") or "2"),
        base_delay_seconds=float(os.environ.get("WORKER_RETRY_BASE_DELAY_SECONDS", "5") or "5"),
        max_delay_seconds=float(os.environ.get("WORKER_RETRY_MAX_DELAY_SECONDS", "60") or "60"),
        jitter_ratio=float(os.environ.get("WORKER_RETRY_JITTER_RATIO", "0.2") or "0.2"),
        storm_ttl_seconds=int(os.environ.get("WORKER_RETRY_STORM_TTL_SECONDS", "180") or "180"),
    )


def compute_retry_delay_seconds(*, attempt_number: int, policy: RetryPolicy) -> int:
    exponent = max(int(attempt_number), 0)
    base = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**exponent))
    jitter = max(base * policy.jitter_ratio, 0.0)
    low = max(base - jitter, 0.0)
    high = base + jitter
    return max(1, int(round(random.uniform(low, high))))


def allow_retry_for_key(retry_key: str, *, policy: RetryPolicy) -> bool:
    key = f"worker:retry-storm:{retry_key}"
    try:
        allowed = cache.add(key, "1", timeout=max(int(policy.storm_ttl_seconds), 1))
    except Exception:
        allowed = True
    if not bool(allowed):
        increment_retry_storm_prevented()
    return bool(allowed)


def increment_worker_retry_metric() -> None:
    increment_worker_retries()
