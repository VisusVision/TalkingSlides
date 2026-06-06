from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Mapping


class PreviewTaskTimeLimitConfigError(RuntimeError):
    """Raised when preview task limits cannot contain configured stage budgets."""


@dataclass(frozen=True)
class PreviewTaskTimeLimits:
    soft_seconds: int
    hard_seconds: int
    required_soft_seconds: int
    required_hard_seconds: int
    safety_margin_seconds: int
    hard_margin_seconds: int
    stage_maxima_seconds: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def largest_stage_timeout_seconds(self) -> float:
        return max(self.stage_maxima_seconds.values() or [0.0])

    @property
    def total_stage_budget_seconds(self) -> float:
        return float(sum(self.stage_maxima_seconds.values()))


def _env_value(env: Mapping[str, str], *names: str) -> tuple[str, str]:
    for name in names:
        raw = str(env.get(name, "")).strip()
        if raw:
            return raw, name
    return "", ""


def _env_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
) -> float:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    return max(float(value), float(minimum))


def _env_int_first(
    env: Mapping[str, str],
    names: tuple[str, ...],
    default: int,
    *,
    minimum: int = 1,
) -> tuple[int, str]:
    raw, source = _env_value(env, *names)
    if not raw:
        return max(int(default), int(minimum)), ""
    try:
        value = int(float(raw))
    except Exception:
        return max(int(default), int(minimum)), source
    return max(int(value), int(minimum)), source


