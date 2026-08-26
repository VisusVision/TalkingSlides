"""Deterministic render-time planning from a Motion Style V2 package."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import math
from typing import Any


MOTION_PLAN_VERSION = "motion-plan-v2"
_MOTION_STYLE_VERSION = "motion-style-v2"
_CALM_EMOTIONS = {"calm", "serious", "sad", "thoughtful", "focused"}
_EXPRESSIVE_EMOTIONS = {"happy", "excited", "enthusiastic", "energetic", "surprised"}
_STYLE_PRESETS = {
    "calm": "natural_conservative",
    "natural": "natural_visible",
    "expressive": "natural_visible",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(float(minimum), min(float(maximum), _finite(value)))


def _emotion(value: Any) -> str:
    normalized = str(value or "neutral").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized[:48] or "neutral"


def _style_for_request(emotion: str, intensity: float) -> tuple[str, float]:
    effective = _clamp(intensity)
    if emotion in _CALM_EMOTIONS:
        effective = min(effective, 0.30)
    elif emotion in _EXPRESSIVE_EMOTIONS:
        effective = max(effective, 0.70)
    if effective < 0.34:
        return "calm", effective
    if effective >= 0.67:
        return "expressive", effective
    return "natural", effective


def _seed(seed_material: Any, package: Mapping[str, Any], emotion: str, intensity: float, style: str) -> int:
    raw = "|".join(
        [
            str(seed_material or ""),
            str(package.get("source_hash") or ""),
            str(package.get("version") or ""),
            emotion,
            f"{intensity:.6f}",
            style,
        ]
    )
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)


def _valid_intervals(package: Mapping[str, Any], style: str) -> list[dict[str, Any]]:
    selected = package.get("selected_intervals")
    if not isinstance(selected, Mapping):
        return []
    raw_candidates = selected.get(style)
    if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
        return []
    source_duration = max(_finite(package.get("duration_seconds")), 0.0)
    candidates: list[dict[str, Any]] = []
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            continue
        start = _finite(raw.get("start_seconds"), -1.0)
        end = _finite(raw.get("end_seconds"), -1.0)
        duration = _finite(raw.get("duration_seconds"), end - start)
        face_coverage = _clamp(raw.get("face_coverage"))
        landmark_coverage = _clamp(raw.get("landmark_coverage"))
        if start < 0.0 or end <= start or duration < 1.0:
            continue
        if source_duration > 0.0 and (start >= source_duration or end > source_duration + 0.25):
            continue
        if face_coverage < 0.60 or landmark_coverage < 0.35:
            continue
        candidates.append(
            {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": round(min(duration, end - start), 3),
                "score": round(_clamp(raw.get("score")), 5),
                "motion_intensity": round(_clamp(raw.get("motion_intensity")), 5),
                "expression_intensity": round(_clamp(raw.get("expression_intensity")), 5),
                "head_activity": round(_clamp(raw.get("head_activity")), 5),
                "face_coverage": round(face_coverage, 5),
                "landmark_coverage": round(landmark_coverage, 5),
            }
        )
    return candidates


def build_personal_motion_plan(
    motion_style_package: Mapping[str, Any] | None,
    request_payload: Mapping[str, Any] | None,
    *,
    seed_material: Any,
) -> dict[str, Any]:
    """Choose a personal performance interval without claiming model inference."""

    package = dict(motion_style_package or {})
    request = dict(request_payload or {})
    emotion = _emotion(request.get("emotion"))
    requested_intensity = _clamp(_finite(request.get("motion_intensity"), 0.5), 0.0, 1.0)
    style, effective_intensity = _style_for_request(emotion, requested_intensity)
    seed = _seed(seed_material, package, emotion, effective_intensity, style)
    fallback_reasons: list[str] = []
    if str(package.get("version") or "") != _MOTION_STYLE_VERSION:
        fallback_reasons.append("motion_style_v2_missing")
    if not bool(package.get("accepted")):
        fallback_reasons.append("motion_style_not_accepted")
    if not bool(package.get("usable_for_motion_planning")):
        fallback_reasons.append("motion_style_not_usable")

    candidates = [] if fallback_reasons else _valid_intervals(package, style)
    if not candidates and not fallback_reasons:
        fallback_reasons.append(f"{style}_interval_unavailable")
    selected_interval = dict(candidates[seed % len(candidates)]) if candidates else {}
    if selected_interval:
        selected_interval["style"] = style
        selected_interval["candidate_count"] = len(candidates)

    personal_window = bool(selected_interval)
    source = "motion_style_v2" if personal_window else "performance_window_v1_fallback"
    return {
        "version": MOTION_PLAN_VERSION,
        "source": source,
        "motion_style_version": str(package.get("version") or ""),
        "seed": seed,
        "emotion": emotion,
        "requested_intensity": round(requested_intensity, 5),
        "effective_intensity": round(effective_intensity, 5),
        "style": style,
        "motion_preset": _STYLE_PRESETS[style],
        "personal_window_selected": personal_window,
        "selected_interval": selected_interval,
        "performance_window": {
            "enabled": personal_window,
            "source": "motion_style_v2" if personal_window else "",
            "style": style if personal_window else "",
            "start_seconds": selected_interval.get("start_seconds", 0.0),
            "duration_seconds": selected_interval.get("duration_seconds", 0.0),
            "profile_score": selected_interval.get("score", 0.0),
        },
        "profile_snapshot": {
            "coverage": dict(package.get("coverage") or {}),
            "motion": dict(package.get("motion") or {}),
            "expression": dict(package.get("expression") or {}),
            "gaze": dict(package.get("gaze") or {}),
            "blink": dict(package.get("blink") or {}),
        },
        "fallback_reasons": list(dict.fromkeys(fallback_reasons)),
    }
