from __future__ import annotations

import json
import hashlib
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

from .canonical_adapters import EngineResult
from .hashing import sha256_file
from .preprocess import AvatarValidationError, preprocess_avatar_image

logger = logging.getLogger(__name__)

_LIVEPORTRAIT_HEALTH_CACHE: dict[str, bool] = {}
_COMPOSER_SUBTLE_MOTION_PRESETS = {"natural_conservative", "natural_visible", "subtle_blink", "subtle_gaze"}


@dataclass
class AvatarRenderRequest:
    source_image_path: str
    audio_path: str
    output_path: str
    source_video_path: str = ""
    source_image_original_path: str = ""
    avatar_reference_type: str = "image"
    motion_preset: str = "natural"
    quality_preset: str = "high"
    lipsync_engine: str = "musetalk"
    restoration_enabled: bool | None = None
    liveportrait_enabled: bool | None = None
    cache_text_hash: str = ""
    enforce_exact_audio_duration: bool = False
    musetalk_params: dict[str, Any] = field(default_factory=dict)
    target_frame_count: int = 0
    target_duration_seconds: float = 0.0
    preview_teacher_id: int = 0
    preview_job_id: int = 0
    preview_source_meta: dict[str, Any] = field(default_factory=dict)


def preprocess_teacher_avatar_image(
    *,
    image_bytes: bytes,
    original_filename: str,
    storage_root: str,
    teacher_id: int,
    model_version: str,
):
    return preprocess_avatar_image(
        image_bytes=image_bytes,
        original_filename=original_filename,
        storage_root=storage_root,
        teacher_id=teacher_id,
        model_version=model_version,
    )


def _run_ffmpeg(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")


def _render_ffmpeg_fallback(request: AvatarRenderRequest) -> str:
    raise RuntimeError(
        "ffmpeg image-loop fallback is disabled. Static/drifting avatar output is not allowed."
    )


def _video_frame_count(video_path: str) -> int:
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_read_frames",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    proc = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        return 0
    raw = (proc.stdout or "").strip()
    try:
        return int(float(raw)) if raw else 0
    except Exception:
        return 0


def _probe_media_duration_seconds(path: str, *, stream_selector: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        stream_selector,
        "-show_entries",
        "stream=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode == 0:
        try:
            raw = (proc.stdout or "").strip()
            if raw:
                return max(float(raw), 0.0)
        except Exception:
            pass

    fallback_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    fallback_proc = subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if fallback_proc.returncode != 0:
        return 0.0
    try:
        raw = (fallback_proc.stdout or "").strip()
        return max(float(raw), 0.0) if raw else 0.0
    except Exception:
        return 0.0


def _probe_audio_duration_seconds(audio_path: str) -> float:
    return _probe_media_duration_seconds(audio_path, stream_selector="a:0")


def _probe_video_duration_seconds(video_path: str) -> float:
    return _probe_media_duration_seconds(video_path, stream_selector="v:0")


def _assert_audio_contract(audio_path: str, *, stage_name: str = "audio") -> dict[str, Any]:
    path = Path(str(audio_path or "")).expanduser()
    if not path.exists():
        raise RuntimeError(f"{stage_name}_missing")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"{stage_name}_empty")

    duration = _probe_audio_duration_seconds(str(path))
    if duration <= 0.0:
        raise RuntimeError(f"{stage_name}_not_decodable")
    if duration < 0.25:
        raise RuntimeError(f"{stage_name}_too_short:{duration:.4f}s")

    return {
        "path": str(path),
        "size_bytes": int(path.stat().st_size),
        "duration_seconds": round(float(duration), 4),
    }


def _assert_video_contract(video_path: str, *, stage_name: str = "video") -> dict[str, Any]:
    path = Path(str(video_path or "")).expanduser()
    if not path.exists():
        raise RuntimeError(f"{stage_name}_output_missing")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"{stage_name}_output_empty")

    frame_count = _video_frame_count(str(path))
    if frame_count <= 0:
        raise RuntimeError(f"{stage_name}_output_invalid_frames")

    duration = _probe_video_duration_seconds(str(path))
    if duration <= 0.0:
        raise RuntimeError(f"{stage_name}_output_not_decodable")

    return {
        "path": str(path),
        "size_bytes": int(path.stat().st_size),
        "frame_count": int(frame_count),
        "duration_seconds": round(float(duration), 4),
    }


def _trim_video_to_exact_audio_duration(*, video_path: str, audio_path: str) -> dict[str, Any]:
    video_duration = _probe_video_duration_seconds(video_path)
    audio_duration = _probe_audio_duration_seconds(audio_path)
    if audio_duration <= 0.0:
        raise RuntimeError("Audio duration is invalid; cannot enforce exact preview duration")

    # If render is shorter than audio, fail fast. Stretching/looping is disallowed.
    shortfall = audio_duration - video_duration
    max_shortfall = float(os.environ.get("AVATAR_MAX_SHORTFALL_SECONDS", "0.10"))
    if shortfall > max_shortfall:
        raise RuntimeError(
            "Rendered video is shorter than preview audio and cannot be stretched "
            f"(video={round(video_duration, 4)}s audio={round(audio_duration, 4)}s shortfall={round(shortfall, 4)}s)"
        )

    # If close enough already, keep original to avoid unnecessary re-encode.
    if video_duration <= audio_duration + 0.015:
        return {
            "trimmed": False,
            "audio_duration_seconds": round(audio_duration, 4),
            "video_duration_before_seconds": round(video_duration, 4),
            "video_duration_after_seconds": round(video_duration, 4),
            "duration_delta_seconds": round(abs(video_duration - audio_duration), 4),
        }

    source = Path(video_path)
    tmp = source.with_suffix(source.suffix + ".trim.mp4")
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vsync",
            "0",
            "-fps_mode:v",
            "passthrough",
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
    )
    if not tmp.exists() or tmp.stat().st_size <= 0:
        raise RuntimeError("Failed to trim avatar render to exact preview audio duration")
    shutil.move(str(tmp), str(source))
    after_duration = _probe_video_duration_seconds(video_path)
    return {
        "trimmed": True,
        "audio_duration_seconds": round(audio_duration, 4),
        "video_duration_before_seconds": round(video_duration, 4),
        "video_duration_after_seconds": round(after_duration, 4),
        "duration_delta_seconds": round(abs(after_duration - audio_duration), 4),
    }


def _write_contact_sheet(images: list[Any], out_path: Path, *, cols: int = 4) -> None:
    if cv2 is None or not images:
        return
    import numpy as np

    h, w = images[0].shape[:2]
    rows = (len(images) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), dtype=np.uint8)
    for idx, img in enumerate(images):
        r = idx // cols
        c = idx % cols
        y0 = r * h
        y1 = y0 + h
        x0 = c * w
        x1 = x0 + w
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        sheet[y0:y1, x0:x1] = img
    cv2.imwrite(str(out_path), sheet)