def _env_enabled(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = str(env.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def preview_stage_timeout_maxima(env: Mapping[str, str] | None = None) -> dict[str, float]:
    """Return the largest configured timeout each preview stage may receive.

    These values intentionally model the timeout contract, not quality. MuseTalk
    low-VRAM mode can expand the subprocess timeout after the orchestrator
    budget is computed, so the effective MuseTalk maximum includes that adapter
    multiplier/floor.
    """

    env_map: Mapping[str, str] = env or os.environ

    tts_max = _env_float(env_map, "AVATAR_ORCH_TTS_TIMEOUT_MAX_SECONDS", 360.0, minimum=1.0)
    liveportrait_max = _env_float(env_map, "AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_MAX_SECONDS", 7200.0, minimum=1.0)
    musetalk_budget_max = _env_float(env_map, "AVATAR_PREVIEW_MUSETALK_TIMEOUT_MAX_SECONDS", 7200.0, minimum=1.0)
    restoration_max = (
        _env_float(env_map, "AVATAR_ORCH_RESTORATION_TIMEOUT_MAX_SECONDS", 900.0, minimum=1.0)
        if _env_enabled(env_map, "AVATAR_PREVIEW_USE_RESTORATION", False)
        else 0.0
    )

    low_vram_multiplier = _env_float(env_map, "AVATAR_LOW_VRAM_MUSETALK_TIMEOUT_MULTIPLIER", 2.0, minimum=1.0)
    musetalk_low_vram_max = max(
        musetalk_budget_max * low_vram_multiplier,
        musetalk_budget_max + 120.0,
    )
    service_floor = _env_float(env_map, "AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", 420.0, minimum=1.0)
    musetalk_effective_max = max(musetalk_budget_max, musetalk_low_vram_max, service_floor)

    return {
        "tts": float(tts_max),
        "liveportrait": float(liveportrait_max),
        "musetalk": float(musetalk_effective_max),
        "restoration": float(restoration_max),
    }


def resolve_preview_task_time_limits(
    env: Mapping[str, str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> PreviewTaskTimeLimits:
    env_map: Mapping[str, str] = env or os.environ
    log = logger or logging.getLogger(__name__)

    stage_maxima = preview_stage_timeout_maxima(env_map)
    safety_margin, safety_source = _env_int_first(
        env_map,
        ("AVATAR_PREVIEW_TASK_TIME_LIMIT_SAFETY_MARGIN_SECONDS",),
        300,
        minimum=1,
    )
    hard_margin, hard_margin_source = _env_int_first(
        env_map,
        ("AVATAR_PREVIEW_TASK_HARD_TIME_LIMIT_MARGIN_SECONDS",),
        300,
        minimum=1,
    )
    minimum_soft, minimum_soft_source = _env_int_first(
        env_map,
        ("AVATAR_PREVIEW_TASK_DEFAULT_SOFT_TIME_LIMIT_SECONDS",),
        3600,
        minimum=1,
    )

    required_soft = int(math.ceil(sum(stage_maxima.values()) + float(safety_margin)))
    required_soft = max(required_soft, int(math.ceil(max(stage_maxima.values() or [0.0]) + float(safety_margin))))
    default_soft = max(int(minimum_soft), int(required_soft))

    soft_seconds, soft_source = _env_int_first(
        env_map,
        (
            "AVATAR_PREVIEW_TASK_SOFT_TIME_LIMIT_SECONDS",
            "AVATAR_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS",
        ),
        default_soft,
        minimum=1,
    )
    hard_default = int(soft_seconds + hard_margin)
    hard_seconds, hard_source = _env_int_first(
        env_map,
        (
            "AVATAR_PREVIEW_TASK_HARD_TIME_LIMIT_SECONDS",
            "AVATAR_PREVIEW_TASK_HARD_TIMEOUT_SECONDS",
        ),
        hard_default,
        minimum=1,
    )

    sources = {
        "soft": soft_source or "adaptive_default",
        "hard": hard_source or "soft_plus_margin",
        "safety_margin": safety_source or "default",
        "hard_margin": hard_margin_source or "default",
        "minimum_soft": minimum_soft_source or "default",
    }
    warnings: list[str] = []
    errors: list[str] = []

    if soft_seconds <= max(stage_maxima.values() or [0.0]):
        errors.append(
            "preview task soft time limit must be greater than the largest stage timeout "
            f"(soft={soft_seconds}s largest_stage={max(stage_maxima.values() or [0.0]):.1f}s)"
        )
    if soft_seconds < required_soft:
        errors.append(
            "preview task soft time limit must cover TTS + LivePortrait + MuseTalk + restoration + safety margin "
            f"(soft={soft_seconds}s required={required_soft}s stage_maxima={stage_maxima})"
        )
    if hard_seconds <= soft_seconds:
        errors.append(
            f"preview task hard time limit must be greater than soft time limit (hard={hard_seconds}s soft={soft_seconds}s)"
        )

    required_hard = max(int(required_soft + hard_margin), int(soft_seconds + hard_margin))
    if hard_seconds < required_hard and not hard_source:
        hard_seconds = required_hard
    elif hard_seconds < required_hard:
        warnings.append(
            f"preview task hard time limit has less than the recommended margin (hard={hard_seconds}s recommended={required_hard}s)"
        )

    strict = _env_enabled(env_map, "AVATAR_PREVIEW_TASK_TIME_LIMIT_STRICT", True)
    if errors:
        message = "; ".join(errors)
        if strict:
            raise PreviewTaskTimeLimitConfigError(message)
        warnings.extend(errors)
        log.warning("Avatar preview task time-limit configuration warning: %s", message)

    limits = PreviewTaskTimeLimits(
        soft_seconds=int(soft_seconds),
        hard_seconds=int(hard_seconds),
        required_soft_seconds=int(required_soft),
        required_hard_seconds=int(required_hard),
        safety_margin_seconds=int(safety_margin),
        hard_margin_seconds=int(hard_margin),
        stage_maxima_seconds=stage_maxima,
        sources=sources,
        warnings=warnings,
    )
    log.info(
        "Avatar preview task time-limit policy soft_seconds=%s hard_seconds=%s required_soft_seconds=%s "
        "stage_maxima_seconds=%s sources=%s warnings=%s",
        limits.soft_seconds,
        limits.hard_seconds,
        limits.required_soft_seconds,
        limits.stage_maxima_seconds,
        limits.sources,
        limits.warnings,
    )
    return limits
