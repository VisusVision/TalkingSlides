from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from celery.exceptions import SoftTimeLimitExceeded
except Exception:  # pragma: no cover - celery is present in worker runtime.
    class SoftTimeLimitExceeded(Exception):  # type: ignore[no-redef]
        pass

from . import pipeline as legacy_pipeline
from .canonical_adapters import CANONICAL_ENGINE, MUSETALK_ONLY_ENGINE, EngineResult, normalize_avatar_engine, run_liveportrait, run_musetalk, run_restoration
from .hashing import sha256_file
from .resource_manager import compute_adaptive_timeout, probe_runtime_resources, record_stage_timing, release_stage_resources
from .simple_input import canonicalize_avatar_input

logger = logging.getLogger(__name__)


def _is_preview_request(request: Any) -> bool:
    output_name = Path(str(getattr(request, "output_path", "") or "")).name.lower()
    return bool(
        output_name == "preview.mp4"
        or int(getattr(request, "preview_teacher_id", 0) or 0) > 0
        or int(getattr(request, "preview_job_id", 0) or 0) > 0
    )


def _restore_enabled(is_preview_request: bool) -> bool:
    if not is_preview_request:
        return False
    return str(os.environ.get("AVATAR_PREVIEW_USE_RESTORATION", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _update_preview_task_context(request: Any, **updates: Any) -> None:
    context = getattr(request, "_preview_task_context", None)
    if isinstance(context, dict):
        context.update(updates)


def _video_is_playable(path: Path, *, stage_name: str) -> bool:
    try:
        legacy_pipeline._assert_video_contract(str(path), stage_name=stage_name)
    except Exception:
        return False
    return True


def _file_debug_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "exists": False,
        "size_bytes": 0,
        "mtime": 0.0,
        "mtime_ns": 0,
        "sha256": "",
    }
    try:
        if path.exists() and path.is_file():
            stat = path.stat()
            info.update(
                {
                    "exists": True,
                    "size_bytes": int(stat.st_size),
                    "mtime": round(float(stat.st_mtime), 6),
                    "mtime_ns": int(getattr(stat, "st_mtime_ns", 0) or 0),
                }
            )
            if stat.st_size > 0:
                info["sha256"] = sha256_file(path)
    except Exception as exc:
        info["file_probe_error"] = str(exc)
    return info


def _shared_liveportrait_video_motion_probe(path: Path) -> dict[str, Any]:
    try:
        from scripts.liveportrait_runner import _probe_driving_clip_variation
    except Exception as exc:
        return {
            "path": str(path),
            "duration_seconds": 0.0,
            "fps": 0.0,
            "frame_count": 0,
            "unique_frames": 0,
            "unique_ratio": 0.0,
            "mean_mad": 0.0,
            "near_static": True,
            "failure_reason": f"shared_probe_import_failed:{exc}",
            "probe_errors": [f"shared_probe_import_failed:{exc}"],
        }

    try:
        return dict(_probe_driving_clip_variation(path))
    except Exception as exc:
        return {
            "path": str(path),
            "duration_seconds": 0.0,
            "fps": 0.0,
            "frame_count": 0,
            "unique_frames": 0,
            "unique_ratio": 0.0,
            "mean_mad": 0.0,
            "near_static": True,
            "failure_reason": f"shared_probe_failed:{exc}",
            "probe_errors": [f"shared_probe_failed:{exc}"],
        }


def _safe_validation(video_path: str, audio_path: str, *, fallback_reason: str) -> dict[str, Any]:
    try:
        return legacy_pipeline.validate_avatar_render_with_audio(video_path, audio_path)
    except Exception:
        return {
            "motion_real": False,
            "animated": False,
            "lip_motion_valid": False,
            "eye_motion_valid": False,
            "face_artifacts_detected": True,
            "audio_match": False,
            "frame_count": 0,
            "min_frames": 1,
            "duration_mismatch": True,
            "failure_reason": fallback_reason,
            "quality_checks": {},
        }


def _liveportrait_motion_gate(
    video_path: str,
    *,
    is_preview_request: bool,
    expected_duration_seconds: float = 0.0,
    expected_fps: float = 0.0,
    expected_frame_count: int = 0,
    stage_name: str = "liveportrait_motion_gate",
) -> dict[str, Any]:
    path = Path(str(video_path or ""))
    file_info = _file_debug_info(path)

    video_contract: dict[str, Any] = {}
    playable = False
    contract_error = ""
    try:
        video_contract = legacy_pipeline._assert_video_contract(str(path), stage_name=stage_name)
        playable = True
    except Exception as exc:
        contract_error = str(exc)

    metrics: dict[str, Any] = {}
    quality: dict[str, Any] = {}
    legacy_error = ""
    try:
        metrics = legacy_pipeline.validate_avatar_animation(str(path))
        quality = dict(metrics.get("quality_checks") or {})
    except Exception as exc:
        legacy_error = str(exc)

    default_min_unique = 4 if is_preview_request else 8
    default_min_delta = 0.12 if is_preview_request else 0.8
    default_min_head = 0.0015 if is_preview_request else 0.003
    default_min_mouth = 0.0015 if is_preview_request else 0.003

    try:
        min_unique_frames = int(str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_MIN_UNIQUE_FRAMES", str(default_min_unique))).strip() or default_min_unique)
    except Exception:
        min_unique_frames = default_min_unique
    try:
        min_frame_delta = float(str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_MIN_FRAME_DELTA", str(default_min_delta))).strip() or default_min_delta)
    except Exception:
        min_frame_delta = default_min_delta
    try:
        min_head_motion = float(str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_MIN_HEAD_MOTION", str(default_min_head))).strip() or default_min_head)
    except Exception:
        min_head_motion = default_min_head
    try:
        min_mouth_motion = float(str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_MIN_MOUTH_MOTION", str(default_min_mouth))).strip() or default_min_mouth)
    except Exception:
        min_mouth_motion = default_min_mouth

    unique_frames = int(quality.get("unique_frames") or 0)
    frame_delta = float(quality.get("start_end_frame_diff") or 0.0)
    head_motion_score = float(quality.get("head_motion_score") or 0.0)
    mouth_motion_score = float(quality.get("mouth_openness_change") or 0.0)

    passes_unique_frames = bool(unique_frames >= min_unique_frames)
    passes_frame_delta = bool(frame_delta >= min_frame_delta)
    passes_head_motion = bool(head_motion_score >= min_head_motion)
    passes_mouth_motion = bool(mouth_motion_score >= min_mouth_motion)
    passes_motion = bool(passes_head_motion or passes_mouth_motion)

    failure_parts: list[str] = []
    if not passes_unique_frames:
        failure_parts.append(f"unique_frames={unique_frames}<min_{min_unique_frames}")
    if not passes_frame_delta:
        failure_parts.append(f"frame_delta={round(frame_delta, 6)}<min_{round(float(min_frame_delta), 6)}")
    if not passes_motion:
        failure_parts.append(
            f"head_motion={round(head_motion_score, 6)}<min_{round(float(min_head_motion), 6)}"
            f" and mouth_motion={round(mouth_motion_score, 6)}<min_{round(float(min_mouth_motion), 6)}"
        )
    if legacy_error:
        failure_parts.append(f"legacy_analyzer_error={legacy_error}")

    legacy_passed = bool(passes_unique_frames and passes_frame_delta and passes_motion)
    legacy_failure_reason = "" if legacy_passed else ("legacy_liveportrait_motion_gate_failed:" + ";".join(failure_parts))

    shared_probe = _shared_liveportrait_video_motion_probe(path)
    shared_duration = float(shared_probe.get("duration_seconds") or video_contract.get("duration_seconds") or 0.0)
    shared_fps = float(shared_probe.get("fps") or 0.0)
    shared_frame_count = int(shared_probe.get("frame_count") or video_contract.get("frame_count") or 0)
    shared_unique_frames = int(shared_probe.get("unique_frames") or 0)
    shared_unique_ratio = float(shared_probe.get("unique_ratio") or 0.0)
    shared_mean_mad = float(shared_probe.get("mean_mad") or 0.0)
    shared_near_static = bool(shared_probe.get("near_static", True))

    expected_duration = max(float(expected_duration_seconds or 0.0), 0.0)
    expected_fps_value = max(float(expected_fps or 0.0), 0.0)
    expected_frames = max(int(expected_frame_count or 0), 0)
    if expected_fps_value <= 0.0 and expected_duration > 0.0 and expected_frames > 0:
        expected_fps_value = float(expected_frames) / float(expected_duration)
    if expected_frames <= 0 and expected_duration > 0.0 and expected_fps_value > 0.0:
        expected_frames = int(round(float(expected_duration) * float(expected_fps_value)))

    try:
        min_duration_tolerance = float(
            str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_DURATION_TOLERANCE_SECONDS", "0.25")).strip() or "0.25"
        )
    except Exception:
        min_duration_tolerance = 0.25
    duration_tolerance = max(float(min_duration_tolerance), (2.0 / expected_fps_value) if expected_fps_value > 0.0 else 0.0)
    try:
        min_frame_tolerance = int(str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_FRAME_TOLERANCE", "2")).strip() or "2")
    except Exception:
        min_frame_tolerance = 2
    frame_tolerance = max(
        int(min_frame_tolerance),
        int(round(expected_fps_value * duration_tolerance)) if expected_fps_value > 0.0 else 0,
    )
    try:
        fps_tolerance = float(str(os.environ.get("AVATAR_LIVEPORTRAIT_GATE_FPS_TOLERANCE", "0.75")).strip() or "0.75")
    except Exception:
        fps_tolerance = 0.75

    duration_matches = bool(expected_duration <= 0.0 or (shared_duration > 0.0 and abs(shared_duration - expected_duration) <= duration_tolerance))
    frame_count_matches = bool(expected_frames <= 0 or (shared_frame_count > 0 and abs(shared_frame_count - expected_frames) <= frame_tolerance))
    fps_matches = bool(expected_fps_value <= 0.0 or (shared_fps > 0.0 and abs(shared_fps - expected_fps_value) <= fps_tolerance))
    shared_probe_playable = bool(shared_duration > 0.0 and shared_frame_count > 0)
    shared_motion_passed = bool(shared_probe_playable and not shared_near_static)

    technical_failure_parts: list[str] = []
    motion_failure_parts: list[str] = []
    if not bool(file_info.get("exists")):
        technical_failure_parts.append("file_missing")
    if int(file_info.get("size_bytes") or 0) <= 0:
        technical_failure_parts.append("file_empty")
    if not playable:
        technical_failure_parts.append(f"video_unplayable:{contract_error or 'unknown'}")
    if not shared_probe_playable:
        technical_failure_parts.append("shared_probe_unplayable")
    if not duration_matches:
        technical_failure_parts.append(
            f"duration={round(shared_duration, 6)}!=expected_{round(expected_duration, 6)}+/-{round(duration_tolerance, 6)}"
        )
    if not frame_count_matches:
        technical_failure_parts.append(f"frame_count={shared_frame_count}!=expected_{expected_frames}+/-{frame_tolerance}")
    if not fps_matches:
        technical_failure_parts.append(f"fps={round(shared_fps, 6)}!=expected_{round(expected_fps_value, 6)}+/-{round(fps_tolerance, 6)}")
    if shared_near_static:
        motion_failure_parts.append(str(shared_probe.get("failure_reason") or "shared_probe_near_static"))

    passed = bool(
        bool(file_info.get("exists"))
        and int(file_info.get("size_bytes") or 0) > 0
        and playable
        and shared_motion_passed
        and duration_matches
        and frame_count_matches
        and fps_matches
    )

    technical_passed = bool(
        bool(file_info.get("exists"))
        and int(file_info.get("size_bytes") or 0) > 0
        and playable
        and duration_matches
        and frame_count_matches
        and fps_matches
    )
    motion_passed = bool(technical_passed and shared_motion_passed)

    analyzer_disagrees = bool(legacy_passed != passed)
    analyzer_mismatch = bool((not legacy_passed) and shared_motion_passed and duration_matches and frame_count_matches and fps_matches)
    analyzer_classification = "liveportrait_motion_gate_analyzer_mismatch" if analyzer_mismatch else ""
    handoff_failure_parts = technical_failure_parts + motion_failure_parts
    failure_reason = "" if passed else ("liveportrait_motion_gate_failed:" + ";".join(handoff_failure_parts or ["unknown"]))
    technical_failure_reason = (
        ""
        if technical_passed
        else ("liveportrait_technical_validation_failed:" + ";".join(technical_failure_parts or ["unknown"]))
    )
    motion_failure_reason = (
        ""
        if motion_passed
        else ("liveportrait_low_motion:" + ";".join(motion_failure_parts or ["motion_not_detected"]))
    )

    result = {
        "passed": passed,
        "technical_valid": technical_passed,
        "technical_passed": technical_passed,
        "motion_passed": motion_passed,
        "shared_motion_passed": shared_motion_passed,
        "shared_near_static": shared_near_static,
        "path": str(path),
        "analyzed_path": str(path),
        "file_exists": bool(file_info.get("exists")),
        "file_size_bytes": int(file_info.get("size_bytes") or 0),
        "file_mtime": float(file_info.get("mtime") or 0.0),
        "file_mtime_ns": int(file_info.get("mtime_ns") or 0),
        "file_sha256": str(file_info.get("sha256") or ""),
        "playable": bool(playable),
        "contract_error": str(contract_error or ""),
        "duration": round(float(shared_duration), 6),
        "fps": round(float(shared_fps), 6),
        "frame_count": int(shared_frame_count),
        "expected_duration_seconds": round(float(expected_duration), 6),
        "expected_fps": round(float(expected_fps_value), 6),
        "expected_frame_count": int(expected_frames),
        "duration_tolerance_seconds": round(float(duration_tolerance), 6),
        "fps_tolerance": round(float(fps_tolerance), 6),
        "frame_count_tolerance": int(frame_tolerance),
        "duration_matches_contract": bool(duration_matches),
        "fps_matches_contract": bool(fps_matches),
        "frame_count_matches_contract": bool(frame_count_matches),
        "unique_frames": unique_frames,
        "frame_delta": round(frame_delta, 6),
        "head_motion_score": round(head_motion_score, 6),
        "mouth_motion_score": round(mouth_motion_score, 6),
        "legacy_passed": legacy_passed,
        "legacy_failure_reason": legacy_failure_reason,
        "legacy_metrics_error": str(legacy_error or ""),
        "legacy_frames_sampled": int(quality.get("frames_sampled") or 0),
        "legacy_face_detection_frames": int(quality.get("face_detection_frames") or 0),
        "legacy_mouth_roi_frames": int(quality.get("mouth_roi_frames") or 0),
        "legacy_eye_roi_frames": int(quality.get("eye_roi_frames") or 0),
        "legacy_landmark_valid_frames": int(quality.get("landmark_valid_frames") or 0),
        "min_unique_frames": int(min_unique_frames),
        "min_frame_delta": round(float(min_frame_delta), 6),
        "min_head_motion": round(float(min_head_motion), 6),
        "min_mouth_motion": round(float(min_mouth_motion), 6),
        "passes_unique_frames": passes_unique_frames,
        "passes_frame_delta": passes_frame_delta,
        "passes_head_motion": passes_head_motion,
        "passes_mouth_motion": passes_mouth_motion,
        "shared_probe": dict(shared_probe),
        "shared_probe_unique_frames": int(shared_unique_frames),
        "shared_probe_unique_ratio": round(float(shared_unique_ratio), 6),
        "shared_probe_mean_mad": round(float(shared_mean_mad), 6),
        "shared_probe_near_static": bool(shared_near_static),
        "shared_probe_failure_reason": str(shared_probe.get("failure_reason") or ""),
        "shared_probe_errors": list(shared_probe.get("probe_errors") or []),
        "shared_motion_passed": bool(shared_motion_passed),
        "analyzer_disagrees": bool(analyzer_disagrees),
        "analyzer_mismatch": bool(analyzer_mismatch),
        "analyzer_classification": analyzer_classification,
        "failure_reason": failure_reason,
        "technical_failure_reason": technical_failure_reason,
        "motion_failure_reason": motion_failure_reason,
    }

    logger.info(
        "Avatar liveportrait_motion_gate path=%s size_bytes=%s mtime=%s duration=%s fps=%s frame_count=%s sha256=%s "
        "shared_unique_ratio=%s shared_mean_mad=%s shared_near_static=%s "
        "legacy_unique_frames=%s legacy_frame_delta=%s legacy_head_motion=%s legacy_mouth_motion=%s "
        "legacy_face_detection_frames=%s legacy_mouth_roi_frames=%s passed=%s analyzer_disagrees=%s classification=%s",
        str(path),
        int(result.get("file_size_bytes") or 0),
        result.get("file_mtime"),
        result.get("duration"),
        result.get("fps"),
        result.get("frame_count"),
        str(result.get("file_sha256") or ""),
        result.get("shared_probe_unique_ratio"),
        result.get("shared_probe_mean_mad"),
        bool(result.get("shared_probe_near_static")),
        int(result.get("unique_frames") or 0),
        float(result.get("frame_delta") or 0.0),
        float(result.get("head_motion_score") or 0.0),
        float(result.get("mouth_motion_score") or 0.0),
        int(result.get("legacy_face_detection_frames") or 0),
        int(result.get("legacy_mouth_roi_frames") or 0),
        bool(result.get("passed")),
        bool(result.get("analyzer_disagrees")),
        str(result.get("analyzer_classification") or ""),
    )
    return result


def _combined_warning(*parts: str) -> str:
    cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
    return " | ".join(cleaned)


def _request_source_key(request: Any) -> str:
    return str(
        (getattr(request, "preview_source_meta", {}) or {}).get("source_key")
        or getattr(request, "avatar_reference_type", "image")
        or "image"
    ).strip().lower()


def _path_hash(path_value: str) -> str:
    path = Path(str(path_value or ""))
    if not path.exists() or not path.is_file():
        return ""
    return sha256_file(path)


def _request_trace(request: Any, requested_engine: str, raw_requested_engine: str = "") -> dict[str, str]:
    source_image_path = str(getattr(request, "source_image_path", "") or "")
    source_image_original_path = str(getattr(request, "source_image_original_path", "") or "")
    source_video_path = str(getattr(request, "source_video_path", "") or "")
    audio_path = str(getattr(request, "audio_path", "") or "")
    return {
        "request_source_key": _request_source_key(request),
        "avatar_reference_type": str(getattr(request, "avatar_reference_type", "image") or "image").strip().lower(),
        "request_source_image_path": source_image_path,
        "request_source_image_original_path": source_image_original_path,
        "request_source_video_path": source_video_path,
        "request_audio_path": audio_path,
        "request_output_path": str(getattr(request, "output_path", "") or ""),
        "request_source_image_hash": _path_hash(source_image_path),
        "request_source_image_original_hash": _path_hash(source_image_original_path),
        "request_source_video_hash": _path_hash(source_video_path),
        "request_audio_hash": _path_hash(audio_path),
        "request_text_hash": str(getattr(request, "cache_text_hash", "") or ""),
        "requested_engine_raw": str(raw_requested_engine or ""),
        "requested_engine": str(requested_engine or CANONICAL_ENGINE),
        "normalized_engine": str(requested_engine or CANONICAL_ENGINE),
        "avatar_engine_selected": str(requested_engine or CANONICAL_ENGINE),
        "pipeline_engine": CANONICAL_ENGINE,
    }


def _cache_payload_mismatches(meta_payload: dict[str, Any], expected: dict[str, str]) -> dict[str, dict[str, str]]:
    mismatches: dict[str, dict[str, str]] = {}
    for key, value in expected.items():
        actual = str(meta_payload.get(key) or "")
        expected_value = str(value or "")
        if actual != expected_value:
            mismatches[key] = {
                "expected": expected_value,
                "actual": actual,
            }
    return mismatches


def _clear_preview_stage_artifacts(output_path: Path) -> list[str]:
    removed: list[str] = []
    candidates = [
        output_path,
        output_path.with_suffix(output_path.suffix + ".meta.json"),
        output_path.with_suffix(output_path.suffix + ".liveportrait.mp4"),
        output_path.with_suffix(output_path.suffix + ".liveportrait.reconciled.mp4"),
        output_path.with_suffix(output_path.suffix + ".musetalk_handoff.mp4"),
        output_path.with_suffix(output_path.suffix + ".musetalk.mp4"),
        output_path.with_suffix(output_path.suffix + ".restored.mp4"),
        output_path.with_suffix(output_path.suffix + ".musetalk_debug.json"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            candidate.unlink(missing_ok=True)
            removed.append(str(candidate))
    for candidate in sorted(output_path.parent.glob(output_path.name + ".canonical_*.png")):
        if candidate.exists() and candidate.is_file():
            candidate.unlink(missing_ok=True)
            removed.append(str(candidate))
    return removed


def _normalize_source_key(source_key: str) -> str:
    normalized = str(source_key or "").strip().lower()
    if normalized in {"original_image"}:
        return "image_original"
    if normalized in {"processed_image", "image"}:
        return "image_processed"
    if normalized in {"video_frame"}:
        return "video"
    return normalized


def _resolve_requested_inputs(request: Any, *, allow_image_fallback: bool = False) -> dict[str, str]:
    source_key = _request_source_key(request)
    source_image_processed_path = str(getattr(request, "source_image_path", "") or "")
    source_image_original_path = str(getattr(request, "source_image_original_path", "") or "")
    source_video_path = str(getattr(request, "source_video_path", "") or "")
    avatar_reference_type = str(getattr(request, "avatar_reference_type", "image") or "image").strip().lower()

    if source_key in {"video", "video_frame"} or avatar_reference_type == "video":
        if not source_video_path or not Path(source_video_path).exists():
            raise RuntimeError("avatar_input_source_missing:video")
        return {
            "requested_source_key": source_key or "video",
            "resolved_source_key": "video",
            "source_image_primary": "",
            "source_video_primary": source_video_path,
        }

    if source_key in {"image_original", "original_image"}:
        if not source_image_original_path or not Path(source_image_original_path).exists():
            if not allow_image_fallback:
                raise RuntimeError("avatar_input_source_missing:image_original")
        else:
            return {
                "requested_source_key": source_key,
                "resolved_source_key": "image_original",
                "source_image_primary": source_image_original_path,
                "source_video_primary": "",
            }

    if source_key in {"image_processed", "processed_image"}:
        if not source_image_processed_path or not Path(source_image_processed_path).exists():
            if not allow_image_fallback:
                raise RuntimeError("avatar_input_source_missing:image_processed")
        else:
            return {
                "requested_source_key": source_key,
                "resolved_source_key": "image_processed",
                "source_image_primary": source_image_processed_path,
                "source_video_primary": "",
            }

    if source_image_original_path and Path(source_image_original_path).exists():
        return {
            "requested_source_key": source_key or "image",
            "resolved_source_key": "image_original",
            "source_image_primary": source_image_original_path,
            "source_video_primary": "",
        }

    if source_image_processed_path and Path(source_image_processed_path).exists():
        return {
            "requested_source_key": source_key or "image",
            "resolved_source_key": "image_processed",
            "source_image_primary": source_image_processed_path,
            "source_video_primary": "",
        }

    raise RuntimeError("avatar_input_source_missing:image")


def _append_preview_image_candidate(
    *,
    candidates: list[dict[str, str]],
    seen_paths: set[str],
    source_key: str,
    source_image_primary: str,
    reason: str,
) -> None:
    candidate_path = str(source_image_primary or "").strip()
    if not candidate_path:
        return
    path_obj = Path(candidate_path)
    if not path_obj.exists() or not path_obj.is_file():
        return
    dedupe_key = str(path_obj.resolve())
    if dedupe_key in seen_paths:
        return

    normalized_key = _normalize_source_key(source_key) or "image_processed"
    candidates.append(
        {
            "requested_source_key": normalized_key,
            "resolved_source_key": normalized_key,
            "source_image_primary": str(path_obj),
            "source_video_primary": "",
            "candidate_reason": str(reason or ""),
        }
    )
    seen_paths.add(dedupe_key)


def _resolve_liveportrait_source_candidates(
    *,
    request: Any,
    resolved_inputs: dict[str, str],
    is_preview_request: bool,
) -> list[dict[str, str]]:
    resolved_source_key = _normalize_source_key(str(resolved_inputs.get("resolved_source_key") or ""))
    if (not is_preview_request) or resolved_source_key in {"video"}:
        return [dict(resolved_inputs)]

    preview_source_meta = dict(getattr(request, "preview_source_meta", {}) or {})
    source_candidates: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    configured_candidates = preview_source_meta.get("source_candidates")
    if isinstance(configured_candidates, list):
        for entry in configured_candidates:
            if not isinstance(entry, dict):
                continue
            _append_preview_image_candidate(
                candidates=source_candidates,
                seen_paths=seen_paths,
                source_key=str(entry.get("source_key") or entry.get("key") or ""),
                source_image_primary=str(entry.get("path") or entry.get("source_image_path") or ""),
                reason=str(entry.get("reason") or "preview_source_meta"),
            )

    _append_preview_image_candidate(
        candidates=source_candidates,
        seen_paths=seen_paths,
        source_key="image_original",
        source_image_primary=str(getattr(request, "source_image_original_path", "") or ""),
        reason="default_image_original",
    )
    _append_preview_image_candidate(
        candidates=source_candidates,
        seen_paths=seen_paths,
        source_key="image_processed",
        source_image_primary=str(getattr(request, "source_image_path", "") or ""),
        reason="default_image_processed",
    )

    normalized_source_path = str(
        preview_source_meta.get("preview_normalized_source_path")
        or preview_source_meta.get("current_run_normalized_source_path")
        or ""
    )
    _append_preview_image_candidate(
        candidates=source_candidates,
        seen_paths=seen_paths,
        source_key="preview_normalized",
        source_image_primary=normalized_source_path,
        reason="default_preview_normalized",
    )

    _append_preview_image_candidate(
        candidates=source_candidates,
        seen_paths=seen_paths,
        source_key=resolved_source_key,
        source_image_primary=str(resolved_inputs.get("source_image_primary") or ""),
        reason="resolved_input",
    )

    if source_candidates:
        return source_candidates

    return [dict(resolved_inputs)]


def _assert_current_source_binding(*, canonical_input: Any, resolved_inputs: dict[str, str]) -> None:
    allowed_inputs = {
        str(resolved_inputs.get("source_image_primary") or "").strip(),
        str(resolved_inputs.get("source_video_primary") or "").strip(),
    }
    allowed_inputs.discard("")

    actual_original_input = str(getattr(canonical_input, "original_input_path", "") or "").strip()
    actual_source_key = str(getattr(canonical_input, "selected_source_key", "") or "").strip().lower()
    expected_source_key = str(resolved_inputs.get("resolved_source_key") or "").strip().lower()

    if actual_original_input not in allowed_inputs:
        raise RuntimeError(
            "canonical_input_source_mismatch:"
            f"actual={actual_original_input},allowed={sorted(allowed_inputs)}"
        )
    image_keys = {"image", "image_original", "original_image", "image_processed", "processed_image"}
    video_keys = {"video", "video_frame"}
    source_key_matches = actual_source_key == expected_source_key
    if expected_source_key in image_keys and actual_source_key in image_keys:
        source_key_matches = True
    if expected_source_key in video_keys and actual_source_key in video_keys:
        source_key_matches = True

    if expected_source_key and not source_key_matches:
        raise RuntimeError(
            "canonical_input_key_mismatch:"
            f"actual={actual_source_key},expected={expected_source_key}"
        )

    normalized_input_path = Path(str(getattr(canonical_input, "normalized_input_path", "") or ""))
    if not normalized_input_path.exists() or not normalized_input_path.is_file():
        raise RuntimeError(f"canonical_input_missing_normalized_file:{normalized_input_path}")


def _build_stage_env(canonical_input: Any, request: Any) -> dict[str, str]:
    params = dict(getattr(request, "musetalk_params", {}) or {})
    metrics = dict(getattr(canonical_input, "metrics", {}) or {})
    is_preview = _is_preview_request(request)
    target_frame_count = int(getattr(request, "target_frame_count", 0) or 0)
    target_duration_seconds = float(getattr(request, "target_duration_seconds", 0.0) or 0.0)
    if target_frame_count > 0 and target_duration_seconds > 0.0:
        derived_fps = max(int(round(target_frame_count / target_duration_seconds)), 1)
    else:
        raw_fps = str(params.get("fps") or os.environ.get("MUSETALK_FPS", "25")).strip()
        try:
            derived_fps = max(int(raw_fps), 1)
        except Exception:
            derived_fps = 25

    preview_motion_strength = str(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_MOTION_STRENGTH", "1.0")).strip() if is_preview else ""
    preview_temporal_smoothing = str(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_TEMPORAL_SMOOTHING", "0.00")).strip() if is_preview else ""
    preview_fast_musetalk = str(os.environ.get("AVATAR_PREVIEW_MUSETALK_FAST_MODE", "1")).strip().lower() in {"1", "true", "yes", "on"}
    liveportrait_motion_preset = _liveportrait_motion_preset()
    liveportrait_boosted_retry_allowed = _liveportrait_boosted_retry_allowed(liveportrait_motion_preset)
    preview_max_width_default = "384" if is_preview else "512"
    default_batch_size = 2 if is_preview else 8

    raw_batch_size = (
        params.get("batch_size")
        if "batch_size" in params
        else (
            os.environ.get("AVATAR_PREVIEW_MUSETALK_BATCH_SIZE", str(default_batch_size))
            if is_preview
            else os.environ.get("MUSETALK_BATCH_SIZE", str(default_batch_size))
        )
    )
    try:
        musetalk_batch_size = max(int(str(raw_batch_size).strip() or str(default_batch_size)), 1)
    except Exception:
        musetalk_batch_size = default_batch_size

    if is_preview:
        try:
            preview_max_frames = int(str(os.environ.get("AVATAR_PREVIEW_MUSETALK_MAX_FRAMES", "0")).strip() or 0)
        except Exception:
            preview_max_frames = 0
        if preview_max_frames > 0 and target_frame_count > 0:
            target_frame_count = min(target_frame_count, preview_max_frames)

    return {
        "AVATAR_PIPELINE_ENGINE": CANONICAL_ENGINE,
        "AVATAR_CANONICAL_SOURCE_KEY": str(getattr(canonical_input, "selected_source_key", "") or ""),
        "AVATAR_CANONICAL_SOURCE_KIND": str(getattr(canonical_input, "source_kind", "") or ""),
        "AVATAR_CANONICAL_NORMALIZED_INPUT_PATH": str(getattr(canonical_input, "normalized_input_path", "") or ""),
        "AVATAR_CANONICAL_FACE_AREA_RATIO": f"{float(metrics.get('face_area_ratio_in_crop') or 0.0):.6f}",
        "AVATAR_CANONICAL_MOUTH_POSITION_RATIO": f"{float(metrics.get('mouth_position_ratio') or 0.0):.6f}",
        "AVATAR_CANONICAL_TOP_MARGIN_RATIO": f"{float(metrics.get('top_margin_ratio') or 0.0):.6f}",
        "AVATAR_CANONICAL_BOTTOM_MARGIN_RATIO": f"{float(metrics.get('bottom_margin_ratio') or 0.0):.6f}",
        "AVATAR_LIVEPORTRAIT_FPS": str(int(derived_fps)),
        "AVATAR_LIVEPORTRAIT_MOTION_STRENGTH": preview_motion_strength,
        "AVATAR_LIVEPORTRAIT_TEMPORAL_SMOOTHING": preview_temporal_smoothing,
        "AVATAR_LIVEPORTRAIT_MOTION_PRESET": liveportrait_motion_preset,
        "AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY": "1" if liveportrait_boosted_retry_allowed else "0",
        "MUSETALK_BBOX_SHIFT": str(int(params.get("bbox_shift", (0 if is_preview else 0)))),
        "MUSETALK_EXTRA_MARGIN": str(int(params.get("extra_margin", (6 if is_preview else 10)))),
        "MUSETALK_PARSING_MODE": str(params.get("parsing_mode", "jaw")),
        "MUSETALK_LEFT_CHEEK_WIDTH": str(int(params.get("left_cheek_width", (72 if is_preview else 90)))),
        "MUSETALK_RIGHT_CHEEK_WIDTH": str(int(params.get("right_cheek_width", (72 if is_preview else 90)))),
        "MUSETALK_FPS": str(int(derived_fps)),
        "MUSETALK_AUDIO_PADDING_LEFT": str(int(params.get("audio_padding_left", (0 if is_preview else 1)))),
        "MUSETALK_AUDIO_PADDING_RIGHT": str(int(params.get("audio_padding_right", (0 if is_preview else 1)))),
        "MUSETALK_BATCH_SIZE": str(int(musetalk_batch_size)),
        "MUSETALK_TARGET_FRAME_COUNT": str(int(target_frame_count)),
        "MUSETALK_TARGET_DURATION_SECONDS": f"{float(target_duration_seconds):.6f}",
        "MUSETALK_PREVIEW_FAST_MODE": ("1" if (is_preview and preview_fast_musetalk) else "0"),
        "MUSETALK_PREVIEW_MAX_WIDTH": str(int(os.environ.get("MUSETALK_PREVIEW_MAX_WIDTH", preview_max_width_default) or int(preview_max_width_default))),
        "AVATAR_PROJECT_ID": str(int(getattr(request, "_project_id", 0) or 0)),
        "AVATAR_JOB_ID": str(int(getattr(request, "_avatar_job_id", 0) or getattr(request, "preview_job_id", 0) or 0)),
        "AVATAR_LP_FAILURE_FALLBACK_TO_MUSETALK": str(os.environ.get("AVATAR_LP_FAILURE_FALLBACK_TO_MUSETALK", "1")),
        "AVATAR_SEGMENT_INDEX": str(int(getattr(request, "_segment_index", 0) or 0)),
        "AVATAR_PREVIEW_JOB_ID": str(int(getattr(request, "preview_job_id", 0) or 0)),
        "AVATAR_PREVIEW_TEACHER_ID": str(int(getattr(request, "preview_teacher_id", 0) or 0)),
    }


def _reconcile_duration_contract(
    *,
    video_path: str,
    audio_path: str,
    reconciled_video_path: str | None = None,
    preview_teacher_id: int = 0,
    preview_job_id: int = 0,
) -> dict[str, Any]:
    """
    Reconcile audio/video durations before MuseTalk.

    Strategy:
    - If video < audio: extend to contract by looping the clip or cloning last frame
    - If video > audio: trim video to audio duration

    Returns contract info, strategy, reconciled paths, and whether changes were made.
    """
    raw_clone_pad_threshold = str(os.environ.get("AVATAR_PREVIEW_CLONE_PAD_THRESHOLD_SECONDS", "1.0")).strip()
    try:
        clone_pad_threshold_seconds = float(raw_clone_pad_threshold)
    except Exception:
        clone_pad_threshold_seconds = 1.0
    clone_pad_threshold_seconds = max(clone_pad_threshold_seconds, 0.0)

    source_video_path = Path(video_path)
    reconciled_video_output_path = Path(str(reconciled_video_path or source_video_path.with_suffix(source_video_path.suffix + ".reconciled.mp4")))

    video_duration = legacy_pipeline._probe_video_duration_seconds(video_path)
    audio_duration = legacy_pipeline._probe_audio_duration_seconds(audio_path)

    if video_duration <= 0.0:
        raise RuntimeError("reconcile_contract: video_duration_invalid")
    if audio_duration <= 0.0:
        raise RuntimeError("reconcile_contract: audio_duration_invalid")

    # Determine contract duration: use audio as primary source of truth
    contract_duration = audio_duration
    delta = video_duration - audio_duration
    strategy = "unchanged"
    reconciled_video_path = video_path
    reconciled_audio_path = audio_path
    video_changed = False
    audio_changed = False

    logger.info(
        "Avatar preview duration reconciliation start teacher_id=%s job_id=%s video_duration=%s audio_duration=%s delta=%s clone_pad_threshold_seconds=%s",
        preview_teacher_id,
        preview_job_id,
        round(video_duration, 4),
        round(audio_duration, 4),
        round(delta, 4),
        round(clone_pad_threshold_seconds, 4),
    )

    # If video is too short, extend to contract duration.
    if delta < -0.01:  # video is shorter than audio
        shortfall = abs(delta)
        source = source_video_path

        # For larger shortfalls, loop full motion first; for tiny shortfalls, clone the final frame.
        extend_attempts = [
            "loop_video_to_contract_duration",
            "pad_video_with_last_frame",
        ]
        if shortfall <= clone_pad_threshold_seconds:
            extend_attempts = [
                "pad_video_with_last_frame",
                "loop_video_to_contract_duration",
            ]

        extension_errors: list[str] = []
        for extend_strategy in extend_attempts:
            if extend_strategy == "loop_video_to_contract_duration":
                tmp = reconciled_video_output_path.with_suffix(reconciled_video_output_path.suffix + ".looped.tmp.mp4")
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(source),
                    "-t",
                    f"{audio_duration:.6f}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    str(tmp),
                ]
                timeout_seconds = 120
            else:
                tmp = reconciled_video_output_path.with_suffix(reconciled_video_output_path.suffix + ".padded.tmp.mp4")
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    f"tpad=stop_mode=clone:stop_duration={shortfall:.6f}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    str(tmp),
                ]
                timeout_seconds = 90

            logger.info(
                "Avatar preview reconciliation extend attempt teacher_id=%s job_id=%s strategy=%s shortfall=%s target_duration=%s",
                preview_teacher_id,
                preview_job_id,
                extend_strategy,
                round(shortfall, 4),
                round(audio_duration, 4),
            )
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout_seconds)
            if result.returncode != 0:
                extension_errors.append(
                    f"{extend_strategy}:return_code={int(result.returncode)} stderr={str(result.stderr or '')[-280:]}"
                )
                continue
            if not tmp.exists() or tmp.stat().st_size <= 0:
                extension_errors.append(f"{extend_strategy}:empty_output")
                continue

            tmp.replace(reconciled_video_output_path)
            reconciled_video_path = str(reconciled_video_output_path)
            video_changed = True
            strategy = extend_strategy
            updated_duration = legacy_pipeline._probe_video_duration_seconds(reconciled_video_path)
            logger.info(
                "Avatar preview reconciliation extended video teacher_id=%s job_id=%s strategy=%s duration_before=%s duration_after=%s",
                preview_teacher_id,
                preview_job_id,
                strategy,
                round(video_duration, 4),
                round(updated_duration, 4),
            )
            break

        if not video_changed:
            raise RuntimeError(
                "reconcile_contract:extend_video_failed:" + ";".join(extension_errors[-2:])
            )

    # If video is longer, trim to audio length
    elif delta > 0.01:  # video is longer than audio
        strategy = "trim_video"
        logger.info(
            "Avatar preview reconciliation TRIM video teacher_id=%s job_id=%s overshoot=%s strategy=%s",
            preview_teacher_id,
            preview_job_id,
            round(delta, 4),
            strategy,
        )
        source = source_video_path
        tmp = reconciled_video_output_path.with_suffix(reconciled_video_output_path.suffix + ".trimmed.tmp.mp4")
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-vf",
                "fps=fps=30",  # Re-encode with fixed fps to ensure clean boundaries
                "-t",
                f"{audio_duration:.6f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(tmp),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
            if result.returncode != 0:
                logger.error(
                    "Avatar preview reconciliation FFmpeg trim failed teacher_id=%s stderr=%s",
                    preview_teacher_id,
                    result.stderr[-500:] if result.stderr else "",
                )
                raise RuntimeError(f"ffmpeg_trim_failed:{result.returncode}")
            if not tmp.exists() or tmp.stat().st_size <= 0:
                raise RuntimeError("reconcile_contract: trimmed_video_empty")
            tmp.replace(reconciled_video_output_path)
            reconciled_video_path = str(reconciled_video_output_path)
            video_changed = True
            updated_duration = legacy_pipeline._probe_video_duration_seconds(reconciled_video_path)
            logger.info(
                "Avatar preview reconciliation trimmed video teacher_id=%s job_id=%s duration_before=%s duration_after=%s",
                preview_teacher_id,
                preview_job_id,
                round(video_duration, 4),
                round(updated_duration, 4),
            )
        except Exception as e:
            logger.error(
                "Avatar preview reconciliation TRIM failed teacher_id=%s job_id=%s reason=%s",
                preview_teacher_id,
                preview_job_id,
                str(e),
            )
            raise RuntimeError(f"reconcile_contract:trim_video_failed:{e}")

    # Verify final audio/video match to within tolerance
    final_video_duration = legacy_pipeline._probe_video_duration_seconds(reconciled_video_path)
    final_audio_duration = legacy_pipeline._probe_audio_duration_seconds(reconciled_audio_path)
    final_delta = abs(final_video_duration - final_audio_duration)

    if final_delta > 0.1:
        logger.warning(
            "Avatar preview reconciliation finished with mismatch teacher_id=%s job_id=%s video=%s audio=%s delta=%s "
            "strategy=%s will_show_warning",
            preview_teacher_id,
            preview_job_id,
            round(final_video_duration, 4),
            round(final_audio_duration, 4),
            round(final_delta, 4),
            strategy,
        )

    logger.info(
        "Avatar preview reconciliation complete teacher_id=%s job_id=%s strategy=%s video_changed=%s audio_changed=%s "
        "final_video_duration=%s final_audio_duration=%s delta=%s",
        preview_teacher_id,
        preview_job_id,
        strategy,
        bool(video_changed),
        bool(audio_changed),
        round(final_video_duration, 4),
        round(final_audio_duration, 4),
        round(final_delta, 4),
    )

    return {
        "contract_duration_seconds": round(contract_duration, 4),
        "original_video_duration_seconds": round(video_duration, 4),
        "original_audio_duration_seconds": round(audio_duration, 4),
        "final_video_duration_seconds": round(final_video_duration, 4),
        "final_audio_duration_seconds": round(final_audio_duration, 4),
        "duration_delta_seconds": round(delta, 4),
        "adjustment_seconds": round(abs(delta), 4),
        "strategy": strategy,
        "video_changed": bool(video_changed),
        "audio_changed": bool(audio_changed),
        "reconciled_video_path": str(reconciled_video_path),
        "reconciled_audio_path": str(reconciled_audio_path),
    }


def _normalize_preview_video_for_musetalk(
    *,
    video_path: str,
    handoff_video_path: str | None = None,
    target_frame_count: int,
    target_duration_seconds: float,
    preview_teacher_id: int = 0,
    preview_job_id: int = 0,
) -> dict[str, Any]:
    source = Path(video_path)
    handoff = Path(str(handoff_video_path or source.with_suffix(source.suffix + ".handoff.mp4")))
    if target_frame_count <= 0 or target_duration_seconds <= 0.0:
        if source.exists() and source.resolve() != handoff.resolve():
            handoff.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(handoff))
        return {
            "normalized": False,
            "strategy": "skipped",
            "target_frame_count": int(target_frame_count),
            "target_duration_seconds": round(float(target_duration_seconds), 4),
            "target_fps": 0,
            "frame_count_before": 0,
            "frame_count_after": 0,
            "duration_before_seconds": 0.0,
            "duration_after_seconds": 0.0,
            "video_path": str(handoff if handoff.exists() else source),
        }

    target_fps = max(int(round(float(target_frame_count) / float(target_duration_seconds))), 1)
    frame_count_before = int(legacy_pipeline._video_frame_count(video_path) or 0)
    duration_before = float(legacy_pipeline._probe_video_duration_seconds(video_path) or 0.0)
    duration_tolerance_seconds = max(1.0 / float(target_fps), 0.02)

    logger.info(
        "Avatar preview musetalk handoff normalization start teacher_id=%s job_id=%s video_path=%s frame_count_before=%s duration_before=%s target_frame_count=%s target_duration_seconds=%s target_fps=%s",
        preview_teacher_id,
        preview_job_id,
        str(video_path),
        int(frame_count_before),
        round(duration_before, 4),
        int(target_frame_count),
        round(float(target_duration_seconds), 4),
        int(target_fps),
    )

    if frame_count_before == int(target_frame_count) and abs(duration_before - float(target_duration_seconds)) <= duration_tolerance_seconds:
        if source.exists() and source.resolve() != handoff.resolve():
            handoff.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(handoff))
        return {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": int(target_frame_count),
            "target_duration_seconds": round(float(target_duration_seconds), 4),
            "target_fps": int(target_fps),
            "frame_count_before": int(frame_count_before),
            "frame_count_after": int(frame_count_before),
            "duration_before_seconds": round(duration_before, 4),
            "duration_after_seconds": round(duration_before, 4),
            "video_path": str(handoff if handoff.exists() else source),
        }

    tmp = handoff.with_suffix(handoff.suffix + ".contract.tmp.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        f"fps={int(target_fps)}",
        "-frames:v",
        str(int(target_frame_count)),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=90)
    if result.returncode != 0:
        logger.error(
            "Avatar preview musetalk handoff normalization failed teacher_id=%s job_id=%s stderr=%s",
            preview_teacher_id,
            preview_job_id,
            result.stderr[-500:] if result.stderr else "",
        )
        raise RuntimeError(f"ffmpeg_preview_contract_normalization_failed:{result.returncode}")
    if not tmp.exists() or tmp.stat().st_size <= 0:
        raise RuntimeError("preview_musetalk_handoff_video_empty")

    handoff.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(handoff)
    frame_count_after = int(legacy_pipeline._video_frame_count(str(handoff)) or 0)
    duration_after = float(legacy_pipeline._probe_video_duration_seconds(str(handoff)) or 0.0)

    logger.info(
        "Avatar preview musetalk handoff normalization complete teacher_id=%s job_id=%s strategy=%s frame_count_before=%s frame_count_after=%s duration_before=%s duration_after=%s video_path=%s",
        preview_teacher_id,
        preview_job_id,
        "normalize_contract_fps",
        int(frame_count_before),
        int(frame_count_after),
        round(duration_before, 4),
        round(duration_after, 4),
        str(handoff),
    )

    return {
        "normalized": True,
        "strategy": "normalize_contract_fps",
        "target_frame_count": int(target_frame_count),
        "target_duration_seconds": round(float(target_duration_seconds), 4),
        "target_fps": int(target_fps),
        "frame_count_before": int(frame_count_before),
        "frame_count_after": int(frame_count_after),
        "duration_before_seconds": round(duration_before, 4),
        "duration_after_seconds": round(duration_after, 4),
        "video_path": str(handoff),
    }


def _stage_record(stage: str, result: EngineResult, *, input_path: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "success": bool(result.success),
        "engine": str(result.engine or stage),
        "input_path": str(input_path or ""),
        "output_path": str(result.output_path or ""),
        "command": str(result.command or ""),
        "error": str(result.error or ""),
        "details": dict(result.details or {}),
    }


def _cache_payload_matches(meta_payload: dict[str, Any], expected: dict[str, str]) -> bool:
    for key, value in expected.items():
        if str(meta_payload.get(key) or "") != str(value or ""):
            return False
    return True


def _expected_cache_keys(request: Any, requested_engine: str) -> dict[str, str]:
    source_image_path = str(getattr(request, "source_image_path", "") or "")
    source_video_path = str(getattr(request, "source_video_path", "") or "")
    source_image_original_path = str(getattr(request, "source_image_original_path", "") or source_image_path)
    audio_path = str(getattr(request, "audio_path", "") or "")
    pipeline_mode = "preview_canonical_liveportrait_then_musetalk" if _is_preview_request(request) else "lesson_canonical_liveportrait_then_musetalk"
    liveportrait_motion_preset = _liveportrait_motion_preset()
    liveportrait_boosted_retry_allowed = _liveportrait_boosted_retry_allowed(liveportrait_motion_preset)
    return {
        "audio_hash": sha256_file(audio_path) if audio_path and Path(audio_path).exists() else "",
        "source_image_hash": sha256_file(source_image_path) if Path(source_image_path).exists() else "",
        "source_image_original_hash": sha256_file(source_image_original_path) if Path(source_image_original_path).exists() else "",
        "source_video_hash": sha256_file(source_video_path) if source_video_path and Path(source_video_path).exists() else "",
        "text_hash": str(getattr(request, "cache_text_hash", "") or ""),
        "request_source_key": _request_source_key(request),
        "avatar_reference_type": str(getattr(request, "avatar_reference_type", "image") or "image").strip().lower(),
        "target_frame_count": str(int(getattr(request, "target_frame_count", 0) or 0)),
        "target_duration_seconds": f"{float(getattr(request, 'target_duration_seconds', 0.0) or 0.0):.6f}",
        "requested_engine": requested_engine,
        "pipeline_engine": CANONICAL_ENGINE,
        "pipeline_mode": pipeline_mode,
        "liveportrait_motion_preset": liveportrait_motion_preset,
        "liveportrait_boosted_retry_allowed": "1" if liveportrait_boosted_retry_allowed else "0",
        "liveportrait_motion_profile_policy": "boosted_retry_allowed" if liveportrait_boosted_retry_allowed else "conservative_only",
    }


def _request_contract_duration_seconds(request: Any) -> float:
    target_duration_seconds = float(getattr(request, "target_duration_seconds", 0.0) or 0.0)
    if target_duration_seconds > 0.0:
        return target_duration_seconds
    audio_path = str(getattr(request, "audio_path", "") or "")
    if audio_path and Path(audio_path).exists():
        return float(legacy_pipeline._probe_audio_duration_seconds(audio_path) or 0.0)
    return 0.0


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    return float(value)


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


_LIVEPORTRAIT_MOTION_PRESETS = {"natural_conservative", "subtle_blink", "subtle_gaze", "expressive_debug"}


def _liveportrait_motion_preset() -> str:
    raw = str(os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_PRESET", "")).strip().lower()
    return raw if raw in _LIVEPORTRAIT_MOTION_PRESETS else "natural_conservative"


def _liveportrait_boosted_retry_allowed(preset: str | None = None) -> bool:
    resolved = str(preset or _liveportrait_motion_preset()).strip().lower()
    return resolved == "expressive_debug" or _env_enabled("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", False)


def _stderr_token(stderr_text: str, key: str) -> str:
    pattern = re.compile(r"(?:^|\s)" + re.escape(str(key)) + r"=([^\s]+)")
    match = pattern.search(str(stderr_text or ""))
    return str(match.group(1)) if match else ""


def _stderr_bool_token(stderr_text: str, key: str, default: bool) -> bool:
    raw = _stderr_token(stderr_text, key)
    if not raw:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float_first(names: list[str], default: float) -> tuple[float, str]:
    for name in names:
        raw = str(os.environ.get(name, "")).strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except Exception:
            continue
        if value > 0.0:
            return float(value), name
    return float(default), ""


def _selected_gpu_snapshot(resources: dict[str, Any] | None) -> dict[str, Any]:
    gpu = dict((resources or {}).get("gpu") or {})
    selected = dict(gpu.get("selected") or {})
    return {
        "available": bool(gpu.get("available")),
        "name": str(selected.get("name") or ""),
        "total_mib": int(selected.get("total_mib") or 0),
        "free_mib": int(selected.get("free_mib") or 0),
    }


def _musetalk_chunk_count(duration_seconds: float) -> tuple[int, float]:
    chunk_max = max(_env_float("MUSETALK_CHUNK_MAX_SECONDS", 0.0), 0.0)
    duration = max(float(duration_seconds), 0.0)
    if duration <= 0.0:
        return 1, chunk_max
    if chunk_max <= 0.0:
        return 1, chunk_max
    return max(int((duration + chunk_max - 1e-6) // chunk_max), 1), chunk_max


def _musetalk_history_file() -> Path:
    raw = str(os.environ.get("AVATAR_ORCH_METRICS_FILE", "storage_local/avatar_stage_metrics.json")).strip()
    return Path(raw or "storage_local/avatar_stage_metrics.json")


def _safe_json_file(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def _musetalk_chunk_timings_from_debug(debug_payload: dict[str, Any]) -> list[dict[str, Any]]:
    timings: list[dict[str, Any]] = []
    for entry in list(debug_payload.get("stage_timings") or []):
        if not isinstance(entry, dict):
            continue
        timing_map = dict(entry.get("timings") or {})
        total = sum(float(value or 0.0) for value in timing_map.values())
        timings.append(
            {
                "chunk_index": int(entry.get("chunk_index") or len(timings)),
                "total_seconds": round(float(total), 4),
                "timings": timing_map,
            }
        )
    return timings


def _musetalk_chunk_timing_metrics(
    *,
    details: dict[str, Any],
    debug_payload: dict[str, Any],
    audio_duration_seconds: float,
    frame_count: int,
    elapsed_seconds: float,
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    raw_entries = list(details.get("chunk_metadata") or [])
    if not raw_entries:
        raw_entries = list(debug_payload.get("chunk_timing_metrics") or [])
    if not raw_entries:
        raw_entries = list(debug_payload.get("chunk_metadata") or [])

    for position, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            continue
        duration = float(
            entry.get("audio_duration_seconds")
            or entry.get("duration_seconds")
            or 0.0
        )
        chunk_elapsed = float(
            entry.get("elapsed_seconds")
            or entry.get("service_elapsed_seconds")
            or entry.get("total_seconds")
            or 0.0
        )
        metrics.append(
            {
                "chunk_index": int(entry.get("chunk_index", entry.get("index", position)) or 0),
                "audio_duration_seconds": round(float(duration), 4),
                "frame_count": int(entry.get("frame_count") or 0),
                "elapsed_seconds": round(float(chunk_elapsed), 4),
                "success": bool(entry.get("success", entry.get("service_success", True))),
                "route": str(details.get("route") or debug_payload.get("route") or ""),
            }
        )

    if metrics:
        return metrics
    return [
        {
            "chunk_index": 0,
            "audio_duration_seconds": round(float(audio_duration_seconds), 4),
            "frame_count": int(frame_count),
            "elapsed_seconds": round(float(elapsed_seconds), 4),
            "success": bool(details.get("return_code", 0) in {0, "0", None} and not details.get("stderr") == "timeout"),
            "route": str(details.get("route") or debug_payload.get("route") or ""),
        }
    ]


def _musetalk_debug_history_record(debug_payload: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    elapsed = float(
        debug_payload.get("inference_total_seconds")
        or (dict(debug_payload.get("entrypoint_stage_timings") or {}).get("inference_total_seconds"))
        or 0.0
    )
    if elapsed <= 0.0:
        return None
    chunk_ranges = list(debug_payload.get("chunk_ranges") or [])
    chunk_count = int(len(chunk_ranges) or len(debug_payload.get("chunk_metadata") or []) or 1)
    return {
        "source": source,
        "success": True,
        "total_elapsed_seconds": round(float(elapsed), 4),
        "audio_duration_seconds": float(
            debug_payload.get("target_duration_seconds")
            or debug_payload.get("duration_after_encoding_seconds")
            or 0.0
        ),
        "frame_count": int(
            debug_payload.get("target_frame_count")
            or debug_payload.get("frame_count_after_encoding")
            or debug_payload.get("frame_count_before_encoding")
            or 0
        ),
        "chunk_count": max(int(chunk_count), 1),
        "gpu_total_mib": int(debug_payload.get("gpu_total_mib") or 0),
        "gpu_free_mib": int(debug_payload.get("gpu_free_mib") or 0),
        "per_chunk_timings": _musetalk_chunk_timings_from_debug(debug_payload),
    }


def _musetalk_history_records(request: Any, resources: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not _env_enabled("AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED", True):
        return []

    records: list[dict[str, Any]] = []
    metrics_payload = _safe_json_file(_musetalk_history_file())
    for raw_record in list((metrics_payload.get("stages") or {}).get("musetalk") or []):
        if not isinstance(raw_record, dict) or not bool(raw_record.get("success")):
            continue
        context = dict(raw_record.get("context") or {})
        debug_context = dict(context.get("musetalk_debug") or {})
        chunk_count = int(context.get("chunk_count") or debug_context.get("chunk_count") or 1)
        per_chunk_timings = list(context.get("per_chunk_timings") or debug_context.get("per_chunk_timings") or [])
        selected_gpu = dict(((raw_record.get("resources") or {}).get("gpu") or {}).get("selected") or {})
        records.append(
            {
                "source": "metrics",
                "success": True,
                "total_elapsed_seconds": float(raw_record.get("elapsed_seconds") or 0.0),
                "audio_duration_seconds": float(raw_record.get("audio_duration_seconds") or 0.0),
                "frame_count": int(raw_record.get("frame_count") or 0),
                "chunk_count": max(int(chunk_count), 1),
                "gpu_total_mib": int(selected_gpu.get("total_mib") or 0),
                "gpu_free_mib": int(selected_gpu.get("free_mib") or 0),
                "per_chunk_timings": per_chunk_timings,
            }
        )

    output_path = Path(str(getattr(request, "output_path", "") or ""))
    if output_path.name:
        musetalk_output = output_path.with_suffix(output_path.suffix + ".musetalk.mp4")
        sidecar = musetalk_output.with_suffix(musetalk_output.suffix + ".musetalk_debug.json")
        sidecar_record = _musetalk_debug_history_record(_safe_json_file(sidecar), source=str(sidecar))
        if sidecar_record is not None:
            gpu_snapshot = _selected_gpu_snapshot(resources)
            if not sidecar_record.get("gpu_total_mib"):
                sidecar_record["gpu_total_mib"] = int(gpu_snapshot.get("total_mib") or 0)
            if not sidecar_record.get("gpu_free_mib"):
                sidecar_record["gpu_free_mib"] = int(gpu_snapshot.get("free_mib") or 0)
            records.append(sidecar_record)

    return [record for record in records if float(record.get("total_elapsed_seconds") or 0.0) > 0.0]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = min(max(float(percentile), 0.0), 1.0) * float(len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + ((ordered[high] - ordered[low]) * (rank - low)))


def _musetalk_history_timeout_estimate(
    *,
    records: list[dict[str, Any]],
    duration_seconds: float,
    frame_count: int,
    chunk_count: int,
    gpu_total_mib: int,
) -> dict[str, Any]:
    scaled_samples: list[float] = []
    similar_records: list[dict[str, Any]] = []
    max_observed_chunk_seconds = 0.0
    for record in records:
        elapsed = float(record.get("total_elapsed_seconds") or 0.0)
        if elapsed <= 0.0:
            continue
        record_gpu_total = int(record.get("gpu_total_mib") or 0)
        similar_hardware = (
            record_gpu_total <= 0
            or gpu_total_mib <= 0
            or abs(record_gpu_total - gpu_total_mib) <= 1024
            or (record_gpu_total <= 6144 and gpu_total_mib <= 6144)
        )
        if not similar_hardware:
            continue
        observed_frames = max(int(record.get("frame_count") or 0), 1)
        observed_duration = max(float(record.get("audio_duration_seconds") or 0.0), 0.001)
        observed_chunks = max(int(record.get("chunk_count") or 1), 1)
        scaled = max(
            elapsed * (max(int(frame_count), 1) / float(observed_frames)),
            elapsed * (max(float(duration_seconds), 0.001) / observed_duration),
            (elapsed / float(observed_chunks)) * max(int(chunk_count), 1),
        )
        scaled_samples.append(float(scaled))
        similar_records.append(record)
        for chunk in list(record.get("per_chunk_timings") or []):
            if isinstance(chunk, dict):
                max_observed_chunk_seconds = max(max_observed_chunk_seconds, float(chunk.get("total_seconds") or 0.0))

    return {
        "samples": [round(float(value), 4) for value in scaled_samples],
        "sample_count": int(len(scaled_samples)),
        "p95_seconds": round(_percentile(scaled_samples, 0.95), 4),
        "max_seconds": round(max(scaled_samples or [0.0]), 4),
        "max_observed_chunk_seconds": round(float(max_observed_chunk_seconds), 4),
        "sources": [str(record.get("source") or "") for record in similar_records[-8:]],
    }


def _musetalk_timeout_profile(
    request: Any,
    *,
    resources: dict[str, Any] | None,
    contract_duration_seconds: float | None = None,
    explicit_env_names: list[str],
    is_preview: bool,
) -> tuple[float, dict[str, Any]]:
    duration_seconds = (
        float(contract_duration_seconds)
        if contract_duration_seconds is not None and float(contract_duration_seconds) > 0.0
        else float(_request_contract_duration_seconds(request) or 0.0)
    )
    frame_count = max(int(getattr(request, "target_frame_count", 0) or 0), 0)
    if frame_count <= 0 and duration_seconds > 0.0:
        frame_count = int(round(duration_seconds * float(os.environ.get("MUSETALK_FPS", "16") or 16)))
    fps = round((float(frame_count) / float(duration_seconds)) if duration_seconds > 0.0 and frame_count > 0 else 0.0, 4)
    chunk_count, chunk_max_seconds = _musetalk_chunk_count(duration_seconds)
    gpu_snapshot = _selected_gpu_snapshot(resources)
    gpu_total_mib = int(gpu_snapshot.get("total_mib") or 0)
    gpu_free_mib = int(gpu_snapshot.get("free_mib") or 0)
    low_vram = bool(gpu_total_mib > 0 and gpu_total_mib <= 6144)

    explicit_timeout_seconds = 0.0
    explicit_source = ""
    for env_name in explicit_env_names:
        raw = str(os.environ.get(env_name, "")).strip()
        if not raw:
            continue
        try:
            candidate = float(raw)
        except Exception:
            candidate = 0.0
        if candidate > 0.0:
            explicit_timeout_seconds = float(candidate)
            explicit_source = env_name
            break

    max_seconds, max_source = _env_float_first(
        ["AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS", "AVATAR_PREVIEW_MUSETALK_TIMEOUT_MAX_SECONDS"],
        7200.0,
    )
    min_seconds = _env_float(
        "AVATAR_PREVIEW_MUSETALK_TIMEOUT_MIN_SECONDS" if is_preview else "AVATAR_ORCH_MUSETALK_TIMEOUT_MIN_SECONDS",
        180.0 if is_preview else 240.0,
    )
    max_seconds = max(float(max_seconds), float(min_seconds))

    if explicit_timeout_seconds > 0.0:
        chosen = min(float(explicit_timeout_seconds), float(max_seconds))
        reason = {
            "stage": "musetalk",
            "source": "explicit",
            "explicit_timeout_seconds": round(float(explicit_timeout_seconds), 4),
            "explicit_source_env": explicit_source,
            "explicit_env_candidates": list(explicit_env_names),
            "audio_duration_seconds": round(float(duration_seconds), 4),
            "frame_count": int(frame_count),
            "fps": fps,
            "chunk_count": int(chunk_count),
            "chunk_max_seconds": round(float(chunk_max_seconds), 4),
            "gpu_total_mib": int(gpu_total_mib),
            "gpu_free_mib": int(gpu_free_mib),
            "low_vram": bool(low_vram),
            "max_env": max_source or "default",
            "max_timeout_seconds": round(float(max_seconds), 4),
            "timeout_seconds": round(float(chosen), 4),
        }
        return float(chosen), reason

    base_seconds = _env_float("AVATAR_PREVIEW_MUSETALK_TIMEOUT_BASE_SECONDS" if is_preview else "AVATAR_ORCH_MUSETALK_TIMEOUT_BASE_SECONDS", 180.0)
    per_audio_second = _env_float("AVATAR_PREVIEW_MUSETALK_TIMEOUT_PER_AUDIO_SECOND" if is_preview else "AVATAR_ORCH_MUSETALK_TIMEOUT_PER_AUDIO_SECOND", 32.0)
    per_frame_second = _env_float("AVATAR_PREVIEW_MUSETALK_TIMEOUT_PER_FRAME_SECOND" if is_preview else "AVATAR_ORCH_MUSETALK_TIMEOUT_PER_FRAME_SECOND", 1.1)
    per_chunk_seconds = _env_float("AVATAR_MUSETALK_TIMEOUT_PER_CHUNK_SECONDS", 240.0)
    safety_multiplier = max(_env_float("AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER", 1.4), 1.0)
    low_vram_multiplier, low_vram_multiplier_source = _env_float_first(
        ["AVATAR_MUSETALK_TIMEOUT_LOW_VRAM_MULTIPLIER", "AVATAR_LOW_VRAM_MUSETALK_TIMEOUT_MULTIPLIER"],
        1.35,
    )
    low_vram_multiplier = max(float(low_vram_multiplier), 1.0)

    input_estimate = (
        float(base_seconds)
        + (max(float(duration_seconds), 0.0) * float(per_audio_second))
        + (max(int(frame_count), 0) * float(per_frame_second))
        + (max(int(chunk_count), 1) * float(per_chunk_seconds))
    )
    hardware_estimate = input_estimate * (low_vram_multiplier if low_vram else 1.0)
    history_records = _musetalk_history_records(request, resources)
    history = _musetalk_history_timeout_estimate(
        records=history_records,
        duration_seconds=float(duration_seconds),
        frame_count=int(frame_count),
        chunk_count=int(chunk_count),
        gpu_total_mib=int(gpu_total_mib),
    )
    history_estimate = max(float(history.get("p95_seconds") or 0.0), float(history.get("max_seconds") or 0.0))
    pre_safety = max(
        float(history_estimate),
        float(input_estimate if history_estimate > 0.0 else hardware_estimate),
        float(min_seconds),
    )
    computed = pre_safety * float(safety_multiplier)
    chosen = max(float(min_seconds), min(float(max_seconds), float(computed)))

    max_observed_chunk = float(history.get("max_observed_chunk_seconds") or 0.0)
    per_chunk_timeout = max(
        chosen / max(float(chunk_count), 1.0) * 1.35,
        max_observed_chunk * 1.25,
        min(float(chosen), 900.0),
    )
    per_chunk_timeout = min(float(chosen), float(per_chunk_timeout))
    idle_timeout = min(float(per_chunk_timeout), max(_env_float("MUSETALK_IDLE_TIMEOUT_SECONDS", 1200.0), 60.0))

    reason = {
        "stage": "musetalk",
        "source": "musetalk_hardware_history",
        "audio_duration_seconds": round(float(duration_seconds), 4),
        "frame_count": int(frame_count),
        "fps": fps,
        "chunk_count": int(chunk_count),
        "chunk_max_seconds": round(float(chunk_max_seconds), 4),
        "gpu_name": str(gpu_snapshot.get("name") or ""),
        "gpu_total_mib": int(gpu_total_mib),
        "gpu_free_mib": int(gpu_free_mib),
        "low_vram": bool(low_vram),
        "base_seconds": round(float(base_seconds), 4),
        "per_audio_second": round(float(per_audio_second), 4),
        "per_frame_second": round(float(per_frame_second), 4),
        "per_chunk_seconds": round(float(per_chunk_seconds), 4),
        "input_estimate_seconds": round(float(input_estimate), 4),
        "low_vram_multiplier": round(float(low_vram_multiplier), 4),
        "low_vram_multiplier_env": low_vram_multiplier_source or "default",
        "hardware_estimate_seconds": round(float(hardware_estimate), 4),
        "history_enabled": _env_enabled("AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED", True),
        "history_sample_count": int(history.get("sample_count") or 0),
        "history_samples": list(history.get("samples") or []),
        "history_sources": list(history.get("sources") or []),
        "history_p95_seconds": float(history.get("p95_seconds") or 0.0),
        "history_max_seconds": float(history.get("max_seconds") or 0.0),
        "history_max_observed_chunk_seconds": float(history.get("max_observed_chunk_seconds") or 0.0),
        "pre_safety_timeout_seconds": round(float(pre_safety), 4),
        "safety_multiplier": round(float(safety_multiplier), 4),
        "min_timeout_seconds": round(float(min_seconds), 4),
        "max_timeout_seconds": round(float(max_seconds), 4),
        "max_env": max_source or "default",
        "timeout_seconds": round(float(chosen), 4),
        "per_chunk_timeout_seconds": round(float(per_chunk_timeout), 4),
        "idle_timeout_seconds": round(float(idle_timeout), 4),
        "explicit_source_env": explicit_source,
        "explicit_env_candidates": list(explicit_env_names),
    }
    return float(chosen), reason


def _adaptive_timeout_profile(
    *,
    stage_name: str,
    audio_duration_seconds: float,
    frame_count: int,
    resources: dict[str, Any] | None,
    explicit_env_names: list[str],
    base_env: str,
    per_audio_env: str,
    per_frame_env: str,
    min_env: str,
    max_env: str,
    default_base_seconds: float,
    default_per_audio_second: float,
    default_per_frame_second: float,
    default_min_seconds: float,
    default_max_seconds: float,
) -> tuple[float, dict[str, Any]]:
    explicit_timeout_seconds = 0.0
    explicit_source = ""
    for env_name in explicit_env_names:
        raw = str(os.environ.get(env_name, "")).strip()
        if not raw:
            continue
        try:
            candidate = float(raw)
        except Exception:
            candidate = 0.0
        if candidate > 0.0:
            explicit_timeout_seconds = float(candidate)
            explicit_source = env_name
            break

    base_seconds = _env_float(base_env, default_base_seconds)
    per_audio_second = _env_float(per_audio_env, default_per_audio_second)
    per_frame_second = _env_float(per_frame_env, default_per_frame_second)
    min_seconds = _env_float(min_env, default_min_seconds)
    max_seconds = _env_float(max_env, default_max_seconds)

    if max_seconds < min_seconds:
        max_seconds = min_seconds

    timeout_seconds, reason = compute_adaptive_timeout(
        stage_name=stage_name,
        audio_duration_seconds=max(float(audio_duration_seconds), 0.0),
        frame_count=max(int(frame_count), 0),
        base_seconds=base_seconds,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        per_audio_second=per_audio_second,
        per_frame_second=per_frame_second,
        explicit_timeout_seconds=explicit_timeout_seconds,
        resources=resources,
    )
    reason["explicit_source_env"] = explicit_source
    reason["explicit_env_candidates"] = list(explicit_env_names)
    reason["base_env"] = base_env
    reason["per_audio_env"] = per_audio_env
    reason["per_frame_env"] = per_frame_env
    reason["min_env"] = min_env
    reason["max_env"] = max_env
    return float(timeout_seconds), reason


def _liveportrait_timeout_profile(
    request: Any,
    *,
    audio_duration_seconds: float,
    resources: dict[str, Any] | None,
) -> tuple[float, dict[str, Any]]:
    return _adaptive_timeout_profile(
        stage_name="liveportrait",
        audio_duration_seconds=float(audio_duration_seconds),
        frame_count=int(getattr(request, "target_frame_count", 0) or 0),
        resources=resources,
        explicit_env_names=["AVATAR_ORCH_STAGE_TIMEOUT_LIVEPORTRAIT_SECONDS"],
        base_env="AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_BASE_SECONDS",
        per_audio_env="AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_PER_AUDIO_SECOND",
        per_frame_env="AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_PER_FRAME_SECOND",
        min_env="AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_MIN_SECONDS",
        max_env="AVATAR_ORCH_LIVEPORTRAIT_TIMEOUT_MAX_SECONDS",
        default_base_seconds=120.0,
        default_per_audio_second=22.0,
        default_per_frame_second=0.55,
        default_min_seconds=120.0,
        default_max_seconds=7200.0,
    )


def _preview_musetalk_timeout_profile(
    request: Any,
    *,
    resources: dict[str, Any] | None,
    contract_duration_seconds: float | None = None,
) -> tuple[float, dict[str, Any]]:
    return _musetalk_timeout_profile(
        request,
        resources=resources,
        contract_duration_seconds=contract_duration_seconds,
        explicit_env_names=[
            "AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS",
            "AVATAR_PREVIEW_MUSETALK_TIMEOUT_SECONDS",
            "AVATAR_ORCH_STAGE_TIMEOUT_MUSETALK_SECONDS",
        ],
        is_preview=True,
    )


def _lesson_musetalk_timeout_profile(
    request: Any,
    *,
    resources: dict[str, Any] | None,
) -> tuple[float, dict[str, Any]]:
    return _musetalk_timeout_profile(
        request,
        resources=resources,
        contract_duration_seconds=None,
        explicit_env_names=["AVATAR_ORCH_STAGE_TIMEOUT_MUSETALK_SECONDS"],
        is_preview=False,
    )


def _restoration_timeout_profile(
    request: Any,
    *,
    resources: dict[str, Any] | None,
    contract_duration_seconds: float,
) -> tuple[float, dict[str, Any]]:
    return _adaptive_timeout_profile(
        stage_name="restoration",
        audio_duration_seconds=float(contract_duration_seconds),
        frame_count=int(getattr(request, "target_frame_count", 0) or 0),
        resources=resources,
        explicit_env_names=["AVATAR_ORCH_STAGE_TIMEOUT_RESTORATION_SECONDS"],
        base_env="AVATAR_ORCH_RESTORATION_TIMEOUT_BASE_SECONDS",
        per_audio_env="AVATAR_ORCH_RESTORATION_TIMEOUT_PER_AUDIO_SECOND",
        per_frame_env="AVATAR_ORCH_RESTORATION_TIMEOUT_PER_FRAME_SECOND",
        min_env="AVATAR_ORCH_RESTORATION_TIMEOUT_MIN_SECONDS",
        max_env="AVATAR_ORCH_RESTORATION_TIMEOUT_MAX_SECONDS",
        default_base_seconds=45.0,
        default_per_audio_second=8.0,
        default_per_frame_second=0.12,
        default_min_seconds=60.0,
        default_max_seconds=900.0,
    )


def _preview_musetalk_timeout_seconds(request: Any) -> float:
    timeout_seconds, _ = _preview_musetalk_timeout_profile(
        request,
        resources=probe_runtime_resources(),
    )
    return float(timeout_seconds)


def _cleanup_summary(payload: dict[str, Any]) -> dict[str, Any]:
    before = dict(payload.get("before") or {})
    after = dict(payload.get("after") or {})
    before_gpu = dict((before.get("gpu") or {}).get("selected") or {})
    after_gpu = dict((after.get("gpu") or {}).get("selected") or {})
    before_system = dict(before.get("system") or {})
    after_system = dict(after.get("system") or {})
    torch_payload = dict(payload.get("torch") or {})
    return {
        "reason": str(payload.get("reason") or ""),
        "gc_collected": int(payload.get("gc_collected") or 0),
        "torch_cache_cleared": bool(torch_payload.get("cache_cleared")),
        "torch_error": str(torch_payload.get("error") or ""),
        "before_gpu_free_mib": int(before_gpu.get("free_mib") or 0),
        "after_gpu_free_mib": int(after_gpu.get("free_mib") or 0),
        "before_gpu_total_mib": int(before_gpu.get("total_mib") or 0),
        "after_gpu_total_mib": int(after_gpu.get("total_mib") or 0),
        "before_mem_available_mib": int(before_system.get("available_mib") or 0),
        "after_mem_available_mib": int(after_system.get("available_mib") or 0),
    }


def _load_cached_result(request: Any, *, is_preview_request: bool, output_path: Path, meta_path: Path) -> dict[str, Any] | None:
    if is_preview_request:
        if output_path.exists() or meta_path.exists():
            logger.info(
                "Avatar preview cache bypass output=%s meta_path=%s reason=%s",
                str(output_path),
                str(meta_path),
                "current_run_required",
            )
        return None

    if not output_path.exists() or output_path.stat().st_size <= 0 or not meta_path.exists():
        return None

    requested_engine = normalize_avatar_engine(getattr(request, "lipsync_engine", None))
    expected_cache = _expected_cache_keys(request, requested_engine)
    try:
        meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    mismatches = _cache_payload_mismatches(meta_payload, expected_cache)
    if mismatches:
        logger.info(
            "Avatar canonical pipeline cache reject output=%s meta_path=%s mismatches=%s",
            str(output_path),
            str(meta_path),
            json.dumps(mismatches, ensure_ascii=True, sort_keys=True),
        )
        return None
    if not _video_is_playable(output_path, stage_name="cached_output"):
        return None

    logger.info(
        "Avatar canonical pipeline cache hit output=%s meta_path=%s requested_engine=%s request_source_key=%s text_hash=%s audio_hash=%s",
        str(output_path),
        str(meta_path),
        requested_engine,
        expected_cache.get("request_source_key") or "",
        expected_cache.get("text_hash") or "",
        expected_cache.get("audio_hash") or "",
    )

    validation = dict(meta_payload.get("motion_validation") or _safe_validation(str(output_path), str(request.audio_path), fallback_reason="cached_validation_failed"))
    if not is_preview_request:
        validation = legacy_pipeline.apply_lesson_segment_validation_policy(validation)
    strict_pass = bool(meta_payload.get("strict_validation_passed")) or legacy_pipeline.accept_avatar_render(validation)
    if (not strict_pass) and (not is_preview_request):
        return None

    preview_warning = str(meta_payload.get("preview_warning") or "").strip()
    preview_status = "warning" if (is_preview_request and preview_warning) else ("ready" if is_preview_request else "ok")
    cached_stage_paths = dict(meta_payload.get("stage_paths") or {})
    return {
        "output_path": str(output_path),
        "engine_used": str(meta_payload.get("engine_used") or CANONICAL_ENGINE),
        "pipeline_engine": CANONICAL_ENGINE,
        "requested_engine_raw": str(cached_stage_paths.get("requested_engine_raw") or requested_engine),
        "requested_engine": requested_engine,
        "normalized_engine": requested_engine,
        "avatar_engine_selected": requested_engine,
        "liveportrait_motion_preset": str(cached_stage_paths.get("liveportrait_motion_preset") or _liveportrait_motion_preset()),
        "liveportrait_motion_profile": str(cached_stage_paths.get("liveportrait_motion_profile") or ""),
        "liveportrait_driver_source": str(cached_stage_paths.get("liveportrait_driver_source") or ""),
        "liveportrait_composer_used": bool(cached_stage_paths.get("liveportrait_composer_used")),
        "liveportrait_boosted_retry_used": bool(cached_stage_paths.get("liveportrait_boosted_retry_used")),
        "liveportrait_recenter_enabled": bool(cached_stage_paths.get("liveportrait_recenter_enabled")),
        "liveportrait_whole_frame_drift_guard": bool(cached_stage_paths.get("liveportrait_whole_frame_drift_guard")),
        "liveportrait_enabled": bool(cached_stage_paths.get("liveportrait_enabled")),
        "liveportrait_started": bool(cached_stage_paths.get("liveportrait_started")),
        "liveportrait_succeeded": bool(cached_stage_paths.get("liveportrait_succeeded")),
        "liveportrait_failed": bool(cached_stage_paths.get("liveportrait_failed")),
        "liveportrait_failure_reason": str(cached_stage_paths.get("liveportrait_failure_reason") or ""),
        "liveportrait_quality_warning": str(cached_stage_paths.get("liveportrait_quality_warning") or ""),
        "liveportrait_motion_passed": bool(cached_stage_paths.get("liveportrait_motion_passed")),
        "liveportrait_technical_valid": bool(cached_stage_paths.get("liveportrait_technical_valid")),
        "liveportrait_fallback_used": bool(cached_stage_paths.get("liveportrait_fallback_used")),
        "liveportrait_fallback_reason": str(cached_stage_paths.get("liveportrait_fallback_reason") or ""),
        "musetalk_source_video": str(cached_stage_paths.get("musetalk_source_video") or ""),
        "musetalk_source_kind": str(cached_stage_paths.get("musetalk_source_kind") or ""),
        "restoration_enabled": bool(cached_stage_paths.get("restoration_enabled")),
        "restoration_succeeded": bool(cached_stage_paths.get("restoration_succeeded")),
        "restoration_failed": bool(cached_stage_paths.get("restoration_failed")),
        "fallback_chain_used": ["cache"],
        "final_avatar_engine_chain": list(cached_stage_paths.get("final_avatar_engine_chain") or ["cache"]),
        "audio_hash": expected_cache["audio_hash"],
        "video_hash": str(meta_payload.get("video_hash") or sha256_file(str(output_path))),
        "motion_validation": validation,
        "strict_validation_passed": bool(strict_pass),
        "preview_warning": preview_warning,
        "preview_status": preview_status,
        "unstable_output_accepted": bool(is_preview_request and preview_warning and not strict_pass),
        "failure_category": ("validation_warning" if is_preview_request and preview_warning and not strict_pass else ""),
        "stage_paths": cached_stage_paths,
        "stage_outputs": list(meta_payload.get("stage_outputs") or []),
        "frame_trace": {"paths": cached_stage_paths},
        "canonical_input": dict(meta_payload.get("canonical_input") or {}),
    }


def _write_meta(
    *,
    meta_path: Path,
    request: Any,
    requested_engine: str,
    output_path: Path,
    video_hash: str,
    validation: dict[str, Any],
    strict_pass: bool,
    preview_warning: str,
    engine_used: str,
    stage_paths: dict[str, Any],
    stage_outputs: list[dict[str, Any]],
    canonical_input: dict[str, Any],
) -> None:
    payload = {
        **_expected_cache_keys(request, requested_engine),
        "video_hash": video_hash,
        "engine_used": engine_used,
        "normalized_engine": requested_engine,
        "avatar_engine_selected": requested_engine,
        "liveportrait_motion_preset": str(stage_paths.get("liveportrait_motion_preset") or _liveportrait_motion_preset()),
        "liveportrait_motion_profile": str(stage_paths.get("liveportrait_motion_profile") or ""),
        "liveportrait_driver_source": str(stage_paths.get("liveportrait_driver_source") or ""),
        "liveportrait_composer_used": bool(stage_paths.get("liveportrait_composer_used")),
        "liveportrait_boosted_retry_used": bool(stage_paths.get("liveportrait_boosted_retry_used")),
        "liveportrait_recenter_enabled": bool(stage_paths.get("liveportrait_recenter_enabled")),
        "liveportrait_whole_frame_drift_guard": bool(stage_paths.get("liveportrait_whole_frame_drift_guard")),
        "liveportrait_enabled": bool(stage_paths.get("liveportrait_enabled")),
        "liveportrait_started": bool(stage_paths.get("liveportrait_started")),
        "liveportrait_succeeded": bool(stage_paths.get("liveportrait_succeeded")),
        "liveportrait_failed": bool(stage_paths.get("liveportrait_failed")),
        "liveportrait_failure_reason": str(stage_paths.get("liveportrait_failure_reason") or ""),
        "liveportrait_quality_warning": str(stage_paths.get("liveportrait_quality_warning") or ""),
        "liveportrait_motion_passed": bool(stage_paths.get("liveportrait_motion_passed")),
        "liveportrait_technical_valid": bool(stage_paths.get("liveportrait_technical_valid")),
        "liveportrait_fallback_used": bool(stage_paths.get("liveportrait_fallback_used")),
        "liveportrait_fallback_reason": str(stage_paths.get("liveportrait_fallback_reason") or ""),
        "musetalk_source_video": str(stage_paths.get("musetalk_source_video") or ""),
        "musetalk_source_kind": str(stage_paths.get("musetalk_source_kind") or ""),
        "restoration_enabled": bool(stage_paths.get("restoration_enabled")),
        "restoration_succeeded": bool(stage_paths.get("restoration_succeeded")),
        "restoration_failed": bool(stage_paths.get("restoration_failed")),
        "strict_validation_passed": bool(strict_pass),
        "preview_warning": str(preview_warning or ""),
        "motion_validation": validation,
        "stage_paths": stage_paths,
        "stage_outputs": stage_outputs,
        "canonical_input": canonical_input,
        "final_output_path": str(output_path),
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _final_payload(
    *,
    request: Any,
    requested_engine: str,
    output_path: Path,
    validation: dict[str, Any],
    strict_pass: bool,
    preview_warning: str,
    engine_used: str,
    stage_paths: dict[str, Any],
    stage_outputs: list[dict[str, Any]],
    canonical_input: dict[str, Any],
    failure_category: str = "",
) -> dict[str, Any]:
    video_hash = sha256_file(str(output_path)) if output_path.exists() else ""
    _write_meta(
        meta_path=output_path.with_suffix(output_path.suffix + ".meta.json"),
        request=request,
        requested_engine=requested_engine,
        output_path=output_path,
        video_hash=video_hash,
        validation=validation,
        strict_pass=strict_pass,
        preview_warning=preview_warning,
        engine_used=engine_used,
        stage_paths=stage_paths,
        stage_outputs=stage_outputs,
        canonical_input=canonical_input,
    )
    return {
        "output_path": str(output_path),
        "engine_used": engine_used,
        "pipeline_engine": CANONICAL_ENGINE,
        "requested_engine_raw": str(stage_paths.get("requested_engine_raw") or ""),
        "requested_engine": requested_engine,
        "normalized_engine": requested_engine,
        "avatar_engine_selected": requested_engine,
        "liveportrait_motion_preset": str(stage_paths.get("liveportrait_motion_preset") or _liveportrait_motion_preset()),
        "liveportrait_motion_profile": str(stage_paths.get("liveportrait_motion_profile") or ""),
        "liveportrait_driver_source": str(stage_paths.get("liveportrait_driver_source") or ""),
        "liveportrait_composer_used": bool(stage_paths.get("liveportrait_composer_used")),
        "liveportrait_boosted_retry_used": bool(stage_paths.get("liveportrait_boosted_retry_used")),
        "liveportrait_recenter_enabled": bool(stage_paths.get("liveportrait_recenter_enabled")),
        "liveportrait_whole_frame_drift_guard": bool(stage_paths.get("liveportrait_whole_frame_drift_guard")),
        "liveportrait_enabled": bool(stage_paths.get("liveportrait_enabled")),
        "liveportrait_started": bool(stage_paths.get("liveportrait_started")),
        "liveportrait_succeeded": bool(stage_paths.get("liveportrait_succeeded")),
        "liveportrait_failed": bool(stage_paths.get("liveportrait_failed")),
        "liveportrait_failure_reason": str(stage_paths.get("liveportrait_failure_reason") or ""),
        "liveportrait_quality_warning": str(stage_paths.get("liveportrait_quality_warning") or ""),
        "liveportrait_motion_passed": bool(stage_paths.get("liveportrait_motion_passed")),
        "liveportrait_technical_valid": bool(stage_paths.get("liveportrait_technical_valid")),
        "liveportrait_fallback_used": bool(stage_paths.get("liveportrait_fallback_used")),
        "liveportrait_fallback_reason": str(stage_paths.get("liveportrait_fallback_reason") or ""),
        "musetalk_source_video": str(stage_paths.get("musetalk_source_video") or ""),
        "musetalk_source_kind": str(stage_paths.get("musetalk_source_kind") or ""),
        "restoration_enabled": bool(stage_paths.get("restoration_enabled")),
        "restoration_succeeded": bool(stage_paths.get("restoration_succeeded")),
        "restoration_failed": bool(stage_paths.get("restoration_failed")),
        "fallback_chain_used": [record.get("stage") for record in stage_outputs],
        "final_avatar_engine_chain": list(
            stage_paths.get("final_avatar_engine_chain")
            or [record.get("stage") for record in stage_outputs]
        ),
        "audio_hash": _expected_cache_keys(request, requested_engine).get("audio_hash") or "",
        "video_hash": video_hash,
        "motion_validation": validation,
        "strict_validation_passed": bool(strict_pass),
        "preview_warning": str(preview_warning or ""),
        "preview_file_exists": bool(stage_paths.get("preview_file_exists")),
        "preview_usable": bool(stage_paths.get("preview_usable")),
        "ui_returned_playable_file": str(stage_paths.get("ui_returned_playable_file") or ""),
        "preview_status": (
            "warning"
            if _is_preview_request(request) and preview_warning
            else ("ready" if _is_preview_request(request) else "ok")
        ),
        "unstable_output_accepted": bool(_is_preview_request(request) and preview_warning and not strict_pass),
        "failure_category": str(failure_category or ""),
        "stage_paths": stage_paths,
        "stage_outputs": stage_outputs,
        "frame_trace": {"paths": stage_paths},
        "canonical_input": canonical_input,
    }


def _build_static_handoff_loop(
    *,
    source_image: Path,
    output_path: Path,
    duration_seconds: float,
    fps: float = 25.0,
    stage_name: str = "static_loop_fallback",
) -> Path:
    if not source_image.exists():
        raise RuntimeError(f"{stage_name}_source_missing:{source_image}")

    duration = max(float(duration_seconds or 0.0), 0.05)
    fps_val = max(float(fps or 25.0), 1.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(source_image),
        "-t",
        f"{duration:.6f}",
        "-r",
        str(fps_val),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, check=False, timeout=180)
    if proc.returncode != 0 or (not output_path.exists()) or output_path.stat().st_size <= 0:
        error_tail = (proc.stderr or proc.stdout or str(proc.returncode))[-400:]
        raise RuntimeError(f"{stage_name}_build_failed:{error_tail}")

    return output_path


def render_avatar_segment_local_canonical(request: Any) -> dict[str, Any]:
    output_path = Path(str(getattr(request, "output_path", "") or ""))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = output_path.with_suffix(output_path.suffix + ".meta.json")
    is_preview_request = _is_preview_request(request)
    preview_source_meta = dict(getattr(request, "preview_source_meta", {}) or {})
    raw_requested_engine = str(
        getattr(request, "_requested_engine_raw", "")
        or preview_source_meta.get("requested_engine_raw")
        or getattr(request, "lipsync_engine", "")
        or ""
    ).strip()
    requested_engine = normalize_avatar_engine(raw_requested_engine)

    if is_preview_request:
        removed_artifacts = _clear_preview_stage_artifacts(output_path)
        if removed_artifacts:
            logger.info(
                "Avatar preview stale artifact cleanup output=%s removed=%s",
                str(output_path),
                json.dumps(removed_artifacts, ensure_ascii=True),
            )

    request_trace = _request_trace(request, requested_engine, raw_requested_engine)
    logger.info(
        "Avatar canonical request binding %s",
        json.dumps(request_trace, ensure_ascii=True, sort_keys=True),
    )

    cached = _load_cached_result(request, is_preview_request=is_preview_request, output_path=output_path, meta_path=meta_path)
    if cached is not None:
        return cached

    audio_contract = legacy_pipeline._assert_audio_contract(
        str(getattr(request, "audio_path", "") or ""),
        stage_name=("preview_audio" if is_preview_request else "render_audio"),
    )
    expected_audio_hash = sha256_file(str(getattr(request, "audio_path", "") or ""))
    contract_duration_seconds = _request_contract_duration_seconds(request)
    if contract_duration_seconds <= 0.0:
        contract_duration_seconds = float(audio_contract.get("duration_seconds") or 0.0)
    runtime_resources_start = probe_runtime_resources()
    liveportrait_timeout_seconds, liveportrait_timeout_reason = _liveportrait_timeout_profile(
        request,
        audio_duration_seconds=contract_duration_seconds,
        resources=runtime_resources_start,
    )
    musetalk_timeout_budget = 0.0
    musetalk_timeout_reason: dict[str, Any] = {}
    should_enforce_exact_duration = bool(getattr(request, "enforce_exact_audio_duration", False)) or (
        is_preview_request and contract_duration_seconds > 0.0
    )

    resolved_inputs = _resolve_requested_inputs(request, allow_image_fallback=is_preview_request)
    source_candidates = _resolve_liveportrait_source_candidates(
        request=request,
        resolved_inputs=resolved_inputs,
        is_preview_request=is_preview_request,
    )
    source_key_for_canonical = str(resolved_inputs.get("resolved_source_key") or "")
    source_image_primary = str(resolved_inputs.get("source_image_primary") or "")
    source_video_primary = str(resolved_inputs.get("source_video_primary") or "")
    stage_env: dict[str, str] = {}
    canonical_input: Any | None = None
    stage_outputs: list[dict[str, Any]] = []
    warning_parts: list[str] = []
    candidate_trace = [
        {
            "source_key": str(candidate.get("resolved_source_key") or ""),
            "source_image_path": str(candidate.get("source_image_primary") or ""),
            "source_video_path": str(candidate.get("source_video_primary") or ""),
            "reason": str(candidate.get("candidate_reason") or ""),
        }
        for candidate in source_candidates
    ]

    liveportrait_output = output_path.with_suffix(output_path.suffix + ".liveportrait.mp4")
    liveportrait_reconciled_output = output_path.with_suffix(output_path.suffix + ".liveportrait.reconciled.mp4")
    musetalk_handoff_output = output_path.with_suffix(output_path.suffix + ".musetalk_handoff.mp4")
    musetalk_output = output_path.with_suffix(output_path.suffix + ".musetalk.mp4")
    restoration_output = output_path.with_suffix(output_path.suffix + ".restored.mp4")
    liveportrait_runtime_enabled = _env_enabled("AVATAR_LIVEPORTRAIT_ENABLED", True)
    lp_low_motion_fallback_to_static = _env_enabled("AVATAR_LP_LOW_MOTION_FALLBACK_TO_STATIC", False)
    restoration_runtime_enabled = _restore_enabled(is_preview_request)
    liveportrait_motion_preset = _liveportrait_motion_preset()
    liveportrait_boosted_retry_allowed = _liveportrait_boosted_retry_allowed(liveportrait_motion_preset)
    stage_paths: dict[str, Any] = {
        **request_trace,
        "orchestrator_mode": "resource_adaptive_strict_quality",
        "stage_order": ["tts", "liveportrait", "musetalk", "restoration_optional"],
        "cache_meta_path": str(meta_path),
        "cache_policy": ("disabled_current_run_required" if is_preview_request else "strict_hash_match"),
        "runtime_resources_start": runtime_resources_start,
        "resolved_source_key": str(source_key_for_canonical or ""),
        "resolved_source_image_path": str(source_image_primary),
        "resolved_source_video_path": str(source_video_primary),
        "original_source_path": "",
        "selected_source_key": "",
        "normalized_input_path": "",
        "liveportrait_source_candidates": candidate_trace,
        "liveportrait_candidate_attempts": [],
        "liveportrait_rejected_sources": [],
        "tts_audio_path": str(getattr(request, "audio_path", "") or ""),
        "tts_audio_hash": expected_audio_hash,
        "tts_audio_duration_seconds": round(float(audio_contract.get("duration_seconds") or 0.0), 4),
        "preview_contract_duration_seconds": round(float(contract_duration_seconds), 4),
        "preview_target_frame_count": int(getattr(request, "target_frame_count", 0) or 0),
        "liveportrait_timeout_seconds": round(float(liveportrait_timeout_seconds), 4),
        "liveportrait_timeout_reason": dict(liveportrait_timeout_reason or {}),
        "liveportrait_output_path": str(liveportrait_output),
        "liveportrait_raw_output_path": str(liveportrait_output),
        "liveportrait_reconciled_output_path": str(liveportrait_reconciled_output),
        "liveportrait_enabled": bool(liveportrait_runtime_enabled),
        "liveportrait_started": False,
        "liveportrait_succeeded": False,
        "liveportrait_failed": False,
        "liveportrait_failure_reason": "",
        "liveportrait_quality_warning": "",
        "liveportrait_motion_passed": False,
        "liveportrait_technical_valid": False,
        "liveportrait_fallback_used": False,
        "liveportrait_fallback_reason": "",
        "liveportrait_low_motion_fallback_to_static": bool(lp_low_motion_fallback_to_static),
        "liveportrait_motion_preset": liveportrait_motion_preset,
        "liveportrait_motion_profile": "",
        "liveportrait_driver_source": "",
        "liveportrait_composer_used": False,
        "liveportrait_boosted_retry_used": False,
        "liveportrait_recenter_enabled": liveportrait_motion_preset in _LIVEPORTRAIT_MOTION_PRESETS,
        "liveportrait_whole_frame_drift_guard": liveportrait_motion_preset != "expressive_debug",
        "liveportrait_boosted_retry_allowed": bool(liveportrait_boosted_retry_allowed),
        "liveportrait_bypassed": False,
        "liveportrait_bypass_reason": "",
        "musetalk_handoff_video_path": str(musetalk_handoff_output),
        "musetalk_handoff_source": "",
        "musetalk_source_video": "",
        "musetalk_source_kind": "",
        "musetalk_output_path": str(musetalk_output),
        "musetalk_started": False,
        "musetalk_succeeded": False,
        "musetalk_timeout_budget_seconds": 0.0,
        "musetalk_chunk_timing_metrics": [],
        "restoration_output_path": str(restoration_output),
        "restoration_enabled": bool(restoration_runtime_enabled),
        "restoration_succeeded": False,
        "restoration_failed": False,
        "restoration_failure_reason": "",
        "final_output_path": str(output_path),
        "final_avatar_engine_chain": [],
    }
    canonical_input_payload = {
        "request_trace": dict(request_trace),
        "source_candidates": candidate_trace,
        "original_input_path": "",
        "selected_source_key": "",
        "normalized_input_path": "",
        "normalized_mode": "",
        "engine_name": "",
        "source_kind": "",
        "metrics": {},
        "warning": "",
        "ranking": [],
        "handoff": {},
    }
    _update_preview_task_context(
        request,
        current_stage="liveportrait",
        stage_started_at=time.monotonic(),
        stage_timeout_budget_seconds=round(float(liveportrait_timeout_seconds), 4),
        liveportrait_completed=False,
        musetalk_started=False,
    )

    def _summary_log(preview_status: str, reason: str) -> None:
        logger.info(
            "Avatar canonical pipeline summary %s",
            json.dumps(
                {
                    "preview_teacher_id": int(getattr(request, "preview_teacher_id", 0) or 0),
                    "preview_job_id": int(getattr(request, "preview_job_id", 0) or 0),
                    "request_source_key": stage_paths.get("request_source_key"),
                    "resolved_source_key": stage_paths.get("resolved_source_key"),
                    "request_source_image_path": stage_paths.get("request_source_image_path"),
                    "request_source_image_original_path": stage_paths.get("request_source_image_original_path"),
                    "request_source_video_path": stage_paths.get("request_source_video_path"),
                    "request_source_image_hash": stage_paths.get("request_source_image_hash"),
                    "request_source_image_original_hash": stage_paths.get("request_source_image_original_hash"),
                    "request_source_video_hash": stage_paths.get("request_source_video_hash"),
                    "request_audio_hash": stage_paths.get("request_audio_hash"),
                    "original_source_path": stage_paths.get("original_source_path"),
                    "selected_source_key": stage_paths.get("selected_source_key"),
                    "normalized_input_path": stage_paths.get("normalized_input_path"),
                    "tts_audio_path": stage_paths.get("tts_audio_path"),
                    "tts_audio_hash": stage_paths.get("tts_audio_hash"),
                    "request_text_hash": stage_paths.get("request_text_hash"),
                    "runtime_resources_start": stage_paths.get("runtime_resources_start"),
                    "tts_audio_duration_seconds": stage_paths.get("tts_audio_duration_seconds"),
                    "preview_contract_duration_seconds": stage_paths.get("preview_contract_duration_seconds"),
                    "cleanup_before_liveportrait": stage_paths.get("cleanup_before_liveportrait"),
                    "liveportrait_output_path": stage_paths.get("liveportrait_output_path"),
                    "liveportrait_timeout_seconds": stage_paths.get("liveportrait_timeout_seconds"),
                    "liveportrait_timeout_reason": stage_paths.get("liveportrait_timeout_reason"),
                    "liveportrait_elapsed_seconds": stage_paths.get("liveportrait_elapsed_seconds"),
                    "liveportrait_output_duration_seconds": stage_paths.get("liveportrait_output_duration_seconds"),
                    "liveportrait_motion_source": stage_paths.get("liveportrait_motion_source"),
                    "liveportrait_bypassed": stage_paths.get("liveportrait_bypassed"),
                    "liveportrait_bypass_reason": stage_paths.get("liveportrait_bypass_reason"),
                    "liveportrait_motion_preset": stage_paths.get("liveportrait_motion_preset"),
                    "liveportrait_motion_profile": stage_paths.get("liveportrait_motion_profile"),
                    "liveportrait_driver_source": stage_paths.get("liveportrait_driver_source"),
                    "liveportrait_composer_used": stage_paths.get("liveportrait_composer_used"),
                    "liveportrait_boosted_retry_used": stage_paths.get("liveportrait_boosted_retry_used"),
                    "liveportrait_recenter_enabled": stage_paths.get("liveportrait_recenter_enabled"),
                    "liveportrait_whole_frame_drift_guard": stage_paths.get("liveportrait_whole_frame_drift_guard"),
                    "liveportrait_failed": stage_paths.get("liveportrait_failed"),
                    "liveportrait_failure_reason": stage_paths.get("liveportrait_failure_reason"),
                    "liveportrait_quality_warning": stage_paths.get("liveportrait_quality_warning"),
                    "liveportrait_motion_passed": stage_paths.get("liveportrait_motion_passed"),
                    "liveportrait_technical_valid": stage_paths.get("liveportrait_technical_valid"),
                    "liveportrait_fallback_used": stage_paths.get("liveportrait_fallback_used"),
                    "liveportrait_fallback_reason": stage_paths.get("liveportrait_fallback_reason"),
                    "liveportrait_source_candidates": stage_paths.get("liveportrait_source_candidates"),
                    "liveportrait_candidate_attempts": stage_paths.get("liveportrait_candidate_attempts"),
                    "liveportrait_rejected_sources": stage_paths.get("liveportrait_rejected_sources"),
                    "liveportrait_selected_source_key": stage_paths.get("liveportrait_selected_source_key"),
                    "cleanup_after_liveportrait": stage_paths.get("cleanup_after_liveportrait"),
                    "duration_reconciliation_strategy": stage_paths.get("duration_reconciliation_strategy"),
                    "duration_reconciliation_delta_seconds": stage_paths.get("duration_reconciliation_delta_seconds"),
                    "duration_reconciliation_adjustment_seconds": stage_paths.get("duration_reconciliation_adjustment_seconds"),
                    "duration_reconciliation_contract_duration_seconds": stage_paths.get("duration_reconciliation_contract_duration_seconds"),
                    "reconciliation_final_video_duration": stage_paths.get("reconciliation_final_video_duration"),
                    "reconciliation_final_audio_duration": stage_paths.get("reconciliation_final_audio_duration"),
                    "musetalk_handoff_source": stage_paths.get("musetalk_handoff_source"),
                    "musetalk_handoff_video_path": stage_paths.get("musetalk_handoff_video_path"),
                    "musetalk_source_video": stage_paths.get("musetalk_source_video"),
                    "musetalk_source_kind": stage_paths.get("musetalk_source_kind"),
                    "musetalk_handoff_frame_normalization_strategy": stage_paths.get("musetalk_handoff_frame_normalization_strategy"),
                    "musetalk_handoff_frame_count_before": stage_paths.get("musetalk_handoff_frame_count_before"),
                    "musetalk_handoff_frame_count_after": stage_paths.get("musetalk_handoff_frame_count_after"),
                    "musetalk_handoff_video_duration_seconds": stage_paths.get("musetalk_handoff_video_duration_seconds"),
                    "musetalk_stage_state": stage_paths.get("musetalk_stage_state"),
                    "musetalk_ran": stage_paths.get("musetalk_ran"),
                    "musetalk_timed_out": stage_paths.get("musetalk_timed_out"),
                    "musetalk_fallback_used": stage_paths.get("musetalk_fallback_used"),
                    "musetalk_exit_status": stage_paths.get("musetalk_exit_status"),
                    "musetalk_elapsed_seconds": stage_paths.get("musetalk_elapsed_seconds"),
                    "musetalk_command": stage_paths.get("musetalk_command"),
                    "musetalk_skip_reason": stage_paths.get("musetalk_skip_reason"),
                    "musetalk_output_path": stage_paths.get("musetalk_output_path"),
                    "musetalk_timeout_budget_seconds": stage_paths.get("musetalk_timeout_budget_seconds"),
                    "musetalk_timeout_reason": stage_paths.get("musetalk_timeout_reason"),
                    "musetalk_chunk_timing_metrics": stage_paths.get("musetalk_chunk_timing_metrics"),
                    "cleanup_after_musetalk": stage_paths.get("cleanup_after_musetalk"),
                    "restoration_output_path": stage_paths.get("restoration_output_path"),
                    "restoration_enabled": stage_paths.get("restoration_enabled"),
                    "restoration_succeeded": stage_paths.get("restoration_succeeded"),
                    "restoration_failed": stage_paths.get("restoration_failed"),
                    "restoration_failure_reason": stage_paths.get("restoration_failure_reason"),
                    "restoration_timeout_seconds": stage_paths.get("restoration_timeout_seconds"),
                    "restoration_timeout_reason": stage_paths.get("restoration_timeout_reason"),
                    "restoration_elapsed_seconds": stage_paths.get("restoration_elapsed_seconds"),
                    "final_output_path": stage_paths.get("final_output_path"),
                    "final_playable_path": stage_paths.get("final_playable_path"),
                    "ui_returned_playable_file": stage_paths.get("ui_returned_playable_file"),
                    "final_output_playable_motion": stage_paths.get("final_output_playable_motion"),
                    "preview_status": preview_status,
                    "reason": str(reason or ""),
                    "requested_engine_raw": stage_paths.get("requested_engine_raw"),
                    "requested_engine": requested_engine,
                    "normalized_engine": requested_engine,
                    "avatar_engine_selected": requested_engine,
                    "pipeline_engine": CANONICAL_ENGINE,
                    "liveportrait_enabled": stage_paths.get("liveportrait_enabled"),
                    "liveportrait_motion_preset": stage_paths.get("liveportrait_motion_preset"),
                    "liveportrait_motion_profile": stage_paths.get("liveportrait_motion_profile"),
                    "liveportrait_driver_source": stage_paths.get("liveportrait_driver_source"),
                    "liveportrait_composer_used": stage_paths.get("liveportrait_composer_used"),
                    "liveportrait_boosted_retry_used": stage_paths.get("liveportrait_boosted_retry_used"),
                    "liveportrait_recenter_enabled": stage_paths.get("liveportrait_recenter_enabled"),
                    "liveportrait_whole_frame_drift_guard": stage_paths.get("liveportrait_whole_frame_drift_guard"),
                    "liveportrait_started": stage_paths.get("liveportrait_started"),
                    "liveportrait_succeeded": stage_paths.get("liveportrait_succeeded"),
                    "liveportrait_failed": stage_paths.get("liveportrait_failed"),
                    "liveportrait_failure_reason": stage_paths.get("liveportrait_failure_reason"),
                    "liveportrait_quality_warning": stage_paths.get("liveportrait_quality_warning"),
                    "liveportrait_motion_passed": stage_paths.get("liveportrait_motion_passed"),
                    "liveportrait_technical_valid": stage_paths.get("liveportrait_technical_valid"),
                    "liveportrait_fallback_used": stage_paths.get("liveportrait_fallback_used"),
                    "liveportrait_fallback_reason": stage_paths.get("liveportrait_fallback_reason"),
                    "musetalk_source_kind": stage_paths.get("musetalk_source_kind"),
                    "musetalk_started": stage_paths.get("musetalk_started"),
                    "musetalk_source_video": stage_paths.get("musetalk_source_video"),
                    "musetalk_succeeded": stage_paths.get("musetalk_succeeded"),
                    "final_avatar_engine_chain": stage_paths.get("final_avatar_engine_chain"),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )

    try:
        cleanup_before_liveportrait = release_stage_resources(reason="before_liveportrait")
        stage_paths["cleanup_before_liveportrait"] = _cleanup_summary(cleanup_before_liveportrait)
        liveportrait_attempts: list[dict[str, Any]] = []
        liveportrait_rejections: list[dict[str, Any]] = []
        selected_stage_index = -1
        liveportrait_bypassed = False
        liveportrait_enabled = bool(liveportrait_runtime_enabled)
        liveportrait_succeeded = False
        liveportrait_failed = False
        liveportrait_failure_reason = ""
        liveportrait_quality_warning = ""
        liveportrait_fallback_used = False
        liveportrait_fallback_reason = ""
        musetalk_source_kind = "none"
        restoration_enabled = bool(restoration_runtime_enabled)

        stage_paths["liveportrait_enabled"] = liveportrait_enabled
        stage_paths["liveportrait_succeeded"] = liveportrait_succeeded
        stage_paths["liveportrait_failed"] = liveportrait_failed
        stage_paths["liveportrait_failure_reason"] = liveportrait_failure_reason
        stage_paths["liveportrait_quality_warning"] = liveportrait_quality_warning
        stage_paths["liveportrait_fallback_used"] = liveportrait_fallback_used
        stage_paths["liveportrait_fallback_reason"] = liveportrait_fallback_reason
        stage_paths["musetalk_source_kind"] = musetalk_source_kind
        stage_paths["restoration_enabled"] = restoration_enabled

        if requested_engine == MUSETALK_ONLY_ENGINE:
            try:
                canonical_input = canonicalize_avatar_input(
                    source_image_path=source_image_primary,
                    source_video_path=source_video_primary,
                    output_path=str(output_path),
                    is_preview=is_preview_request,
                    engine_name=MUSETALK_ONLY_ENGINE,
                    source_key=str(source_key_for_canonical or "musetalk_only"),
                )
            except Exception as exc:
                raise RuntimeError(f"musetalk_only_input_failed:{exc}") from exc

            stage_env = _build_stage_env(canonical_input, request)
            source_key_for_canonical = str(source_key_for_canonical or getattr(canonical_input, "selected_source_key", "") or "musetalk_only")
            source_image_primary = str(source_image_primary or getattr(canonical_input, "normalized_input_path", "") or "")
            source_video_primary = str(source_video_primary or "")
            liveportrait_bypassed = True
            liveportrait_enabled = False
            musetalk_source_kind = "static_source"
            stage_paths["liveportrait_enabled"] = False
            stage_paths["musetalk_source_kind"] = musetalk_source_kind
            stage_paths["liveportrait_bypassed"] = True
            stage_paths["liveportrait_bypass_reason"] = "requested_engine:musetalk"
            stage_paths["resolved_source_key"] = str(source_key_for_canonical)
            stage_paths["resolved_source_image_path"] = str(source_image_primary)
            stage_paths["resolved_source_video_path"] = str(source_video_primary)
            stage_paths["original_source_path"] = str(getattr(canonical_input, "original_input_path", "") or "")
            stage_paths["selected_source_key"] = str(getattr(canonical_input, "selected_source_key", "") or "")
            stage_paths["normalized_input_path"] = str(getattr(canonical_input, "normalized_input_path", "") or "")
            warning_parts.append("liveportrait_bypassed:requested_engine_musetalk")
            canonical_input_payload = {
                "request_trace": dict(request_trace),
                "source_candidates": candidate_trace,
                "source_attempts": [],
                "rejected_sources": [],
                "original_input_path": str(getattr(canonical_input, "original_input_path", "") or ""),
                "selected_source_key": str(getattr(canonical_input, "selected_source_key", "") or ""),
                "normalized_input_path": str(getattr(canonical_input, "normalized_input_path", "") or ""),
                "normalized_mode": str(getattr(canonical_input, "normalized_mode", "") or ""),
                "engine_name": str(getattr(canonical_input, "engine_name", "") or ""),
                "source_kind": str(getattr(canonical_input, "source_kind", "") or ""),
                "metrics": dict(getattr(canonical_input, "metrics", {}) or {}),
                "warning": str(getattr(canonical_input, "warning", "") or ""),
                "ranking": list(getattr(canonical_input, "ranking", []) or []),
                "handoff": dict(getattr(canonical_input, "handoff", {}) or {}),
            }
            _update_preview_task_context(
                request,
                current_stage="after_liveportrait",
                liveportrait_completed=False,
                stage_timeout_budget_seconds=0.0,
            )
        elif not liveportrait_enabled:
            try:
                canonical_input = canonicalize_avatar_input(
                    source_image_path=source_image_primary,
                    source_video_path=source_video_primary,
                    output_path=str(output_path),
                    is_preview=is_preview_request,
                    engine_name=CANONICAL_ENGINE,
                    source_key=str(source_key_for_canonical or "static_source"),
                )
            except Exception as exc:
                raise RuntimeError(f"liveportrait_disabled_input_failed:{exc}") from exc

            stage_env = _build_stage_env(canonical_input, request)
            source_key_for_canonical = str(source_key_for_canonical or getattr(canonical_input, "selected_source_key", "") or "static_source")
            source_image_primary = str(source_image_primary or getattr(canonical_input, "normalized_input_path", "") or "")
            source_video_primary = str(source_video_primary or "")
            liveportrait_bypassed = True
            musetalk_source_kind = "static_source"
            stage_paths["liveportrait_enabled"] = False
            stage_paths["musetalk_source_kind"] = musetalk_source_kind
            stage_paths["liveportrait_bypassed"] = True
            stage_paths["liveportrait_bypass_reason"] = "disabled"
            stage_paths["resolved_source_key"] = str(source_key_for_canonical)
            stage_paths["resolved_source_image_path"] = str(source_image_primary)
            stage_paths["resolved_source_video_path"] = str(source_video_primary)
            stage_paths["original_source_path"] = str(getattr(canonical_input, "original_input_path", "") or "")
            stage_paths["selected_source_key"] = str(getattr(canonical_input, "selected_source_key", "") or "")
            stage_paths["normalized_input_path"] = str(getattr(canonical_input, "normalized_input_path", "") or "")
            warning_parts.append("liveportrait_bypassed:disabled")
            canonical_input_payload = {
                "request_trace": dict(request_trace),
                "source_candidates": candidate_trace,
                "source_attempts": [],
                "rejected_sources": [],
                "original_input_path": str(getattr(canonical_input, "original_input_path", "") or ""),
                "selected_source_key": str(getattr(canonical_input, "selected_source_key", "") or ""),
                "normalized_input_path": str(getattr(canonical_input, "normalized_input_path", "") or ""),
                "normalized_mode": str(getattr(canonical_input, "normalized_mode", "") or ""),
                "engine_name": str(getattr(canonical_input, "engine_name", "") or ""),
                "source_kind": str(getattr(canonical_input, "source_kind", "") or ""),
                "metrics": dict(getattr(canonical_input, "metrics", {}) or {}),
                "warning": str(getattr(canonical_input, "warning", "") or ""),
                "ranking": list(getattr(canonical_input, "ranking", []) or []),
                "handoff": dict(getattr(canonical_input, "handoff", {}) or {}),
            }
            _update_preview_task_context(
                request,
                current_stage="after_liveportrait",
                liveportrait_completed=False,
                stage_timeout_budget_seconds=0.0,
            )

        liveportrait_candidates = [] if liveportrait_bypassed else source_candidates
        for attempt_index, candidate in enumerate(liveportrait_candidates, start=1):
            candidate_source_key = str(candidate.get("resolved_source_key") or "")
            candidate_source_image = str(candidate.get("source_image_primary") or "")
            candidate_source_video = str(candidate.get("source_video_primary") or "")
            attempt_payload: dict[str, Any] = {
                "attempt": int(attempt_index),
                "source_key": candidate_source_key,
                "source_image_path": candidate_source_image,
                "source_video_path": candidate_source_video,
                "reason": str(candidate.get("candidate_reason") or ""),
            }
            liveportrait_attempts.append(attempt_payload)

            try:
                candidate_canonical_input = canonicalize_avatar_input(
                    source_image_path=candidate_source_image,
                    source_video_path=candidate_source_video,
                    output_path=str(output_path),
                    is_preview=is_preview_request,
                    engine_name=CANONICAL_ENGINE,
                    source_key=candidate_source_key,
                )
            except Exception as primary_exc:
                rejection_reason = f"canonical_input_failed:{primary_exc}"
                attempt_payload["result"] = "rejected"
                attempt_payload["failure_reason"] = rejection_reason
                liveportrait_rejections.append(dict(attempt_payload))
                logger.warning(
                    "Avatar liveportrait source candidate rejected teacher_id=%s job_id=%s attempt=%s source_key=%s reason=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    int(attempt_index),
                    candidate_source_key,
                    rejection_reason,
                )
                continue

            candidate_resolved_inputs = {
                "requested_source_key": str(candidate.get("requested_source_key") or candidate_source_key),
                "resolved_source_key": candidate_source_key,
                "source_image_primary": candidate_source_image,
                "source_video_primary": candidate_source_video,
            }
            try:
                _assert_current_source_binding(canonical_input=candidate_canonical_input, resolved_inputs=candidate_resolved_inputs)
            except Exception as binding_exc:
                rejection_reason = f"canonical_input_binding_failed:{binding_exc}"
                attempt_payload["result"] = "rejected"
                attempt_payload["failure_reason"] = rejection_reason
                liveportrait_rejections.append(dict(attempt_payload))
                logger.warning(
                    "Avatar liveportrait source candidate rejected teacher_id=%s job_id=%s attempt=%s source_key=%s reason=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    int(attempt_index),
                    candidate_source_key,
                    rejection_reason,
                )
                continue

            stage_env = _build_stage_env(candidate_canonical_input, request)
            candidate_warning = str(getattr(candidate_canonical_input, "warning", "") or "").strip()
            if candidate_warning:
                attempt_payload["input_warning"] = candidate_warning

            liveportrait_resources_before = probe_runtime_resources()
            stage_paths["liveportrait_resources_before"] = liveportrait_resources_before
            stage_paths["liveportrait_started"] = True
            liveportrait_started_at = time.monotonic()
            _update_preview_task_context(
                request,
                current_stage="liveportrait",
                stage_started_at=liveportrait_started_at,
                stage_timeout_budget_seconds=round(float(liveportrait_timeout_seconds), 4),
            )
            try:
                liveportrait_result = run_liveportrait(
                    input_path=str(candidate_canonical_input.normalized_input_path),
                    output_path=str(liveportrait_output),
                    audio_path=str(getattr(request, "audio_path", "") or ""),
                    source_video=str(candidate_source_video or ""),
                    fps=float(stage_env.get("AVATAR_LIVEPORTRAIT_FPS", "0") or 0),
                    target_frame_count=int(getattr(request, "target_frame_count", 0) or 0),
                    env_overrides=stage_env,
                    timeout_seconds=float(liveportrait_timeout_seconds),
                )
            except Exception as liveportrait_exc:
                liveportrait_result = EngineResult(
                    False,
                    "liveportrait",
                    str(liveportrait_output),
                    f"exception:{liveportrait_exc}",
                )
            liveportrait_elapsed_seconds = time.monotonic() - liveportrait_started_at

            stage_record = _stage_record("liveportrait", liveportrait_result, input_path=str(candidate_canonical_input.normalized_input_path))
            stage_record["elapsed_seconds"] = round(float(liveportrait_elapsed_seconds), 4)
            stage_record["attempt"] = int(attempt_index)
            stage_record["source_key"] = candidate_source_key
            stage_outputs.append(stage_record)

            stage_paths["liveportrait_elapsed_seconds"] = round(float(liveportrait_elapsed_seconds), 4)
            stage_paths["liveportrait_resources_after"] = probe_runtime_resources()
            record_stage_timing(
                stage_name="liveportrait",
                elapsed_seconds=float(liveportrait_elapsed_seconds),
                success=bool(liveportrait_result.success),
                audio_duration_seconds=float(contract_duration_seconds),
                frame_count=int(getattr(request, "target_frame_count", 0) or 0),
                resources=stage_paths.get("liveportrait_resources_after"),
                context={
                    "is_preview_request": bool(is_preview_request),
                    "timeout_seconds": round(float(liveportrait_timeout_seconds), 4),
                    "attempt": int(attempt_index),
                    "source_key": candidate_source_key,
                },
            )

            if not liveportrait_result.success:
                rejection_reason = f"liveportrait_failed:{liveportrait_result.error or 'command_failed'}"
                liveportrait_failed = True
                liveportrait_failure_reason = rejection_reason
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = rejection_reason
                attempt_payload["result"] = "rejected"
                attempt_payload["failure_reason"] = rejection_reason
                liveportrait_rejections.append(dict(attempt_payload))
                logger.warning(
                    "Avatar liveportrait source candidate rejected teacher_id=%s job_id=%s attempt=%s source_key=%s reason=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    int(attempt_index),
                    candidate_source_key,
                    rejection_reason,
                )
                continue

            liveportrait_stderr = str((liveportrait_result.details or {}).get("stderr") or "")
            motion_source_marker = ""
            marker_token = "motion_source="
            marker_idx = liveportrait_stderr.find(marker_token)
            if marker_idx >= 0:
                marker_line = liveportrait_stderr[marker_idx:].splitlines()[0].strip()
                motion_source_marker = marker_line
            stage_paths["liveportrait_motion_source"] = motion_source_marker
            stage_paths["liveportrait_motion_preset"] = (
                _stderr_token(liveportrait_stderr, "liveportrait_motion_preset")
                or stage_paths.get("liveportrait_motion_preset")
                or liveportrait_motion_preset
            )
            stage_paths["liveportrait_motion_profile"] = _stderr_token(
                liveportrait_stderr,
                "liveportrait_motion_profile",
            ) or _stderr_token(liveportrait_stderr, "profile")
            stage_paths["liveportrait_driver_source"] = _stderr_token(liveportrait_stderr, "liveportrait_driver_source")
            stage_paths["liveportrait_composer_used"] = _stderr_bool_token(
                liveportrait_stderr,
                "liveportrait_composer_used",
                bool(stage_paths.get("liveportrait_composer_used")),
            )
            stage_paths["liveportrait_boosted_retry_used"] = _stderr_bool_token(
                liveportrait_stderr,
                "liveportrait_boosted_retry_used",
                bool(stage_paths.get("liveportrait_boosted_retry_used")),
            )
            stage_paths["liveportrait_recenter_enabled"] = _stderr_bool_token(
                liveportrait_stderr,
                "liveportrait_recenter_enabled",
                bool(stage_paths.get("liveportrait_recenter_enabled")),
            )
            stage_paths["liveportrait_whole_frame_drift_guard"] = _stderr_bool_token(
                liveportrait_stderr,
                "liveportrait_whole_frame_drift_guard",
                bool(stage_paths.get("liveportrait_whole_frame_drift_guard")),
            )
            if motion_source_marker:
                logger.info(
                    "Avatar preview liveportrait motion_source teacher_id=%s job_id=%s marker=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    motion_source_marker,
                )

            try:
                liveportrait_contract = legacy_pipeline._assert_video_contract(str(liveportrait_output), stage_name="liveportrait")
            except Exception as contract_exc:
                rejection_reason = f"liveportrait_technical_invalid:{contract_exc}"
                liveportrait_failed = True
                liveportrait_failure_reason = rejection_reason
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = rejection_reason
                stage_paths["liveportrait_technical_valid"] = False
                stage_paths["liveportrait_motion_passed"] = False
                attempt_payload["result"] = "rejected"
                attempt_payload["failure_reason"] = rejection_reason
                liveportrait_rejections.append(dict(attempt_payload))
                logger.warning(
                    "Avatar liveportrait source candidate rejected teacher_id=%s job_id=%s attempt=%s source_key=%s reason=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    int(attempt_index),
                    candidate_source_key,
                    rejection_reason,
                )
                continue
            stage_outputs[-1]["duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)
            stage_paths["liveportrait_output_duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)

            try:
                motion_gate = _liveportrait_motion_gate(
                    str(liveportrait_output),
                    is_preview_request=is_preview_request,
                    expected_duration_seconds=float(contract_duration_seconds),
                    expected_fps=float(stage_env.get("AVATAR_LIVEPORTRAIT_FPS", "0") or 0),
                    expected_frame_count=int(getattr(request, "target_frame_count", 0) or 0),
                    stage_name="liveportrait_motion_gate_raw",
                )
            except Exception as motion_gate_exc:
                rejection_reason = f"liveportrait_technical_invalid:motion_gate_error:{motion_gate_exc}"
                liveportrait_failed = True
                liveportrait_failure_reason = rejection_reason
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = rejection_reason
                stage_paths["liveportrait_technical_valid"] = False
                stage_paths["liveportrait_motion_passed"] = False
                attempt_payload["result"] = "rejected"
                attempt_payload["failure_reason"] = rejection_reason
                liveportrait_rejections.append(dict(attempt_payload))
                logger.warning(
                    "Avatar liveportrait source candidate rejected teacher_id=%s job_id=%s attempt=%s source_key=%s reason=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    int(attempt_index),
                    candidate_source_key,
                    rejection_reason,
                )
                continue
            stage_outputs[-1]["motion_gate"] = dict(motion_gate)
            stage_paths["liveportrait_motion_gate"] = dict(motion_gate)
            logger.info(
                "Avatar preview liveportrait_motion_gate teacher_id=%s job_id=%s attempt=%s source_key=%s path=%s passed=%s "
                "duration=%s fps=%s frame_count=%s size_bytes=%s sha256=%s "
                "shared_unique_ratio=%s shared_mean_mad=%s shared_near_static=%s "
                "legacy_unique_frames=%s legacy_frame_delta=%s legacy_head_motion=%s legacy_mouth_motion=%s analyzer_disagrees=%s classification=%s",
                int(getattr(request, "preview_teacher_id", 0) or 0),
                int(getattr(request, "preview_job_id", 0) or 0),
                int(attempt_index),
                candidate_source_key,
                str(motion_gate.get("analyzed_path") or liveportrait_output),
                bool(motion_gate.get("passed")),
                float(motion_gate.get("duration") or 0.0),
                float(motion_gate.get("fps") or 0.0),
                int(motion_gate.get("frame_count") or 0),
                int(motion_gate.get("file_size_bytes") or 0),
                str(motion_gate.get("file_sha256") or ""),
                float(motion_gate.get("shared_probe_unique_ratio") or 0.0),
                float(motion_gate.get("shared_probe_mean_mad") or 0.0),
                bool(motion_gate.get("shared_probe_near_static")),
                int(motion_gate.get("unique_frames") or 0),
                float(motion_gate.get("frame_delta") or 0.0),
                float(motion_gate.get("head_motion_score") or 0.0),
                float(motion_gate.get("mouth_motion_score") or 0.0),
                bool(motion_gate.get("analyzer_disagrees")),
                str(motion_gate.get("analyzer_classification") or ""),
            )
            liveportrait_technical_valid = bool(
                motion_gate.get("technical_valid", motion_gate.get("technical_passed", motion_gate.get("passed")))
            )
            liveportrait_motion_passed = bool(motion_gate.get("motion_passed", motion_gate.get("passed")))
            stage_paths["liveportrait_technical_valid"] = liveportrait_technical_valid
            stage_paths["liveportrait_motion_passed"] = liveportrait_motion_passed
            low_motion_fallback_selected = False
            if not liveportrait_technical_valid:
                rejection_reason = str(
                    motion_gate.get("technical_failure_reason")
                    or motion_gate.get("failure_reason")
                    or "liveportrait_technical_invalid"
                )
                if not rejection_reason.startswith("liveportrait_technical_invalid"):
                    rejection_reason = f"liveportrait_technical_invalid:{rejection_reason}"
                liveportrait_failed = True
                liveportrait_failure_reason = rejection_reason
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = rejection_reason
                attempt_payload["result"] = "rejected"
                attempt_payload["failure_reason"] = rejection_reason
                attempt_payload["motion_gate"] = dict(motion_gate)
                liveportrait_rejections.append(dict(attempt_payload))
                logger.warning(
                    "Avatar liveportrait source candidate rejected teacher_id=%s job_id=%s attempt=%s source_key=%s reason=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    int(attempt_index),
                    candidate_source_key,
                    rejection_reason,
                )
                continue
            if not liveportrait_motion_passed:
                liveportrait_quality_warning = str(
                    motion_gate.get("motion_failure_reason")
                    or motion_gate.get("failure_reason")
                    or "liveportrait_low_motion"
                )
                stage_paths["liveportrait_quality_warning"] = liveportrait_quality_warning
                attempt_payload["quality_warning"] = liveportrait_quality_warning
                if lp_low_motion_fallback_to_static:
                    low_motion_fallback_selected = True
                    liveportrait_fallback_used = True
                    liveportrait_fallback_reason = "low_motion"
                    stage_paths["liveportrait_fallback_used"] = True
                    stage_paths["liveportrait_fallback_reason"] = liveportrait_fallback_reason

            canonical_input = candidate_canonical_input
            selected_stage_index = len(stage_outputs) - 1
            source_key_for_canonical = candidate_source_key
            source_image_primary = candidate_source_image
            source_video_primary = candidate_source_video

            stage_paths["resolved_source_key"] = str(source_key_for_canonical)
            stage_paths["resolved_source_image_path"] = str(source_image_primary)
            stage_paths["resolved_source_video_path"] = str(source_video_primary)
            stage_paths["original_source_path"] = str(getattr(canonical_input, "original_input_path", "") or "")
            stage_paths["selected_source_key"] = str(getattr(canonical_input, "selected_source_key", "") or "")
            stage_paths["normalized_input_path"] = str(getattr(canonical_input, "normalized_input_path", "") or "")
            stage_paths["liveportrait_selected_attempt"] = int(attempt_index)
            stage_paths["liveportrait_selected_source_key"] = str(source_key_for_canonical)
            stage_paths["liveportrait_selected_source_image_path"] = str(source_image_primary)
            stage_paths["liveportrait_succeeded"] = True
            stage_paths["liveportrait_failed"] = False
            stage_paths["liveportrait_failure_reason"] = ""
            liveportrait_succeeded = True
            liveportrait_failed = False
            liveportrait_failure_reason = ""
            musetalk_source_kind = "static_fallback" if low_motion_fallback_selected else "liveportrait"
            stage_paths["musetalk_source_kind"] = musetalk_source_kind
            _update_preview_task_context(
                request,
                current_stage="after_liveportrait",
                liveportrait_completed=True,
                stage_timeout_budget_seconds=0.0,
            )

            warning_parts = []
            if candidate_warning:
                warning_parts.append(f"input_warning:{candidate_warning}")
            if liveportrait_quality_warning:
                warning_parts.append(f"liveportrait_quality_warning:{liveportrait_quality_warning}")

            canonical_input_payload = {
                "request_trace": dict(request_trace),
                "source_candidates": candidate_trace,
                "source_attempts": list(liveportrait_attempts),
                "rejected_sources": list(liveportrait_rejections),
                "original_input_path": str(getattr(canonical_input, "original_input_path", "") or ""),
                "selected_source_key": str(getattr(canonical_input, "selected_source_key", "") or ""),
                "normalized_input_path": str(getattr(canonical_input, "normalized_input_path", "") or ""),
                "normalized_mode": str(getattr(canonical_input, "normalized_mode", "") or ""),
                "engine_name": str(getattr(canonical_input, "engine_name", "") or ""),
                "source_kind": str(getattr(canonical_input, "source_kind", "") or ""),
                "metrics": dict(getattr(canonical_input, "metrics", {}) or {}),
                "warning": str(getattr(canonical_input, "warning", "") or ""),
                "ranking": list(getattr(canonical_input, "ranking", []) or []),
                "handoff": dict(getattr(canonical_input, "handoff", {}) or {}),
            }
            attempt_payload["result"] = "selected_static_fallback" if low_motion_fallback_selected else "selected"
            break

        stage_paths["liveportrait_candidate_attempts"] = list(liveportrait_attempts)
        stage_paths["liveportrait_rejected_sources"] = list(liveportrait_rejections)

        if canonical_input is None or not liveportrait_succeeded:
            if not liveportrait_bypassed:
                rejected_sources = [
                    f"{entry.get('source_key') or 'unknown'}:{entry.get('failure_reason') or 'rejected'}"
                    for entry in liveportrait_rejections
                ]
                rejection_summary = "|".join(rejected_sources)
                fallback_reason = str(
                    liveportrait_failure_reason
                    or ("all_candidates_failed" + (f":{rejection_summary}" if rejection_summary else ""))
                )
                logger.warning(
                    "Avatar liveportrait failed for all candidates. Falling back to static image loop. project_id=%s teacher_id=%s reason=%s",
                    int(getattr(request, "_project_id", 0) or 0),
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    fallback_reason,
                )
                liveportrait_failed = True
                liveportrait_failure_reason = fallback_reason
                liveportrait_fallback_used = True
                liveportrait_fallback_reason = fallback_reason
                musetalk_source_kind = "static_fallback"
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = fallback_reason
                stage_paths["liveportrait_fallback_used"] = True
                stage_paths["liveportrait_fallback_reason"] = liveportrait_fallback_reason
                stage_paths["musetalk_source_kind"] = musetalk_source_kind
                # If we have no canonical_input yet (it failed for all candidates), we need to try to get one for the static fallback
                if canonical_input is None:
                    try:
                        canonical_input = canonicalize_avatar_input(
                            source_image_path=source_image_primary,
                            source_video_path=source_video_primary,
                            output_path=str(output_path),
                            is_preview=is_preview_request,
                            engine_name=CANONICAL_ENGINE,
                            source_key=str(source_key_for_canonical or "fallback"),
                        )
                    except Exception as fallback_exc:
                        raise RuntimeError(f"liveportrait_fallback_input_failed:{fallback_exc}") from fallback_exc
                    stage_env = _build_stage_env(canonical_input, request)
                    stage_paths["original_source_path"] = str(getattr(canonical_input, "original_input_path", "") or "")
                    stage_paths["selected_source_key"] = str(getattr(canonical_input, "selected_source_key", "") or "")
                    stage_paths["normalized_input_path"] = str(getattr(canonical_input, "normalized_input_path", "") or "")
                canonical_input_payload = {
                    "request_trace": dict(request_trace),
                    "source_candidates": candidate_trace,
                    "source_attempts": list(liveportrait_attempts),
                    "rejected_sources": list(liveportrait_rejections),
                    "original_input_path": str(getattr(canonical_input, "original_input_path", "") or ""),
                    "selected_source_key": str(getattr(canonical_input, "selected_source_key", "") or ""),
                    "normalized_input_path": str(getattr(canonical_input, "normalized_input_path", "") or ""),
                    "normalized_mode": str(getattr(canonical_input, "normalized_mode", "") or ""),
                    "engine_name": str(getattr(canonical_input, "engine_name", "") or ""),
                    "source_kind": str(getattr(canonical_input, "source_kind", "") or ""),
                    "metrics": dict(getattr(canonical_input, "metrics", {}) or {}),
                    "warning": str(getattr(canonical_input, "warning", "") or ""),
                    "ranking": list(getattr(canonical_input, "ranking", []) or []),
                    "handoff": dict(getattr(canonical_input, "handoff", {}) or {}),
                }

        musetalk_handoff_video = liveportrait_output

        def _use_static_handoff(*, reason: str, source_kind: str, stage_name: str) -> None:
            nonlocal liveportrait_fallback_used, liveportrait_fallback_reason, musetalk_handoff_video, musetalk_source_kind
            if canonical_input is None:
                raise RuntimeError(f"{stage_name}_missing_canonical_input")
            if source_kind == "static_fallback":
                liveportrait_fallback_used = True
                liveportrait_fallback_reason = str(reason or "liveportrait_static_fallback")
                stage_paths["liveportrait_fallback_used"] = True
                stage_paths["liveportrait_fallback_reason"] = liveportrait_fallback_reason
            static_source = Path(str(getattr(canonical_input, "normalized_input_path", "") or source_image_primary))
            _build_static_handoff_loop(
                source_image=static_source,
                output_path=musetalk_handoff_output,
                duration_seconds=float(contract_duration_seconds),
                fps=float(stage_env.get("AVATAR_LIVEPORTRAIT_FPS") or stage_env.get("MUSETALK_FPS") or 25.0),
                stage_name=stage_name,
            )
            musetalk_handoff_video = musetalk_handoff_output
            musetalk_source_kind = source_kind
            stage_paths["musetalk_handoff_video_path"] = str(musetalk_handoff_video)
            stage_paths["musetalk_handoff_source"] = "static_image_loop"
            stage_paths["musetalk_source_kind"] = source_kind
            stage_paths["liveportrait_reconciled_output_path"] = str(musetalk_handoff_video)
            stage_paths["liveportrait_fallback_used"] = liveportrait_fallback_used

        if liveportrait_bypassed:
            _use_static_handoff(
                reason=str(stage_paths.get("liveportrait_bypass_reason") or "liveportrait_bypassed"),
                source_kind="static_source",
                stage_name=("musetalk_only_static_loop" if requested_engine == MUSETALK_ONLY_ENGINE else "liveportrait_disabled_static_loop"),
            )
        elif liveportrait_fallback_used:
            _use_static_handoff(
                reason=str(liveportrait_fallback_reason or "liveportrait_failed"),
                source_kind="static_fallback",
                stage_name="liveportrait_fallback_static_loop",
            )
        reconciliation_info: dict[str, Any] = {}
        if is_preview_request:
            try:
                handoff_source_for_reconciliation = (
                    musetalk_handoff_video
                    if (liveportrait_bypassed or liveportrait_fallback_used)
                    else liveportrait_output
                )
                try:
                    reconciliation_info = _reconcile_duration_contract(
                        video_path=str(handoff_source_for_reconciliation),
                        audio_path=str(getattr(request, "audio_path", "") or ""),
                        reconciled_video_path=str(liveportrait_reconciled_output),
                        preview_teacher_id=int(getattr(request, "preview_teacher_id", 0) or 0),
                        preview_job_id=int(getattr(request, "preview_job_id", 0) or 0),
                    )
                except TypeError as type_exc:
                    if "reconciled_video_path" not in str(type_exc):
                        raise
                    reconciliation_info = _reconcile_duration_contract(
                        video_path=str(handoff_source_for_reconciliation),
                        audio_path=str(getattr(request, "audio_path", "") or ""),
                        preview_teacher_id=int(getattr(request, "preview_teacher_id", 0) or 0),
                        preview_job_id=int(getattr(request, "preview_job_id", 0) or 0),
                    )
                contract_duration_seconds = float(reconciliation_info.get("contract_duration_seconds") or contract_duration_seconds)
                stage_env["MUSETALK_TARGET_DURATION_SECONDS"] = f"{float(contract_duration_seconds):.6f}"
                if selected_stage_index >= 0:
                    stage_outputs[selected_stage_index]["reconciliation_info"] = reconciliation_info
                stage_paths["duration_reconciliation_strategy"] = str(reconciliation_info.get("strategy", ""))
                stage_paths["duration_reconciliation_video_changed"] = bool(reconciliation_info.get("video_changed"))
                stage_paths["duration_reconciliation_audio_changed"] = bool(reconciliation_info.get("audio_changed"))
                stage_paths["duration_reconciliation_delta_seconds"] = float(reconciliation_info.get("duration_delta_seconds", 0.0))
                stage_paths["duration_reconciliation_adjustment_seconds"] = float(reconciliation_info.get("adjustment_seconds", 0.0))
                stage_paths["duration_reconciliation_contract_duration_seconds"] = round(float(contract_duration_seconds), 4)
                stage_paths["duration_reconciliation_video_path"] = str(reconciliation_info.get("reconciled_video_path", ""))
                stage_paths["liveportrait_reconciled_output_path"] = str(reconciliation_info.get("reconciled_video_path", "") or liveportrait_output)
                stage_paths["duration_reconciliation_audio_path"] = str(reconciliation_info.get("reconciled_audio_path", ""))
                stage_paths["reconciliation_final_video_duration"] = float(reconciliation_info.get("final_video_duration_seconds", 0.0))
                stage_paths["reconciliation_final_audio_duration"] = float(reconciliation_info.get("final_audio_duration_seconds", 0.0))
                stage_paths["preview_contract_duration_seconds"] = round(float(contract_duration_seconds), 4)

                liveportrait_reconciled_path = Path(str(reconciliation_info.get("reconciled_video_path") or handoff_source_for_reconciliation))
                liveportrait_contract = legacy_pipeline._assert_video_contract(
                    str(liveportrait_reconciled_path),
                    stage_name=(
                        "static_handoff_after_reconciliation"
                        if (liveportrait_bypassed or liveportrait_fallback_used)
                        else "liveportrait_after_reconciliation"
                    ),
                )
                if selected_stage_index >= 0:
                    stage_outputs[selected_stage_index]["duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)
                stage_paths["liveportrait_output_duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)

                logger.info(
                    "Avatar preview duration reconciliation handoff teacher_id=%s job_id=%s strategy=%s video_duration=%s audio_duration=%s delta=%s",
                    int(getattr(request, "preview_teacher_id", 0) or 0),
                    int(getattr(request, "preview_job_id", 0) or 0),
                    str(reconciliation_info.get("strategy", "")),
                    round(float(reconciliation_info.get("final_video_duration_seconds", 0.0)), 4),
                    round(float(reconciliation_info.get("final_audio_duration_seconds", 0.0)), 4),
                    round(abs(float(reconciliation_info.get("final_video_duration_seconds", 0.0)) - float(reconciliation_info.get("final_audio_duration_seconds", 0.0))), 4),
                )

                handoff_video_info = _normalize_preview_video_for_musetalk(
                    video_path=str(liveportrait_reconciled_path),
                    handoff_video_path=str(musetalk_handoff_output),
                    target_frame_count=int(getattr(request, "target_frame_count", 0) or 0),
                    target_duration_seconds=float(contract_duration_seconds),
                    preview_teacher_id=int(getattr(request, "preview_teacher_id", 0) or 0),
                    preview_job_id=int(getattr(request, "preview_job_id", 0) or 0),
                )
                musetalk_handoff_video = Path(str(handoff_video_info.get("video_path", str(liveportrait_reconciled_path))))
                if selected_stage_index >= 0:
                    stage_outputs[selected_stage_index]["musetalk_handoff_video_info"] = handoff_video_info
                stage_paths["musetalk_handoff_video_path"] = str(musetalk_handoff_video)
                stage_paths["musetalk_handoff_frame_normalization_strategy"] = str(handoff_video_info.get("strategy", ""))
                stage_paths["musetalk_handoff_frame_count_before"] = int(handoff_video_info.get("frame_count_before", 0) or 0)
                stage_paths["musetalk_handoff_frame_count_after"] = int(handoff_video_info.get("frame_count_after", 0) or 0)
                stage_paths["musetalk_handoff_video_duration_seconds"] = float(handoff_video_info.get("duration_after_seconds", 0.0) or 0.0)

                liveportrait_contract = legacy_pipeline._assert_video_contract(str(musetalk_handoff_video), stage_name="liveportrait_musetalk_handoff")
                if selected_stage_index >= 0:
                    stage_outputs[selected_stage_index]["duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)
                stage_paths["liveportrait_output_duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)
            except Exception as reconciliation_exc:
                stage_paths["duration_reconciliation_strategy"] = "failed"
                stage_paths["duration_reconciliation_failure_reason"] = str(reconciliation_exc)
                if liveportrait_bypassed or liveportrait_fallback_used:
                    raise RuntimeError(f"preview_duration_reconciliation_failed:{reconciliation_exc}") from reconciliation_exc
                liveportrait_failed = True
                liveportrait_failure_reason = f"handoff_reconciliation_failed:{reconciliation_exc}"
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = liveportrait_failure_reason
                _use_static_handoff(
                    reason=liveportrait_failure_reason,
                    source_kind="static_fallback",
                    stage_name="liveportrait_reconciliation_static_fallback",
                )
        elif should_enforce_exact_duration:
            if not liveportrait_bypassed and not liveportrait_fallback_used:
                shutil.copy2(str(liveportrait_output), str(musetalk_handoff_output))
                musetalk_handoff_video = musetalk_handoff_output
                stage_paths["musetalk_handoff_video_path"] = str(musetalk_handoff_video)
                stage_paths["liveportrait_reconciled_output_path"] = str(liveportrait_output)
            liveportrait_trim_info = legacy_pipeline._trim_video_to_exact_audio_duration(
                video_path=str(musetalk_handoff_video),
                audio_path=str(getattr(request, "audio_path", "") or ""),
            )
            liveportrait_contract = legacy_pipeline._assert_video_contract(
                str(musetalk_handoff_video),
                stage_name=(
                    "static_handoff"
                    if (liveportrait_bypassed or liveportrait_fallback_used)
                    else "liveportrait"
                ),
            )
            if selected_stage_index >= 0:
                stage_outputs[selected_stage_index]["duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)
                stage_outputs[selected_stage_index]["trim_info"] = dict(liveportrait_trim_info or {})
            stage_paths["liveportrait_trim_applied"] = bool(liveportrait_trim_info.get("trimmed"))
            stage_paths["liveportrait_trim_info"] = dict(liveportrait_trim_info or {})
            stage_paths["liveportrait_output_duration_seconds"] = round(float(liveportrait_contract.get("duration_seconds") or 0.0), 4)
            logger.info(
                "Avatar render liveportrait trimmed output_path=%s trim_info=%s",
                str(musetalk_handoff_video),
                dict(liveportrait_trim_info or {}),
            )
        else:
            if not liveportrait_bypassed and not liveportrait_fallback_used and liveportrait_output.exists():
                shutil.copy2(str(liveportrait_output), str(musetalk_handoff_output))
                musetalk_handoff_video = musetalk_handoff_output
                stage_paths["musetalk_handoff_video_path"] = str(musetalk_handoff_video)
            stage_paths["liveportrait_reconciled_output_path"] = str(
                musetalk_handoff_video if (liveportrait_bypassed or liveportrait_fallback_used) else liveportrait_output
            )

        if liveportrait_bypassed or liveportrait_fallback_used:
            stage_paths["liveportrait_handoff_motion_gate"] = {
                "skipped": True,
                "reason": "liveportrait_bypassed" if liveportrait_bypassed else "liveportrait_static_fallback",
            }
        else:
            handoff_motion_gate = _liveportrait_motion_gate(
                str(musetalk_handoff_video),
                is_preview_request=is_preview_request,
                expected_duration_seconds=float(contract_duration_seconds),
                expected_fps=float(stage_env.get("AVATAR_LIVEPORTRAIT_FPS", "0") or 0),
                expected_frame_count=int(getattr(request, "target_frame_count", 0) or 0),
                stage_name="liveportrait_motion_gate_handoff",
            )
            if selected_stage_index >= 0:
                stage_outputs[selected_stage_index]["handoff_motion_gate"] = dict(handoff_motion_gate)
            stage_paths["liveportrait_handoff_motion_gate"] = dict(handoff_motion_gate)
            handoff_technical_valid = bool(
                handoff_motion_gate.get("technical_valid", handoff_motion_gate.get("technical_passed", handoff_motion_gate.get("passed")))
            )
            handoff_motion_passed = bool(handoff_motion_gate.get("motion_passed", handoff_motion_gate.get("passed")))
            stage_paths["liveportrait_technical_valid"] = handoff_technical_valid
            stage_paths["liveportrait_motion_passed"] = handoff_motion_passed
            if not handoff_technical_valid:
                liveportrait_failed = True
                liveportrait_failure_reason = str(
                    handoff_motion_gate.get("technical_failure_reason")
                    or handoff_motion_gate.get("failure_reason")
                    or "liveportrait_handoff_technical_invalid"
                )
                stage_paths["liveportrait_failed"] = True
                stage_paths["liveportrait_failure_reason"] = liveportrait_failure_reason
                _use_static_handoff(
                    reason=liveportrait_failure_reason,
                    source_kind="static_fallback",
                    stage_name="liveportrait_handoff_static_fallback",
                )
            elif not handoff_motion_passed:
                liveportrait_quality_warning = str(
                    handoff_motion_gate.get("motion_failure_reason")
                    or handoff_motion_gate.get("failure_reason")
                    or "liveportrait_low_motion"
                )
                stage_paths["liveportrait_quality_warning"] = liveportrait_quality_warning
                if f"liveportrait_quality_warning:{liveportrait_quality_warning}" not in warning_parts:
                    warning_parts.append(f"liveportrait_quality_warning:{liveportrait_quality_warning}")
                if lp_low_motion_fallback_to_static:
                    _use_static_handoff(
                        reason="low_motion",
                        source_kind="static_fallback",
                        stage_name="liveportrait_low_motion_static_fallback",
                    )

        logger.info(
            "Avatar preview liveportrait output teacher_id=%s job_id=%s canonical_input_path=%s raw_output_path=%s reconciled_output_path=%s handoff_video_path=%s liveportrait_output_duration_seconds=%s tts_audio_duration_seconds=%s",
            int(getattr(request, "preview_teacher_id", 0) or 0),
            int(getattr(request, "preview_job_id", 0) or 0),
            str(getattr(canonical_input, "normalized_input_path", "") or ""),
            str(liveportrait_output),
            str(stage_paths.get("liveportrait_reconciled_output_path") or ""),
            str(musetalk_handoff_video),
            stage_paths.get("liveportrait_output_duration_seconds"),
            stage_paths.get("tts_audio_duration_seconds"),
        )

        cleanup_after_liveportrait = release_stage_resources(reason="after_liveportrait_before_musetalk")
        stage_paths["cleanup_after_liveportrait"] = _cleanup_summary(cleanup_after_liveportrait)
        stage_paths["runtime_resources_before_musetalk"] = probe_runtime_resources()

        current_audio_hash = sha256_file(str(getattr(request, "audio_path", "") or ""))
        if current_audio_hash != expected_audio_hash:
            raise RuntimeError("preview_audio_hash_changed_before_musetalk")

        final_stage_path = musetalk_handoff_video if (liveportrait_bypassed or liveportrait_fallback_used) else liveportrait_output
        engine_used_for_output = MUSETALK_ONLY_ENGINE if requested_engine == MUSETALK_ONLY_ENGINE else CANONICAL_ENGINE
        restoration_warning = ""
        stage_paths["musetalk_stage_state"] = "running"
        stage_paths["musetalk_ran"] = True
        stage_paths["musetalk_started"] = True
        stage_paths["musetalk_timed_out"] = False
        stage_paths["musetalk_fallback_used"] = False

        runtime_resources_before_musetalk = dict(stage_paths.get("runtime_resources_before_musetalk") or {})
        if is_preview_request:
            musetalk_timeout_budget, musetalk_timeout_reason = _preview_musetalk_timeout_profile(
                request,
                resources=runtime_resources_before_musetalk,
                contract_duration_seconds=float(contract_duration_seconds),
            )
        else:
            musetalk_timeout_budget, musetalk_timeout_reason = _lesson_musetalk_timeout_profile(
                request,
                resources=runtime_resources_before_musetalk,
            )
        stage_paths["musetalk_timeout_budget_seconds"] = round(float(musetalk_timeout_budget), 4)
        stage_paths["musetalk_timeout_reason"] = dict(musetalk_timeout_reason or {})
        stage_env["MUSETALK_TOTAL_TIMEOUT_SECONDS"] = f"{float(musetalk_timeout_budget):.6f}"
        stage_env["MUSETALK_CHUNK_TIMEOUT_SECONDS"] = f"{float(musetalk_timeout_reason.get('per_chunk_timeout_seconds') or musetalk_timeout_budget):.6f}"
        stage_env["MUSETALK_IDLE_TIMEOUT_SECONDS"] = f"{float(musetalk_timeout_reason.get('idle_timeout_seconds') or 1200.0):.6f}"
        stage_env["AVATAR_MUSETALK_RUN_ID"] = (
            f"preview-{int(getattr(request, 'preview_teacher_id', 0) or 0)}-"
            f"{int(getattr(request, 'preview_job_id', 0) or 0)}-{int(time.time() * 1000)}"
        )
        _update_preview_task_context(
            request,
            current_stage=("preview_musetalk" if is_preview_request else "musetalk"),
            stage_started_at=time.monotonic(),
            stage_timeout_budget_seconds=round(float(musetalk_timeout_budget), 4),
            musetalk_started=True,
        )

        logger.info(
            "Avatar preview musetalk handoff teacher_id=%s job_id=%s canonical_input_path=%s liveportrait_raw_output_path=%s "
            "liveportrait_reconciled_output_path=%s musetalk_handoff_video_path=%s audio_path=%s contract_duration_seconds=%s "
            "reconciliation_strategy=%s handoff_strategy=%s handoff_frame_count=%s timeout_budget_seconds=%s",
            int(getattr(request, "preview_teacher_id", 0) or 0),
            int(getattr(request, "preview_job_id", 0) or 0),
            str(canonical_input.normalized_input_path),
            str(liveportrait_output),
            str(stage_paths.get("liveportrait_reconciled_output_path") or ""),
            str(musetalk_handoff_video),
            str(getattr(request, "audio_path", "") or ""),
            round(float(contract_duration_seconds), 4),
            str(stage_paths.get("duration_reconciliation_strategy") or "unchanged"),
            str(stage_paths.get("musetalk_handoff_frame_normalization_strategy") or "unchanged"),
            int(stage_paths.get("musetalk_handoff_frame_count_after") or 0),
            round(float(musetalk_timeout_budget), 4),
        )
        logger.info(
            "Avatar preview musetalk timeout_prediction teacher_id=%s job_id=%s total_timeout_seconds=%s "
            "per_chunk_timeout_seconds=%s idle_timeout_seconds=%s reason=%s",
            int(getattr(request, "preview_teacher_id", 0) or 0),
            int(getattr(request, "preview_job_id", 0) or 0),
            round(float(musetalk_timeout_budget), 4),
            stage_env["MUSETALK_CHUNK_TIMEOUT_SECONDS"],
            stage_env["MUSETALK_IDLE_TIMEOUT_SECONDS"],
            json.dumps(musetalk_timeout_reason, ensure_ascii=True, sort_keys=True),
        )

        # ---- Timeout audit log (one authoritative place for all timeout sources) ----
        _svc_floor = float(str(os.environ.get("AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", "420")).strip() or 420.0)
        _effective_svc_timeout = max(musetalk_timeout_budget, _svc_floor)
        logger.info(
            "Avatar preview musetalk timeout_audit "
            "teacher_id=%s job_id=%s "
            "preview_timeout_budget_seconds=%s "
            "service_infer_floor_seconds=%s "
            "effective_svc_timeout_seconds=%s "
            "subprocess_timeout_seconds=%s "
            "timeout_reason=%s "
            "contract_duration_seconds=%s "
            "liveportrait_output_duration_seconds=%s "
            "env_AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS=%r",
            int(getattr(request, "preview_teacher_id", 0) or 0),
            int(getattr(request, "preview_job_id", 0) or 0),
            round(float(musetalk_timeout_budget), 4),
            round(_svc_floor, 1),
            round(_effective_svc_timeout, 1),
            round(float(musetalk_timeout_budget), 4),
            json.dumps(musetalk_timeout_reason, ensure_ascii=True, sort_keys=True),
            round(float(contract_duration_seconds), 4),
            round(float(stage_paths.get("liveportrait_output_duration_seconds") or 0.0), 4),
            os.environ.get("AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", ""),
        )
        stage_paths["musetalk_effective_svc_timeout_seconds"] = round(_effective_svc_timeout, 1)

        musetalk_started_at = time.monotonic()
        stage_paths["musetalk_source_video"] = str(musetalk_handoff_video)
        _update_preview_task_context(
            request,
            current_stage=("preview_musetalk" if is_preview_request else "musetalk"),
            stage_started_at=musetalk_started_at,
            stage_timeout_budget_seconds=round(float(musetalk_timeout_budget), 4),
            musetalk_started=True,
        )
        musetalk_result = run_musetalk(
            source_image=str(canonical_input.normalized_input_path),
            source_video=str(musetalk_handoff_video),
            audio_path=str(getattr(request, "audio_path", "") or ""),
            output_path=str(musetalk_output),
            env_overrides=stage_env,
            timeout_seconds=float(musetalk_timeout_budget),
            stage_name=("preview_musetalk" if is_preview_request else "musetalk"),
        )
        stage_outputs.append(_stage_record("musetalk", musetalk_result, input_path=str(musetalk_handoff_video)))
        musetalk_elapsed_seconds_total = time.monotonic() - musetalk_started_at
        stage_outputs[-1]["elapsed_seconds"] = round(float(musetalk_elapsed_seconds_total), 4)
        musetalk_details = dict(musetalk_result.details or {})
        stage_paths["musetalk_command"] = str(musetalk_result.command or musetalk_details.get("command") or "")
        stage_paths["musetalk_elapsed_seconds"] = float(
            musetalk_details.get("elapsed_seconds")
            or musetalk_elapsed_seconds_total
            or 0.0
        )
        stage_paths["musetalk_cold_start_seconds"] = float(musetalk_details.get("cold_start_seconds") or 0.0)
        stage_paths["musetalk_inference_seconds"] = float(musetalk_details.get("inference_seconds") or musetalk_details.get("elapsed_seconds") or 0.0)
        stage_paths["musetalk_svc_timeout_used_seconds"] = float(musetalk_details.get("svc_timeout_seconds") or 0.0)
        stage_paths["musetalk_route"] = str(musetalk_details.get("route") or "")
        stage_paths["musetalk_route_reason"] = str(musetalk_details.get("route_reason") or "")
        stage_paths["musetalk_chunk_count"] = int(musetalk_details.get("chunk_count") or musetalk_timeout_reason.get("chunk_count") or 1)
        stage_paths["musetalk_chunk_metadata"] = list(musetalk_details.get("chunk_metadata") or [])
        stage_paths["musetalk_final_stitched_output_path"] = str(musetalk_details.get("final_stitched_output_path") or "")
        stage_paths["musetalk_resources_after"] = probe_runtime_resources()
        musetalk_debug_path = musetalk_output.with_suffix(musetalk_output.suffix + ".musetalk_debug.json")
        musetalk_debug_payload = _safe_json_file(musetalk_debug_path)
        musetalk_debug_context = {}
        if musetalk_debug_payload:
            musetalk_debug_context = {
                "debug_path": str(musetalk_debug_path),
                "chunk_count": len(musetalk_debug_payload.get("chunk_ranges") or []),
                "per_chunk_timings": _musetalk_chunk_timings_from_debug(musetalk_debug_payload),
                "inference_total_seconds": float(musetalk_debug_payload.get("inference_total_seconds") or 0.0),
            }
        chunk_timing_metrics = _musetalk_chunk_timing_metrics(
            details=musetalk_details,
            debug_payload=musetalk_debug_payload,
            audio_duration_seconds=float(contract_duration_seconds),
            frame_count=int(getattr(request, "target_frame_count", 0) or 0),
            elapsed_seconds=float(musetalk_elapsed_seconds_total),
        )
        if not musetalk_result.success:
            for metric in chunk_timing_metrics:
                metric["success"] = False
        stage_paths["musetalk_chunk_timing_metrics"] = chunk_timing_metrics
        for metric in chunk_timing_metrics:
            logger.info(
                "Avatar musetalk chunk_timing project_id=%s job_id=%s segment_index=%s "
                "preview_teacher_id=%s preview_job_id=%s chunk_index=%s audio_duration_seconds=%s "
                "frame_count=%s elapsed_seconds=%s route=%s success=%s",
                int(getattr(request, "_project_id", 0) or 0),
                int(getattr(request, "_avatar_job_id", 0) or getattr(request, "preview_job_id", 0) or 0),
                int(getattr(request, "_segment_index", 0) or 0),
                int(getattr(request, "preview_teacher_id", 0) or 0),
                int(getattr(request, "preview_job_id", 0) or 0),
                int(metric.get("chunk_index") or 0),
                round(float(metric.get("audio_duration_seconds") or 0.0), 4),
                int(metric.get("frame_count") or 0),
                round(float(metric.get("elapsed_seconds") or 0.0), 4),
                str(metric.get("route") or stage_paths.get("musetalk_route") or ""),
                bool(metric.get("success", True)),
            )
        record_stage_timing(
            stage_name="musetalk",
            elapsed_seconds=float(musetalk_elapsed_seconds_total),
            success=bool(musetalk_result.success),
            audio_duration_seconds=float(contract_duration_seconds),
            frame_count=int(getattr(request, "target_frame_count", 0) or 0),
            resources=stage_paths.get("musetalk_resources_after"),
            context={
                "is_preview_request": bool(is_preview_request),
                "timeout_seconds": round(float(musetalk_timeout_budget), 4),
                "timeout_reason": dict(musetalk_timeout_reason or {}),
                "chunk_count": int(musetalk_timeout_reason.get("chunk_count") or 1),
                "per_chunk_timeout_seconds": float(musetalk_timeout_reason.get("per_chunk_timeout_seconds") or 0.0),
                "idle_timeout_seconds": float(musetalk_timeout_reason.get("idle_timeout_seconds") or 0.0),
                "musetalk_debug": musetalk_debug_context,
                "chunk_timing_metrics": chunk_timing_metrics,
            },
        )
        stage_paths["musetalk_exit_status"] = (
            "timeout"
            if musetalk_details.get("return_code") is None and str(musetalk_details.get("stderr") or "").lower() == "timeout"
            else str(musetalk_details.get("return_code") if musetalk_details.get("return_code") is not None else "unknown")
        )
        if not musetalk_result.success:
            stage_paths["musetalk_stage_state"] = "failed"
            stage_paths["musetalk_succeeded"] = False
            musetalk_error_text = str(musetalk_result.error or musetalk_details.get("stderr") or "").lower()
            stage_paths["musetalk_timed_out"] = bool(
                (musetalk_details.get("return_code") is None and str(musetalk_details.get("stderr") or "").lower() == "timeout")
                or "musetalk_idle_timeout" in musetalk_error_text
                or "musetalk_chunk_timeout" in musetalk_error_text
                or "musetalk_total_timeout" in musetalk_error_text
                or "preview_musetalk_timeout" in musetalk_error_text
            )
            logger.error(
                "Avatar musetalk failed teacher_id=%s job_id=%s error=%s elapsed_seconds=%s timed_out=%s command=%s",
                int(getattr(request, "preview_teacher_id", 0) or 0),
                int(getattr(request, "preview_job_id", 0) or 0),
                str(musetalk_result.error or "command_failed"),
                round(float(musetalk_details.get("elapsed_seconds") or 0.0), 4),
                bool(stage_paths["musetalk_timed_out"]),
                str(stage_paths.get("musetalk_command") or ""),
            )
            raise RuntimeError(f"musetalk_failed:{musetalk_result.error or 'command_failed'}")
        if musetalk_result.success:
            stage_paths["musetalk_stage_state"] = "completed"
            stage_paths["musetalk_succeeded"] = True
            musetalk_contract = legacy_pipeline._assert_video_contract(str(musetalk_output), stage_name="musetalk")
            stage_outputs[-1]["duration_seconds"] = round(float(musetalk_contract.get("duration_seconds") or 0.0), 4)
            
            # Log successful MuseTalk completion
            logger.info(
                "Avatar preview musetalk_complete job_id=%s musetalk_elapsed_seconds=%s musetalk_output_path=%s preview_status=ready",
                int(getattr(request, "preview_job_id", 0) or 0),
                float(musetalk_details.get("elapsed_seconds") or 0.0),
                str(musetalk_output),
            )

            final_stage_path = musetalk_output
            if restoration_enabled:
                cleanup_after_musetalk = release_stage_resources(reason="after_musetalk_before_restoration")
                stage_paths["cleanup_after_musetalk"] = _cleanup_summary(cleanup_after_musetalk)
                stage_paths["runtime_resources_before_restoration"] = probe_runtime_resources()
                restoration_timeout_seconds, restoration_timeout_reason = _restoration_timeout_profile(
                    request,
                    resources=dict(stage_paths.get("runtime_resources_before_restoration") or {}),
                    contract_duration_seconds=float(contract_duration_seconds),
                )
                stage_paths["restoration_timeout_seconds"] = round(float(restoration_timeout_seconds), 4)
                stage_paths["restoration_timeout_reason"] = dict(restoration_timeout_reason or {})
                restoration_started_at = time.monotonic()
                _update_preview_task_context(
                    request,
                    current_stage="preview_restoration",
                    stage_started_at=restoration_started_at,
                    stage_timeout_budget_seconds=round(float(restoration_timeout_seconds), 4),
                )
                try:
                    restoration_result = run_restoration(
                        input_video=str(musetalk_output),
                        output_path=str(restoration_output),
                        source_image=str(canonical_input.normalized_input_path),
                        audio_path=str(getattr(request, "audio_path", "") or ""),
                        env_overrides=stage_env,
                        timeout_seconds=float(restoration_timeout_seconds),
                    )
                except Exception as restoration_exc:
                    restoration_result = EngineResult(
                        False,
                        "restoration",
                        str(restoration_output),
                        f"exception:{restoration_exc}",
                    )
                restoration_elapsed_seconds = time.monotonic() - restoration_started_at
                stage_outputs.append(_stage_record("restoration", restoration_result, input_path=str(musetalk_output)))
                stage_outputs[-1]["elapsed_seconds"] = round(float(restoration_elapsed_seconds), 4)
                stage_paths["restoration_elapsed_seconds"] = round(float(restoration_elapsed_seconds), 4)
                stage_paths["restoration_resources_after"] = probe_runtime_resources()
                record_stage_timing(
                    stage_name="restoration",
                    elapsed_seconds=float(restoration_elapsed_seconds),
                    success=bool(restoration_result.success),
                    audio_duration_seconds=float(contract_duration_seconds),
                    frame_count=int(getattr(request, "target_frame_count", 0) or 0),
                    resources=stage_paths.get("restoration_resources_after"),
                    context={
                        "timeout_seconds": round(float(restoration_timeout_seconds), 4),
                        "timeout_reason": dict(restoration_timeout_reason or {}),
                    },
                )
                if restoration_result.success and _video_is_playable(restoration_output, stage_name="restoration"):
                    final_stage_path = restoration_output
                    stage_paths["restoration_succeeded"] = True
                    stage_paths["restoration_failed"] = False
                    stage_paths["restoration_failure_reason"] = ""
                else:
                    restoration_warning = f"restoration_failed:{restoration_result.error or 'missing_output'}"
                    stage_paths["restoration_succeeded"] = False
                    stage_paths["restoration_failed"] = True
                    stage_paths["restoration_failure_reason"] = str(restoration_result.error or "missing_output")
                    warning_parts.append(restoration_warning)

        if final_stage_path != output_path:
            shutil.copy2(str(final_stage_path), str(output_path))
        stage_paths["final_playable_path"] = str(output_path)
        stage_paths["ui_returned_playable_file"] = str(final_stage_path)
        stage_paths["preview_file_exists"] = bool(_video_is_playable(final_stage_path, stage_name="ui_returned_playable_file"))
        stage_paths["final_avatar_engine_chain"] = [
            str(record.get("stage") or "")
            for record in stage_outputs
            if str(record.get("stage") or "")
        ]
        logger.info(
            "Avatar preview final playable output teacher_id=%s job_id=%s output_path=%s ui_returned_playable_file=%s",
            int(getattr(request, "preview_teacher_id", 0) or 0),
            int(getattr(request, "preview_job_id", 0) or 0),
            str(output_path),
            str(final_stage_path),
        )
        legacy_pipeline._assert_video_contract(str(output_path), stage_name="final_render")

        if should_enforce_exact_duration and not is_preview_request:
            legacy_pipeline._trim_video_to_exact_audio_duration(
                video_path=str(output_path),
                audio_path=str(getattr(request, "audio_path", "") or ""),
            )

        if is_preview_request:
            final_validation = legacy_pipeline.validate_avatar_render_with_audio(
                str(output_path),
                str(getattr(request, "audio_path", "") or ""),
            )
        else:
            final_validation = legacy_pipeline.validate_avatar_lesson_segment_with_audio(
                str(output_path),
                str(getattr(request, "audio_path", "") or ""),
            )
        strict_pass = (
            legacy_pipeline.accept_avatar_render(final_validation)
            if is_preview_request
            else legacy_pipeline.accept_avatar_lesson_segment_render(final_validation)
        )
        stage_paths["final_validation_classification"] = str(
            final_validation.get("validation_classification")
            or ("passed" if strict_pass else "hard_failure")
        )
        stage_paths["final_validation_warning_only"] = bool(final_validation.get("validation_warning_only"))
        stage_paths["final_validation_warnings"] = list(final_validation.get("validation_warnings") or [])
        stage_paths["final_validation_failure_reason"] = str(final_validation.get("failure_reason") or "")
        if bool(final_validation.get("whole_frame_drift_diagnostic_only")):
            logger.warning(
                "Avatar lesson segment whole_frame_drift diagnostic-only output=%s face_drift_ratio=%s audio_match=%s",
                str(output_path),
                (final_validation.get("quality_checks") or {}).get("face_drift_ratio"),
                final_validation.get("audio_match"),
            )
        stage_paths["final_output_playable_motion"] = bool(
            _video_is_playable(output_path, stage_name="final_render_check")
            and bool(final_validation.get("motion_real"))
            and bool(final_validation.get("animated"))
            and bool(final_validation.get("audio_match"))
            and not bool(final_validation.get("duration_mismatch"))
        )
        stage_paths["preview_usable"] = bool(stage_paths.get("preview_file_exists") and stage_paths.get("final_output_playable_motion"))
        preview_warning = _combined_warning(*warning_parts)
        if not strict_pass:
            debug_path = legacy_pipeline._save_failed_render_debug(
                output_path=output_path,
                request=request,
                metrics=final_validation,
                engine=CANONICAL_ENGINE,
                attempts=[record.get("stage") for record in stage_outputs],
                result_error=str(final_validation.get("failure_reason") or "strict_validation_failed"),
                stage_traces={"paths": stage_paths, "stages": stage_outputs, "canonical_input": canonical_input_payload},
            )
            raise RuntimeError(
                f"strict_validation_failed:{final_validation.get('failure_reason') or 'unknown'}"
                + (f" debug_path={debug_path}" if debug_path else "")
            )
        stage_paths["runtime_resources_end"] = probe_runtime_resources()
        payload = _final_payload(
            request=request,
            requested_engine=requested_engine,
            output_path=output_path,
            validation=final_validation,
            strict_pass=bool(strict_pass),
            preview_warning=preview_warning,
            engine_used=engine_used_for_output,
            stage_paths=stage_paths,
            stage_outputs=stage_outputs,
            canonical_input=canonical_input_payload,
            failure_category=("validation_warning" if preview_warning and not strict_pass else ""),
        )
        _summary_log(str(payload.get("preview_status") or ""), preview_warning or str(final_validation.get("failure_reason") or ""))
        return payload
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        failure_reason = str(exc or "avatar_pipeline_failed")
        stage_paths["runtime_resources_end"] = probe_runtime_resources()
        stage_paths["final_output_playable_motion"] = False
        playable_candidates = [
            ("restoration", restoration_output),
            ("musetalk", musetalk_output),
            ("musetalk_handoff", musetalk_handoff_output),
            ("liveportrait_reconciled", liveportrait_reconciled_output),
            ("liveportrait", liveportrait_output),
        ]
        playable_stage = ""
        playable_path: Path | None = None
        for stage_name, candidate_path in playable_candidates:
            if _video_is_playable(candidate_path, stage_name=stage_name):
                playable_stage = stage_name
                playable_path = candidate_path
                break

        debug_output_path = playable_path or next(
            (
                candidate
                for _, candidate in playable_candidates
                if candidate.exists() and candidate.is_file()
            ),
            output_path,
        )

        if playable_path is not None:
            failure_metrics = _safe_validation(
                str(playable_path),
                str(getattr(request, "audio_path", "") or ""),
                fallback_reason=failure_reason,
            )
        else:
            failure_metrics = {
                "motion_real": False,
                "animated": False,
                "lip_motion_valid": False,
                "eye_motion_valid": False,
                "face_artifacts_detected": False,
                "audio_match": False,
                "frame_count": 0,
                "min_frames": 1,
                "duration_mismatch": True,
                "failure_reason": failure_reason,
                "quality_checks": {},
            }

        debug_path = legacy_pipeline._save_failed_render_debug(
            output_path=debug_output_path,
            request=request,
            metrics=failure_metrics,
            engine=playable_stage or CANONICAL_ENGINE,
            attempts=[record.get("stage") for record in stage_outputs],
            result_error=failure_reason,
            stage_traces={"paths": stage_paths, "stages": stage_outputs, "canonical_input": canonical_input_payload},
        )
        if debug_path:
            failure_reason = f"{failure_reason} debug_path={debug_path}"

        stage_paths["final_playable_path"] = ""

        _summary_log("failed", failure_reason)
        raise RuntimeError(failure_reason) from exc
