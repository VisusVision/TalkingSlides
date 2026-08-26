"""Lightweight, fail-safe speech energy analysis for avatar motion timing."""

from __future__ import annotations

from array import array
from collections.abc import Sequence
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


PROSODY_PROFILE_VERSION = "prosody-v1"
_PCM_SAMPLE_RATE = 16_000
_FRAME_SECONDS = 0.10
_MAX_TIMELINE_SEGMENTS = 24


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(float(minimum), min(float(maximum), _finite(value)))


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = _clamp(quantile) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _frame_dbfs(samples: Sequence[int]) -> float:
    if not samples:
        return -96.0
    stride = max(len(samples) // 400, 1)
    selected = samples[::stride]
    mean_square = sum(float(value) * float(value) for value in selected) / max(len(selected), 1)
    if mean_square <= 0.0:
        return -96.0
    return max(20.0 * math.log10(math.sqrt(mean_square) / 32768.0), -96.0)


def _unavailable_profile(reason: str, *, duration_seconds: float = 0.0) -> dict[str, Any]:
    return {
        "version": PROSODY_PROFILE_VERSION,
        "status": "unavailable",
        "accepted": False,
        "source": "audio_energy",
        "duration_seconds": round(max(_finite(duration_seconds), 0.0), 4),
        "sample_rate": _PCM_SAMPLE_RATE,
        "frame_seconds": _FRAME_SECONDS,
        "summary": {},
        "segments": [],
        "warnings": [str(reason or "prosody_analysis_unavailable")],
    }


def build_prosody_profile(samples: Sequence[int], *, sample_rate: int) -> dict[str, Any]:
    """Build a bounded calm/natural/expressive timeline from mono PCM samples."""

    resolved_rate = max(int(sample_rate or 0), 0)
    if resolved_rate <= 0 or not samples:
        return _unavailable_profile("prosody_pcm_empty")
    duration = len(samples) / float(resolved_rate)
    frame_size = max(int(round(resolved_rate * _FRAME_SECONDS)), 1)
    frames: list[dict[str, float]] = []
    for offset in range(0, len(samples), frame_size):
        frame = samples[offset : offset + frame_size]
        if not frame:
            continue
        frames.append(
            {
                "start_seconds": offset / float(resolved_rate),
                "end_seconds": min((offset + len(frame)) / float(resolved_rate), duration),
                "dbfs": _frame_dbfs(frame),
            }
        )
    if not frames:
        return _unavailable_profile("prosody_pcm_empty", duration_seconds=duration)

    levels = [float(frame["dbfs"]) for frame in frames]
    noise_floor = _percentile(levels, 0.20)
    peak = _percentile(levels, 0.90)
    dynamic_range = max(peak - noise_floor, 0.0)
    if peak < -55.0:
        profile = _unavailable_profile("prosody_audio_near_silent", duration_seconds=duration)
        profile["summary"] = {
            "noise_floor_dbfs": round(noise_floor, 3),
            "peak_dbfs": round(peak, 3),
            "dynamic_range_db": round(dynamic_range, 3),
        }
        return profile

    activity_threshold = min(peak - 6.0, max(-52.0, noise_floor + 6.0))
    normalization_span = max(peak - activity_threshold, 6.0)
    for frame in frames:
        level = float(frame["dbfs"])
        frame["active"] = 1.0 if level >= activity_threshold else 0.0
        frame["energy"] = _clamp((level - activity_threshold) / normalization_span)

    bucket_seconds = max(0.60, duration / float(_MAX_TIMELINE_SEGMENTS))
    raw_segments: list[dict[str, Any]] = []
    cursor = 0.0
    previous_energy = 0.0
    while cursor < duration - 1e-6:
        end = min(cursor + bucket_seconds, duration)
        selected = [frame for frame in frames if float(frame["start_seconds"]) < end and float(frame["end_seconds"]) > cursor]
        active_ratio = sum(float(frame["active"]) for frame in selected) / max(len(selected), 1)
        energy = sum(float(frame["energy"]) for frame in selected) / max(len(selected), 1)
        pause = active_ratio < 0.25
        if pause or energy < 0.38:
            style = "calm"
        elif energy < 0.72:
            style = "natural"
        else:
            style = "expressive"
        raw_segments.append(
            {
                "start_seconds": round(cursor, 4),
                "end_seconds": round(end, 4),
                "duration_seconds": round(end - cursor, 4),
                "style": style,
                "pause": pause,
                "energy": round(_clamp(energy), 5),
                "active_ratio": round(_clamp(active_ratio), 5),
                "emphasis": bool(not pause and (energy >= 0.72 or energy - previous_energy >= 0.28)),
            }
        )
        previous_energy = energy
        cursor = end

    merged: list[dict[str, Any]] = []
    for segment in raw_segments:
        if (
            merged
            and merged[-1]["style"] == segment["style"]
            and bool(merged[-1]["pause"]) == bool(segment["pause"])
            and bool(merged[-1]["emphasis"]) == bool(segment["emphasis"])
        ):
            previous_duration = float(merged[-1]["duration_seconds"])
            segment_duration = float(segment["duration_seconds"])
            total_duration = previous_duration + segment_duration
            merged[-1]["end_seconds"] = segment["end_seconds"]
            merged[-1]["duration_seconds"] = round(total_duration, 4)
            for key in ("energy", "active_ratio"):
                merged[-1][key] = round(
                    (
                        float(merged[-1][key]) * previous_duration
                        + float(segment[key]) * segment_duration
                    )
                    / max(total_duration, 1e-6),
                    5,
                )
            continue
        merged.append(dict(segment))

    if merged:
        elapsed_before_last = sum(float(segment["duration_seconds"]) for segment in merged[:-1])
        merged[-1]["duration_seconds"] = round(max(duration - elapsed_before_last, 0.0), 4)
        merged[-1]["end_seconds"] = round(duration, 4)

    speech_frames = sum(float(frame["active"]) for frame in frames)
    warnings = ["prosody_dynamic_range_low"] if dynamic_range < 6.0 else []
    return {
        "version": PROSODY_PROFILE_VERSION,
        "status": "ready",
        "accepted": True,
        "source": "audio_energy",
        "duration_seconds": round(duration, 4),
        "sample_rate": resolved_rate,
        "frame_seconds": _FRAME_SECONDS,
        "summary": {
            "noise_floor_dbfs": round(noise_floor, 3),
            "peak_dbfs": round(peak, 3),
            "dynamic_range_db": round(dynamic_range, 3),
            "speech_ratio": round(_clamp(speech_frames / max(len(frames), 1)), 5),
            "segment_count": len(merged),
            "emphasis_count": sum(bool(segment["emphasis"]) for segment in merged),
        },
        "segments": merged,
        "warnings": warnings,
    }


def analyze_audio_prosody(audio_path: str | Path, *, duration_hint: float = 0.0) -> dict[str, Any]:
    """Decode generated speech to mono PCM and fail open when tooling is unavailable."""

    path = Path(audio_path)
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return _unavailable_profile("prosody_audio_missing", duration_seconds=duration_hint)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _unavailable_profile("prosody_ffmpeg_unavailable", duration_seconds=duration_hint)
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(_PCM_SAMPLE_RATE),
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable_profile(
            f"prosody_decode_failed:{type(exc).__name__}",
            duration_seconds=duration_hint,
        )
    if process.returncode != 0 or len(process.stdout or b"") < 2:
        return _unavailable_profile("prosody_decode_failed", duration_seconds=duration_hint)
    payload = bytes(process.stdout)
    if len(payload) % 2:
        payload = payload[:-1]
    pcm = array("h")
    pcm.frombytes(payload)
    if sys.byteorder != "little":
        pcm.byteswap()
    return build_prosody_profile(pcm, sample_rate=_PCM_SAMPLE_RATE)
