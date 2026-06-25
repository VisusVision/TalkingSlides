from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone


PROGRESSIVE_ENHANCEMENT_KEY = "progressive_enhancement"
PENDING_ENHANCEMENT_STATUSES = {
    "pending",
    "running",
    "analyzing_chunks",
    "chunk_processing",
    "synthesizing",
    "final_synthesis",
    "final_aggregation",
}
TERMINAL_ENHANCEMENT_STATUSES = {"done", "failed", "unavailable", "disabled", "stale"}
TERMINAL_ENHANCEMENT_STATUSES.add("partial")
TERMINAL_ENHANCEMENT_STATUSES.add("superseded")
TERMINAL_ENHANCEMENT_STATUSES.add("degraded")
LESSON_SECTION_KEYS = ("summary", "clarity", "page_suggestions", "expanded_narration", "tags")
_OLLAMA_CALIBRATION_CACHE: dict[str, dict[str, Any]] = {}


def provider_chain_contains_ollama(chain: list[str] | tuple[str, ...] | None) -> bool:
    return any(str(item or "").strip().lower() == "ollama" for item in (chain or []))


def first_provider_name(chain: list[str] | tuple[str, ...] | None) -> str:
    for item in chain or []:
        name = str(item or "").strip().lower()
        if name and name != "auto":
            return name
    return ""