def _analyze_frame_sequence(
    *,
    video_path: str,
    export_dir: Path | None = None,
    export_limit: int = 32,
) -> dict[str, Any]:
    if cv2 is None:
        return {
            "frame_trace_available": False,
            "frame_trace_reason": "opencv_unavailable",
        }

    import numpy as np

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {
            "frame_trace_available": False,
            "frame_trace_reason": "video_open_failed",
        }

    face_detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    hashes: list[str] = []
    sampled_images: list[Any] = []
    frame_diffs: list[float] = []
    mouth_diffs: list[float] = []
    eye_diffs: list[float] = []
    face_diffs: list[float] = []
    repeated_pairs = 0
    last_frame_hold = 0
    prev_hash = ""
    prev_gray = None
    prev_mouth = None
    prev_eye = None
    prev_face = None
    first_small = None
    last_small = None
    frame_idx = 0

    if export_dir is not None:
        export_dir.mkdir(parents=True, exist_ok=True)

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break

        digest = hashlib.sha256(frame.tobytes()).hexdigest()[:16]
        hashes.append(digest)
        if prev_hash and prev_hash == digest:
            repeated_pairs += 1
            if frame_idx == len(hashes) - 1:
                last_frame_hold += 1
        prev_hash = digest

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
        if first_small is None:
            first_small = small
        last_small = small
        if prev_gray is not None:
            frame_diffs.append(float(np.mean(np.abs(gray.astype("float32") - prev_gray.astype("float32")))))
        prev_gray = gray

        faces = face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(120, 120))
        if faces is not None and len(faces) > 0:
            x, y, w, h = [int(v) for v in max(faces, key=lambda f: int(f[2]) * int(f[3]))]
            face_roi = gray[max(0, y):min(gray.shape[0], y + h), max(0, x):min(gray.shape[1], x + w)]
            eye_roi = gray[
                max(0, y + int(0.20 * h)):min(gray.shape[0], y + int(0.42 * h)),
                max(0, x + int(0.18 * w)):min(gray.shape[1], x + int(0.82 * w)),
            ]
            mouth_roi = gray[
                max(0, y + int(0.62 * h)):min(gray.shape[0], y + int(0.92 * h)),
                max(0, x + int(0.2 * w)):min(gray.shape[1], x + int(0.8 * w)),
            ]
            if face_roi.size > 0:
                face_roi = cv2.resize(face_roi, (96, 96), interpolation=cv2.INTER_AREA)
                if prev_face is not None:
                    face_diffs.append(float(np.mean(np.abs(face_roi.astype("float32") - prev_face.astype("float32")))))
                prev_face = face_roi
            if mouth_roi.size > 0:
                mouth_roi = cv2.resize(mouth_roi, (84, 48), interpolation=cv2.INTER_AREA)
                if prev_mouth is not None:
                    mouth_diffs.append(float(np.mean(np.abs(mouth_roi.astype("float32") - prev_mouth.astype("float32")))))
                prev_mouth = mouth_roi
            if eye_roi.size > 0:
                eye_roi = cv2.resize(eye_roi, (84, 34), interpolation=cv2.INTER_AREA)
                if prev_eye is not None:
                    eye_diffs.append(float(np.mean(np.abs(eye_roi.astype("float32") - prev_eye.astype("float32")))))
                prev_eye = eye_roi

        if len(sampled_images) < export_limit:
            sampled_images.append(frame.copy())
        if export_dir is not None and frame_idx < export_limit:
            cv2.imwrite(str(export_dir / f"frame_{frame_idx:04d}.jpg"), frame)
        frame_idx += 1

    cap.release()

    unique_count = len(set(hashes))
    start_end_frame_diff = 0.0
    if first_small is not None and last_small is not None:
        start_end_frame_diff = float(np.mean(np.abs(first_small.astype("float32") - last_small.astype("float32"))))
    max_loop_similarity = float(os.environ.get("AVATAR_MAX_LOOP_START_END_DIFF", "1.1"))
    semantic_loop_similarity = bool(len(hashes) >= 16 and start_end_frame_diff <= max_loop_similarity)
    repeated_block = False
    max_block = min(24, len(hashes) // 2)
    for block_len in range(3, max_block + 1):
        if hashes[-block_len:] == hashes[-2 * block_len:-block_len]:
            repeated_block = True
            break

    if export_dir is not None and sampled_images:
        _write_contact_sheet(sampled_images[:16], export_dir / "contact_sheet.jpg", cols=4)
        diff_report = {
            "frame_diff": {
                "count": len(frame_diffs),
                "mean": round(float(sum(frame_diffs) / len(frame_diffs)), 6) if frame_diffs else 0.0,
                "min": round(float(min(frame_diffs)), 6) if frame_diffs else 0.0,
                "max": round(float(max(frame_diffs)), 6) if frame_diffs else 0.0,
            },
            "mouth_diff": {
                "count": len(mouth_diffs),
                "mean": round(float(sum(mouth_diffs) / len(mouth_diffs)), 6) if mouth_diffs else 0.0,
                "min": round(float(min(mouth_diffs)), 6) if mouth_diffs else 0.0,
                "max": round(float(max(mouth_diffs)), 6) if mouth_diffs else 0.0,
            },
            "eye_diff": {
                "count": len(eye_diffs),
                "mean": round(float(sum(eye_diffs) / len(eye_diffs)), 6) if eye_diffs else 0.0,
                "min": round(float(min(eye_diffs)), 6) if eye_diffs else 0.0,
                "max": round(float(max(eye_diffs)), 6) if eye_diffs else 0.0,
            },
            "face_diff": {
                "count": len(face_diffs),
                "mean": round(float(sum(face_diffs) / len(face_diffs)), 6) if face_diffs else 0.0,
                "min": round(float(min(face_diffs)), 6) if face_diffs else 0.0,
                "max": round(float(max(face_diffs)), 6) if face_diffs else 0.0,
            },
        }
        (export_dir / "frame_diff_report.json").write_text(json.dumps(diff_report, ensure_ascii=True, indent=2), encoding="utf-8")

    return {
        "frame_trace_available": True,
        "frame_count": int(len(hashes)),
        "unique_frames": int(unique_count),
        "first_frame_hashes": hashes[:5],
        "last_frame_hashes": hashes[-5:] if hashes else [],
        "frame_diff_mean": round(float(sum(frame_diffs) / len(frame_diffs)), 6) if frame_diffs else 0.0,
        "frame_diff_min": round(float(min(frame_diffs)), 6) if frame_diffs else 0.0,
        "frame_diff_max": round(float(max(frame_diffs)), 6) if frame_diffs else 0.0,
        "start_end_frame_diff": round(start_end_frame_diff, 6),
        "semantic_loop_similarity": semantic_loop_similarity,
        "mouth_diff_mean": round(float(sum(mouth_diffs) / len(mouth_diffs)), 6) if mouth_diffs else 0.0,
        "eye_diff_mean": round(float(sum(eye_diffs) / len(eye_diffs)), 6) if eye_diffs else 0.0,
        "face_diff_mean": round(float(sum(face_diffs) / len(face_diffs)), 6) if face_diffs else 0.0,
        "repeated_adjacent_pairs": int(repeated_pairs),
        "repeated_block_detected": bool(repeated_block),
        "last_frame_hold_count": int(last_frame_hold),
        "loop_like_frame_repetition": bool(repeated_block or repeated_pairs > max(2, len(hashes) // 20)),
    }


def _animation_score(video_path: str, max_samples: int = 48) -> float:
    if cv2 is None:
        return 10.0

    import numpy as np

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return 0.0

    sampled: list[Any] = []
    frame_idx = 0
    stride = 2
    while len(sampled) < max_samples:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_idx % stride == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            resized = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
            sampled.append(resized)
        frame_idx += 1
    capture.release()

    if len(sampled) < 2:
        return 0.0

    diffs = []
    for idx in range(1, len(sampled)):
        prev = sampled[idx - 1].astype("float32")
        curr = sampled[idx].astype("float32")
        diffs.append(float(np.mean(np.abs(curr - prev))))
    if not diffs:
        return 0.0
    return float(sum(diffs) / len(diffs))



def _face_centric_animation_score(
    *,
    global_frame_diff_mean: float,
    quality: dict[str, Any],
    min_lip: float,
    min_eye: float,
) -> float:
    lip = float(quality.get("lip_movement_score") or 0.0)
    eye = float(quality.get("eye_movement_score") or 0.0)
    mouth_open = float(quality.get("mouth_openness_change") or 0.0)
    eye_blink = float(quality.get("eye_blink_change") or 0.0)
    head_motion = float(quality.get("head_motion_score") or 0.0)
    start_end = float(quality.get("start_end_frame_diff") or 0.0)

    min_mouth_open = float(quality.get("min_mouth_open_change") or 0.0035)
    min_eye_blink = float(quality.get("min_eye_blink_change") or 0.0025)
    max_loop_similarity = float(quality.get("max_loop_similarity") or 1.0)

    global_ref = float(os.environ.get("AVATAR_ANIMATION_GLOBAL_SIGNAL_REF", "2.2"))
    head_motion_ref = float(os.environ.get("AVATAR_ANIMATION_HEAD_MOTION_REF", "0.006"))

    global_norm = min(global_frame_diff_mean / max(global_ref, 1e-6), 2.0)
    lip_norm = min(lip / max(min_lip, 1e-6), 3.0)
    mouth_norm = min(mouth_open / max(min_mouth_open, 1e-6), 3.0)
    eye_norm = min(eye / max(min_eye, 1e-6), 3.0)
    blink_norm = min(eye_blink / max(min_eye_blink, 1e-6), 3.0)
    head_norm = min(head_motion / max(head_motion_ref, 1e-6), 2.0)

    strict_weights = {
        "global_frame_diff_mean": 0.12,
        "lip_motion": 0.78,
        "mouth_openness_change": 0.56,
        "eye_motion": 0.24,
        "eye_blink_change": 0.18,
        "head_motion": 0.16,
        "start_end_similarity_penalty": 0.12,
        "artifact_penalty": 0.20,
    }

    start_end_norm = min(start_end / max(max_loop_similarity, 1e-6), 2.0)
    artifact_flags = sum(
        1
        for flag in [
            bool(quality.get("loop_detected")),
            bool(quality.get("drift_detected")),
            bool(quality.get("glitch_detected")),
            bool(quality.get("face_artifact_detected")),
        ]
        if flag
    )

    score = (
        global_norm * strict_weights["global_frame_diff_mean"]
        + lip_norm * strict_weights["lip_motion"]
        + mouth_norm * strict_weights["mouth_openness_change"]
        + eye_norm * strict_weights["eye_motion"]
        + blink_norm * strict_weights["eye_blink_change"]
        + head_norm * strict_weights["head_motion"]
        - max(0.0, 1.0 - start_end_norm) * strict_weights["start_end_similarity_penalty"]
        - (artifact_flags * strict_weights["artifact_penalty"])
    )

    return max(float(score), 0.0)


def _truthy(value: Any) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_composer_subtle_validation_context(
    *,
    validation_context: dict[str, Any],
    quality: dict[str, Any],
    min_eye: float,
) -> bool:
    if str(validation_context.get("liveportrait_driver_source") or "").strip().lower() != "composer":
        return False
    if str(validation_context.get("liveportrait_motion_preset") or "").strip().lower() not in _COMPOSER_SUBTLE_MOTION_PRESETS:
        return False
    if not _truthy(validation_context.get("liveportrait_succeeded")):
        return False
    if str(validation_context.get("musetalk_source_kind") or "").strip().lower() != "liveportrait":
        return False
    if _truthy(validation_context.get("liveportrait_fallback_used")):
        return False
    if _truthy(validation_context.get("whole_frame_drift")) or bool(quality.get("drift_detected")):
        return False
    if bool(quality.get("glitch_detected")) or bool(quality.get("structural_face_artifact_detected")):
        return False
    strong_eye_multiplier = float(os.environ.get("AVATAR_COMPOSER_EYE_STRONG_MARGIN_MULTIPLIER", "4.0"))
    strong_eye_threshold = max(float(min_eye) * strong_eye_multiplier, float(min_eye) + 0.5)
    return float(quality.get("eye_movement_score") or 0.0) >= strong_eye_threshold


def _is_calm_template_subtle_validation_context(
    *,
    validation_context: dict[str, Any],
    quality: dict[str, Any],
    min_eye: float,
) -> bool:
    if not _truthy(validation_context.get("liveportrait_calm_template_used")):
        return False
    if _truthy(validation_context.get("liveportrait_vetted_template_fallback_used")):
        return False
    if _truthy(validation_context.get("liveportrait_composer_used")):
        return False
    if _truthy(validation_context.get("liveportrait_composer_fallback_used")):
        return False
    if not _truthy(validation_context.get("liveportrait_succeeded")):
        return False
    if _truthy(validation_context.get("liveportrait_fallback_used")):
        return False
    if str(validation_context.get("musetalk_source_kind") or "").strip().lower() != "liveportrait":
        return False
    if _truthy(validation_context.get("whole_frame_drift")) or bool(quality.get("drift_detected")):
        return False
    if (
        bool(quality.get("glitch_detected"))
        or bool(quality.get("face_artifact_detected"))
        or bool(quality.get("structural_face_artifact_detected"))
        or bool(quality.get("face_warp_detected"))
        or not bool(quality.get("landmark_stable", True))
    ):
        return False
    strong_eye_multiplier = float(os.environ.get("AVATAR_CALM_TEMPLATE_EYE_STRONG_MARGIN_MULTIPLIER", "4.0"))
    strong_eye_threshold = max(float(min_eye) * strong_eye_multiplier, float(min_eye) + 0.5)
    return float(quality.get("eye_movement_score") or 0.0) >= strong_eye_threshold


def _apply_avatar_validation_profile(
    quality: dict[str, Any],
    *,
    min_eye: float,
    validation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adjusted = dict(quality or {})
    context = dict(validation_context or {})
    strict_blink_threshold = float(adjusted.get("min_eye_blink_change") or os.environ.get("AVATAR_MIN_EYE_BLINK_CHANGE", "0.0025"))
    composer_blink_threshold = float(os.environ.get("AVATAR_MIN_EYE_BLINK_CHANGE_COMPOSER", "0.0015"))
    calm_template_blink_threshold = float(
        os.environ.get(
            "AVATAR_MIN_EYE_BLINK_CHANGE_CALM_TEMPLATE",
            os.environ.get("AVATAR_MIN_EYE_BLINK_CHANGE_COMPOSER", "0.0015"),
        )
    )
    profile = "strict"
    threshold_used = strict_blink_threshold
    if _is_composer_subtle_validation_context(validation_context=context, quality=adjusted, min_eye=min_eye):
        profile = "composer_subtle_motion"
        threshold_used = min(strict_blink_threshold, composer_blink_threshold)
    elif _is_calm_template_subtle_validation_context(validation_context=context, quality=adjusted, min_eye=min_eye):
        profile = "calm_template_subtle_motion"
        threshold_used = min(strict_blink_threshold, calm_template_blink_threshold)

    blink = float(adjusted.get("eye_blink_change") or 0.0)
    mouth_open = float(adjusted.get("mouth_openness_change") or 0.0)
    min_mouth_open = float(adjusted.get("min_mouth_open_change") or 0.0035)
    low_blink_under_strict = blink < strict_blink_threshold
    low_blink_under_used = blink < threshold_used
    low_mouth = mouth_open < min_mouth_open
    structural_artifact = bool(adjusted.get("structural_face_artifact_detected"))

    adjusted["avatar_validation_profile"] = profile
    adjusted["min_eye_blink_change_strict"] = round(strict_blink_threshold, 6)
    adjusted["min_eye_blink_change"] = round(threshold_used, 6)
    adjusted["eye_blink_threshold_used"] = round(threshold_used, 6)
    adjusted["low_eye_blink_change"] = bool(low_blink_under_used)
    adjusted["low_eye_blink_change_warning"] = bool(
        profile in {"composer_subtle_motion", "calm_template_subtle_motion"}
        and low_blink_under_strict
        and not low_blink_under_used
    )
    adjusted["low_mouth_openness_change"] = bool(low_mouth)
    adjusted["face_artifact_detected"] = bool(structural_artifact)
    adjusted["face_roi_artifact_source"] = (
        "actual_roi"
        if structural_artifact
        else ("cascade_removed" if low_blink_under_strict or low_mouth else "none")
    )
    return adjusted


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _visual_quality_targets() -> dict[str, float]:
    return {
        "eye_blink_change": _safe_float(os.environ.get("AVATAR_VISUAL_EYE_BLINK_TARGET"), 0.0023),
        "head_motion_score": _safe_float(os.environ.get("AVATAR_VISUAL_HEAD_MOTION_TARGET"), 0.0045),
        "global_frame_diff_mean": _safe_float(os.environ.get("AVATAR_VISUAL_GLOBAL_FRAME_DIFF_TARGET"), 0.35),
        "eye_movement_score": _safe_float(os.environ.get("AVATAR_VISUAL_EYE_MOVEMENT_TARGET"), 3.0),
        "visual_motion_score": _safe_float(os.environ.get("AVATAR_VISUAL_MOTION_SCORE_TARGET"), 1.0),
        "driver_mean_mad": _safe_float(os.environ.get("AVATAR_VISUAL_DRIVER_MEAN_MAD_TARGET"), 0.001),
    }


def _quality_checks_from_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    quality = metrics.get("quality_checks")
    return dict(quality) if isinstance(quality, dict) else {}


def _avatar_visual_motion_score(metrics: dict[str, Any] | None) -> float:
    quality = _quality_checks_from_metrics(metrics)
    targets = _visual_quality_targets()

    def ratio(value: float, target: float, cap: float = 1.5) -> float:
        return min(max(float(value) / max(float(target), 1e-9), 0.0), float(cap))

    score = (
        ratio(_safe_float(quality.get("eye_blink_change")), targets["eye_blink_change"]) * 0.42
        + ratio(_safe_float(quality.get("head_motion_score")), targets["head_motion_score"]) * 0.25
        + ratio(_safe_float((metrics or {}).get("global_frame_diff_mean")), targets["global_frame_diff_mean"], cap=1.2) * 0.20
        + ratio(_safe_float(quality.get("eye_movement_score")), targets["eye_movement_score"]) * 0.13
    )
    return round(float(score), 6)


def _avatar_stage_quality_entry(stage: str, metrics: dict[str, Any] | None) -> dict[str, Any]:
    metric_payload = dict(metrics or {})
    quality = _quality_checks_from_metrics(metric_payload)
    failure_reason = str(metric_payload.get("failure_reason") or "")
    drift = bool(quality.get("drift_detected")) or "whole_frame_drift" in failure_reason
    artifact = bool(
        quality.get("face_artifact_detected")
        or quality.get("structural_face_artifact_detected")
        or quality.get("mouth_artifact_detected")
        or quality.get("eye_artifact_detected")
        or quality.get("face_warp_detected")
        or "face_roi_artifact" in failure_reason
    )
    return {
        "stage": str(stage),
        "path": str(metric_payload.get("path") or ""),
        "frame_count": int(metric_payload.get("frame_count") or 0),
        "animated": bool(metric_payload.get("animated")),
        "failure_reason": failure_reason,
        "visual_motion_score": _avatar_visual_motion_score(metric_payload),
        "eye_blink_change": round(_safe_float(quality.get("eye_blink_change")), 6),
        "eye_movement_score": round(_safe_float(quality.get("eye_movement_score")), 6),
        "lip_movement_score": round(_safe_float(quality.get("lip_movement_score")), 6),
        "mouth_openness_change": round(_safe_float(quality.get("mouth_openness_change")), 6),
        "head_motion_score": round(_safe_float(quality.get("head_motion_score")), 6),
        "global_frame_diff_mean": round(_safe_float(metric_payload.get("global_frame_diff_mean")), 6),
        "whole_frame_drift": bool(drift),
        "face_roi_artifact": bool(artifact),
    }


def evaluate_avatar_visual_quality(
    validation: dict[str, Any],
    *,
    validation_context: dict[str, Any] | None = None,
    stage_metrics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Warning-only production visual target layer; hard validation remains separate."""
    context = dict(validation_context or {})
    stages = {
        str(name): _avatar_stage_quality_entry(str(name), metrics)
        for name, metrics in dict(stage_metrics or {}).items()
    }
    final_entry = _avatar_stage_quality_entry("final", validation)
    stages.setdefault("final", final_entry)
    targets = _visual_quality_targets()
    quality = _quality_checks_from_metrics(validation)
    if not quality:
        return {
            "avatar_quality_profile": "strict_motion_quality",
            "avatar_quality_warning": "",
            "avatar_visual_motion_score": 0.0,
            "avatar_visual_motion_target_met": False,
            "avatar_visual_motion_targets": {key: round(float(value), 6) for key, value in targets.items()},
            "avatar_stage_quality_summary": stages,
            "lp_visual_motion_score": round(float(stages.get("liveportrait", {}).get("visual_motion_score") or 0.0), 6),
            "mt_visual_motion_score": round(float(stages.get("musetalk", {}).get("visual_motion_score") or 0.0), 6),
            "restored_visual_motion_score": round(float(stages.get("restored", stages.get("final", {})).get("visual_motion_score") or 0.0), 6),
            "motion_loss_stage": "none",
        }

    composer_liveportrait = bool(
        str(context.get("liveportrait_driver_source") or "").strip().lower() == "composer"
        and str(context.get("musetalk_source_kind") or "").strip().lower() == "liveportrait"
        and _truthy(context.get("liveportrait_succeeded"))
        and not _truthy(context.get("liveportrait_fallback_used"))
    )
    static_fallback = bool(
        str(context.get("musetalk_source_kind") or "").strip().lower() == "static_fallback"
        or _truthy(context.get("liveportrait_fallback_used"))
    )
    hard_artifact = bool(final_entry.get("whole_frame_drift") or final_entry.get("face_roi_artifact"))
    hard_validation_passed = bool(
        validation.get("motion_real")
        and validation.get("animated")
        and not str(validation.get("failure_reason") or "").strip()
    )

    score = float(final_entry.get("visual_motion_score") or 0.0)
    blink = _safe_float(quality.get("eye_blink_change"))
    head = _safe_float(quality.get("head_motion_score"))
    profile = "composer_visible_motion" if composer_liveportrait else "strict_motion_quality"

    warning_reasons: list[str] = []
    if static_fallback:
        warning_reasons.append("static_fallback_not_production_quality")
    if not hard_artifact:
        if score < targets["visual_motion_score"]:
            warning_reasons.append("low_visible_motion")
        if blink < targets["eye_blink_change"]:
            warning_reasons.append("low_visible_blink")
        if head < targets["head_motion_score"]:
            warning_reasons.append("low_head_motion")

    target_met = bool(
        hard_validation_passed
        and not static_fallback
        and not hard_artifact
        and score >= targets["visual_motion_score"]
        and blink >= targets["eye_blink_change"]
        and head >= targets["head_motion_score"]
    )
    if target_met:
        warning_reasons = []

    stage_scores = {name: float(entry.get("visual_motion_score") or 0.0) for name, entry in stages.items()}
    lp_score = float(stage_scores.get("liveportrait") or stage_scores.get("lp") or 0.0)
    mt_score = float(stage_scores.get("musetalk") or stage_scores.get("mt") or 0.0)
    restored_score = float(stage_scores.get("restored") or stage_scores.get("final") or 0.0)
    driver_mean_mad = _safe_float(context.get("liveportrait_driver_mean_mad"))
    motion_loss_stage = "none"
    if static_fallback:
        motion_loss_stage = "liveportrait"
    elif not target_met:
        if composer_liveportrait and lp_score > 0.0 and lp_score < targets["visual_motion_score"] and 0.0 < driver_mean_mad < targets["driver_mean_mad"]:
            motion_loss_stage = "composer"
        elif lp_score > 0.0 and lp_score < targets["visual_motion_score"]:
            motion_loss_stage = "liveportrait"
        elif lp_score > 0.0 and mt_score > 0.0 and mt_score < max(lp_score * 0.75, targets["visual_motion_score"]):
            motion_loss_stage = "musetalk"
        elif mt_score > 0.0 and restored_score > 0.0 and restored_score < mt_score * 0.75:
            motion_loss_stage = "restoration"

    warning = ",".join(dict.fromkeys(warning_reasons)) if warning_reasons else ""
    return {
        "avatar_quality_profile": profile,
        "avatar_quality_warning": warning if hard_validation_passed or static_fallback else "",
        "avatar_visual_motion_score": round(float(score), 6),
        "avatar_visual_motion_target_met": bool(target_met),
        "avatar_visual_motion_targets": {key: round(float(value), 6) for key, value in targets.items()},
        "avatar_stage_quality_summary": stages,
        "lp_visual_motion_score": round(float(lp_score), 6),
        "mt_visual_motion_score": round(float(mt_score), 6),
        "restored_visual_motion_score": round(float(restored_score), 6),
        "motion_loss_stage": motion_loss_stage,
    }


def _analyze_avatar_motion_quality(video_path: str, max_samples: int = 64) -> dict[str, Any]:
    if cv2 is None:
        return {
            "frames_sampled": 0,
            "unique_frames": 0,
            "face_detection_frames": 0,
            "mouth_roi_frames": 0,
            "eye_roi_frames": 0,
            "landmark_valid_frames": 0,
            "lip_movement_score": 0.0,
            "eye_movement_score": 0.0,
            "mouth_openness_change": 0.0,
            "eye_blink_change": 0.0,
            "face_drift_ratio": 1.0,
            "glitch_score": 1.0,
            "mouth_artifact_score": 1.0,
            "eye_artifact_score": 1.0,
            "landmark_jitter": 1.0,
            "face_warp_score": 1.0,
            "head_motion_score": 0.0,
            "drift_detected": True,
            "glitch_detected": True,
            "mouth_artifact_detected": True,
            "eye_artifact_detected": True,
            "landmark_stable": False,
            "face_warp_detected": True,
            "face_artifact_detected": True,
            "structural_face_artifact_detected": True,
            "face_roi_artifact_source": "actual_roi",
        }

    import numpy as np

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return {
            "frames_sampled": 0,
            "unique_frames": 0,
            "face_detection_frames": 0,
            "mouth_roi_frames": 0,
            "eye_roi_frames": 0,
            "landmark_valid_frames": 0,
            "lip_movement_score": 0.0,
            "eye_movement_score": 0.0,
            "mouth_openness_change": 0.0,
            "eye_blink_change": 0.0,
            "face_drift_ratio": 1.0,
            "glitch_score": 1.0,
            "mouth_artifact_score": 1.0,
            "eye_artifact_score": 1.0,
            "landmark_jitter": 1.0,
            "face_warp_score": 1.0,
            "head_motion_score": 0.0,
            "drift_detected": True,
            "glitch_detected": True,
            "mouth_artifact_detected": True,
            "eye_artifact_detected": True,
            "landmark_stable": False,
            "face_warp_detected": True,
            "face_artifact_detected": True,
            "structural_face_artifact_detected": True,
            "face_roi_artifact_source": "actual_roi",
        }

    face_detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    sampled_gray: list[Any] = []
    sampled_hashes: list[str] = []
    lip_deltas: list[float] = []
    eye_deltas: list[float] = []
    face_centers: list[tuple[float, float]] = []
    face_scales: list[float] = []
    face_ratios: list[float] = []
    mouth_open_values: list[float] = []
    eye_open_values: list[float] = []

    prev_gray = None
    prev_lip = None
    prev_eye = None
    sampled = 0
    frame_idx = 0
    stride = 2

    while sampled < max_samples:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_idx % stride != 0:
            frame_idx += 1
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sampled_small = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
        sampled_gray.append(sampled_small)
        sampled_hashes.append(hashlib.sha256(sampled_small.tobytes()).hexdigest()[:16])

        faces = face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(120, 120))
        if faces is not None and len(faces) > 0:
            x, y, w, h = [int(v) for v in max(faces, key=lambda f: int(f[2]) * int(f[3]))]
            cx = (x + (w / 2.0)) / float(max(gray.shape[1], 1))
            cy = (y + (h / 2.0)) / float(max(gray.shape[0], 1))
            face_centers.append((cx, cy))
            face_scales.append((w * h) / float(max(gray.shape[0] * gray.shape[1], 1)))
            face_ratios.append(w / float(max(h, 1)))

            eye_y0 = y + int(0.20 * h)
            eye_y1 = y + int(0.42 * h)
            eye_x0 = x + int(0.18 * w)
            eye_x1 = x + int(0.82 * w)
            mouth_y0 = y + int(0.62 * h)
            mouth_y1 = y + int(0.92 * h)
            mouth_x0 = x + int(0.20 * w)
            mouth_x1 = x + int(0.80 * w)

            eye_roi = gray[max(0, eye_y0):min(gray.shape[0], eye_y1), max(0, eye_x0):min(gray.shape[1], eye_x1)]
            mouth_roi = gray[max(0, mouth_y0):min(gray.shape[0], mouth_y1), max(0, mouth_x0):min(gray.shape[1], mouth_x1)]

            if eye_roi.size > 0:
                eye_roi = cv2.resize(eye_roi, (84, 34), interpolation=cv2.INTER_AREA)
                eye_open_values.append(float(np.mean(eye_roi.astype("float32")) / 255.0))
                if prev_eye is not None:
                    eye_deltas.append(float(np.mean(np.abs(eye_roi.astype("float32") - prev_eye.astype("float32")))))
                prev_eye = eye_roi

            if mouth_roi.size > 0:
                mouth_roi = cv2.resize(mouth_roi, (84, 48), interpolation=cv2.INTER_AREA)
                mouth_open_values.append(float(np.std(mouth_roi.astype("float32")) / 255.0))
                if prev_lip is not None:
                    lip_deltas.append(float(np.mean(np.abs(mouth_roi.astype("float32") - prev_lip.astype("float32")))))
                prev_lip = mouth_roi

        if prev_gray is not None:
            pass
        prev_gray = gray
        sampled += 1
        frame_idx += 1

    capture.release()

    unique_frames = 0
    first_last_diff = 0.0
    if len(sampled_gray) >= 2:
        for idx in range(1, len(sampled_gray)):
            prev = sampled_gray[idx - 1].astype("float32")
            curr = sampled_gray[idx].astype("float32")
            if float(np.mean(np.abs(curr - prev))) > 0.12:
                unique_frames += 1
        first_last_diff = float(
            np.mean(
                np.abs(
                    sampled_gray[0].astype("float32") - sampled_gray[-1].astype("float32")
                )
            )
        )

    repeated_adjacent_pairs = 0
    if len(sampled_hashes) >= 2:
        repeated_adjacent_pairs = sum(
            1
            for idx in range(1, len(sampled_hashes))
            if sampled_hashes[idx] == sampled_hashes[idx - 1]
        )

    repeated_block_detected = False
    max_block = min(12, len(sampled_hashes) // 2)
    for block_len in range(3, max_block + 1):
        if sampled_hashes[-block_len:] == sampled_hashes[-2 * block_len:-block_len]:
            repeated_block_detected = True
            break

    lip_score = float(sum(lip_deltas) / len(lip_deltas)) if lip_deltas else 0.0
    eye_score = float(sum(eye_deltas) / len(eye_deltas)) if eye_deltas else 0.0

    mouth_open_change = 0.0
    if len(mouth_open_values) >= 2:
        mouth_open_change = float(
            sum(abs(mouth_open_values[idx] - mouth_open_values[idx - 1]) for idx in range(1, len(mouth_open_values)))
            / float(len(mouth_open_values) - 1)
        )

    eye_blink_change = 0.0
    if len(eye_open_values) >= 2:
        eye_blink_change = float(
            sum(abs(eye_open_values[idx] - eye_open_values[idx - 1]) for idx in range(1, len(eye_open_values)))
            / float(len(eye_open_values) - 1)
        )

    drift_ratio = 0.0
    head_motion_score = 0.0
    if len(face_centers) >= 2:
        xs = [p[0] for p in face_centers]
        ys = [p[1] for p in face_centers]
        drift_ratio = float(max(max(xs) - min(xs), max(ys) - min(ys)))
        step_sizes: list[float] = []
        for idx in range(1, len(face_centers)):
            dx = float(face_centers[idx][0] - face_centers[idx - 1][0])
            dy = float(face_centers[idx][1] - face_centers[idx - 1][1])
            step_sizes.append(float((dx * dx + dy * dy) ** 0.5))
        if step_sizes:
            head_motion_score = float(sum(step_sizes) / len(step_sizes))
        if len(face_scales) >= 2:
            scale_steps = [abs(face_scales[idx] - face_scales[idx - 1]) for idx in range(1, len(face_scales))]
            if scale_steps:
                head_motion_score += float(sum(scale_steps) / len(scale_steps)) * 0.8

    glitch_score = 0.0
    if lip_deltas:
        lip_max = max(lip_deltas)
        lip_mean = max(sum(lip_deltas) / len(lip_deltas), 1e-6)
        glitch_score = max(glitch_score, float(lip_max / lip_mean))
    if eye_deltas:
        eye_max = max(eye_deltas)
        eye_mean = max(sum(eye_deltas) / len(eye_deltas), 1e-6)
        glitch_score = max(glitch_score, float(eye_max / eye_mean))

    mouth_artifact_score = 0.0
    if lip_deltas:
        mouth_artifact_score = float(max(lip_deltas) / max(sum(lip_deltas) / len(lip_deltas), 1e-6))

    eye_artifact_score = 0.0
    if eye_deltas:
        eye_artifact_score = float(max(eye_deltas) / max(sum(eye_deltas) / len(eye_deltas), 1e-6))

    landmark_jitter = 0.0
    if len(face_centers) >= 3:
        center_x = [p[0] for p in face_centers]
        center_y = [p[1] for p in face_centers]
        landmark_jitter = float(np.std(center_x) + np.std(center_y))
        if len(face_scales) >= 3:
            landmark_jitter += float(np.std(face_scales) * 2.0)

    face_warp_score = 0.0
    if len(face_ratios) >= 2:
        ratio_delta = max(abs(face_ratios[idx] - face_ratios[idx - 1]) for idx in range(1, len(face_ratios)))
        scale_delta = 0.0
        if len(face_scales) >= 2:
            scale_delta = max(abs(face_scales[idx] - face_scales[idx - 1]) for idx in range(1, len(face_scales)))
        face_warp_score = float(ratio_delta + (scale_delta * 3.0))

    max_drift = float(os.environ.get("AVATAR_MAX_FACE_DRIFT_RATIO", "0.16"))
    max_glitch = float(os.environ.get("AVATAR_MAX_GLITCH_SCORE", "5.8"))
    max_loop_similarity = float(os.environ.get("AVATAR_MAX_LOOP_START_END_DIFF", "1.1"))
    max_mouth_artifact = float(os.environ.get("AVATAR_MAX_MOUTH_ARTIFACT_SCORE", "5.3"))
    max_eye_artifact = float(os.environ.get("AVATAR_MAX_EYE_ARTIFACT_SCORE", "5.0"))
    max_landmark_jitter = float(os.environ.get("AVATAR_MAX_LANDMARK_JITTER", "0.08"))
    max_face_warp = float(os.environ.get("AVATAR_MAX_FACE_WARP_SCORE", "0.20"))
    min_mouth_open_change = float(os.environ.get("AVATAR_MIN_MOUTH_OPENNESS_CHANGE", "0.0035"))
    min_eye_blink_change = float(os.environ.get("AVATAR_MIN_EYE_BLINK_CHANGE", "0.0025"))
    semantic_loop_similarity = bool(len(sampled_gray) >= 16 and unique_frames >= 6 and first_last_diff <= max_loop_similarity)
    loop_detected = bool(
        repeated_block_detected
        or repeated_adjacent_pairs > max(2, len(sampled_hashes) // 20)
    )

    mouth_artifact_detected = bool(mouth_artifact_score > max_mouth_artifact)
    eye_artifact_detected = bool(eye_artifact_score > max_eye_artifact)
    landmark_stable = bool(landmark_jitter <= max_landmark_jitter)
    face_warp_detected = bool(face_warp_score > max_face_warp)
    structural_face_artifact_detected = bool(
        mouth_artifact_detected
        or eye_artifact_detected
        or face_warp_detected
        or not landmark_stable
    )
    face_artifact_detected = bool(structural_face_artifact_detected)
    low_mouth_openness_change = bool(mouth_open_change < min_mouth_open_change)
    low_eye_blink_change = bool(eye_blink_change < min_eye_blink_change)

    return {
        "frames_sampled": int(len(sampled_gray)),
        "unique_frames": int(unique_frames),
        "face_detection_frames": int(len(face_centers)),
        "mouth_roi_frames": int(len(mouth_open_values)),
        "eye_roi_frames": int(len(eye_open_values)),
        "landmark_valid_frames": int(len(face_centers)),
        "start_end_frame_diff": round(first_last_diff, 4),
        "semantic_loop_similarity": semantic_loop_similarity,
        "lip_movement_score": round(lip_score, 4),
        "eye_movement_score": round(eye_score, 4),
        "mouth_openness_change": round(mouth_open_change, 6),
        "eye_blink_change": round(eye_blink_change, 6),
        "face_drift_ratio": round(drift_ratio, 4),
        "glitch_score": round(glitch_score, 4),
        "mouth_artifact_score": round(mouth_artifact_score, 4),
        "eye_artifact_score": round(eye_artifact_score, 4),
        "landmark_jitter": round(landmark_jitter, 6),
        "face_warp_score": round(face_warp_score, 6),
        "head_motion_score": round(head_motion_score, 6),
        "loop_detected": loop_detected,
        "repeated_adjacent_pairs": int(repeated_adjacent_pairs),
        "repeated_block_detected": bool(repeated_block_detected),
        "drift_detected": bool(drift_ratio > max_drift),
        "glitch_detected": bool(glitch_score > max_glitch),
        "mouth_artifact_detected": mouth_artifact_detected,
        "eye_artifact_detected": eye_artifact_detected,
        "landmark_stable": landmark_stable,
        "face_warp_detected": face_warp_detected,
        "structural_face_artifact_detected": structural_face_artifact_detected,
        "face_artifact_detected": face_artifact_detected,
        "face_roi_artifact_source": "actual_roi" if structural_face_artifact_detected else "none",
        "low_mouth_openness_change": low_mouth_openness_change,
        "low_eye_blink_change": low_eye_blink_change,
        "low_eye_blink_change_warning": False,
        "max_drift": max_drift,
        "max_glitch": max_glitch,
        "max_loop_similarity": max_loop_similarity,
        "max_mouth_artifact": max_mouth_artifact,
        "max_eye_artifact": max_eye_artifact,
        "max_landmark_jitter": max_landmark_jitter,
        "max_face_warp": max_face_warp,
        "min_mouth_open_change": min_mouth_open_change,
        "min_eye_blink_change": min_eye_blink_change,
    }


def _build_animation_score_breakdown(
    *,
    raw_animation_score: float,
    global_frame_diff_mean: float,
    quality: dict[str, Any],
    min_score: float,
    min_lip: float,
    min_eye: float,
) -> dict[str, Any]:
    lip = float(quality.get("lip_movement_score") or 0.0)
    eye = float(quality.get("eye_movement_score") or 0.0)
    mouth = float(quality.get("mouth_openness_change") or 0.0)
    blink = float(quality.get("eye_blink_change") or 0.0)
    head_motion = float(quality.get("head_motion_score") or 0.0)
    start_end = float(quality.get("start_end_frame_diff") or 0.0)
    loop_detected = bool(quality.get("loop_detected"))
    glitch_detected = bool(quality.get("glitch_detected"))
    drift_detected = bool(quality.get("drift_detected"))
    artifact_detected = bool(quality.get("face_artifact_detected"))

    min_mouth = float(quality.get("min_mouth_open_change") or 0.0035)
    min_blink = float(quality.get("min_eye_blink_change") or 0.0025)
    max_loop_similarity = float(quality.get("max_loop_similarity") or 1.0)
    global_ref = float(os.environ.get("AVATAR_ANIMATION_GLOBAL_SIGNAL_REF", "2.2"))
    head_motion_ref = float(os.environ.get("AVATAR_ANIMATION_HEAD_MOTION_REF", "0.006"))

    global_norm = min(global_frame_diff_mean / max(global_ref, 1e-6), 2.0)
    lip_norm = min(lip / max(min_lip, 1e-6), 3.0)
    mouth_norm = min(mouth / max(min_mouth, 1e-6), 3.0)
    eye_norm = min(eye / max(min_eye, 1e-6), 3.0)
    blink_norm = min(blink / max(min_blink, 1e-6), 3.0)
    head_norm = min(head_motion / max(head_motion_ref, 1e-6), 2.0)

    artifact_flags = sum(
        1
        for flag in [artifact_detected, loop_detected, glitch_detected, drift_detected]
        if flag
    )
    start_end_norm = min(start_end / max(max_loop_similarity, 1e-6), 2.0)

    # This is the actual strict animation_score used by validator today.
    strict_formula_weights = {
        "global_frame_diff_mean": 0.12,
        "lip_motion": 0.78,
        "mouth_openness_change": 0.56,
        "eye_motion": 0.24,
        "eye_blink_change": 0.18,
        "head_motion": 0.16,
        "start_end_similarity_penalty": 0.12,
        "artifact_penalty": 0.20,
        "normalization_factor": 1.0,
    }
    strict_formula_inputs = {
        "global_frame_diff_mean": round(global_frame_diff_mean, 6),
        "lip_movement_score": round(lip, 6),
        "mouth_openness_change": round(mouth, 6),
        "eye_movement_score": round(eye, 6),
        "eye_blink_change": round(blink, 6),
        "head_motion_score": round(head_motion, 6),
        "start_end_frame_diff": round(start_end, 6),
        "loop_detected": loop_detected,
        "drift_detected": drift_detected,
        "glitch_detected": glitch_detected,
        "face_artifact_detected": artifact_detected,
    }

    strict_formula_contributions = {
        "global_frame_diff_component": round(global_norm * strict_formula_weights["global_frame_diff_mean"], 6),
        "lip_component": round(lip_norm * strict_formula_weights["lip_motion"], 6),
        "mouth_component": round(mouth_norm * strict_formula_weights["mouth_openness_change"], 6),
        "eye_component": round(eye_norm * strict_formula_weights["eye_motion"], 6),
        "blink_component": round(blink_norm * strict_formula_weights["eye_blink_change"], 6),
        "head_motion_component": round(head_norm * strict_formula_weights["head_motion"], 6),
        "start_end_similarity_penalty": round(max(0.0, 1.0 - start_end_norm) * strict_formula_weights["start_end_similarity_penalty"], 6),
        "artifact_penalty": round(artifact_flags * strict_formula_weights["artifact_penalty"], 6),
    }

    # Diagnostic-only weighted view to explain perceived harshness/misweighting.
    diagnostic_weights = {
        "global_frame_diff_mean": 0.20,
        "lip_motion": 0.45,
        "mouth_openness_change": 0.30,
        "eye_motion": 0.14,
        "eye_blink_change": 0.12,
        "head_motion": 0.10,
        "start_end_similarity_penalty": 0.12,
        "artifact_penalty": 0.20,
    }
    diagnostic_score = (
        global_norm * diagnostic_weights["global_frame_diff_mean"]
        + lip_norm * diagnostic_weights["lip_motion"]
        + mouth_norm * diagnostic_weights["mouth_openness_change"]
        + eye_norm * diagnostic_weights["eye_motion"]
        + blink_norm * diagnostic_weights["eye_blink_change"]
        + head_norm * diagnostic_weights["head_motion"]
        - max(0.0, 1.0 - start_end_norm) * diagnostic_weights["start_end_similarity_penalty"]
        - (artifact_flags * diagnostic_weights["artifact_penalty"])
    )

    harsh_or_misweighted = bool(
        raw_animation_score < min_score
        and lip >= min_lip
        and eye >= min_eye
        and not artifact_detected
        and not loop_detected
        and int(quality.get("unique_frames") or 0) > 0
    )

    return {
        "strict_formula": {
            "name": "mean_absolute_frame_difference_on_downsampled_grayscale",
            "inputs": strict_formula_inputs,
            "weights": strict_formula_weights,
            "contributions": strict_formula_contributions,
            "normalization": "face-centric weighted score from normalized lip/mouth/eye/blink/head motion signals with global frame diff as secondary",
            "raw_score": round(raw_animation_score, 6),
            "min_required": round(min_score, 6),
            "passes_threshold": bool(raw_animation_score >= min_score),
        },
        "diagnostic_formula": {
            "note": "debug_only_not_used_for_acceptance",
            "weights": diagnostic_weights,
            "normalized_inputs": {
                "global_norm": round(global_norm, 6),
                "lip_norm": round(lip_norm, 6),
                "mouth_norm": round(mouth_norm, 6),
                "eye_norm": round(eye_norm, 6),
                "blink_norm": round(blink_norm, 6),
                "head_norm": round(head_norm, 6),
                "start_end_norm": round(start_end_norm, 6),
                "artifact_flag_count": int(artifact_flags),
            },
            "diagnostic_score": round(float(diagnostic_score), 6),
        },
        "misweighting_signal": {
            "possible_harsh_global_motion_weighting": harsh_or_misweighted,
            "reason": (
                "global score below threshold despite strong lip/eye and no artifact flags"
                if harsh_or_misweighted
                else "score failure appears consistent with other quality constraints"
            ),
        },
    }


def validate_avatar_animation(video_path: str, *, validation_context: dict[str, Any] | None = None) -> dict[str, Any]:
    min_frames = int(os.environ.get("AVATAR_MIN_ANIMATED_FRAMES", "18"))
    min_score = float(os.environ.get("AVATAR_MIN_ANIMATION_SCORE", "1.8"))
    min_lip = float(os.environ.get("AVATAR_MIN_LIP_MOVEMENT_SCORE", "1.05"))
    min_eye = float(os.environ.get("AVATAR_MIN_EYE_MOVEMENT_SCORE", "0.18"))
    duration_tolerance = float(os.environ.get("AVATAR_AUDIO_VIDEO_DURATION_TOLERANCE_SEC", "0.45"))
    frame_count = _video_frame_count(video_path)
    quality = _apply_avatar_validation_profile(
        _analyze_avatar_motion_quality(video_path),
        min_eye=min_eye,
        validation_context=validation_context,
    )
    global_frame_diff_mean = _animation_score(video_path)
    score = _face_centric_animation_score(
        global_frame_diff_mean=global_frame_diff_mean,
        quality=quality,
        min_lip=min_lip,
        min_eye=min_eye,
    )
    animation_score_breakdown = _build_animation_score_breakdown(
        raw_animation_score=score,
        global_frame_diff_mean=global_frame_diff_mean,
        quality=quality,
        min_score=min_score,
        min_lip=min_lip,
        min_eye=min_eye,
    )
    # Audio duration is optional at this function level and can be added by caller.
    duration_delta = None
    animated = (
        frame_count > 1
        and frame_count >= min_frames
        and score >= min_score
        and int(quality.get("unique_frames") or 0) > 0
        and float(quality.get("lip_movement_score") or 0.0) >= min_lip
        and float(quality.get("eye_movement_score") or 0.0) >= min_eye
        and float(quality.get("mouth_openness_change") or 0.0) >= float(quality.get("min_mouth_open_change") or 0.0)
        and float(quality.get("eye_blink_change") or 0.0) >= float(quality.get("min_eye_blink_change") or 0.0)
        and not bool(quality.get("loop_detected"))
        and not bool(quality.get("drift_detected"))
        and not bool(quality.get("glitch_detected"))
        and not bool(quality.get("mouth_artifact_detected", False))
        and not bool(quality.get("eye_artifact_detected", False))
        and bool(quality.get("landmark_stable", True))
        and not bool(quality.get("face_warp_detected", False))
        and not bool(quality.get("face_artifact_detected", False))
    )
    return {
        "frame_count": frame_count,
        "animation_score": round(score, 4),
        "global_frame_diff_mean": round(global_frame_diff_mean, 4),
        "animated": bool(animated),
        "quality_checks": quality,
        "animation_score_breakdown": animation_score_breakdown,
        "duration_delta_seconds": duration_delta,
        "duration_tolerance_seconds": duration_tolerance,
        "min_frames": min_frames,
        "min_score": min_score,
        "min_lip_movement": min_lip,
        "min_eye_movement": min_eye,
        "avatar_validation_profile": str(quality.get("avatar_validation_profile") or "strict"),
        "eye_blink_threshold_used": float(quality.get("eye_blink_threshold_used") or quality.get("min_eye_blink_change") or 0.0),
        "low_eye_blink_change_warning": bool(quality.get("low_eye_blink_change_warning")),
        "face_roi_artifact_source": str(quality.get("face_roi_artifact_source") or "none"),
        "invalid_eye_motion_source": "none",
    }


def is_truly_animated(video_path: str) -> bool:
    metrics = validate_avatar_animation(video_path)
    return bool(metrics.get("animated"))


def validate_avatar_render_with_audio(
    video_path: str,
    audio_path: str,
    *,
    validation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = validate_avatar_animation(video_path, validation_context=validation_context)
    video_duration = _probe_video_duration_seconds(video_path)
    audio_duration = _probe_audio_duration_seconds(audio_path)
    duration_delta = abs(video_duration - audio_duration)
    tolerance = float(metrics.get("duration_tolerance_seconds") or 0.45)
    metrics["video_duration_seconds"] = round(video_duration, 4)
    metrics["audio_duration_seconds"] = round(audio_duration, 4)
    metrics["duration_delta_seconds"] = round(duration_delta, 4)
    metrics["audio_match"] = bool(duration_delta <= tolerance)
    if duration_delta > tolerance:
        metrics["animated"] = False
        metrics["duration_mismatch"] = True
    else:
        metrics["duration_mismatch"] = False

    quality = metrics.get("quality_checks") or {}
    lip_valid = bool(
        float(quality.get("lip_movement_score") or 0.0) >= float(metrics.get("min_lip_movement") or 0.0)
        and float(quality.get("mouth_openness_change") or 0.0) >= float(quality.get("min_mouth_open_change") or 0.0)
        and not bool(metrics.get("duration_mismatch"))
    )
    eye_valid = bool(
        float(quality.get("eye_movement_score") or 0.0) >= float(metrics.get("min_eye_movement") or 0.0)
        and float(quality.get("eye_blink_change") or 0.0) >= float(quality.get("min_eye_blink_change") or 0.0)
    )
    artifact_detected = bool(
        quality.get("face_artifact_detected")
        or quality.get("mouth_artifact_detected")
        or quality.get("eye_artifact_detected")
        or quality.get("face_warp_detected")
        or not quality.get("landmark_stable", True)
    )
    metrics["lip_motion_valid"] = bool(lip_valid)
    metrics["eye_motion_valid"] = bool(eye_valid)
    metrics["face_artifacts_detected"] = bool(artifact_detected)
    invalid_eye_motion_source = "none"
    if float(quality.get("eye_movement_score") or 0.0) < float(metrics.get("min_eye_movement") or 0.0):
        invalid_eye_motion_source = "eye_movement"
    elif float(quality.get("eye_blink_change") or 0.0) < float(quality.get("min_eye_blink_change") or 0.0):
        invalid_eye_motion_source = "blink_amplitude"
    metrics["invalid_eye_motion_source"] = invalid_eye_motion_source
    metrics["face_roi_artifact_source"] = str(quality.get("face_roi_artifact_source") or ("actual_roi" if artifact_detected else "none"))
    metrics["avatar_validation_profile"] = str(quality.get("avatar_validation_profile") or "strict")
    metrics["eye_blink_threshold_used"] = float(quality.get("eye_blink_threshold_used") or quality.get("min_eye_blink_change") or 0.0)
    metrics["low_eye_blink_change_warning"] = bool(quality.get("low_eye_blink_change_warning"))
    warnings = list(metrics.get("validation_warnings") or [])
    if bool(metrics["low_eye_blink_change_warning"]) and "low_eye_blink_change" not in warnings:
        warnings.append("low_eye_blink_change")
    metrics["validation_warnings"] = warnings
    if not lip_valid or not eye_valid or artifact_detected:
        metrics["animated"] = False

    reason_parts: list[str] = []
    if metrics.get("frame_count", 0) <= 1:
        reason_parts.append("single_frame_output")
    if int(metrics.get("frame_count") or 0) < int(metrics.get("min_frames") or 0):
        reason_parts.append("too_few_frames")
    if float(metrics.get("animation_score") or 0.0) < float(metrics.get("min_score") or 0.0):
        reason_parts.append("low_animation_score")
    if int(quality.get("unique_frames") or 0) <= 0:
        reason_parts.append("identical_frames")
    if float(quality.get("lip_movement_score") or 0.0) < float(metrics.get("min_lip_movement") or 0.0):
        reason_parts.append("low_lip_motion")
    if float(quality.get("eye_movement_score") or 0.0) < float(metrics.get("min_eye_movement") or 0.0):
        reason_parts.append("low_eye_motion")
    if bool(quality.get("loop_detected")):
        reason_parts.append("loop_like_motion")
    if bool(quality.get("drift_detected")):
        reason_parts.append("whole_frame_drift")
    if bool(quality.get("glitch_detected")):
        reason_parts.append("glitch_artifact")
    if not bool(metrics.get("lip_motion_valid")):
        reason_parts.append("invalid_lip_sync")
    if not bool(metrics.get("eye_motion_valid")):
        reason_parts.append("invalid_eye_motion")
    if bool(metrics.get("face_artifacts_detected")):
        reason_parts.append("face_roi_artifact")
    if bool(quality.get("mouth_artifact_detected")):
        reason_parts.append("mouth_artifact")
    if bool(quality.get("eye_artifact_detected")):
        reason_parts.append("eye_artifact")
    if bool(quality.get("face_warp_detected")):
        reason_parts.append("face_warp")
    if not bool(quality.get("landmark_stable", True)):
        reason_parts.append("landmark_instability")
    if float(quality.get("mouth_openness_change") or 0.0) < float(quality.get("min_mouth_open_change") or 0.0):
        reason_parts.append("low_mouth_openness_change")
    if float(quality.get("eye_blink_change") or 0.0) < float(quality.get("min_eye_blink_change") or 0.0):
        reason_parts.append("low_eye_blink_change")
    if bool(metrics.get("duration_mismatch")):
        reason_parts.append("duration_mismatch")
    if not bool(metrics.get("audio_match")):
        reason_parts.append("audio_match_false")

    metrics["failure_reason"] = ",".join(reason_parts) if reason_parts else ""
    metrics["motion_real"] = bool(metrics.get("animated") and not metrics.get("failure_reason"))
    return metrics


def _failure_reason_set(metrics: dict[str, Any]) -> set[str]:
    raw = str(metrics.get("failure_reason") or "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _lesson_segment_validation_is_warning_only(metrics: dict[str, Any]) -> bool:
    reasons = _failure_reason_set(metrics)
    warning_reasons = {"whole_frame_drift", "landmark_instability", "face_roi_artifact"}
    if not reasons or not reasons.issubset(warning_reasons):
        return False

    quality = metrics.get("quality_checks") or {}
    frame_count = int(metrics.get("frame_count") or 0)
    min_frames = int(metrics.get("min_frames") or 1)
    score = float(metrics.get("animation_score") or 0.0)
    min_score = float(metrics.get("min_score") or 0.0)

    return bool(
        frame_count >= min_frames
        and score >= min_score
        and int(quality.get("unique_frames") or 0) > 0
        and bool(metrics.get("audio_match"))
        and not bool(metrics.get("duration_mismatch"))
        and bool(metrics.get("lip_motion_valid"))
        and bool(metrics.get("eye_motion_valid"))
        and not bool(quality.get("loop_detected"))
        and not bool(quality.get("glitch_detected"))
        and not bool(quality.get("mouth_artifact_detected"))
        and not bool(quality.get("eye_artifact_detected"))
        and not bool(quality.get("face_warp_detected"))
        and float(quality.get("mouth_openness_change") or 0.0) >= float(quality.get("min_mouth_open_change") or 0.0)
        and float(quality.get("eye_blink_change") or 0.0) >= float(quality.get("min_eye_blink_change") or 0.0)
    )


def apply_lesson_segment_validation_policy(metrics: dict[str, Any]) -> dict[str, Any]:
    adjusted = dict(metrics or {})
    quality = dict(adjusted.get("quality_checks") or {})
    adjusted["validation_policy"] = "lesson_avatar_overlay_segment"

    if not _lesson_segment_validation_is_warning_only(adjusted):
        adjusted["whole_frame_drift_diagnostic_only"] = False
        adjusted["validation_classification"] = "hard_failure" if adjusted.get("failure_reason") else "passed"
        adjusted["validation_warning_only"] = False
        adjusted["quality_checks"] = quality
        return adjusted

    warning_reasons = sorted(_failure_reason_set(adjusted))
    warnings = list(adjusted.get("validation_warnings") or [])
    for reason in warning_reasons:
        if reason not in warnings:
            warnings.append(reason)
    quality["lesson_overlay_warning_only"] = True
    if "whole_frame_drift" in warning_reasons:
        quality["drift_diagnostic_only"] = True
        quality["drift_warning"] = True
    if "landmark_instability" in warning_reasons:
        quality["landmark_instability_warning"] = True
    if "face_roi_artifact" in warning_reasons:
        quality["face_roi_artifact_warning"] = True
    adjusted["quality_checks"] = quality
    adjusted["failure_reason"] = ""
    adjusted["animated"] = True
    adjusted["motion_real"] = True
    adjusted["whole_frame_drift_diagnostic_only"] = "whole_frame_drift" in warning_reasons
    adjusted["validation_warning_only"] = True
    adjusted["validation_classification"] = "warning"
    adjusted["validation_warnings"] = warnings
    return adjusted


def validate_avatar_lesson_segment_with_audio(
    video_path: str,
    audio_path: str,
    *,
    validation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_lesson_segment_validation_policy(
        validate_avatar_render_with_audio(video_path, audio_path, validation_context=validation_context)
    )


def has_valid_lip_motion(video_path: str, audio_path: str | None = None) -> bool:
    metrics = validate_avatar_animation(video_path)
    quality = metrics.get("quality_checks") or {}
    lip_ok = float(quality.get("lip_movement_score") or 0.0) >= float(metrics.get("min_lip_movement") or 0.0)
    mouth_ok = float(quality.get("mouth_openness_change") or 0.0) >= float(quality.get("min_mouth_open_change") or 0.0)
    if not lip_ok or not mouth_ok:
        return False
    if not audio_path:
        return True
    tolerance = float(metrics.get("duration_tolerance_seconds") or 0.45)
    return abs(_probe_video_duration_seconds(video_path) - _probe_audio_duration_seconds(audio_path)) <= tolerance


def has_valid_eye_motion(video_path: str) -> bool:
    metrics = validate_avatar_animation(video_path)
    quality = metrics.get("quality_checks") or {}
    eye_ok = float(quality.get("eye_movement_score") or 0.0) >= float(metrics.get("min_eye_movement") or 0.0)
    blink_ok = float(quality.get("eye_blink_change") or 0.0) >= float(quality.get("min_eye_blink_change") or 0.0)
    return bool(eye_ok and blink_ok)


def has_face_artifacts(video_path: str) -> bool:
    metrics = validate_avatar_animation(video_path)
    quality = metrics.get("quality_checks") or {}
    return bool(
        quality.get("face_artifact_detected")
        or quality.get("mouth_artifact_detected")
        or quality.get("eye_artifact_detected")
        or quality.get("face_warp_detected")
        or not quality.get("landmark_stable", True)
    )


def accept_avatar_render(metrics: dict[str, Any]) -> bool:
    frame_count = int(metrics.get("frame_count") or 0)
    min_frames = int(metrics.get("min_frames") or 1)
    return bool(
        metrics.get("motion_real") is True
        and metrics.get("animated") is True
        and metrics.get("lip_motion_valid") is True
        and metrics.get("eye_motion_valid") is True
        and metrics.get("face_artifacts_detected") is False
        and metrics.get("audio_match") is True
        and frame_count >= min_frames
        and metrics.get("duration_mismatch") is False
    )


def accept_avatar_lesson_segment_render(metrics: dict[str, Any]) -> bool:
    adjusted = dict(metrics or {})
    if not bool(adjusted.get("validation_warning_only")):
        adjusted = apply_lesson_segment_validation_policy(adjusted)
    if bool(adjusted.get("validation_warning_only")):
        frame_count = int(adjusted.get("frame_count") or 0)
        min_frames = int(adjusted.get("min_frames") or 1)
        return bool(
            adjusted.get("motion_real") is True
            and adjusted.get("animated") is True
            and adjusted.get("lip_motion_valid") is True
            and adjusted.get("eye_motion_valid") is True
            and adjusted.get("audio_match") is True
            and frame_count >= min_frames
            and adjusted.get("duration_mismatch") is False
        )
    return accept_avatar_render(adjusted)


def _save_failed_render_debug(
    *,
    output_path: Path,
    request: AvatarRenderRequest,
    metrics: dict[str, Any],
    engine: str,
    attempts: list[str],
    result_error: str,
    stage_traces: dict[str, Any] | None = None,
) -> str:
    enabled = str(os.environ.get("AVATAR_SAVE_FAILED_DEBUG", "1")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return ""

    debug_dir = output_path.parent / "debug_failed" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    debug_dir.mkdir(parents=True, exist_ok=True)

    input_image = Path(request.source_image_path)
    if input_image.exists():
        shutil.copy2(input_image, debug_dir / f"input_reference_image{input_image.suffix}")

    if request.source_video_path:
        input_video = Path(request.source_video_path)
        if input_video.exists():
            shutil.copy2(input_video, debug_dir / f"input_reference_video{input_video.suffix}")

    audio_src = Path(request.audio_path)
    if audio_src.exists():
        shutil.copy2(audio_src, debug_dir / f"input_audio{audio_src.suffix}")

    if output_path.exists():
        shutil.copy2(output_path, debug_dir / f"output_preview{output_path.suffix}")

    diag_enabled = str(os.environ.get("AVATAR_PREVIEW_DIAGNOSTIC_MODE", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    frame_trace: dict[str, Any] = {}
    if output_path.exists() and diag_enabled:
        frame_trace = _analyze_frame_sequence(
            video_path=str(output_path),
            export_dir=debug_dir / "frames",
            export_limit=int(os.environ.get("AVATAR_PREVIEW_DIAG_EXPORT_FRAMES", "32")),
        )

    if output_path.exists() and cv2 is not None:
        try:
            capture = cv2.VideoCapture(str(output_path))
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            sample_indices = [0, max(frame_count // 2, 0), max(frame_count - 1, 0)]
            saved = 0
            for idx in sample_indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                cv2.imwrite(str(debug_dir / f"frame_sample_{saved:02d}.jpg"), frame)
                saved += 1
            capture.release()
        except Exception:
            logger.warning("Failed to save debug frame samples for %s", output_path, exc_info=True)

    report = {
        "engine": engine,
        "attempts": attempts,
        "result_error": result_error,
        "debug_path": str(debug_dir),
        "request": {
            "source_image_path": request.source_image_path,
            "source_video_path": request.source_video_path,
            "audio_path": request.audio_path,
            "output_path": str(output_path),
            "avatar_reference_type": request.avatar_reference_type,
            "quality_preset": request.quality_preset,
            "lipsync_engine": request.lipsync_engine,
        },
        "metrics": metrics,
        "frame_trace": frame_trace,
        "stage_traces": stage_traces or {},
    }
    (debug_dir / "validation_report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return str(debug_dir)


def _preview_stage_timeout_seconds(stage_name: str, default_seconds: float) -> float:
    key = f"AVATAR_STAGE_TIMEOUT_{str(stage_name or '').upper()}_SECONDS"
    raw = str(os.environ.get(key, "")).strip()
    if not raw:
        return float(default_seconds)
    try:
        value = float(raw)
    except Exception:
        return float(default_seconds)
    return value if value > 0 else float(default_seconds)


def _preview_stage_log(
    *,
    event: str,
    teacher_id: int,
    job_id: int,
    stage: str,
    input_path: str = "",
    output_path: str = "",
    elapsed_seconds: float | None = None,
    details: str = "",
) -> None:
    payload = {
        "event": str(event),
        "teacher_id": int(teacher_id or 0),
        "job_id": int(job_id or 0),
        "stage": str(stage),
        "input_path": str(input_path or ""),
        "output_path": str(output_path or ""),
        "elapsed_seconds": round(float(elapsed_seconds or 0.0), 3),
    }
    if details:
        payload["details"] = str(details)
    logger.info("avatar_preview_stage %s", json.dumps(payload, ensure_ascii=True, sort_keys=True))


def _build_liveportrait_attempt_profiles(*, is_preview_render: bool) -> list[dict[str, Any]]:
    if not is_preview_render:
        return [
            {
                "name": "default",
                "retry": False,
                "motion_strength": float(os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_STRENGTH", "1.0") or 1.0),
                "warp_strength": float(os.environ.get("AVATAR_LIVEPORTRAIT_WARP_STRENGTH", "1.0") or 1.0),
                "temporal_smoothing": float(os.environ.get("AVATAR_LIVEPORTRAIT_TEMPORAL_SMOOTHING", "0.0") or 0.0),
                "stabilize_window": int(float(os.environ.get("AVATAR_LIVEPORTRAIT_STABILIZE_WINDOW", "11") or 11)),
                "stabilize_max_shift_px": float(os.environ.get("AVATAR_LIVEPORTRAIT_STABILIZE_MAX_SHIFT_PX", "14.0") or 14.0),
            }
        ]

    retry_enabled = str(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_SAFE_RETRY", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    primary = {
        "name": "preview_primary",
        "retry": False,
        "motion_strength": float(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_MOTION_STRENGTH", "1.0") or 1.0),
        "warp_strength": float(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_WARP_STRENGTH", "0.84") or 0.84),
        "temporal_smoothing": float(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_TEMPORAL_SMOOTHING", "0.10") or 0.10),
        "stabilize_window": int(float(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_STABILIZE_WINDOW", "8") or 8)),
        "stabilize_max_shift_px": float(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_STABILIZE_MAX_SHIFT_PX", "7.0") or 7.0),
    }
    ultra_conservative = {
        "name": "preview_ultra_retry",
        "retry": True,
        # Keep one bounded retry but bias strongly toward stability.
        "motion_strength": float(
            os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_ULTRA_RETRY_MOTION_STRENGTH", "0.52") or 0.52
        ),
        "warp_strength": float(
            os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_ULTRA_RETRY_WARP_STRENGTH", "0.62") or 0.62
        ),
        "temporal_smoothing": float(
            os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_ULTRA_RETRY_TEMPORAL_SMOOTHING", "0.05") or 0.05
        ),
        "stabilize_window": int(
            float(os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_ULTRA_RETRY_STABILIZE_WINDOW", "5") or 5)
        ),
        "stabilize_max_shift_px": float(
            os.environ.get("AVATAR_PREVIEW_LIVEPORTRAIT_ULTRA_RETRY_STABILIZE_MAX_SHIFT_PX", "4.0") or 4.0
        ),
    }
    return [primary, ultra_conservative] if retry_enabled else [primary]


def _liveportrait_env_for_attempt(attempt_profile: dict[str, Any]) -> dict[str, str]:
    return {
        "AVATAR_LIVEPORTRAIT_MOTION_STRENGTH": f"{float(attempt_profile.get('motion_strength') or 1.0):.6f}",
        "AVATAR_LIVEPORTRAIT_WARP_STRENGTH": f"{float(attempt_profile.get('warp_strength') or 1.0):.6f}",
        "AVATAR_LIVEPORTRAIT_TEMPORAL_SMOOTHING": f"{float(attempt_profile.get('temporal_smoothing') or 0.0):.6f}",
    }


def _stabilize_config_for_attempt(attempt_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "window": int(float(attempt_profile.get("stabilize_window") or 9)),
        "max_shift_px": float(attempt_profile.get("stabilize_max_shift_px") or 8.0),
    }


def _run_shell_template(
    template: str,
    replacements: dict[str, str],
    *,
    timeout_seconds: float,
    stage_name: str,
    teacher_id: int,
    job_id: int,
    input_path: str,
    output_path: str,
    env_overrides: dict[str, str] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    command = str(template or "")
    for key, value in replacements.items():
        command = command.replace("{" + str(key) + "}", str(value))

    started_at = time.monotonic()
    _preview_stage_log(
        event="stage_started",
        teacher_id=teacher_id,
        job_id=job_id,
        stage=stage_name,
        input_path=input_path,
        output_path=output_path,
    )

    details: dict[str, Any] = {
        "stage": stage_name,
        "command": command,
        "timeout_seconds": float(timeout_seconds),
        "input_path": str(input_path or ""),
        "output_path": str(output_path or ""),
        "env_overrides": dict(env_overrides or {}),
    }

    command_env = os.environ.copy()
    if env_overrides:
        command_env.update({str(k): str(v) for k, v in env_overrides.items()})

    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=(float(timeout_seconds) if float(timeout_seconds) > 0 else None),
            env=command_env,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started_at
        reason = f"{stage_name}_timeout:exceeded_{int(float(timeout_seconds))}s"
        details["return_code"] = -1
        details["stderr_summary"] = reason
        details["output_exists"] = bool(Path(output_path).exists()) if output_path else False
        _preview_stage_log(
            event="stage_finished",
            teacher_id=teacher_id,
            job_id=job_id,
            stage=stage_name,
            input_path=input_path,
            output_path=output_path,
            elapsed_seconds=elapsed,
            details=reason,
        )
        return False, reason, details

    elapsed = time.monotonic() - started_at
    details["return_code"] = int(proc.returncode)
    details["stderr_summary"] = str((proc.stderr or proc.stdout or "").strip()[:500])
    details["output_exists"] = bool(Path(output_path).exists()) if output_path else False
    details["output_size"] = int(Path(output_path).stat().st_size) if (output_path and Path(output_path).exists()) else 0
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "command failed").strip()
        _preview_stage_log(
            event="stage_finished",
            teacher_id=teacher_id,
            job_id=job_id,
            stage=stage_name,
            input_path=input_path,
            output_path=output_path,
            elapsed_seconds=elapsed,
            details=f"failed:{err[:280]}",
        )
        return False, err, details
    _preview_stage_log(
        event="stage_finished",
        teacher_id=teacher_id,
        job_id=job_id,
        stage=stage_name,
        input_path=input_path,
        output_path=output_path,
        elapsed_seconds=elapsed,
        details="success",
    )
    return True, "", details


def _preview_stage_report(
    *,
    stage_name: str,
    video_path: str,
    audio_path: str,
    trace_root: Path | None,
    validate_with_audio: bool,
) -> dict[str, Any]:
    path = Path(video_path)
    exists = path.exists() and path.stat().st_size > 0
    report: dict[str, Any] = {
        "stage": stage_name,
        "output_path": str(path),
        "exists": bool(exists),
        "frame_count": int(_video_frame_count(str(path))) if exists else 0,
        "video_duration_seconds": round(_probe_video_duration_seconds(str(path)), 4) if exists else 0.0,
        "audio_duration_seconds": round(_probe_audio_duration_seconds(audio_path), 4) if Path(audio_path).exists() else 0.0,
    }
    if exists:
        try:
            validation = (
                validate_avatar_render_with_audio(str(path), audio_path)
                if validate_with_audio
                else validate_avatar_animation(str(path))
            )
        except Exception as exc:
            validation = {"error": str(exc)}
        report["validation"] = validation
        quality = validation.get("quality_checks") if isinstance(validation, dict) else {}
        quality = quality or {}
        report["artifact_score"] = round(
            float(
                max(
                    float(quality.get("mouth_artifact_score") or 0.0),
                    float(quality.get("eye_artifact_score") or 0.0),
                    float(quality.get("face_warp_score") or 0.0),
                    float(quality.get("glitch_score") or 0.0),
                )
            ),
            6,
        )
        report["failure_reason"] = str((validation.get("failure_reason") if isinstance(validation, dict) else "") or "")
        if validate_with_audio and isinstance(validation, dict):
            report["strict_pass"] = bool(accept_avatar_render(validation))
        if trace_root is not None:
            export_dir = trace_root / f"{stage_name}_frames"
            report["frame_trace"] = _analyze_frame_sequence(video_path=str(path), export_dir=export_dir)
    else:
        report["validation"] = {"error": "stage_output_missing"}
        if validate_with_audio:
            report["strict_pass"] = False
    return report


def _stabilize_face_motion(
    video_path: str,
    *,
    window: int | None = None,
    max_shift_px: float | None = None,
    min_detection_ratio: float | None = None,
) -> dict[str, Any]:
    """
    Stabilize global face center drift/jitter while preserving local expression motion.

    This does not relax quality thresholds; it reduces camera-like frame wobble that
    can trigger landmark instability in otherwise valid LP outputs.
    """
    if cv2 is None:
        return {"applied": False, "reason": "opencv_unavailable"}

    enabled = str(os.environ.get("AVATAR_LIVEPORTRAIT_STABILIZE_ENABLED", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return {"applied": False, "reason": "disabled"}

    try:
        import numpy as np
    except Exception:
        return {"applied": False, "reason": "numpy_unavailable"}

    source = Path(video_path)
    if not source.exists() or source.stat().st_size <= 0:
        return {"applied": False, "reason": "missing_input"}

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return {"applied": False, "reason": "video_open_failed"}

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    if fps <= 0:
        fps = 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        return {"applied": False, "reason": "invalid_dimensions"}

    face_detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    frames: list[Any] = []
    centers: list[tuple[float, float] | None] = []
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        frames.append(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(120, 120))
        if faces is None or len(faces) == 0:
            centers.append(None)
            continue
        x, y, fw, fh = [int(v) for v in max(faces, key=lambda f: int(f[2]) * int(f[3]))]
        centers.append((x + (fw / 2.0), y + (fh / 2.0)))
    cap.release()

    frame_count = len(frames)
    if frame_count <= 0:
        return {"applied": False, "reason": "no_frames"}

    valid_centers = [c for c in centers if c is not None]
    detected_ratio = float(len(valid_centers)) / float(max(frame_count, 1))
    min_ratio = float(
        min_detection_ratio
        if min_detection_ratio is not None
        else (os.environ.get("AVATAR_LIVEPORTRAIT_STABILIZE_MIN_DETECTION_RATIO", "0.65") or 0.65)
    )
    if len(valid_centers) < 6 or detected_ratio < min_ratio:
        return {
            "applied": False,
            "reason": "insufficient_face_tracking",
            "frame_count": int(frame_count),
            "detected_ratio": round(detected_ratio, 6),
        }

    # Fill missing centers by carrying nearest known value.
    filled: list[tuple[float, float]] = []
    last = valid_centers[0]
    for c in centers:
        if c is None:
            filled.append(last)
        else:
            last = c
            filled.append(c)

    cx = np.array([c[0] for c in filled], dtype=np.float32)
    cy = np.array([c[1] for c in filled], dtype=np.float32)
    win = int(float(window if window is not None else (os.environ.get("AVATAR_LIVEPORTRAIT_STABILIZE_WINDOW", "11") or 11)))
    win = max(5, min(31, win))
    if win % 2 == 0:
        win += 1
    kernel = np.ones((win,), dtype=np.float32) / float(win)
    smooth_cx = np.convolve(cx, kernel, mode="same")
    smooth_cy = np.convolve(cy, kernel, mode="same")

    dx = smooth_cx - cx
    dy = smooth_cy - cy
    max_shift = float(
        max_shift_px
        if max_shift_px is not None
        else (os.environ.get("AVATAR_LIVEPORTRAIT_STABILIZE_MAX_SHIFT_PX", "14.0") or 14.0)
    )
    magnitude = np.sqrt((dx ** 2) + (dy ** 2))
    scale = np.ones_like(magnitude)
    mask = magnitude > max_shift
    scale[mask] = max_shift / np.maximum(magnitude[mask], 1e-6)
    dx = dx * scale
    dy = dy * scale

    tmp = source.with_suffix(source.suffix + ".stabilized.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        return {"applied": False, "reason": "writer_open_failed"}

    for idx, frame in enumerate(frames):
        m = np.float32([[1, 0, float(dx[idx])], [0, 1, float(dy[idx])]])
        stabilized = cv2.warpAffine(
            frame,
            m,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        writer.write(stabilized)
    writer.release()

    if not tmp.exists() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        return {"applied": False, "reason": "stabilized_output_missing"}

    shutil.move(str(tmp), str(source))
    return {
        "applied": True,
        "frame_count": int(frame_count),
        "detected_ratio": round(float(detected_ratio), 6),
        "window": int(win),
        "max_shift_px": round(float(max_shift), 4),
        "mean_shift_px": round(float(np.mean(np.sqrt((dx ** 2) + (dy ** 2)))), 6),
        "max_applied_shift_px": round(float(np.max(np.sqrt((dx ** 2) + (dy ** 2)))), 6),
    }


def _write_stage_report(*, trace_root: Path | None, stage_name: str, report: dict[str, Any]) -> None:
    if trace_root is None:
        return
    stage_dir = trace_root / "stages"
    stage_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(ch if ch.isalnum() else "_" for ch in str(stage_name or "stage")).strip("_") or "stage"
    path = stage_dir / f"{slug}.json"
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")


def _validate_preview_motion_base(stage_report: dict[str, Any]) -> tuple[bool, str]:
    validation = stage_report.get("validation") or {}
    quality = validation.get("quality_checks") or {}
    frame_count = int(stage_report.get("frame_count") or 0)
    if frame_count < int(validation.get("min_frames") or 18):
        return False, "liveportrait_too_few_frames"
    if int(quality.get("unique_frames") or 0) <= 0:
        return False, "liveportrait_static_frames"
    if bool(quality.get("loop_detected")):
        return False, "liveportrait_loop_like_motion"
    if bool(quality.get("drift_detected")):
        return False, "liveportrait_frame_drift"
    if bool(quality.get("glitch_detected")):
        return False, "liveportrait_glitch_artifact"
    # Base-stage gate should focus on structural output quality; mouth/blink
    # movement thresholds are lip-sync concerns and are enforced later.
    if bool(quality.get("mouth_artifact_detected")):
        return False, "liveportrait_mouth_artifact"
    if bool(quality.get("eye_artifact_detected")):
        return False, "liveportrait_eye_artifact"
    if bool(quality.get("face_warp_detected")):
        return False, "liveportrait_face_warp"
    if not bool(quality.get("landmark_stable", True)):
        return False, "liveportrait_landmark_instability"
    # Backward-compatible fallback only for legacy payloads that do not expose
    # structural breakdown keys. Do not reject LP base on mouth/blink minima.
    has_structural_keys = any(
        key in quality
        for key in [
            "mouth_artifact_detected",
            "eye_artifact_detected",
            "face_warp_detected",
            "landmark_stable",
            "structural_face_artifact_detected",
        ]
    )
    if not has_structural_keys and bool(quality.get("face_artifact_detected")):
        return False, "liveportrait_face_artifact"
    if bool(quality.get("structural_face_artifact_detected")):
        return False, "liveportrait_face_artifact"
    return True, ""


def render_avatar_segment_local(request: AvatarRenderRequest) -> dict:
    from .canonical_pipeline import render_avatar_segment_local_canonical

    return render_avatar_segment_local_canonical(request)


__all__ = [
    "AvatarValidationError",
    "AvatarRenderRequest",
    "preprocess_teacher_avatar_image",
    "render_avatar_segment_local",
    "validate_avatar_animation",
    "validate_avatar_render_with_audio",
    "validate_avatar_lesson_segment_with_audio",
    "is_truly_animated",
    "has_valid_lip_motion",
    "has_valid_eye_motion",
    "has_face_artifacts",
    "evaluate_avatar_visual_quality",
    "accept_avatar_render",
    "accept_avatar_lesson_segment_render",
    "apply_lesson_segment_validation_policy",
]
