#!/usr/bin/env python3
"""
liveportrait_motion_composer.py
================================
Builds deterministic driving clips for LivePortrait with explicit input kinds.

Current strategy:
    - image input: generate one continuous image-driven driving clip
    - video input: generate one continuous clip from the provided real video

This module intentionally avoids splicing together unrelated tiny clips for the
active compose path. Legacy helper routines remain available for tests and
debugging utilities.
"""
from __future__ import annotations

import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# ── tuneable constants (overridable via env) ──────────────────────────────────
# Human-like default cadence: blink around every 5s; gaze shifts every 40-100s.
_BLINK_MIN        = float(os.environ.get("LP_MOTION_BLINK_INTERVAL_MIN_S", "4.4"))
_BLINK_MAX        = float(os.environ.get("LP_MOTION_BLINK_INTERVAL_MAX_S", "5.8"))
_BLINK_DUR        = float(os.environ.get("LP_MOTION_BLINK_DURATION_S", "0.22"))
_BLINK_SHIFT_PX   = float(os.environ.get("LP_MOTION_BLINK_SHIFT_PX", "0.30"))

_GAZE_MIN         = float(os.environ.get("LP_MOTION_GAZE_INTERVAL_MIN_S",  "40.0"))
_GAZE_MAX         = float(os.environ.get("LP_MOTION_GAZE_INTERVAL_MAX_S",  "100.0"))
_GAZE_DUR_MIN     = float(os.environ.get("LP_MOTION_GAZE_DURATION_MIN_S", "2.0"))
_GAZE_DUR_MAX     = float(os.environ.get("LP_MOTION_GAZE_DURATION_MAX_S", "3.0"))

_HEAD_SHIFT_MAX_PX = float(os.environ.get("LP_MOTION_HEAD_SHIFT_MAX_PX", "1.20"))
_SHORT_PREVIEW_BOOTSTRAP_S = float(os.environ.get("LP_MOTION_SHORT_PREVIEW_BOOTSTRAP_S", "2.5"))
_TARGET_FPS       = int(os.environ.get("LP_MOTION_TARGET_FPS",             "25"))
_BASE_SWAY_X_PX   = float(os.environ.get("LP_MOTION_BASE_SWAY_X_PX",       "0.24"))
_BASE_SWAY_Y_PX   = float(os.environ.get("LP_MOTION_BASE_SWAY_Y_PX",       "0.18"))

_NODS_ENABLED = str(os.environ.get("LP_MOTION_ENABLE_NODS", "0")).strip().lower() in {"1", "true", "yes", "on"}
_CONTINUOUS_EYE_WANDER_ENABLED = str(
    os.environ.get("LP_MOTION_ENABLE_CONTINUOUS_EYE_WANDER", "0")
).strip().lower() in {"1", "true", "yes", "on"}

_DIRECTIONS = ["left", "right", "up", "down", "top-left", "top-right", "bottom-left", "bottom-right"]
_DEFAULT_MOTION_PRESET = "natural_conservative"
_ALLOWED_MOTION_PRESETS = {"natural_conservative", "subtle_blink", "subtle_gaze", "expressive_debug"}
_BOOSTED_PROFILES = {"boosted", "boosted_strong", "stronger", "strong"}