def _float_setting(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


def _int_setting(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _bool_setting(name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def intelligence_retry_cooldown_seconds() -> int:
    return _int_setting("INTELLIGENCE_RETRY_COOLDOWN_SECONDS", 60, minimum=0, maximum=86400)


def stable_json_fingerprint(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8", errors="ignore")).hexdigest()


def build_intelligence_run_identity(
    *,
    kind: str,
    owner_id: int | str,
    source_hash: str,
    provider: str,
    model: str,
    output_language: str,
    prompt_version: str,
    filters: dict[str, Any] | None = None,
    hardware_profile: str | None = None,
) -> dict[str, str]:
    profile = str(hardware_profile or intelligence_hardware_profile()).strip().lower()
    input_payload = {
        "kind": str(kind or "").strip().lower(),
        "owner_id": str(owner_id or ""),
        "source_hash": str(source_hash or ""),
        "provider": str(provider or "").strip().lower(),
        "model": str(model or "").strip(),
        "output_language": str(output_language or "auto").strip().lower() or "auto",
        "prompt_version": str(prompt_version or "").strip(),
        "hardware_profile": profile,
        "filters": filters if isinstance(filters, dict) else {},
    }
    return {
        "run_key": stable_json_fingerprint(input_payload),
        "source_hash": input_payload["source_hash"],
        "provider": input_payload["provider"],
        "model": input_payload["model"],
        "output_language": input_payload["output_language"],
        "prompt_version": input_payload["prompt_version"],
        "hardware_profile": profile,
        "input_fingerprint": stable_json_fingerprint(
            {
                "kind": input_payload["kind"],
                "owner_id": input_payload["owner_id"],
                "source_hash": input_payload["source_hash"],
                "hardware_profile": profile,
                "filters": input_payload["filters"],
            }
        ),
    }


def enhancement_lock_key(run_key: str) -> str:
    normalized = str(run_key or "").strip()
    return f"intelligence:enhancement:run:{normalized}" if normalized else ""


def lesson_section_statuses(
    *,
    status: str,
    provider: str,
    sections: tuple[str, ...] = LESSON_SECTION_KEYS,
    error: Exception | str | None = None,
) -> dict[str, dict[str, Any]]:
    now = timezone.now().isoformat()
    safe_error = safe_enhancement_error(error)
    return {
        key: {
            "status": str(status or "pending").strip().lower(),
            "provider": str(provider or "").strip().lower(),
            "updated_at": now,
            **({"error": safe_error} if safe_error else {}),
        }
        for key in sections
    }


def merge_lesson_section_statuses(
    current: dict[str, Any] | None,
    updates: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    source = current if isinstance(current, dict) else {}
    for key in LESSON_SECTION_KEYS:
        value = source.get(key)
        merged[key] = dict(value) if isinstance(value, dict) else {
            "status": "",
            "provider": "",
            "updated_at": "",
        }
    for key, value in (updates or {}).items():
        if key not in LESSON_SECTION_KEYS or not isinstance(value, dict):
            continue
        merged[key] = {**merged.get(key, {}), **value}
        merged[key]["status"] = str(merged[key].get("status") or "").strip().lower()
        merged[key]["provider"] = str(merged[key].get("provider") or "").strip().lower()
        if not value.get("updated_at"):
            merged[key]["updated_at"] = timezone.now().isoformat()
    return merged


def ollama_chunk_max_chars() -> int:
    return _int_setting("INTELLIGENCE_OLLAMA_CHUNK_MAX_CHARS", 6000, minimum=1000, maximum=50000)


def intelligence_hardware_profile() -> str:
    profile = str(getattr(settings, "INTELLIGENCE_HARDWARE_PROFILE", "local_mid") or "local_mid").strip().lower()
    return profile if profile in {"local_low", "local_mid", "production_gpu"} else "local_mid"


def ollama_chunk_concurrency() -> int:
    maximum = 4 if intelligence_hardware_profile() == "production_gpu" else 1
    return _int_setting("INTELLIGENCE_OLLAMA_CHUNK_CONCURRENCY", 1, minimum=1, maximum=maximum)


def intelligence_runtime_profile_metadata() -> dict[str, Any]:
    return {
        "hardware_profile": intelligence_hardware_profile(),
        "chunk_max_chars": ollama_chunk_max_chars(),
        "chunk_concurrency": ollama_chunk_concurrency(),
        "chunk_timeout_min_seconds": _float_setting("INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MIN_SECONDS", 45.0, minimum=1.0, maximum=3600.0),
        "chunk_timeout_max_seconds": _float_setting("INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MAX_SECONDS", 120.0, minimum=1.0, maximum=3600.0),
        "total_timeout_max_seconds": ollama_total_timeout_budget_seconds(),
    }


def ollama_chunk_timeout_seconds(input_chars: int) -> float:
    min_seconds = _float_setting("INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MIN_SECONDS", 45.0, minimum=1.0, maximum=3600.0)
    max_seconds = _float_setting(
        "INTELLIGENCE_OLLAMA_CHUNK_TIMEOUT_MAX_SECONDS",
        120.0,
        minimum=min_seconds,
        maximum=3600.0,
    )
    per_1000_chars = _float_setting("INTELLIGENCE_BACKGROUND_TIMEOUT_PER_1000_CHARS", 4.0, minimum=0.0, maximum=60.0)
    try:
        chars = max(0, int(input_chars or 0))
    except (TypeError, ValueError):
        chars = 0
    adaptive = min_seconds + (chars / 1000.0) * per_1000_chars
    return round(max(min_seconds, min(max_seconds, adaptive)), 2)


def ollama_no_progress_timeout_seconds(input_chars: int, *, hard_max_seconds: float | int | None = None) -> float:
    """Return the maximum wait for one chunk to finish before treating it as stalled."""
    chunk_timeout = ollama_chunk_timeout_seconds(input_chars)
    hard_max = float(hard_max_seconds) if hard_max_seconds is not None else ollama_total_timeout_budget_seconds()
    return round(max(1.0, min(max(1.0, hard_max), chunk_timeout)), 2)


def ollama_total_timeout_budget_seconds() -> float:
    return _float_setting("INTELLIGENCE_OLLAMA_TOTAL_TIMEOUT_MAX_SECONDS", 600.0, minimum=30.0, maximum=7200.0)


def ollama_timeout_safety_factor() -> float:
    return _float_setting("INTELLIGENCE_OLLAMA_TIMEOUT_SAFETY_FACTOR", 1.15, minimum=1.0, maximum=2.0)


def ollama_timeout_safety_margin_seconds() -> float:
    return _float_setting("INTELLIGENCE_OLLAMA_TIMEOUT_SAFETY_MARGIN_SECONDS", 0.0, minimum=0.0, maximum=300.0)


def _model_parameter_size_b(model: str) -> float:
    lowered = str(model or "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*b", lowered)
    if not match:
        return 0.0
    try:
        return max(0.0, float(match.group(1)))
    except (TypeError, ValueError):
        return 0.0


def _fallback_tokens_per_second(model: str) -> float:
    profile = intelligence_hardware_profile()
    base = {
        "local_low": 5.0,
        "local_mid": 9.0,
        "production_gpu": 28.0,
    }.get(profile, 8.0)
    size_b = _model_parameter_size_b(model)
    if 0 < size_b <= 4:
        base *= 2.2
    elif size_b >= 30:
        base *= 0.25
    elif size_b >= 13:
        base *= 0.45
    elif size_b >= 7:
        base *= 1.0
    return round(max(1.0, base), 2)


def clear_ollama_calibration_cache() -> None:
    _OLLAMA_CALIBRATION_CACHE.clear()


def ollama_model_calibration_details(
    *,
    model: str,
    base_url: str = "",
    timeout_seconds: float | int | None = None,
) -> dict[str, Any]:
    normalized_model = str(model or "").strip()
    normalized_base_url = str(base_url or getattr(settings, "OLLAMA_BASE_URL", "") or "").strip().rstrip("/")
    fallback_tps = _fallback_tokens_per_second(normalized_model)
    fallback_cps = round(fallback_tps * 4.0, 2)
    base_payload: dict[str, Any] = {
        "model": normalized_model,
        "hardware_profile": intelligence_hardware_profile(),
        "calibration_enabled": _bool_setting("INTELLIGENCE_OLLAMA_CALIBRATION_ENABLED", True),
        "calibration_used": False,
        "calibration_cache_hit": False,
        "calibration_failed": False,
        "calibration_elapsed_seconds": 0.0,
        "measured_chars_per_second": None,
        "measured_tokens_per_second": None,
        "fallback_chars_per_second": fallback_cps,
        "fallback_tokens_per_second": fallback_tps,
        "throughput_chars_per_second": fallback_cps,
        "throughput_tokens_per_second": fallback_tps,
    }
    if not base_payload["calibration_enabled"]:
        return base_payload
    if not normalized_model or not normalized_base_url:
        return {
            **base_payload,
            "calibration_failed": True,
            "calibration_error": "missing_model_or_base_url",
        }

    ttl_seconds = _float_setting("INTELLIGENCE_OLLAMA_CALIBRATION_TTL_SECONDS", 1800.0, minimum=0.0, maximum=86400.0)
    cache_key = f"{normalized_base_url}|{normalized_model}"
    now = time.monotonic()
    cached = _OLLAMA_CALIBRATION_CACHE.get(cache_key)
    if cached and (ttl_seconds <= 0 or now - float(cached.get("cached_at") or 0.0) <= ttl_seconds):
        payload = {k: v for k, v in cached.items() if k != "cached_at"}
        payload["calibration_cache_hit"] = True
        return payload

    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else _float_setting("INTELLIGENCE_OLLAMA_CALIBRATION_TIMEOUT_SECONDS", 20.0, minimum=1.0, maximum=120.0)
    )
    request_payload = {
        "model": normalized_model,
        "prompt": "Calibration check. Reply with one short sentence about readiness.",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 32},
    }
    request = Request(
        f"{normalized_base_url}/api/generate",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started_at = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        elapsed = max(0.001, time.monotonic() - started_at)
        data = json.loads(body)
        response_text = str(data.get("response") or "")
        eval_count = int(data.get("eval_count") or 0)
        eval_duration_seconds = float(data.get("eval_duration") or 0.0) / 1_000_000_000.0
        measured_tps = (
            round(eval_count / eval_duration_seconds, 2)
            if eval_count > 0 and eval_duration_seconds > 0
            else round(max(1, len(response_text) // 4) / elapsed, 2)
        )
        measured_cps = round(len(response_text) / elapsed, 2)
        payload = {
            **base_payload,
            "calibration_used": True,
            "calibration_elapsed_seconds": round(elapsed, 2),
            "measured_chars_per_second": measured_cps,
            "measured_tokens_per_second": measured_tps,
            "throughput_chars_per_second": measured_cps if measured_cps > 0 else fallback_cps,
            "throughput_tokens_per_second": measured_tps if measured_tps > 0 else fallback_tps,
        }
        _OLLAMA_CALIBRATION_CACHE[cache_key] = {**payload, "cached_at": now}
        return payload
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        elapsed = max(0.0, time.monotonic() - started_at)
        payload = {
            **base_payload,
            "calibration_failed": True,
            "calibration_elapsed_seconds": round(elapsed, 2),
            "calibration_error": safe_enhancement_error(exc),
        }
        _OLLAMA_CALIBRATION_CACHE[cache_key] = {**payload, "cached_at": now}
        return payload


def _ollama_estimated_output_tokens(*, kind: str, chunk_count: int, finalization: bool = False) -> int:
    normalized = str(kind or "").strip().lower()
    per_chunk = 450 if normalized == "lesson" else 320
    if finalization:
        per_chunk = 220 if normalized == "lesson" else 180
    return max(0, int(chunk_count or 1) * per_chunk)


def ollama_workload_timeout_budget_details(
    *,
    input_chars: int = 0,
    chunk_count: int = 1,
    base_seconds: float | int | None = None,
    hard_max_seconds: float | int | None = None,
    kind: str = "",
    model: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    try:
        chars = max(0, int(input_chars or 0))
    except (TypeError, ValueError):
        chars = 0
    try:
        chunks = max(1, int(chunk_count or 1))
    except (TypeError, ValueError):
        chunks = 1
    hard_max = float(hard_max_seconds) if hard_max_seconds is not None else ollama_total_timeout_budget_seconds()
    hard_max = max(1.0, hard_max)
    if base_seconds is None:
        base = ollama_chunk_timeout_seconds(chars)
    else:
        try:
            base = max(1.0, float(base_seconds))
        except (TypeError, ValueError):
            base = ollama_chunk_timeout_seconds(chars)
    avg_chunk_chars = int((chars + chunks - 1) / chunks) if chunks else chars
    avg_chunk_timeout = ollama_chunk_timeout_seconds(avg_chunk_chars)
    estimated_input_tokens = int((chars + 3) / 4) if chars else 0
    estimated_output_tokens = _ollama_estimated_output_tokens(kind=kind, chunk_count=chunks)
    estimated_tokens = estimated_input_tokens + estimated_output_tokens
    calibration = ollama_model_calibration_details(model=model, base_url=base_url) if model else {
        "model": str(model or "").strip(),
        "hardware_profile": intelligence_hardware_profile(),
        "calibration_enabled": False,
        "calibration_used": False,
        "calibration_cache_hit": False,
        "calibration_failed": False,
        "calibration_elapsed_seconds": 0.0,
        "measured_chars_per_second": None,
        "measured_tokens_per_second": None,
        "fallback_chars_per_second": None,
        "fallback_tokens_per_second": None,
        "throughput_chars_per_second": None,
        "throughput_tokens_per_second": None,
    }
    calibrated_estimate = 0.0
    if calibration.get("calibration_enabled"):
        throughput_tps = float(calibration.get("throughput_tokens_per_second") or 0.0)
        if throughput_tps > 0:
            per_chunk_overhead = _float_setting(
                "INTELLIGENCE_OLLAMA_CALIBRATION_CHUNK_OVERHEAD_SECONDS",
                2.0,
                minimum=0.0,
                maximum=60.0,
            )
            calibrated_estimate = (estimated_tokens / throughput_tps) + (chunks * per_chunk_overhead)
            avg_chunk_timeout = max(avg_chunk_timeout, round(calibrated_estimate / chunks, 2))
    chunk_estimate = chunks * avg_chunk_timeout
    calculated_budget = round(min(hard_max, max(base, chunk_estimate, calibrated_estimate)), 2)
    safety_factor = ollama_timeout_safety_factor()
    configured_margin = ollama_timeout_safety_margin_seconds()
    budget_with_factor = calculated_budget * safety_factor
    budget_with_margin = calculated_budget + configured_margin
    timeout_budget = round(min(hard_max, max(budget_with_factor, budget_with_margin)), 2)
    no_progress_timeout = ollama_no_progress_timeout_seconds(avg_chunk_chars, hard_max_seconds=hard_max)
    return {
        "input_chars": chars,
        "estimated_input_chars": chars,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_tokens": estimated_tokens,
        "chunk_count": chunks,
        "calculated_budget_seconds": calculated_budget,
        "safety_factor": round(safety_factor, 4),
        "configured_safety_margin_seconds": round(configured_margin, 2),
        "safety_margin_seconds": round(max(0.0, timeout_budget - calculated_budget), 2),
        "timeout_budget_seconds": timeout_budget,
        "hard_max_seconds": round(hard_max, 2),
        "absolute_cap_seconds": round(hard_max, 2),
        "no_progress_timeout_seconds": no_progress_timeout,
        "average_chunk_timeout_seconds": avg_chunk_timeout,
        "model_profile": intelligence_hardware_profile(),
        "hardware_profile": intelligence_hardware_profile(),
        "workload_kind": str(kind or "").strip().lower(),
        "model": str(model or "").strip(),
        "calibrated_estimate_seconds": round(calibrated_estimate, 2),
        **calibration,
    }


def ollama_workload_timeout_budget_seconds(
    *,
    input_chars: int = 0,
    chunk_count: int = 1,
    base_seconds: float | int | None = None,
    hard_max_seconds: float | int | None = None,
) -> float:
    details = ollama_workload_timeout_budget_details(
        input_chars=input_chars,
        chunk_count=chunk_count,
        base_seconds=base_seconds,
        hard_max_seconds=hard_max_seconds,
    )
    return float(details["timeout_budget_seconds"])


def _model_size_multiplier(model: str) -> float:
    size_b = _model_parameter_size_b(model)
    if size_b <= 0:
        return 1.0
    if size_b >= 30:
        return 1.7
    if size_b >= 13:
        return 1.35
    return 1.0


def ollama_finalization_timeout_budget_details(
    *,
    input_chars: int = 0,
    output_chars: int = 0,
    chunk_count: int = 1,
    hard_max_seconds: float | int | None = None,
    kind: str = "",
    model: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    try:
        source_chars = max(0, int(input_chars or 0))
    except (TypeError, ValueError):
        source_chars = 0
    try:
        result_chars = max(0, int(output_chars or 0))
    except (TypeError, ValueError):
        result_chars = 0
    try:
        chunks = max(1, int(chunk_count or 1))
    except (TypeError, ValueError):
        chunks = 1

    configured_cap = _float_setting(
        "INTELLIGENCE_OLLAMA_FINALIZATION_TIMEOUT_MAX_SECONDS",
        120.0,
        minimum=1.0,
        maximum=ollama_total_timeout_budget_seconds(),
    )
    absolute_cap = float(hard_max_seconds) if hard_max_seconds is not None else configured_cap
    absolute_cap = max(1.0, min(float(ollama_total_timeout_budget_seconds()), absolute_cap))

    base_seconds = _float_setting("INTELLIGENCE_OLLAMA_FINALIZATION_TIMEOUT_BASE_SECONDS", 8.0, minimum=1.0, maximum=600.0)
    per_chunk_seconds = _float_setting("INTELLIGENCE_OLLAMA_FINALIZATION_TIMEOUT_PER_CHUNK_SECONDS", 1.5, minimum=0.0, maximum=60.0)
    per_1000_tokens_seconds = _float_setting(
        "INTELLIGENCE_OLLAMA_FINALIZATION_TIMEOUT_PER_1000_TOKENS_SECONDS",
        2.0,
        minimum=0.0,
        maximum=120.0,
    )

    estimated_input_tokens = int((source_chars + 3) / 4) if source_chars else 0
    estimated_output_tokens = int((result_chars + 3) / 4) if result_chars else 0
    estimated_tokens = estimated_input_tokens + estimated_output_tokens + _ollama_estimated_output_tokens(
        kind=kind,
        chunk_count=chunks,
        finalization=True,
    )
    profile_multiplier = {
        "local_low": 1.35,
        "local_mid": 1.0,
        "production_gpu": 0.75,
    }.get(intelligence_hardware_profile(), 1.0)
    calculated = (
        base_seconds
        + (chunks * per_chunk_seconds)
        + ((estimated_tokens / 1000.0) * per_1000_tokens_seconds)
    ) * profile_multiplier * _model_size_multiplier(model)
    calibration = ollama_model_calibration_details(model=model, base_url=base_url) if model else {
        "model": str(model or "").strip(),
        "hardware_profile": intelligence_hardware_profile(),
        "calibration_enabled": False,
        "calibration_used": False,
        "calibration_cache_hit": False,
        "calibration_failed": False,
        "calibration_elapsed_seconds": 0.0,
        "measured_chars_per_second": None,
        "measured_tokens_per_second": None,
        "fallback_chars_per_second": None,
        "fallback_tokens_per_second": None,
        "throughput_chars_per_second": None,
        "throughput_tokens_per_second": None,
    }
    calibrated = 0.0
    if calibration.get("calibration_enabled"):
        throughput_tps = float(calibration.get("throughput_tokens_per_second") or 0.0)
        if throughput_tps > 0:
            final_overhead = _float_setting(
                "INTELLIGENCE_OLLAMA_CALIBRATION_FINALIZATION_OVERHEAD_SECONDS",
                3.0,
                minimum=0.0,
                maximum=90.0,
            )
            calibrated = (estimated_tokens / throughput_tps) + final_overhead
            calculated = max(calculated, calibrated)
    calculated_budget = round(min(absolute_cap, max(1.0, calculated)), 2)

    safety_factor = ollama_timeout_safety_factor()
    configured_margin = ollama_timeout_safety_margin_seconds()
    budget_with_factor = calculated_budget * safety_factor
    budget_with_margin = calculated_budget + configured_margin
    timeout_budget = round(min(absolute_cap, max(budget_with_factor, budget_with_margin)), 2)
    return {
        "input_chars": source_chars,
        "estimated_input_chars": source_chars,
        "output_chars": result_chars,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_tokens": estimated_tokens,
        "chunk_count": chunks,
        "calculated_budget_seconds": calculated_budget,
        "safety_factor": round(safety_factor, 4),
        "configured_safety_margin_seconds": round(configured_margin, 2),
        "safety_margin_seconds": round(max(0.0, timeout_budget - calculated_budget), 2),
        "timeout_budget_seconds": timeout_budget,
        "hard_max_seconds": round(absolute_cap, 2),
        "absolute_cap_seconds": round(absolute_cap, 2),
        "model_profile": intelligence_hardware_profile(),
        "hardware_profile": intelligence_hardware_profile(),
        "workload_kind": str(kind or "").strip().lower(),
        "model": str(model or "").strip(),
        "calibrated_estimate_seconds": round(calibrated, 2),
        **calibration,
    }



def bounded_adaptive_background_timeout(
    *,
    base_seconds: float,
    input_chars: int = 0,
    page_count: int = 0,
    comment_count: int = 0,
) -> float:
    """Return a bounded background timeout scaled by workload size."""
    min_seconds = _float_setting("INTELLIGENCE_BACKGROUND_TIMEOUT_MIN_SECONDS", 60.0, minimum=1.0, maximum=3600.0)
    max_seconds = _float_setting(
        "INTELLIGENCE_BACKGROUND_TIMEOUT_MAX_SECONDS",
        300.0,
        minimum=min_seconds,
        maximum=3600.0,
    )
    per_1000_chars = _float_setting("INTELLIGENCE_BACKGROUND_TIMEOUT_PER_1000_CHARS", 4.0, minimum=0.0, maximum=60.0)
    per_page = _float_setting("INTELLIGENCE_BACKGROUND_TIMEOUT_PER_PAGE_SECONDS", 2.0, minimum=0.0, maximum=60.0)
    per_comment = _float_setting("INTELLIGENCE_BACKGROUND_TIMEOUT_PER_COMMENT_SECONDS", 1.0, minimum=0.0, maximum=60.0)
    try:
        chars = max(0, int(input_chars or 0))
    except (TypeError, ValueError):
        chars = 0
    try:
        pages = max(0, int(page_count or 0))
    except (TypeError, ValueError):
        pages = 0
    try:
        comments = max(0, int(comment_count or 0))
    except (TypeError, ValueError):
        comments = 0
    adaptive = min_seconds
    adaptive += (chars / 1000.0) * per_1000_chars
    adaptive += pages * per_page
    adaptive += comments * per_comment
    return round(max(min_seconds, min(max_seconds, adaptive)), 2)


def safe_enhancement_error(error: Exception | str | None, *, limit: int = 180) -> str:
    text = str(error or "").strip()
    if not text:
        return ""
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s\"')]+", "[url]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def safe_response_preview(text: Exception | str | None, *, limit: int = 300) -> str:
    preview = safe_enhancement_error(text, limit=limit)
    if not preview:
        return ""
    preview = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", preview)
    preview = re.sub(r"(?i)\b(bearer|token|api[_-]?key|secret|password)\s*[:=]\s*\S+", r"\1=[redacted]", preview)
    return preview[:limit].rstrip()


def enhancement_metadata(
    *,
    provider: str = "ollama",
    status: str = "pending",
    task_id: str = "",
    queue: str = "",
    timeout_seconds: float | int | None = None,
    error: Exception | str | None = None,
    enabled: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = timezone.now().isoformat()
    normalized_status = str(status or "pending").strip().lower()
    payload = {
        "enabled": bool(enabled),
        "provider": str(provider or "ollama").strip().lower() or "ollama",
        "status": normalized_status,
        "queued_at": now if normalized_status == "pending" else "",
        "started_at": now if normalized_status == "running" else "",
        "finished_at": now if normalized_status in TERMINAL_ENHANCEMENT_STATUSES else "",
        "failed_at": now if normalized_status in {"failed", "stale"} else "",
        "task_id": str(task_id or ""),
        "queue": str(queue or "").strip(),
        "error": safe_enhancement_error(error),
        "last_update_at": now,
    }
    if timeout_seconds is not None:
        try:
            payload["timeout_seconds"] = float(timeout_seconds)
        except (TypeError, ValueError):
            pass
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is not None:
                if str(key) == "sections" and isinstance(value, dict):
                    payload["sections"] = merge_lesson_section_statuses(payload.get("sections"), value)
                else:
                    payload[str(key)] = value
    if normalized_status == "failed" and payload.get("provider") == "ollama":
        failure_at = payload.get("failed_at") or now
        payload.setdefault("last_ollama_failure_at", failure_at)
        payload.setdefault("last_failure_reason", safe_enhancement_error(error))
        payload.setdefault(
            "retry_available_at",
            (timezone.now() + timedelta(seconds=intelligence_retry_cooldown_seconds())).isoformat(),
        )
        payload.setdefault("retry_cooldown_seconds", intelligence_retry_cooldown_seconds())
        payload.setdefault("retry_count", 0)
    return payload


def merge_enhancement_metadata(
    metadata: dict[str, Any] | None,
    *,
    provider: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    queue: str | None = None,
    timeout_seconds: float | int | None = None,
    error: Exception | str | None = None,
    enabled: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = dict(metadata or {})
    current = source.get(PROGRESSIVE_ENHANCEMENT_KEY)
    enhancement = dict(current) if isinstance(current, dict) else enhancement_metadata()
    now = timezone.now().isoformat()
    extra_has_last_failure_reason = isinstance(extra, dict) and bool(extra.get("last_failure_reason"))

    if provider is not None:
        enhancement["provider"] = str(provider or "ollama").strip().lower() or "ollama"
    if enabled is not None:
        enhancement["enabled"] = bool(enabled)
    if task_id is not None:
        enhancement["task_id"] = str(task_id or "")
    if queue is not None:
        enhancement["queue"] = str(queue or "").strip()
    if timeout_seconds is not None:
        try:
            enhancement["timeout_seconds"] = float(timeout_seconds)
        except (TypeError, ValueError):
            enhancement.pop("timeout_seconds", None)
    if status is not None:
        normalized_status = str(status or "").strip().lower() or "pending"
        previous_status = str(enhancement.get("status") or "").strip().lower()
        enhancement["status"] = normalized_status
        if normalized_status == "pending" and not enhancement.get("queued_at"):
            enhancement["queued_at"] = now
        if normalized_status == "running" and previous_status != "running":
            enhancement["started_at"] = now
        if normalized_status in TERMINAL_ENHANCEMENT_STATUSES:
            enhancement["finished_at"] = now
        if normalized_status in {"failed", "stale"}:
            enhancement["failed_at"] = now
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is None:
                continue
            if str(key) == "sections" and isinstance(value, dict):
                enhancement["sections"] = merge_lesson_section_statuses(enhancement.get("sections"), value)
            else:
                enhancement[str(key)] = value
    if error is not None:
        enhancement["error"] = safe_enhancement_error(error)
    elif status in {"done", "pending", "running"}:
        enhancement["error"] = ""
    if status is not None and str(status or "").strip().lower() == "failed" and str(enhancement.get("provider") or "").strip().lower() == "ollama":
        failure_at = str(enhancement.get("failed_at") or now)
        enhancement["last_ollama_failure_at"] = failure_at
        if error is not None and not extra_has_last_failure_reason:
            enhancement["last_failure_reason"] = safe_enhancement_error(error)
        else:
            enhancement.setdefault("last_failure_reason", safe_enhancement_error(enhancement.get("error")))
        enhancement["retry_available_at"] = (
            timezone.now() + timedelta(seconds=intelligence_retry_cooldown_seconds())
        ).isoformat()
        enhancement["retry_cooldown_seconds"] = intelligence_retry_cooldown_seconds()
        try:
            enhancement["retry_count"] = int(enhancement.get("retry_count") or 0)
        except (TypeError, ValueError):
            enhancement["retry_count"] = 0
    enhancement["last_update_at"] = now

    source[PROGRESSIVE_ENHANCEMENT_KEY] = enhancement
    return source


def enhancement_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    source = metadata if isinstance(metadata, dict) else {}
    enhancement = source.get(PROGRESSIVE_ENHANCEMENT_KEY)
    return dict(enhancement) if isinstance(enhancement, dict) else {}


def enhancement_response_fields(
    metadata: dict[str, Any] | None,
    *,
    default_available: bool = False,
) -> dict[str, Any]:
    enhancement = enhancement_from_metadata(metadata)
    status = str(enhancement.get("status") or "").strip().lower()
    provider = str(enhancement.get("provider") or "").strip().lower()
    enabled = bool(enhancement.get("enabled", bool(provider or default_available)))
    available = bool(enabled and (provider or default_available))
    try:
        retry_count = max(0, int(enhancement.get("retry_count") or 0))
    except (TypeError, ValueError):
        retry_count = 0
    last_failure_reason = safe_enhancement_error(enhancement.get("last_failure_reason"))
    if not last_failure_reason or "chunk analysis failed for all chunks" in last_failure_reason:
        diagnostics = enhancement.get("chunk_diagnostics")
        if isinstance(diagnostics, list) and diagnostics:
            last_diagnostic = diagnostics[-1]
            if isinstance(last_diagnostic, dict):
                last_failure_reason = safe_enhancement_error(
                    last_diagnostic.get("safe_reason") or last_diagnostic.get("reason") or last_failure_reason
                )
    return {
        "enhancement_available": available,
        "enhancement_pending": bool(status in PENDING_ENHANCEMENT_STATUSES),
        "enhancement_status": status,
        "enhancement_provider": provider,
        "enhancement_error_safe": safe_enhancement_error(enhancement.get("error")),
        "enhancement_last_failure_reason": last_failure_reason,
        "retry_available_at": str(enhancement.get("retry_available_at") or ""),
        "retry_cooldown_seconds": intelligence_retry_cooldown_seconds(),
        "retry_count": retry_count,
    }


def provider_attempt(provider: str, status_value: str, error: Exception | str | None = None) -> dict[str, str]:
    payload = {"provider": str(provider or "").strip().lower(), "status": str(status_value or "").strip().lower()}
    if error:
        payload["error"] = safe_enhancement_error(error, limit=240)
    return payload