def _truthy_env(name: str, default: str = "0") -> bool:
    raw = str(os.environ.get(name, default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_motion_preset(value: str | None = None) -> str:
    raw = str(value if value is not None else os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_PRESET", "")).strip().lower()
    if raw in _ALLOWED_MOTION_PRESETS:
        return raw
    return _DEFAULT_MOTION_PRESET


def boosted_retry_allowed(*, motion_preset: str | None = None, env_value: str | None = None) -> bool:
    preset = resolve_motion_preset(motion_preset)
    if preset == "expressive_debug":
        return True
    if env_value is not None:
        return str(env_value).strip().lower() in {"1", "true", "yes", "on"}
    return _truthy_env("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", "0")


def profile_sequence_for_preset(*, motion_preset: str | None = None, allow_boosted_retry: bool | None = None) -> list[str]:
    preset = resolve_motion_preset(motion_preset)
    allow_boosted = boosted_retry_allowed(motion_preset=preset) if allow_boosted_retry is None else bool(allow_boosted_retry)
    profiles = ["default"]
    if allow_boosted:
        profiles.extend(["boosted", "boosted_strong"])
    return profiles


def _preset_settings(preset: str, *, motion_profile: str = "default") -> dict[str, Any]:
    resolved = resolve_motion_preset(preset)
    profile = str(motion_profile or "default").strip().lower()
    boosted_profile = profile in _BOOSTED_PROFILES
    if resolved == "subtle_blink":
        return {
            "preset": resolved,
            "gaze_enabled": False,
            "directions": ["left", "right"],
            "head_shift_cap_px": 0.12,
            "boosted_head_shift_cap_px": 0.28,
            "head_shift_min_px": 0.0,
            "blink_shift_cap_px": 0.26 if not boosted_profile else 0.42,
            "blink_shift_min_px": 0.10,
            "base_sway_x_cap_px": 0.018,
            "base_sway_y_cap_px": 0.014,
            "gaze_interval_scale": 1.6,
            "gaze_duration_scale": 0.55,
            "short_gaze_scale": 0.0,
            "short_gaze_min_px": 0.0,
            "recenter_enabled": True,
            "whole_frame_drift_guard": True,
        }
    if resolved == "subtle_gaze":
        return {
            "preset": resolved,
            "gaze_enabled": True,
            "directions": ["left", "right"],
            "head_shift_cap_px": 0.48,
            "boosted_head_shift_cap_px": 0.85,
            "head_shift_min_px": 0.18,
            "blink_shift_cap_px": 0.28 if not boosted_profile else 0.52,
            "blink_shift_min_px": 0.12,
            "base_sway_x_cap_px": 0.045,
            "base_sway_y_cap_px": 0.035,
            "gaze_interval_scale": 1.15,
            "gaze_duration_scale": 0.78,
            "short_gaze_scale": 0.62,
            "short_gaze_min_px": 0.22,
            "recenter_enabled": True,
            "whole_frame_drift_guard": True,
        }
    if resolved == "expressive_debug":
        return {
            "preset": resolved,
            "gaze_enabled": True,
            "directions": list(_DIRECTIONS),
            "head_shift_cap_px": 2.8,
            "boosted_head_shift_cap_px": 2.8,
            "head_shift_min_px": 0.45,
            "blink_shift_cap_px": 1.2,
            "blink_shift_min_px": 0.15,
            "base_sway_x_cap_px": 0.80,
            "base_sway_y_cap_px": 0.70,
            "gaze_interval_scale": 1.0,
            "gaze_duration_scale": 1.0,
            "short_gaze_scale": 0.85,
            "short_gaze_min_px": 0.60,
            "recenter_enabled": True,
            "whole_frame_drift_guard": False,
        }
    return {
        "preset": "natural_conservative",
        "gaze_enabled": True,
        "directions": ["left", "right", "up", "down"],
        "head_shift_cap_px": 0.72,
        "boosted_head_shift_cap_px": 1.05,
        "head_shift_min_px": 0.24,
        "blink_shift_cap_px": 0.30 if not boosted_profile else 0.58,
        "blink_shift_min_px": 0.12,
        "base_sway_x_cap_px": 0.060,
        "base_sway_y_cap_px": 0.045,
        "gaze_interval_scale": 1.10,
        "gaze_duration_scale": 0.82,
        "short_gaze_scale": 0.58,
        "short_gaze_min_px": 0.28,
        "recenter_enabled": True,
        "whole_frame_drift_guard": True,
    }


def _profile_scales(profile: str) -> dict[str, float]:
    normalized = str(profile or "default").strip().lower()
    if normalized in {"boosted", "stronger"}:
        return {
            "blink_interval": 0.78,
            "gaze_interval": 0.55,
            "head_shift": 1.45,
            "blink_shift": 1.70,
            "base_sway": 1.35,
        }
    if normalized in {"boosted_strong", "strong"}:
        return {
            "blink_interval": 0.65,
            "gaze_interval": 0.40,
            "head_shift": 1.80,
            "blink_shift": 2.10,
            "base_sway": 1.70,
        }
    return {
        "blink_interval": 1.00,
        "gaze_interval": 1.00,
        "head_shift": 1.00,
        "blink_shift": 1.00,
        "base_sway": 1.00,
    }


def _direction_to_offsets(direction: str, amplitude: float) -> tuple[float, float]:
    amp = max(float(amplitude), 0.0)
    direction_map: dict[str, tuple[float, float]] = {
        "left": (-amp, 0.0),
        "right": (amp, 0.0),
        "up": (0.0, -amp),
        "down": (0.0, amp),
        "top-left": (-amp * 0.72, -amp * 0.72),
        "top-right": (amp * 0.72, -amp * 0.72),
        "bottom-left": (-amp * 0.72, amp * 0.72),
        "bottom-right": (amp * 0.72, amp * 0.72),
    }
    return direction_map.get(str(direction or "").strip().lower(), (0.0, 0.0))


def _bounded(value: float, *, minimum: float, maximum: float) -> float:
    return min(max(float(value), float(minimum)), float(maximum))


def _gaze_event(
    *,
    start_s: float,
    duration_s: float,
    direction: str,
    dx_px: float,
    dy_px: float,
    recenter_enabled: bool,
) -> dict[str, Any]:
    event = {
        "start_s": round(float(start_s), 4),
        "duration_s": round(float(duration_s), 4),
        "direction": str(direction),
        "dx_px": round(float(dx_px), 4),
        "dy_px": round(float(dy_px), 4),
    }
    if recenter_enabled:
        event.update(
            {
                "recenter_enabled": True,
                "recenter_after_s": round(float(start_s) + float(duration_s), 4),
                "recenter_duration_s": 0.24,
            }
        )
    return event


def _build_motion_recipe(
    target_duration_s: float,
    seed: int = 42,
    *,
    motion_profile: str = "default",
    motion_preset: str | None = None,
) -> dict[str, Any]:
    duration = max(float(target_duration_s), 0.0)
    rng = random.Random(int(seed))
    resolved_preset = resolve_motion_preset(motion_preset)
    preset_settings = _preset_settings(resolved_preset, motion_profile=motion_profile)
    profile_name = str(motion_profile or "default").strip().lower() or "default"
    boosted_profile = profile_name in _BOOSTED_PROFILES
    scales = _profile_scales(motion_profile)

    effective_blink_min = max(float(_BLINK_MIN) * float(scales["blink_interval"]), 0.9)
    effective_blink_max = max(float(_BLINK_MAX) * float(scales["blink_interval"]), effective_blink_min + 0.2)
    effective_gaze_min = max(float(_GAZE_MIN) * float(scales["gaze_interval"]) * float(preset_settings["gaze_interval_scale"]), 1.2)
    effective_gaze_max = max(float(_GAZE_MAX) * float(scales["gaze_interval"]), effective_gaze_min + 0.6)
    head_cap = float(preset_settings["boosted_head_shift_cap_px"] if boosted_profile else preset_settings["head_shift_cap_px"])
    effective_head_shift_max_px = _bounded(
        float(_HEAD_SHIFT_MAX_PX) * float(scales["head_shift"]),
        minimum=float(preset_settings["head_shift_min_px"]),
        maximum=head_cap,
    )
    effective_blink_shift_px = _bounded(
        float(_BLINK_SHIFT_PX) * float(scales["blink_shift"]),
        minimum=float(preset_settings["blink_shift_min_px"]),
        maximum=float(preset_settings["blink_shift_cap_px"]),
    )
    effective_base_sway_x_px = _bounded(
        float(_BASE_SWAY_X_PX) * float(scales["base_sway"]),
        minimum=0.0,
        maximum=float(preset_settings["base_sway_x_cap_px"]),
    )
    effective_base_sway_y_px = _bounded(
        float(_BASE_SWAY_Y_PX) * float(scales["base_sway"]),
        minimum=0.0,
        maximum=float(preset_settings["base_sway_y_cap_px"]),
    )
    short_preview_threshold = max(float(_SHORT_PREVIEW_BOOTSTRAP_S), 6.0)

    blink_events_s: list[float] = []
    gaze_events: list[dict[str, Any]] = []

    if duration <= short_preview_threshold:
        if bool(preset_settings["gaze_enabled"]):
            directions = list(preset_settings["directions"] or ["left", "right"])
            gaze_dir = rng.choice(directions)
            gaze_start = max(min(duration * 0.08, 0.20), 0.03)
            gaze_duration = min(max(duration * 0.55, 0.60), 1.60)
            short_gaze_amplitude = _bounded(
                effective_head_shift_max_px * float(preset_settings["short_gaze_scale"]),
                minimum=float(preset_settings["short_gaze_min_px"]),
                maximum=head_cap,
            )
            gaze_dx, gaze_dy = _direction_to_offsets(gaze_dir, short_gaze_amplitude)
            gaze_events.append(
                _gaze_event(
                    start_s=gaze_start,
                    duration_s=gaze_duration,
                    direction=gaze_dir,
                    dx_px=gaze_dx,
                    dy_px=gaze_dy,
                    recenter_enabled=bool(preset_settings["recenter_enabled"]),
                )
            )
            if duration >= 3.2:
                second_choices = [direction for direction in directions if direction != gaze_dir] or directions
                second_gaze_dir = rng.choice(second_choices)
                second_gaze_start = max(min(duration * 0.56, duration - 0.65), 0.55)
                second_gaze_duration = min(max(duration * 0.28, 0.45), 1.05)
                second_amplitude = _bounded(
                    effective_head_shift_max_px * max(float(preset_settings["short_gaze_scale"]) * 0.82, 0.1),
                    minimum=max(float(preset_settings["short_gaze_min_px"]) * 0.75, 0.0),
                    maximum=head_cap,
                )
                second_dx, second_dy = _direction_to_offsets(second_gaze_dir, second_amplitude)
                gaze_events.append(
                    _gaze_event(
                        start_s=second_gaze_start,
                        duration_s=second_gaze_duration,
                        direction=second_gaze_dir,
                        dx_px=second_dx,
                        dy_px=second_dy,
                        recenter_enabled=bool(preset_settings["recenter_enabled"]),
                    )
                )
        if duration >= 0.8:
            first_blink = max(min(duration * 0.34, duration - 0.20), 0.24)
            blink_events_s.append(round(float(first_blink), 4))
            if duration >= 3.8:
                second_blink = max(min(duration * 0.74, duration - 0.14), first_blink + 0.55)
                if second_blink < duration:
                    blink_events_s.append(round(float(second_blink), 4))
    else:
        first_blink_s = rng.uniform(max(effective_blink_min * 0.7, 1.0), max(effective_blink_max * 0.95, 1.8))
        blink_cursor = float(first_blink_s)
        while blink_cursor < duration:
            blink_events_s.append(round(blink_cursor, 4))
            blink_cursor += rng.uniform(effective_blink_min, effective_blink_max)

        if bool(preset_settings["gaze_enabled"]):
            directions = list(preset_settings["directions"] or _DIRECTIONS)
            gaze_cursor = rng.uniform(effective_gaze_min, effective_gaze_max)
            while gaze_cursor < duration:
                gaze_dir = rng.choice(directions)
                gaze_duration = rng.uniform(_GAZE_DUR_MIN, _GAZE_DUR_MAX) * float(preset_settings["gaze_duration_scale"])
                gaze_duration = _bounded(gaze_duration, minimum=0.45, maximum=float(_GAZE_DUR_MAX))
                gaze_dx, gaze_dy = _direction_to_offsets(gaze_dir, effective_head_shift_max_px)
                gaze_events.append(
                    _gaze_event(
                        start_s=gaze_cursor,
                        duration_s=gaze_duration,
                        direction=gaze_dir,
                        dx_px=gaze_dx,
                        dy_px=gaze_dy,
                        recenter_enabled=bool(preset_settings["recenter_enabled"]),
                    )
                )
                gaze_cursor += rng.uniform(effective_gaze_min, effective_gaze_max)

    blink_intervals_s: list[float] = []
    for idx in range(1, len(blink_events_s)):
        blink_intervals_s.append(round(float(blink_events_s[idx] - blink_events_s[idx - 1]), 4))

    return {
        "recipe": "calm_sparse_human_v1",
        "motion_preset": resolved_preset,
        "motion_profile": str(motion_profile or "default"),
        "boosted_profile": bool(boosted_profile),
        "target_duration_s": round(duration, 4),
        "seed": int(seed),
        "nods_enabled": bool(_NODS_ENABLED),
        "continuous_eye_wander_enabled": bool(_CONTINUOUS_EYE_WANDER_ENABLED),
        "recenter_enabled": bool(preset_settings["recenter_enabled"]),
        "whole_frame_drift_guard": bool(preset_settings["whole_frame_drift_guard"]),
        "gaze_enabled": bool(preset_settings["gaze_enabled"]),
        "gaze_shift_max_px": round(float(effective_head_shift_max_px), 4),
        "blink_events_s": blink_events_s,
        "blink_intervals_s": blink_intervals_s,
        "blink_duration_s": round(float(_BLINK_DUR), 4),
        "blink_shift_px": round(float(effective_blink_shift_px), 4),
        "gaze_events": gaze_events,
        "head_shift_max_px": round(float(effective_head_shift_max_px), 4),
        "base_sway_x_px": round(float(effective_base_sway_x_px), 4),
        "base_sway_y_px": round(float(effective_base_sway_y_px), 4),
        "base_sway_x_period_s": 3.4,
        "base_sway_y_period_s": 4.2,
        "base_sway_phase_x": round((float(seed % 17) / 17.0) * math.pi * 2.0, 6),
        "base_sway_phase_y": round((float(seed % 13) / 13.0) * math.pi * 2.0, 6),
        "short_preview_mode": bool(duration <= short_preview_threshold),
    }


def _event_window_expr(start_s: float, duration_s: float) -> str:
    start = max(float(start_s), 0.0)
    dur = max(float(duration_s), 1e-4)
    end = start + dur
    # 0->1->0 pulse over [start, end] with smooth sin profile.
    return f"between(t,{start:.6f},{end:.6f})*sin(PI*(t-{start:.6f})/{dur:.6f})"


def _build_axis_expr(*, recipe: dict[str, Any], axis: str, center_px: float) -> str:
    expr = f"{float(center_px):.6f}"

    if axis == "x":
        base_sway = float(recipe.get("base_sway_x_px") or 0.0)
        period = max(float(recipe.get("base_sway_x_period_s") or 3.4), 0.2)
        phase = float(recipe.get("base_sway_phase_x") or 0.0)
    else:
        base_sway = float(recipe.get("base_sway_y_px") or 0.0)
        period = max(float(recipe.get("base_sway_y_period_s") or 4.2), 0.2)
        phase = float(recipe.get("base_sway_phase_y") or 0.0)

    if abs(base_sway) >= 1e-6:
        expr += f"+({base_sway:.6f})*sin((2*PI/{period:.6f})*t+{phase:.6f})"

    for gaze in list(recipe.get("gaze_events") or []):
        delta = float(gaze.get("dx_px") if axis == "x" else gaze.get("dy_px") or 0.0)
        if abs(delta) < 1e-6:
            continue
        window = _event_window_expr(float(gaze.get("start_s") or 0.0), float(gaze.get("duration_s") or 0.0))
        expr += f"+({delta:.6f})*({window})"

    if axis == "y":
        blink_dur = max(float(recipe.get("blink_duration_s") or _BLINK_DUR), 1e-4)
        blink_shift = float(recipe.get("blink_shift_px") or _BLINK_SHIFT_PX)
        for blink_center in list(recipe.get("blink_events_s") or []):
            blink_start = max(float(blink_center) - (blink_dur / 2.0), 0.0)
            blink_window = _event_window_expr(blink_start, blink_dur)
            expr += f"+({float(blink_shift):.6f})*({blink_window})"

    return expr.replace(",", r"\,")

def _lp_driving_dir() -> Path:
    lp_home = os.environ.get("AVATAR_LIVEPORTRAIT_HOME", "/opt/liveportrait")
    return Path(lp_home) / "assets" / "examples" / "driving"


# ── clip definitions ──────────────────────────────────────────────────────────
# We use the FIRST FRAME of d0.mp4 for frozen neutral (no nodding/scanning).
# We use d11.mp4 for a natural blink.
# Gaze is mathematically generated by panning the frozen frame.

_CLIP_SPECS = {
    "idle_src": ("d0.mp4",  0.0),    # we will extract a frozen frame
    "blink":    ("d11.mp4", 2.3),    # natural blink at t=2.3
}
_FALLBACK = "d0.mp4"


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def _extract_frozen_frame(src: Path, out: Path) -> bool:
    """Extract a single frozen frame to be used for idle and synthentic gaze."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "trim=start=0:end=0.04",
        "-frames:v", "1",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0

def _loop_frozen(src_img: Path, duration_s: float, fps: int, amp: float, out: Path) -> bool:
    """Loop the frozen frame for IDLE so it has identical crop boundaries."""
    vf = (
        f"loop=loop=-1:size=1,format=yuv420p,"
        f"crop=iw-{2*int(amp)}:ih-{2*int(amp)}:{int(amp)}:{int(amp)},"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(src_img),
        "-vf", vf, "-t", str(duration_s),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=60)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0

def _extract_blink(src: Path, start_s: float, duration_s: float, fps: int, amp: float, out: Path) -> bool:
    """Extract blink and apply matching crop."""
    vf = (
        f"trim=start={start_s}:duration={duration_s},"
        "setpts=PTS-STARTPTS,"
        f"crop=iw-{2*int(amp)}:ih-{2*int(amp)}:{int(amp)}:{int(amp)},"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0

def _generate_synthetic_gaze(src_img: Path, duration_s: float, fps: int, dx: float, dy: float, out: Path) -> bool:
    """Generate subtle head translation aligned with chosen gaze direction."""
    amp = _HEAD_SHIFT_MAX_PX
    # For short preview clips, scale easing to duration so motion can still
    # reach full amplitude instead of staying near-static.
    effective_ease = min(1.0, max(float(duration_s) / 2.0, 0.12))
    m_expr = f"sin(PI/2*min(1,max(0,min(t/{effective_ease:.6f},({duration_s}-t)/{effective_ease:.6f}))))"
    m_expr = m_expr.replace(",", r"\,")
    vf = (
        f"loop=loop=-1:size=1,format=yuv420p,"
        f"crop=iw-{2*int(amp)}:ih-{2*int(amp)}:{int(amp)}+({dx})*{m_expr}:{int(amp)}+({dy})*{m_expr},"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(src_img),
        "-vf", vf, "-t", str(duration_s),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=60)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0

def _concat_clips(clip_paths: list[Path], out: Path) -> bool:
    """Concatenate a list of mp4s into a single output using robust filter_complex."""
    cmd = ["ffmpeg", "-y"]
    filter_inputs = ""
    for idx, cp in enumerate(clip_paths):
        cmd.extend(["-i", str(cp)])
        filter_inputs += f"[{idx}:v]"
        
    filter_complex = f"{filter_inputs}concat=n={len(clip_paths)}:v=1:a=0[v]"
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264", 
        "-preset", "veryfast", 
        "-crf", "18",
        str(out)
    ])
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def _loop_clip(src: Path, target_duration_s: float, fps: int, out: Path) -> bool:
    """Loop src until target_duration_s is reached, then trim exactly."""
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(src),
        "-vf", f"fps={fps},scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-t", str(target_duration_s),
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def _render_continuous_image_motion(
    *,
    src_image: Path,
    target_duration_s: float,
    fps: int,
    recipe: dict[str, Any],
    out: Path,
) -> bool:
    """Render one continuous image-driven clip with sparse blink/gaze events."""
    recipe_head_shift = abs(float(recipe.get("head_shift_max_px") or _HEAD_SHIFT_MAX_PX))
    recipe_blink_shift = abs(float(recipe.get("blink_shift_px") or _BLINK_SHIFT_PX))
    recipe_base_sway = max(
        abs(float(recipe.get("base_sway_x_px") or 0.0)),
        abs(float(recipe.get("base_sway_y_px") or 0.0)),
    )
    border_px = max(
        int(math.ceil(recipe_head_shift + recipe_blink_shift + recipe_base_sway + 1.0)),
        2,
    )
    x_expr = _build_axis_expr(recipe=recipe, axis="x", center_px=float(border_px))
    y_expr = _build_axis_expr(recipe=recipe, axis="y", center_px=float(border_px))
    vf = (
        f"crop=iw-{2 * border_px}:ih-{2 * border_px}:{x_expr}:{y_expr},"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={int(fps)}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(src_image),
        "-vf", vf,
        "-t", f"{float(target_duration_s):.6f}",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def _resolve_default_source_image() -> Path | None:
    raw_env = str(os.environ.get("LP_MOTION_DEFAULT_SOURCE_IMAGE", "")).strip()
    if raw_env:
        candidate = Path(raw_env)
        if candidate.exists():
            return candidate
    lp_home = Path(str(os.environ.get("AVATAR_LIVEPORTRAIT_HOME", "/opt/liveportrait")) or "/opt/liveportrait")
    fallback = lp_home / "assets" / "examples" / "source" / "s0.jpg"
    if fallback.exists():
        return fallback
    return None


# ── schedule builder ──────────────────────────────────────────────────────────

def _build_schedule(target_duration_s: float, seed: int = 42) -> list[tuple[str, float]]:
    recipe = _build_motion_recipe(target_duration_s, seed=int(seed))
    schedule: list[tuple[str, float]] = []
    timeline: list[tuple[float, str, float]] = []
    blink_duration = max(float(recipe.get("blink_duration_s") or _BLINK_DUR), 1e-4)
    for blink_center in list(recipe.get("blink_events_s") or []):
        start_s = max(float(blink_center) - (blink_duration / 2.0), 0.0)
        timeline.append((start_s, "blink", blink_duration))
    for gaze in list(recipe.get("gaze_events") or []):
        timeline.append(
            (
                max(float(gaze.get("start_s") or 0.0), 0.0),
                f"gaze_{str(gaze.get('direction') or '').strip().lower()}",
                max(float(gaze.get("duration_s") or 0.0), 0.0),
            )
        )
    timeline.sort(key=lambda item: item[0])

    cursor = 0.0
    target = max(float(target_duration_s), 0.0)
    for start_s, event_name, duration_s in timeline:
        event_start = max(float(start_s), cursor)
        if event_start > target:
            break
        idle_dur = event_start - cursor
        if idle_dur > 1e-6:
            schedule.append(("idle", idle_dur))
            cursor += idle_dur

        event_dur = max(min(float(duration_s), target - cursor), 0.0)
        if event_dur > 1e-6:
            schedule.append((event_name, event_dur))
            cursor += event_dur

    tail = target - cursor
    if tail > 1e-6:
        schedule.append(("idle", tail))

    return schedule


# ── public API ────────────────────────────────────────────────────────────────

def compose(
    target_duration_s: float,
    output_path: Path,
    *,
    source_kind: str = "image",
    source_image_path: Path | None = None,
    source_video_path: Path | None = None,
    seed: int = 42,
    verbose: bool = True,
    motion_profile: str = "default",
    motion_preset: str | None = None,
    requested_fps: float = 0.0,
    target_frame_count: int = 0,
    expected_duration_seconds: float | None = None,
    render_fps: int | None = None,
) -> bool:
    """
    Create a composite LP driving video at output_path.

    Returns True on success, False on failure (caller should fall back to a
    simple driving video rather than failing the whole pipeline).
    """
    fps = max(int(render_fps or _TARGET_FPS), 1)
    resolved_kind = str(source_kind or "image").strip().lower()
    if resolved_kind not in {"image", "video"}:
        print(f"[motion_composer] ERROR: unsupported source_kind={source_kind}", file=sys.stderr)
        return False

    target_duration_s = max(float(target_duration_s), 0.5)
    resolved_preset = resolve_motion_preset(motion_preset)
    requested_fps_value = max(float(requested_fps or 0.0), 0.0)
    target_frame_count_value = max(int(target_frame_count or 0), 0)
    expected_duration_value = (
        max(float(expected_duration_seconds or 0.0), 0.0)
        if expected_duration_seconds is not None
        else max(float(target_duration_s), 0.0)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(
            "[motion_composer] contract "
            f"requested_fps={requested_fps_value:.4f} "
            f"internal_fps={fps} "
            f"target_frame_count={target_frame_count_value} "
            f"target_duration_seconds={float(target_duration_s):.4f} "
            f"expected_duration_seconds={expected_duration_value:.4f}",
            file=sys.stderr,
        )

    if resolved_kind == "image":
        resolved_image = source_image_path or _resolve_default_source_image()
        if resolved_image is None or not resolved_image.exists():
            print("[motion_composer] ERROR: image source is missing", file=sys.stderr)
            return False
        recipe = _build_motion_recipe(
            target_duration_s,
            seed=int(seed),
            motion_profile=motion_profile,
            motion_preset=resolved_preset,
        )
        if verbose:
            import json as _json
            print(
                "[motion_composer] "
                f"nods_disabled={int(not bool(recipe.get('nods_enabled')))} "
                f"continuous_eye_wander_disabled={int(not bool(recipe.get('continuous_eye_wander_enabled')))} "
                f"motion_preset={resolved_preset} "
                f"motion_profile={motion_profile} "
                f"recenter_enabled={int(bool(recipe.get('recenter_enabled')))} "
                f"whole_frame_drift_guard={int(bool(recipe.get('whole_frame_drift_guard')))}",
                file=sys.stderr,
            )
            print(
                "[motion_composer] "
                f"blink_schedule_s={recipe.get('blink_events_s') or []} "
                f"blink_intervals_s={recipe.get('blink_intervals_s') or []} "
                f"blink_interval_range_s=({float(_BLINK_MIN):.3f},{float(_BLINK_MAX):.3f})",
                file=sys.stderr,
            )
            for idx, gaze in enumerate(list(recipe.get("gaze_events") or [])):
                print(
                    "[motion_composer] "
                    f"gaze_event index={idx} "
                    f"start_s={float(gaze.get('start_s') or 0.0):.3f} "
                    f"duration_s={float(gaze.get('duration_s') or 0.0):.3f} "
                    f"direction={str(gaze.get('direction') or '')} "
                    f"head_dx_px={float(gaze.get('dx_px') or 0.0):.3f} "
                    f"head_dy_px={float(gaze.get('dy_px') or 0.0):.3f}",
                    file=sys.stderr,
                )
            print(
                "[motion_composer] motion_recipe=" + _json.dumps(recipe, sort_keys=True),
                file=sys.stderr,
            )
            print(
                "[motion_composer] "
                f"source_kind=image source_path={resolved_image} "
                f"strategy=single_continuous_image_motion duration={target_duration_s:.3f}s fps={fps} seed={seed} "
                f"motion_preset={resolved_preset} motion_profile={motion_profile}",
                file=sys.stderr,
            )
        ok = _render_continuous_image_motion(
            src_image=resolved_image,
            target_duration_s=target_duration_s,
            fps=fps,
            recipe=recipe,
            out=output_path,
        )
    else:
        if source_video_path is None or not source_video_path.exists():
            print("[motion_composer] ERROR: video source is missing", file=sys.stderr)
            return False
        if verbose:
            print(
                "[motion_composer] "
                f"nods_disabled={int(not _NODS_ENABLED)} "
                f"continuous_eye_wander_disabled={int(not _CONTINUOUS_EYE_WANDER_ENABLED)}",
                file=sys.stderr,
            )
            print(
                "[motion_composer] "
                f"source_kind=video source_path={source_video_path} "
                f"strategy=single_continuous_video_loop duration={target_duration_s:.3f}s fps={fps} "
                f"motion_preset={resolved_preset}",
                file=sys.stderr,
            )
        ok = _loop_clip(source_video_path, target_duration_s, fps, output_path)

    if not ok:
        return False

    if verbose:
        import json as _json
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration,nb_frames",
             "-of", "json", str(output_path)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        try:
            st = _json.loads(probe.stdout)["streams"][0]
            print(f"[motion_composer] output={output_path}  "
                  f"duration={st.get('duration','?')}s  "
                  f"frames={st.get('nb_frames','?')}", file=sys.stderr)
        except Exception:
            pass

    return True


# ── CLI (smoke test) ──────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="LP motion composer — standalone smoke test")
    parser.add_argument("--duration", type=float, default=30.0, help="Target driving video duration (s)")
    parser.add_argument("--output",   default="/tmp/lp_composed_drive.mp4")
    parser.add_argument("--source_kind", choices=["image", "video"], default="image")
    parser.add_argument("--source_image", default="")
    parser.add_argument("--source_video", default="")
    parser.add_argument("--seed",     type=int, default=42)
    parser.add_argument("--motion_preset", default="")
    parser.add_argument("--motion_profile", default="default")
    args = parser.parse_args()

    t0 = time.monotonic()
    ok = compose(
        args.duration,
        Path(args.output),
        source_kind=str(args.source_kind or "image"),
        source_image_path=(Path(args.source_image) if str(args.source_image or "").strip() else None),
        source_video_path=(Path(args.source_video) if str(args.source_video or "").strip() else None),
        seed=args.seed,
        verbose=True,
        motion_preset=str(args.motion_preset or ""),
        motion_profile=str(args.motion_profile or "default"),
    )
    elapsed = time.monotonic() - t0

    if ok:
        print(f"[motion_composer] DONE in {elapsed:.1f}s  output={args.output}")
        return 0
    else:
        print(f"[motion_composer] FAILED after {elapsed:.1f}s", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
