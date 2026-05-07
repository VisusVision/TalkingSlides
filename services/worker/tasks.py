"""
services/worker/tasks.py
=========================
Celery task definitions for AI_ACADEMY worker.

Preview runtime policy
----------------------
Only the canonical preview path is active:
  current upload -> canonical source -> TTS -> LivePortrait -> MuseTalk -> final preview state

Legacy preview implementations and generic fallback preview render paths are
intentionally removed from this module.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback as tb
import wave
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = None
    ImageDraw = None
    ImageFont = None

# Ensure scripts package is importable both in Docker (/app/scripts) and local dev.
_SCRIPT_DIRS = [
    Path(__file__).resolve().parent.parent,
    Path(__file__).resolve().parent.parent / "scripts",
    Path(__file__).resolve().parent.parent / "avatar",
    Path("/app"),
    Path("/app/scripts"),
    Path("/app/avatar"),
]
for _sd in _SCRIPT_DIRS:
    if _sd.exists() and str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

from celery import chord, group  # noqa: E402
from celery.exceptions import SoftTimeLimitExceeded  # noqa: E402
from celery.signals import worker_ready  # noqa: E402

from .avatar_timeout_policy import resolve_preview_task_time_limits  # noqa: E402
from .celery_app import app  # noqa: E402

logger = logging.getLogger(__name__)

# Storage root for worker output (shared with the API/media serving layer).
STORAGE_ROOT = os.environ.get("STORAGE_ROOT", "storage_local")
MEDIA_SERVE_BASE = os.environ.get("MEDIA_SERVE_BASE_URL", "http://localhost:8000/api/v1/media").rstrip("/")
DRM_STREAMING_ENABLED = os.environ.get("DRM_STREAMING_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
DRM_HLS_ENCRYPTION_ENABLED = os.environ.get("DRM_HLS_ENCRYPTION_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DRM_HLS_KEY_HEX = os.environ.get("DRM_HLS_KEY_HEX", "").strip() or None
DRM_ASSET_ID_PREFIX = os.environ.get("DRM_ASSET_ID_PREFIX", "lesson-")
DRM_CONTENT_ID_PREFIX = os.environ.get("DRM_CONTENT_ID_PREFIX", "project-")
WORKER_TRIM_TRAILING_SILENCE = os.environ.get("WORKER_TRIM_TRAILING_SILENCE", "0").lower() in {"1", "true", "yes", "on"}
JOB_CANCELLED_MARKER = "__cancelled_by_user__"

_PREVIEW_TASK_TIME_LIMITS = resolve_preview_task_time_limits(logger=logger)
_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS = _PREVIEW_TASK_TIME_LIMITS.soft_seconds
_PREVIEW_TASK_HARD_TIMEOUT_SECONDS = _PREVIEW_TASK_TIME_LIMITS.hard_seconds


def _render_queue_name() -> str:
    return str(os.environ.get("CELERY_RENDER_QUEUE", "render") or "render").strip() or "render"


def _render_fast_queue_name() -> str:
    return str(os.environ.get("CELERY_RENDER_FAST_QUEUE", "render_fast") or "render_fast").strip() or "render_fast"


def _render_quality_queue_name() -> str:
    return str(os.environ.get("CELERY_RENDER_QUALITY_QUEUE", "render_quality") or "render_quality").strip() or "render_quality"


def _avatar_queue_name() -> str:
    return str(os.environ.get("CELERY_AVATAR_QUEUE", "avatar") or "avatar").strip() or "avatar"


def _queue_for_render_profile(render_profile: str | None) -> str:
    profile = str(render_profile or "balanced").strip().lower()
    if profile == "fast":
        return _render_fast_queue_name()
    if profile == "quality":
        return _render_quality_queue_name()
    return _render_queue_name()


def _queue_for_pipeline(avatar_options: dict[str, Any] | None, render_profile: str | None) -> str:
    return _avatar_queue_name() if bool((avatar_options or {}).get("enabled")) else _queue_for_render_profile(render_profile)


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _avatar_gpu_lock_path() -> Path:
    configured = str(os.environ.get("AVATAR_GPU_SERIAL_LOCK_PATH", "")).strip()
    if configured:
        return Path(configured)
    return Path(_avatar_storage_root()) / "avatar_gpu_jobs.lock"


@contextlib.contextmanager
def _avatar_gpu_serial_section(*, stage_name: str):
    if not _env_enabled("AVATAR_GPU_SERIAL_LOCK_ENABLED", True):
        yield
        return

    lock_path = _avatar_gpu_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    with lock_path.open("a+b") as handle:
        logger.info("Avatar GPU serial lock waiting stage=%s lock_path=%s", stage_name, str(lock_path))
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

            def unlock() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

            def unlock() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            logger.info(
                "Avatar GPU serial lock acquired stage=%s wait_seconds=%s lock_path=%s",
                stage_name,
                round(float(time.monotonic() - started_at), 4),
                str(lock_path),
            )
            yield
        finally:
            unlock()
            logger.info("Avatar GPU serial lock released stage=%s lock_path=%s", stage_name, str(lock_path))


@worker_ready.connect
def _log_avatar_engine_startup_status(sender=None, **kwargs):
    if not _env_enabled("AVATAR_BOOTSTRAP_ON_WORKER_STARTUP", True):
        logger.info("Worker avatar bootstrap skipped by AVATAR_BOOTSTRAP_ON_WORKER_STARTUP=0")
        return

    def _fatal_startup(message: str) -> None:
        logger.critical(message)
        os._exit(70)

    try:
        from . import bootstrap_musetalk

        exit_code = int(bootstrap_musetalk.main())
        if exit_code != 0:
            _fatal_startup(f"Worker bootstrap failed exit_code={exit_code}")
        logger.info("Worker avatar bootstrap ready selected_engine=%s", "liveportrait+musetalk")
    except SystemExit as exc:
        code = int(exc.code or 70)
        _fatal_startup(f"Worker bootstrap exited during startup exit_code={code}")
    except Exception as exc:
        _fatal_startup(f"Worker bootstrap exception: {exc}")


def _avatar_storage_root() -> str:
    return str(Path(os.environ.get("AVATAR_STORAGE_ROOT", STORAGE_ROOT) or STORAGE_ROOT))


def _resolve_storage_path(path_value: str, storage_root: str | Path | None = None) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        return str(path)
    root = Path(storage_root or _avatar_storage_root())
    return str(root / path)


def _safe_rel_path(storage_root: str | Path, absolute_path: str | Path) -> str:
    root = Path(storage_root).resolve()
    target = Path(absolute_path)
    try:
        resolved = target.resolve()
        relative = resolved.relative_to(root)
        return str(relative).replace("\\", "/")
    except Exception:
        return str(target).replace("\\", "/")


def _concise_error_text(error: Any, *, fallback: str = "unknown_error", limit: int = 500) -> str:
    text = str(error or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _workspace(project_id: str | int) -> dict[str, Path]:
    root = Path(STORAGE_ROOT) / str(project_id)
    ws = {
        "root": root,
        "images": root / "images",
        "notes": root / "notes",
        "audio": root / "audio",
        "parts": root / "parts",
        "final": root,
    }
    for path in ws.values():
        path.mkdir(parents=True, exist_ok=True)
    return ws


def _update_job(
    project_id: str | int,
    *,
    status: str | None = None,
    progress: int | None = None,
    result_url: str | None = None,
    srt_url: str | None = None,
    error_message: str | None = None,
) -> None:
    try:
        from core.models import Job
    except Exception:
        logger.warning("_update_job skipped for project=%s because core.models.Job is unavailable", project_id, exc_info=True)
        return

    job = Job.objects.filter(project_id=int(project_id)).order_by("-created_at", "-id").first()
    if job is None:
        return

    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = str(status)
    if progress is not None:
        updates["progress"] = max(0, min(int(progress), 100))
    if result_url is not None:
        updates["result_url"] = str(result_url)
    if srt_url is not None:
        updates["srt_url"] = str(srt_url)
    if error_message is not None:
        updates["error_message"] = str(error_message)
    if updates:
        Job.objects.filter(id=job.id).update(**updates)


def _update_job_progress_floor(project_id: str | int, target_progress: int) -> None:
    """Monotonic progress update helper (never decreases current progress)."""
    try:
        from core.models import Job
    except Exception:
        return

    job = Job.objects.filter(project_id=int(project_id)).order_by("-created_at", "-id").first()
    if job is None:
        return

    target = max(0, min(int(target_progress), 100))
    current = int(getattr(job, "progress", 0) or 0)
    if target <= current:
        return
    Job.objects.filter(id=job.id).update(progress=target)


def _is_job_cancelled(project_id: str | int) -> bool:
    try:
        from core.models import Job
    except Exception:
        return False
    job = Job.objects.filter(project_id=int(project_id)).order_by("-created_at", "-id").first()
    if job is None:
        return False
    status_name = str(getattr(job, "status", "") or "").strip().lower()
    if status_name == "cancelled":
        return True
    if status_name != "failed":
        return False
    return JOB_CANCELLED_MARKER in str(getattr(job, "error_message", "") or "")


def _is_specific_job_cancelled(project_id: str | int, job_id: str | int | None = None) -> bool:
    try:
        from core.models import Job
    except Exception:
        return False
    query = Job.objects.filter(project_id=int(project_id))
    if job_id is not None:
        query = query.filter(id=int(job_id))
    job = query.order_by("-created_at", "-id").first()
    if job is None:
        return False
    status_name = str(getattr(job, "status", "") or "").strip().lower()
    if status_name == "cancelled":
        return True
    if status_name != "failed":
        return False
    return JOB_CANCELLED_MARKER in str(getattr(job, "error_message", "") or "")


def _upsert_job_checkpoint(
    project_id: str | int,
    *,
    stage_name: str,
    stage_status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Upsert a named stage checkpoint for the latest job of the project."""
    try:
        from core.models import Job, JobCheckpoint
    except Exception:
        return

    job = Job.objects.filter(project_id=int(project_id)).order_by("-created_at", "-id").first()
    if job is None:
        return

    clean_stage = str(stage_name or "").strip()[:80]
    clean_status = str(stage_status or "").strip().lower()
    if not clean_stage or clean_status not in {"pending", "running", "done", "failed"}:
        return

    JobCheckpoint.objects.update_or_create(
        job=job,
        stage_name=clean_stage,
        defaults={
            "stage_status": clean_status,
            "payload": dict(payload or {}),
        },
    )


def _checkpoint_status(project_id: str | int, stage_name: str) -> str:
    try:
        from core.models import Job, JobCheckpoint
    except Exception:
        return ""
    job = Job.objects.filter(project_id=int(project_id)).order_by("-created_at", "-id").first()
    if job is None:
        return ""
    row = JobCheckpoint.objects.filter(job=job, stage_name=str(stage_name)).first()
    return str(getattr(row, "stage_status", "") or "").strip().lower()


def _checkpoint_payload(project_id: str | int, stage_name: str) -> dict[str, Any]:
    try:
        from core.models import Job, JobCheckpoint
    except Exception:
        return {}
    job = Job.objects.filter(project_id=int(project_id)).order_by("-created_at", "-id").first()
    if job is None:
        return {}
    row = JobCheckpoint.objects.filter(job=job, stage_name=str(stage_name)).first()
    payload = getattr(row, "payload", {}) if row is not None else {}
    return dict(payload) if isinstance(payload, dict) else {}


def _worker_protection_mode() -> str:
    try:
        from django.conf import settings
    except Exception:
        fallback = "public" if os.environ.get("DEBUG", "0").lower() in {"1", "true", "yes", "on"} else "secure_stream"
        mode = str(os.environ.get("LESSON_PROTECTION_DEFAULT_MODE", fallback) or fallback).strip().lower()
        return mode if mode in {"public", "secure_stream", "drm_protected"} else fallback

    fallback = "public" if bool(getattr(settings, "DEBUG", False)) else "secure_stream"
    mode = str(getattr(settings, "LESSON_PROTECTION_DEFAULT_MODE", fallback) or fallback).strip().lower()
    return mode if mode in {"public", "secure_stream", "drm_protected"} else fallback


def _write_json_sidecar(project_id: str | int, file_name: str, payload: dict[str, Any]) -> str:
    target = Path(STORAGE_ROOT) / str(project_id) / str(file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return str(target)


def _write_language_detection_sidecar(project_id: str | int, payload: dict[str, Any]) -> str:
    return _write_json_sidecar(project_id, "language_detection.json", payload)


def _export_manifest_path(project_id: str | int) -> Path:
    return Path(STORAGE_ROOT) / str(project_id) / "export_manifest.json"


def _write_export_manifest(project_id: str | int, *, slides: list[dict[str, Any]], source_path: str) -> str:
    payload = {
        "project_id": str(project_id),
        "source_path": str(source_path),
        "slides": slides,
        "saved_at": time.time(),
    }
    target = _export_manifest_path(project_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    return str(target)


def _read_export_manifest(project_id: str | int) -> list[dict[str, Any]] | None:
    target = _export_manifest_path(project_id)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None
    slides = payload.get("slides")
    if not isinstance(slides, list):
        return None
    normalized: list[dict[str, Any]] = []
    for row in slides:
        if isinstance(row, dict):
            normalized.append(dict(row))
    return normalized or None


def _summarize_tts_settings(tts_settings: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(tts_settings, dict):
        return {}
    overrides = tts_settings.get("overrides") if isinstance(tts_settings.get("overrides"), dict) else {}
    technical = overrides.get("technical") if isinstance(overrides.get("technical"), dict) else {}
    abbreviation = overrides.get("abbreviation") if isinstance(overrides.get("abbreviation"), dict) else {}
    mixed_word = overrides.get("mixed_word") if isinstance(overrides.get("mixed_word"), dict) else {}
    return {
        "provider_preference": str(tts_settings.get("provider_preference") or "auto"),
        "normalization_enabled": bool(tts_settings.get("normalization_enabled", True)),
        "normalization_mode": str(tts_settings.get("normalization_mode") or "loose"),
        "unknown_word_strategy": str(tts_settings.get("unknown_word_strategy") or "keep"),
        "speech_speed": tts_settings.get("speech_speed", 1.0),
        "volume_gain_db": tts_settings.get("volume_gain_db", 0),
        "pause_seconds": tts_settings.get("pause_seconds"),
        "applied_overrides": {
            "technical_count": len(technical),
            "abbreviation_count": len(abbreviation),
            "mixed_word_count": len(mixed_word),
            "merged_override_count": len({**technical, **abbreviation, **mixed_word}),
        },
    }


def _is_finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _clean_caption_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _normalize_caption_compare(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean_caption_text(value)).strip()


def _page_display_text(page: Any) -> str:
    editor_document = getattr(page, "editor_document", None)
    if isinstance(editor_document, dict):
        paragraphs = editor_document.get("paragraphs")
        if isinstance(paragraphs, list):
            paragraph_text = "\n".join(
                _clean_caption_text(item.get("text"))
                for item in paragraphs
                if isinstance(item, dict) and _clean_caption_text(item.get("text"))
            )
            if paragraph_text.strip():
                return paragraph_text.strip()
        html_text = _clean_caption_text(editor_document.get("text") or editor_document.get("plain_text"))
        if html_text:
            return html_text
    return _clean_caption_text(getattr(page, "narration_text", ""))


def _caption_chunks_match_display(chunks: list[str], display_text: str) -> bool:
    if not display_text:
        return True
    joined = _normalize_caption_compare(" ".join(chunks))
    return joined == _normalize_caption_compare(display_text)


def _safe_subtitle_chunks_for_page(page: Any) -> list[str]:
    raw_chunks = getattr(page, "subtitle_chunks", None)
    if not isinstance(raw_chunks, list):
        raw_chunks = []
    chunks = [
        cleaned
        for cleaned in (
            _clean_caption_text(item)
            for item in raw_chunks
        )
        if cleaned
    ]
    display_text = _page_display_text(page)
    if chunks and _caption_chunks_match_display(chunks, display_text):
        return chunks
    if chunks and not display_text:
        return chunks
    if chunks:
        logger.warning(
            "Subtitle chunks ignored because they do not match display text: project=%s page_key=%s",
            getattr(page, "project_id", None),
            getattr(page, "page_key", ""),
        )
    return []


def _duration_from_value(value: Any) -> float:
    if not _is_finite_number(value):
        return 0.0
    return max(float(value), 0.0)


def _render_duration_for_slide(slide: dict[str, Any], fallback: Any = None) -> float:
    duration = _duration_from_value(slide.get("duration") if isinstance(slide, dict) else None)
    if duration > 0:
        return duration
    return _duration_from_value(fallback)


def _caption_duration_for_slide(slide: dict[str, Any], render_duration: float) -> float:
    """Return the spoken caption window, excluding configured end-of-slide hold time."""
    duration = _duration_from_value(render_duration)
    pause_seconds = _duration_from_value(slide.get("pause_seconds") if isinstance(slide, dict) else None)
    if duration > pause_seconds:
        return max(duration - pause_seconds, 0.05)
    return max(duration, 0.05)


def _build_page_timeline_from_render_results(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from scripts.text_segmentation import allocate_chunk_timings

    page_timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for result in ordered or []:
        duration = _duration_from_value(result.get("duration"))
        caption_duration = _caption_duration_for_slide(result, duration)
        page_start = round(cursor, 3)
        page_end = round(cursor + duration, 3)
        display_fallback = _clean_caption_text(result.get("text") or result.get("original_text") or "")
        raw_subtitle_chunks = result.get("subtitle_chunks") if isinstance(result.get("subtitle_chunks"), list) else []
        subtitle_chunks = [
            chunk
            for chunk in (
                _clean_caption_text(item)
                for item in raw_subtitle_chunks
            )
            if chunk
        ]
        if not subtitle_chunks and display_fallback:
            subtitle_chunks = [display_fallback]

        absolute_chunks: list[dict[str, Any]] = []
        for chunk_index, chunk in enumerate(allocate_chunk_timings(subtitle_chunks, caption_duration)):
            start_abs = max(page_start, min(page_end, cursor + float(chunk.get("start") or 0.0)))
            end_abs = max(page_start, min(page_end, cursor + float(chunk.get("end") or 0.0)))
            text = _clean_caption_text(chunk.get("text") or "")
            if not text or end_abs <= start_abs:
                continue
            absolute_chunks.append(
                {
                    "index": chunk_index,
                    "chunk_index": chunk_index,
                    "start": round(start_abs, 3),
                    "end": round(end_abs, 3),
                    "text": text,
                }
            )

        page_timeline.append(
            {
                "order": result.get("index", 0),
                "page_key": result.get("page_key"),
                "source_slide_index": result.get("source_slide_index", result.get("index", 0)),
                "split_index": result.get("split_index", 0),
                "start": page_start,
                "end": page_end,
                "duration": round(duration, 3),
                "subtitle_chunks": [c.get("text") for c in absolute_chunks],
                "chunk_timeline": absolute_chunks,
            }
        )
        cursor += duration
    return page_timeline


def _build_render_duration_lookup(
    ordered_slides: list[dict[str, Any]] | None,
    ordered_durations: list[float] | None,
) -> tuple[dict[str, float], list[float]]:
    slides = list(ordered_slides or [])
    durations = list(ordered_durations or [])
    by_key: dict[str, float] = {}
    by_position: list[float] = []
    for index, slide in enumerate(slides):
        fallback = durations[index] if index < len(durations) else None
        duration = _render_duration_for_slide(slide, fallback)
        by_position.append(duration)
        page_key = str(slide.get("page_key") or "") if isinstance(slide, dict) else ""
        if page_key and duration > 0:
            by_key[page_key] = duration
    if len(by_position) < len(durations):
        by_position.extend(_duration_from_value(value) for value in durations[len(by_position):])
    return by_key, by_position


def _page_duration_for_cues(
    page: Any,
    position: int,
    duration_by_key: dict[str, float],
    duration_by_position: list[float],
) -> float:
    page_key = str(getattr(page, "page_key", "") or "")
    if page_key in duration_by_key and duration_by_key[page_key] > 0:
        return duration_by_key[page_key]
    if position < len(duration_by_position) and duration_by_position[position] > 0:
        return duration_by_position[position]
    page_duration = _duration_from_value(getattr(page, "duration_seconds", None))
    if page_duration > 0:
        return page_duration
    page_start = getattr(page, "start_seconds", None)
    page_end = getattr(page, "end_seconds", None)
    if _is_finite_number(page_start) and _is_finite_number(page_end):
        return max(float(page_end) - float(page_start), 0.0)
    return 0.05


def _page_boundaries_for_cues(page: Any, fallback_start: float, duration: float) -> tuple[float, float]:
    page_start = getattr(page, "start_seconds", None)
    page_end = getattr(page, "end_seconds", None)
    if _is_finite_number(page_start) and _is_finite_number(page_end):
        start = max(float(page_start), 0.0)
        end = max(float(page_end), start)
        if end > start:
            return round(start, 3), round(end, 3)
    start = max(float(fallback_start or 0.0), 0.0)
    end = start + max(float(duration or 0.0), 0.05)
    return round(start, 3), round(end, 3)


def _cue_from_values(
    *,
    page_key: str,
    chunk_index: int,
    start: float,
    end: float,
    text: str,
    source: str,
) -> dict[str, Any] | None:
    cleaned = _clean_caption_text(text)
    if not cleaned:
        return None
    start = max(float(start), 0.0)
    end = max(float(end), 0.0)
    if end <= start:
        return None
    return {
        "page_key": str(page_key or ""),
        "chunk_index": int(chunk_index),
        "start": round(start, 3),
        "end": round(end, 3),
        "text": cleaned,
        "source": source,
    }


def _distributed_subtitle_cues(
    *,
    page: Any,
    page_start: float,
    page_end: float,
    source: str,
) -> list[dict[str, Any]]:
    from scripts.text_segmentation import allocate_chunk_timings

    page_key = str(getattr(page, "page_key", "") or "")
    chunks = _safe_subtitle_chunks_for_page(page)
    display_text = _page_display_text(page)
    if not chunks and display_text:
        chunks = [display_text]
        source = "page_fallback"
    if not chunks:
        return []

    duration = max(page_end - page_start, 0.05)
    cues: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(allocate_chunk_timings(chunks, duration)):
        start = max(page_start, min(page_end, page_start + float(chunk.get("start") or 0.0)))
        end = max(page_start, min(page_end, page_start + float(chunk.get("end") or 0.0)))
        cue = _cue_from_values(
            page_key=page_key,
            chunk_index=chunk_index,
            start=start,
            end=end,
            text=chunk.get("text") or "",
            source=source,
        )
        if cue is not None:
            cues.append(cue)
    return cues


def _validated_chunk_timeline_cues(page: Any, page_start: float, page_end: float) -> list[dict[str, Any]] | None:
    raw_timeline = getattr(page, "chunk_timeline", None)
    if not isinstance(raw_timeline, list) or not raw_timeline:
        return None

    page_key = str(getattr(page, "page_key", "") or "")
    safe_chunks = _safe_subtitle_chunks_for_page(page)
    display_text = _page_display_text(page)
    if safe_chunks and len(safe_chunks) != len(raw_timeline):
        return None
    cues: list[dict[str, Any]] = []
    previous_end: float | None = None
    epsilon = 0.001

    for position, item in enumerate(raw_timeline):
        if not isinstance(item, dict):
            return None
        if not _is_finite_number(item.get("start")) or not _is_finite_number(item.get("end")):
            return None
        start = float(item.get("start"))
        end = float(item.get("end"))
        if start < 0 or end < 0 or end <= start:
            return None
        if start < page_start - epsilon or end > page_end + epsilon:
            return None
        start = max(page_start, min(page_end, start))
        end = max(page_start, min(page_end, end))
        if end <= start:
            return None
        if previous_end is not None and start < previous_end - epsilon:
            return None

        raw_index = item.get("chunk_index", item.get("index", position))
        chunk_index = int(raw_index) if _is_finite_number(raw_index) else position
        timeline_text = _clean_caption_text(item.get("text") or "")
        if safe_chunks and position < len(safe_chunks):
            text = safe_chunks[position]
        elif timeline_text:
            text = timeline_text
        elif display_text and len(raw_timeline) == 1:
            text = display_text
        else:
            return None

        cue = _cue_from_values(
            page_key=page_key,
            chunk_index=chunk_index,
            start=start,
            end=end,
            text=text,
            source="chunk_timeline",
        )
        if cue is None:
            return None
        cues.append(cue)
        previous_end = end

    if display_text and not safe_chunks:
        joined = _normalize_caption_compare(" ".join(cue["text"] for cue in cues))
        if joined != _normalize_caption_compare(display_text):
            return None
    return cues


def _fallback_cues_from_render_results(
    ordered_slides: list[dict[str, Any]] | None,
    ordered_durations: list[float] | None,
) -> list[dict[str, Any]]:
    from scripts.text_segmentation import allocate_chunk_timings

    slides = list(ordered_slides or [])
    durations = list(ordered_durations or [])
    cues: list[dict[str, Any]] = []
    cursor = 0.0
    for slide_index, slide in enumerate(slides):
        duration = _render_duration_for_slide(
            slide,
            durations[slide_index] if slide_index < len(durations) else None,
        )
        caption_duration = _caption_duration_for_slide(slide, duration)
        page_start = round(cursor, 3)
        page_end = round(cursor + max(duration, 0.05), 3)
        page_key = str(slide.get("page_key") or "")
        raw_subtitle_chunks = slide.get("subtitle_chunks") if isinstance(slide.get("subtitle_chunks"), list) else []
        chunks = [
            chunk
            for chunk in (_clean_caption_text(item) for item in raw_subtitle_chunks)
            if chunk
        ]
        source = "distributed_chunks"
        if not chunks:
            fallback_text = _clean_caption_text(slide.get("text") or slide.get("original_text") or slide.get("notes_text") or "")
            chunks = [fallback_text] if fallback_text else []
            source = "page_fallback"
        for chunk_index, chunk in enumerate(allocate_chunk_timings(chunks, caption_duration)):
            cue = _cue_from_values(
                page_key=page_key,
                chunk_index=chunk_index,
                start=page_start + float(chunk.get("start") or 0.0),
                end=min(page_end, page_start + float(chunk.get("end") or 0.0)),
                text=chunk.get("text") or "",
                source=source,
            )
            if cue is not None:
                cues.append(cue)
        cursor += max(duration, 0.05)
    return sorted(cues, key=lambda cue: (float(cue["start"]), str(cue["page_key"]), int(cue["chunk_index"])))


def build_subtitle_cues_from_transcript_pages(
    project_id: str | int,
    ordered_slides: list[dict[str, Any]] | None,
    ordered_durations: list[float] | None,
) -> list[dict[str, Any]]:
    """
    Build canonical original-language subtitle cues from active TranscriptPage rows.

    Caption text is sourced from editor/display transcript fields and subtitle
    chunks only. Spoken/provider-normalized TTS text is intentionally ignored.
    """
    try:
        from core.models import Project, TranscriptPage
    except Exception:
        logger.warning("Subtitle cue builder falling back to render results because core models are unavailable", exc_info=True)
        return _fallback_cues_from_render_results(ordered_slides, ordered_durations)

    try:
        project = Project.objects.filter(pk=int(project_id)).first()
    except Exception:
        logger.warning(
            "Subtitle cue builder falling back to render results because project=%s could not be loaded",
            project_id,
            exc_info=True,
        )
        return _fallback_cues_from_render_results(ordered_slides, ordered_durations)
    if project is None:
        logger.warning("Subtitle cue builder falling back to render results because project=%s was not found", project_id)
        return _fallback_cues_from_render_results(ordered_slides, ordered_durations)

    try:
        pages = list(
            TranscriptPage.objects.filter(
                project=project,
                is_active=True,
                deleted_at__isnull=True,
            ).order_by("order", "id")
        )
    except Exception:
        logger.warning(
            "Subtitle cue builder falling back to render results because TranscriptPage rows could not be loaded: project=%s",
            project_id,
            exc_info=True,
        )
        return _fallback_cues_from_render_results(ordered_slides, ordered_durations)
    if not pages:
        logger.warning("Subtitle cue builder falling back to render results because project=%s has no active transcript pages", project_id)
        return _fallback_cues_from_render_results(ordered_slides, ordered_durations)

    duration_by_key, duration_by_position = _build_render_duration_lookup(ordered_slides, ordered_durations)
    cues: list[dict[str, Any]] = []
    cursor = 0.0
    for position, page in enumerate(pages):
        duration = _page_duration_for_cues(page, position, duration_by_key, duration_by_position)
        page_start, page_end = _page_boundaries_for_cues(page, cursor, duration)
        timeline_cues = _validated_chunk_timeline_cues(page, page_start, page_end)
        if timeline_cues:
            page_cues = timeline_cues
        else:
            distributed = _distributed_subtitle_cues(
                page=page,
                page_start=page_start,
                page_end=page_end,
                source="distributed_chunks",
            )
            page_cues = distributed
            source = distributed[0]["source"] if distributed else "empty"
            logger.info(
                "Subtitle cue builder used fallback source=%s project=%s page_key=%s",
                source,
                project_id,
                getattr(page, "page_key", ""),
            )
        cues.extend(page_cues)
        cursor = max(cursor, page_end)

    return sorted(cues, key=lambda cue: (float(cue["start"]), int(cue["chunk_index"])))


def _write_playback_sidecar(project_id: str | int, payload: dict[str, Any]) -> str:
    return _write_json_sidecar(project_id, "playback_assets.json", payload)


def _detect_language_from_slides(slides: list[dict[str, Any]], *, lang_hint: str = "auto") -> dict[str, Any]:
    supported_languages = ["en", "tr"]
    hint = str(lang_hint or "auto").strip().lower()
    if hint in supported_languages:
        return {
            "detected_language": hint,
            "resolved_language": hint,
            "source": "explicit_hint",
            "confidence": 1.0,
            "fallback_used": False,
            "supported_languages": supported_languages,
            "detector": "placeholder_v1",
        }

    sample = "\n".join(
        str(item.get("narration_text") or item.get("notes_text") or item.get("text") or "")
        for item in (slides or [])
        if isinstance(item, dict)
    ).strip().lower()[:7000]
    if not sample:
        return {
            "detected_language": "en",
            "resolved_language": "en",
            "source": "fallback_empty_text",
            "confidence": 0.0,
            "fallback_used": True,
            "supported_languages": supported_languages,
            "detector": "placeholder_v1",
        }

    tr_chars = sum(1 for ch in sample if ch in "çğıöşü")
    tr_words = sum(1 for token in [" ve ", " için ", " bir ", " bu ", " ile ", " olarak ", " değil "] if token in f" {sample} ")
    en_words = sum(1 for token in [" the ", " and ", " with ", " for ", " of ", " is ", " are "] if token in f" {sample} ")

    if (tr_chars + tr_words) > en_words:
        resolved = "tr"
        confidence = 0.84 if (tr_chars + tr_words) >= 2 else 0.56
    else:
        resolved = "en"
        confidence = 0.84 if en_words >= 2 else 0.56

    return {
        "detected_language": resolved,
        "resolved_language": resolved,
        "source": "text_heuristic",
        "confidence": round(float(confidence), 2),
        "fallback_used": False,
        "supported_languages": supported_languages,
        "detector": "placeholder_v1",
    }


def _prepare_narration_for_tts(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    lines = [line.strip() for line in value.split("\n")]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned).strip()


def _load_font(size: int) -> Any:
    if ImageFont is None:
        return None
    candidates = [
        "DejaVuSans.ttf",
        "arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _wrap_text_lines(text: str, width: int = 48) -> list[str]:
    normalized = _prepare_narration_for_tts(text)
    if not normalized:
        return []
    lines: list[str] = []
    for paragraph in normalized.split("\n"):
        wrapped = textwrap.wrap(paragraph, width=width) or [""]
        lines.extend(wrapped)
    return lines


def _make_whiteboard_image(text: str, output_path: str) -> str:
    if Image is None or ImageDraw is None:
        raise RuntimeError("whiteboard_render_requires_pillow")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1600, 900), color="white")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(42)
    body_font = _load_font(32)
    draw.text((80, 60), "Whiteboard", fill="black", font=title_font)
    y = 150
    for line in _wrap_text_lines(text, width=52):
        draw.text((90, y), line, fill="black", font=body_font)
        y += 44
        if y > 820:
            break
    image.save(output, format="PNG")
    return str(output)


def _render_transcript_overlay_image(base_image_path: str, narration_text: str, rich_text_html: str, output_path: str) -> str:
    source = Path(base_image_path)
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"transcript_overlay_source_missing:{source}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if Image is None or ImageDraw is None:
        shutil.copy2(str(source), str(output))
        return str(output)

    text = _prepare_narration_for_tts(narration_text)
    if not text and rich_text_html:
        text = _prepare_narration_for_tts(re.sub(r"<[^>]+>", " ", rich_text_html))
    if not text:
        shutil.copy2(str(source), str(output))
        return str(output)

    image = Image.open(source).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(28)
    lines = _wrap_text_lines(text, width=58)[:6]
    box_height = 80 + (len(lines) * 40)
    box_top = max(image.size[1] - box_height - 40, 20)
    draw.rounded_rectangle(
        [(40, box_top), (image.size[0] - 40, image.size[1] - 30)],
        radius=24,
        fill=(0, 0, 0, 170),
    )
    y = box_top + 24
    for line in lines:
        draw.text((70, y), line, fill=(255, 255, 255, 255), font=font)
        y += 36

    combined = Image.alpha_composite(image, overlay).convert("RGB")
    combined.save(output, format="PNG")
    return str(output)


def _render_avatar_safe_slide_image(source_image_path: str, output_path: str) -> str:
    source = Path(source_image_path)
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"avatar_safe_slide_source_missing:{source}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(output))
    return str(output)


def _sync_transcript_pages_from_export(project_id: str | int, slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from core.models import Project, TranscriptPage
    except Exception:
        logger.warning("Transcript page sync skipped for project=%s because core models are unavailable", project_id, exc_info=True)
        return slides

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return slides

    existing_pages = list(TranscriptPage.objects.filter(project=project).order_by("order", "id"))
    active_existing = {
        page.page_key: page
        for page in existing_pages
        if getattr(page, "is_active", True) and getattr(page, "deleted_at", None) is None
    }
    inactive_keys = {
        page.page_key
        for page in existing_pages
        if not getattr(page, "is_active", True) or getattr(page, "deleted_at", None) is not None
    }
    payload_by_key: dict[str, dict[str, Any]] = {}
    source_templates: dict[int, dict[str, Any]] = {}
    first_template: dict[str, Any] = {}

    for order, slide in enumerate(slides or []):
        slide_payload = dict(slide)
        source_index = int(slide_payload.get("source_slide_index") or slide_payload.get("index") or order)
        source_templates.setdefault(source_index, slide_payload)
        if not first_template:
            first_template = slide_payload

        page_key = str(slide_payload.get("page_key") or f"s{int(slide_payload.get('source_slide_num') or slide_payload.get('slide_num') or order + 1)}-p{int(slide_payload.get('split_index') or 0) + 1}")
        if page_key in inactive_keys:
            logger.info("Skipping inactive transcript page during sync: project=%s page_key=%s", project_id, page_key)
            continue

        original_text = str(slide_payload.get("original_text") or slide_payload.get("notes_text") or "")
        page = active_existing.get(page_key)
        is_new_page = page is None
        if page is None:
            page = TranscriptPage(project=project, page_key=page_key)

        if is_new_page:
            page.order = int(slide_payload.get("index") or order)
        page.source_slide_index = int(slide_payload.get("source_slide_index") or slide_payload.get("index") or order)
        page.split_index = int(slide_payload.get("split_index") or 0)
        if hasattr(page, "is_active"):
            page.is_active = True
        if hasattr(page, "deleted_at"):
            page.deleted_at = None
        if is_new_page or not str(page.original_text or "").strip():
            page.original_text = original_text
        if not str(page.narration_text or "").strip():
            page.narration_text = str(slide_payload.get("narration_text") or original_text)
        if not str(page.rich_text_html or "").strip():
            page.rich_text_html = str(slide_payload.get("rich_text_html") or "")
        if not dict(page.editor_document or {}):
            page.editor_document = dict(slide_payload.get("editor_document") or {})
        subtitle_source = str(page.narration_text or page.original_text or original_text)
        subtitle_chunks = list(slide_payload.get("subtitle_chunks") or ([subtitle_source] if subtitle_source else []))
        if not list(page.subtitle_chunks or []):
            page.subtitle_chunks = subtitle_chunks
        if is_new_page and not bool(page.whiteboard_mode):
            page.whiteboard_mode = bool(slide_payload.get("whiteboard_mode"))
        page.save()

        payload_by_key[page_key] = slide_payload

    ws = _workspace(project_id)
    updated_slides: list[dict[str, Any]] = []
    # Structural rerenders use persisted active transcript rows as the render sequence.
    # This is not raw export order; inactive/deleted transcript pages are skipped above.
    active_pages = list(
        TranscriptPage.objects.filter(project=project, is_active=True, deleted_at__isnull=True).order_by("order", "id")
    )
    for display_index, page in enumerate(active_pages):
        template = payload_by_key.get(page.page_key)
        if template is None:
            template = source_templates.get(int(page.source_slide_index), first_template)
        slide_payload = dict(template or {})
        subtitle_source = str(page.narration_text or page.original_text or slide_payload.get("notes_text") or "")
        slide_payload.update(
            {
                "index": display_index,
                "slide_num": display_index + 1,
                "source_slide_index": int(page.source_slide_index or 0),
                "source_slide_num": int(page.source_slide_index or 0) + 1,
                "split_index": int(page.split_index or 0),
                "page_key": str(page.page_key or ""),
                "notes_text": str(page.original_text or slide_payload.get("notes_text") or subtitle_source),
                "original_text": str(page.original_text or slide_payload.get("original_text") or ""),
                "narration_text": str(page.narration_text or subtitle_source),
                "rich_text_html": str(page.rich_text_html or ""),
                "editor_document": dict(page.editor_document or {}),
                "subtitle_chunks": list(page.subtitle_chunks or ([subtitle_source] if subtitle_source else [])),
                "whiteboard_mode": bool(page.whiteboard_mode),
                "audio_out": str(ws["audio"] / f"slide_{display_index + 1:03d}.mp3"),
                "part_out": str(ws["parts"] / f"part_{display_index + 1:03d}.mp4"),
            }
        )
        updated_slides.append(slide_payload)

    return updated_slides


def _update_transcript_timeline(project_id: str | int, page_timeline: list[dict[str, Any]]) -> None:
    try:
        from core.models import Project, TranscriptPage
    except Exception:
        logger.warning("Transcript timeline update skipped for project=%s because core models are unavailable", project_id, exc_info=True)
        return

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return

    existing = {page.page_key: page for page in TranscriptPage.objects.filter(project=project)}
    for item in page_timeline or []:
        page_key = str(item.get("page_key") or "")
        if not page_key:
            continue
        page = existing.get(page_key)
        if page is not None and (not bool(getattr(page, "is_active", True)) or getattr(page, "deleted_at", None) is not None):
            continue
        if page is None:
            page = TranscriptPage(
                project=project,
                page_key=page_key,
                order=int(item.get("order") or 0),
                source_slide_index=int(item.get("source_slide_index") or 0),
                split_index=int(item.get("split_index") or 0),
            )
        page.order = int(item.get("order") or 0)
        page.source_slide_index = int(item.get("source_slide_index") or 0)
        page.split_index = int(item.get("split_index") or 0)
        page.is_active = True
        page.deleted_at = None
        page.start_seconds = float(item.get("start") or 0.0)
        page.end_seconds = float(item.get("end") or 0.0)
        page.duration_seconds = float(item.get("duration") or 0.0)
        page.chunk_timeline = list(item.get("chunk_timeline") or [])
        page.save()


def _sync_lesson_segments(project_id: str | int, ordered: list[dict[str, Any]]) -> None:
    try:
        from core.models import LessonSegment, Project
    except Exception:
        logger.warning("Lesson segment sync skipped for project=%s because core models are unavailable", project_id, exc_info=True)
        return

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return

    existing = {segment.segment_order: segment for segment in LessonSegment.objects.filter(project=project)}
    touched_orders: set[int] = set()
    for item in ordered or []:
        order = int(item.get("index") or 0)
        segment = existing.get(order)
        if segment is None:
            segment = LessonSegment(project=project, segment_order=order)
        segment.segment_text = str(item.get("text") or "")
        segment.segment_slide_path = _safe_rel_path(STORAGE_ROOT, str(item.get("slide_path") or "")) if item.get("slide_path") else ""
        segment.segment_tts_path = _safe_rel_path(STORAGE_ROOT, str(item.get("tts_audio_path") or "")) if item.get("tts_audio_path") else ""
        segment.segment_avatar_path = str(item.get("avatar_segment_rel_path") or "")
        segment.segment_pause_seconds = float(item.get("pause_seconds") or 0.0)
        segment.status = "ready" if item.get("part_path") else "failed"
        segment.error_message = str(item.get("avatar_failure_reason") or "")
        segment.save()
        touched_orders.add(order)

    stale_orders = [order for order in existing.keys() if order not in touched_orders]
    if stale_orders:
        LessonSegment.objects.filter(project=project, segment_order__in=stale_orders).delete()


def _record_avatar_render_job(
    *,
    lesson_id: int,
    teacher_id: int,
    source_image_hash: str,
    tts_audio_hash: str,
    lesson_text_hash: str,
    slide_hash: str,
    engine_used: str,
    render_status: str,
    render_error: str,
    output_path: str,
    fallback_chain_used: list[str],
    metadata: dict[str, Any],
) -> None:
    try:
        from django.contrib.auth.models import User
        from core.models import AvatarRenderJob, Project
    except Exception:
        logger.warning("Avatar render telemetry skipped for lesson=%s teacher=%s", lesson_id, teacher_id, exc_info=True)
        return

    lesson = Project.objects.filter(pk=int(lesson_id)).first()
    teacher = User.objects.filter(pk=int(teacher_id)).first()
    if lesson is None or teacher is None:
        return

    AvatarRenderJob.objects.create(
        lesson=lesson,
        teacher=teacher,
        avatar_version=str((metadata or {}).get("avatar_version") or "liveportrait+musetalk:v1"),
        source_image_hash=str(source_image_hash or ""),
        tts_audio_hash=str(tts_audio_hash or ""),
        lesson_text_hash=str(lesson_text_hash or ""),
        slide_hash=str(slide_hash or ""),
        engine_used=str(engine_used or "none"),
        render_status=str(render_status or "pending"),
        render_error=str(render_error or ""),
        output_path=str(output_path or ""),
        fallback_chain_used=list(fallback_chain_used or []),
        metadata=dict(metadata or {}),
    )


def _get_teacher_avatar_config(teacher_id: int | None) -> dict[str, Any]:
    if not teacher_id:
        return {"enabled": False}

    try:
        from avatar.canonical_adapters import normalize_avatar_engine
        from core.avatar_source_validation import refresh_avatar_source_validation, stored_avatar_source_state
        from core.models import UserProfile
    except Exception:
        logger.warning("Teacher avatar config lookup skipped for teacher=%s because dependencies are unavailable", teacher_id, exc_info=True)
        return {"enabled": False}

    profile = UserProfile.objects.filter(user_id=int(teacher_id)).first()
    if profile is None:
        return {"enabled": False}

    processed_rel_path = str(profile.avatar_image_processed or "").strip()
    original_rel_path = str(profile.avatar_image_original or processed_rel_path or "").strip()
    video_rel_path = str(profile.avatar_video_processed or profile.avatar_video_original or "").strip()
    reference_type = str(profile.avatar_reference_type or ("video" if video_rel_path and not original_rel_path else "image")).strip().lower()
    if reference_type not in {"image", "video"}:
        reference_type = "image"

    storage_root = Path(_avatar_storage_root())
    try:
        source_state = stored_avatar_source_state(profile, storage_root=storage_root)
        if (processed_rel_path or original_rel_path or video_rel_path) and not bool(source_state.get("validation_current")):
            source_state = refresh_avatar_source_validation(profile, storage_root=storage_root, persist=True)
    except Exception as exc:
        logger.warning("Teacher avatar source validation lookup failed teacher=%s", teacher_id, exc_info=True)
        source_state = {
            "valid": False,
            "error": str(exc or "avatar_source_validation_failed"),
            "source_hash": "",
            "preview_stale": False,
        }

    enabled = bool(
        profile.avatar_enabled
        and profile.avatar_consent_confirmed
        and (processed_rel_path or original_rel_path or video_rel_path)
    )

    return {
        "enabled": enabled,
        "processed_rel_path": processed_rel_path,
        "source_rel_path": original_rel_path,
        "video_rel_path": video_rel_path,
        "reference_type": reference_type,
        "motion_preset": str(profile.avatar_motion_preset or "natural"),
        "quality_preset": str(profile.avatar_quality_preset or "high"),
        "lipsync_engine": normalize_avatar_engine(
            os.environ.get("AVATAR_ENGINE")
            or profile.avatar_lipsync_engine
            or profile.avatar_engine_primary
        ),
        "model_version": str(profile.avatar_model_version or "liveportrait+musetalk:v1"),
        "avatar_source_valid": bool(source_state.get("valid")),
        "avatar_source_validation_error": str(source_state.get("error") or profile.avatar_source_validation_error or ""),
        "avatar_source_hash": str(source_state.get("source_hash") or profile.avatar_source_hash or ""),
        "avatar_preview_stale": bool(source_state.get("preview_stale")),
        "avatar_preview_source_hash": str(source_state.get("preview_source_hash") or profile.avatar_preview_source_hash or ""),
    }


@app.task(bind=True, name="worker.tasks.render_avatar_segment")
def render_avatar_segment(
    self,
    project_id: int | None = None,
    teacher_id: int | None = None,
    slide_index: int = 0,
    audio_path: str = "",
    output_path: str = "",
    source_image_rel_path: str = "",
    source_image_original_rel_path: str = "",
    source_video_rel_path: str = "",
    avatar_reference_type: str = "image",
    motion_preset: str = "natural",
    quality_preset: str = "high",
    lipsync_engine: str = "",
    cache_text_hash: str = "",
) -> dict[str, Any]:
    from avatar.canonical_adapters import normalize_avatar_engine
    from avatar.hashing import sha256_file
    from avatar.pipeline import AvatarRenderRequest, render_avatar_segment_local

    storage_root = Path(_avatar_storage_root())
    audio_abs = _resolve_storage_path(audio_path, storage_root)
    output_abs = _resolve_storage_path(output_path, storage_root)
    source_image_abs = _resolve_storage_path(source_image_rel_path, storage_root)
    source_image_original_abs = _resolve_storage_path(source_image_original_rel_path, storage_root) or source_image_abs
    source_video_abs = _resolve_storage_path(source_video_rel_path, storage_root)

    reference_type = str(avatar_reference_type or "image").strip().lower()
    if reference_type not in {"image", "video"}:
        reference_type = "image"

    if not audio_abs or not Path(audio_abs).exists():
        raise RuntimeError(f"avatar_render_audio_missing:{audio_abs}")
    if reference_type == "video":
        if not source_video_abs or not Path(source_video_abs).exists():
            raise RuntimeError(f"avatar_render_source_missing:video:{source_video_abs}")
    else:
        if not ((source_image_original_abs and Path(source_image_original_abs).exists()) or (source_image_abs and Path(source_image_abs).exists())):
            raise RuntimeError(
                f"avatar_render_source_missing:image:processed={source_image_abs},original={source_image_original_abs}"
            )
    Path(output_abs).parent.mkdir(parents=True, exist_ok=True)

    request = AvatarRenderRequest(
        source_image_path=(source_image_abs or source_image_original_abs),
        source_image_original_path=(source_image_original_abs or source_image_abs),
        source_video_path=source_video_abs,
        avatar_reference_type=reference_type,
        audio_path=audio_abs,
        output_path=output_abs,
        motion_preset=str(motion_preset or "natural"),
        quality_preset=str(quality_preset or "high"),
        lipsync_engine=normalize_avatar_engine(lipsync_engine or os.environ.get("AVATAR_ENGINE")),
        cache_text_hash=str(cache_text_hash or ""),
    )

    logger.info(
        "Avatar segment dispatch project_id=%s teacher_id=%s slide_index=%s source_image_path=%s source_image_original_path=%s source_video_path=%s audio_path=%s output_path=%s text_hash=%s requested_engine=%s",
        int(project_id or 0),
        int(teacher_id or 0),
        int(slide_index or 0),
        request.source_image_path,
        request.source_image_original_path,
        request.source_video_path,
        request.audio_path,
        request.output_path,
        request.cache_text_hash,
        request.lipsync_engine,
    )

    with _avatar_gpu_serial_section(stage_name="render_avatar_segment"):
        render_info = render_avatar_segment_local(request)
    render_info["project_id"] = int(project_id) if project_id is not None else None
    render_info["teacher_id"] = int(teacher_id) if teacher_id is not None else None
    render_info["slide_index"] = int(slide_index or 0)
    render_info["avatar_reference_type"] = reference_type
    render_info["source_image_hash"] = sha256_file(source_image_abs) if source_image_abs and Path(source_image_abs).exists() else ""
    render_info["source_image_original_hash"] = sha256_file(source_image_original_abs) if source_image_original_abs and Path(source_image_original_abs).exists() else ""
    render_info["source_video_hash"] = sha256_file(source_video_abs) if source_video_abs and Path(source_video_abs).exists() else ""
    logger.info(
        "Avatar segment completed project_id=%s teacher_id=%s slide_index=%s output_path=%s engine_used=%s preview_status=%s",
        int(project_id or 0),
        int(teacher_id or 0),
        int(slide_index or 0),
        str(render_info.get("output_path") or output_abs),
        str(render_info.get("engine_used") or ""),
        str(render_info.get("preview_status") or ""),
    )
    return render_info


@app.task(
    bind=True,
    name="worker.tasks.render_avatar_preview",
    soft_time_limit=_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS,
    time_limit=_PREVIEW_TASK_HARD_TIMEOUT_SECONDS,
)
def render_avatar_preview(self, *, teacher_id: int, job_id: int | None = None) -> dict[str, Any]:
    """Render avatar preview asynchronously through the canonical preview flow only."""
    from worker.avatar_preview_flow import render_avatar_preview_canonical

    with _avatar_gpu_serial_section(stage_name="render_avatar_preview"):
        return render_avatar_preview_canonical(self, teacher_id=teacher_id, job_id=job_id)


@app.task(bind=True, name="worker.tasks.fallback_avatar_render")
def fallback_avatar_render(self, source_image_path: str, audio_path: str, output_path: str) -> dict[str, Any]:
    raise RuntimeError(
        "fallback_avatar_render_disabled: use the canonical current-upload avatar pipeline only"
    )


@app.task(bind=True, name="worker.tasks.render_avatar_lesson")
def render_avatar_lesson(self, project_id: int, teacher_id: int, segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Batch render all avatar segments for a lesson (idempotent by deterministic output paths)."""
    outputs: list[dict[str, Any]] = []
    avatar_cfg = _get_teacher_avatar_config(int(teacher_id))
    if not avatar_cfg.get("enabled"):
        return {"project_id": int(project_id), "teacher_id": int(teacher_id), "segments": [], "status": "skipped"}

    for segment in segments:
        result = render_avatar_segment.apply(
            kwargs={
                "project_id": int(project_id),
                "teacher_id": int(teacher_id),
                "slide_index": int(segment.get("slide_index") or 0),
                "audio_path": str(segment["audio_path"]),
                "output_path": str(segment["output_path"]),
                "source_image_rel_path": str(avatar_cfg.get("processed_rel_path")),
                "source_image_original_rel_path": str(avatar_cfg.get("source_rel_path") or avatar_cfg.get("processed_rel_path") or ""),
                "source_video_rel_path": str(avatar_cfg.get("video_rel_path") or ""),
                "avatar_reference_type": str(avatar_cfg.get("reference_type") or "image"),
                "motion_preset": str(avatar_cfg.get("motion_preset") or "natural"),
                "quality_preset": str(avatar_cfg.get("quality_preset") or "high"),
                "lipsync_engine": str(avatar_cfg.get("lipsync_engine") or "musetalk"),
                "cache_text_hash": str(segment.get("text_hash") or ""),
            }
        ).result
        outputs.append(result)
    return {"project_id": int(project_id), "teacher_id": int(teacher_id), "segments": outputs, "status": "done"}


@app.task(bind=True, name="worker.tasks.avatar_cache_cleanup")
def avatar_cache_cleanup(self, days: int = 30) -> dict[str, Any]:
    """Delete stale avatar cache folders older than *days* from local storage."""
    storage_root = Path(_avatar_storage_root())
    avatars_root = storage_root / "avatars"
    if not avatars_root.exists():
        return {"status": "noop", "removed": 0}

    cutoff_seconds = max(int(days), 1) * 24 * 60 * 60
    now = int(time.time())
    removed = 0
    for path in avatars_root.rglob("*"):
        if not path.is_dir():
            continue
        try:
            age = now - int(path.stat().st_mtime)
            if age > cutoff_seconds and any(path.iterdir()):
                for child in path.rglob("*"):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                removed += 1
        except Exception:
            logger.warning("avatar_cache_cleanup failed for %s", path, exc_info=True)
    return {"status": "ok", "removed": removed}


@app.task(bind=True, name="worker.tasks.cleanup_avatar_cache")
def cleanup_avatar_cache(self, days: int = 30) -> dict[str, Any]:
    """Alias task name for compatibility with requested task contract."""
    return avatar_cache_cleanup.apply(args=[days]).result


@app.task(bind=True, name="worker.tasks.cleanup_cancelled_project_artifacts")
def cleanup_cancelled_project_artifacts(
    self,
    project_id: str | int,
    job_id: str | int | None = None,
) -> dict[str, Any]:
    """
    Best-effort cleanup for cancelled render jobs.

    Deletes transient per-render artifacts while preserving stable export/cache
    assets that can accelerate future rerenders.
    """
    if not _is_specific_job_cancelled(project_id, job_id):
        return {"status": "skipped", "project_id": int(project_id), "reason": "job_not_cancelled"}

    ws = _workspace(project_id)
    removed_files = 0
    removed_dirs = 0

    # Safe deletion scope: generated audio + part clips + temporary overlay artifacts.
    candidate_dirs = [ws["audio"], ws["parts"], ws["root"] / "avatar_segments"]
    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        if directory.name in {"audio", "parts", "avatar_segments"}:
            try:
                shutil.rmtree(directory)
                removed_dirs += 1
            except Exception:
                logger.warning("cleanup_cancelled_project_artifacts failed to remove dir=%s", directory, exc_info=True)

    # Remove known temp manifests from cancelled run only.
    for file_name in ["export_manifest.json"]:
        target = ws["root"] / file_name
        if target.exists() and target.is_file():
            try:
                target.unlink()
                removed_files += 1
            except Exception:
                logger.warning("cleanup_cancelled_project_artifacts failed to remove file=%s", target, exc_info=True)

    _upsert_job_checkpoint(
        project_id,
        stage_name="cleanup_cancelled",
        stage_status="done",
        payload={
            "removed_dirs": removed_dirs,
            "removed_files": removed_files,
            "job_id": int(job_id) if job_id is not None else None,
        },
    )
    return {
        "status": "done",
        "project_id": int(project_id),
        "removed_dirs": removed_dirs,
        "removed_files": removed_files,
    }


@app.task(bind=True, name="worker.tasks.cleanup_orphan_render_artifacts")
def cleanup_orphan_render_artifacts(
    self,
    *,
    min_age_hours: int = 6,
) -> dict[str, Any]:
    """
    Periodic janitor for orphaned render artifacts left by crashes/restarts.

    Safety rules:
    - never touches projects with active jobs (pending/running)
    - only removes known transient files under project work dirs
    """
    threshold_seconds = max(int(min_age_hours), 1) * 3600
    now = time.time()
    removed_files = 0
    scanned_projects = 0
    skipped_active_projects = 0

    try:
        from core.models import Job
    except Exception:
        Job = None

    active_project_ids: set[int] = set()
    if Job is not None:
        try:
            active_project_ids = set(
                int(pid)
                for pid in Job.objects.filter(status__in=["pending", "running"]).values_list("project_id", flat=True)
                if pid is not None
            )
        except Exception:
            active_project_ids = set()

    root = Path(STORAGE_ROOT)
    if not root.exists() or not root.is_dir():
        return {
            "status": "noop",
            "reason": "storage_root_missing",
            "removed_files": 0,
            "scanned_projects": 0,
            "skipped_active_projects": 0,
        }

    transient_suffixes = (
        ".overlay.png",
        ".whiteboard.png",
        ".avatar-safe.png",
    )

    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        project_id_raw = str(project_dir.name).strip()
        if not project_id_raw.isdigit():
            continue
        scanned_projects += 1
        project_id = int(project_id_raw)
        if project_id in active_project_ids:
            skipped_active_projects += 1
            continue

        candidate_files: list[Path] = []
        parts_dir = project_dir / "parts"
        audio_dir = project_dir / "audio"
        avatar_segments_dir = project_dir / "avatar_segments"

        if parts_dir.exists() and parts_dir.is_dir():
            candidate_files.extend(parts_dir.glob("part_*.mp4"))
            candidate_files.extend(parts_dir.glob("*.overlay.png"))
            candidate_files.extend(parts_dir.glob("*.whiteboard.png"))
            candidate_files.extend(parts_dir.glob("*.avatar-safe.png"))
        if audio_dir.exists() and audio_dir.is_dir():
            candidate_files.extend(audio_dir.glob("slide_*.mp3"))
        if avatar_segments_dir.exists() and avatar_segments_dir.is_dir():
            candidate_files.extend(avatar_segments_dir.glob("avatar_*.mp4"))

        for file_path in candidate_files:
            try:
                if not file_path.exists() or not file_path.is_file():
                    continue
                if not str(file_path).endswith(transient_suffixes) and file_path.suffix.lower() not in {".mp3", ".mp4"}:
                    continue
                age_seconds = max(0.0, now - float(file_path.stat().st_mtime))
                if age_seconds < threshold_seconds:
                    continue
                file_path.unlink()
                removed_files += 1
            except Exception:
                logger.warning("cleanup_orphan_render_artifacts failed for file=%s", file_path, exc_info=True)

    return {
        "status": "done",
        "removed_files": removed_files,
        "scanned_projects": scanned_projects,
        "skipped_active_projects": skipped_active_projects,
        "min_age_hours": int(min_age_hours),
    }


# ---------------------------------------------------------------------------
# Smoke-test tasks
# ---------------------------------------------------------------------------

@app.task(name="worker.tasks.ping")
def ping(message: str = "ping") -> str:
    """Smoke-test task. Send 'ping', get 'pong'."""
    return "pong" if message == "ping" else f"echo: {message}"


@app.task(bind=True, name="worker.tasks.tts_render")
def tts_render(self, slide_id: int) -> dict:
    """Placeholder TTS render stub — superseded by the parallel pipeline."""
    return {"slide_id": slide_id, "status": "not_implemented"}


# ---------------------------------------------------------------------------
# Pipeline step 1: Export
# ---------------------------------------------------------------------------

@app.task(
    bind=True,
    name="worker.tasks.export_project",
    max_retries=0,
)
def export_project(
    self,
    project_id: str,
    pptx_path: str,
    whiteboard_mode_all: bool = False,
) -> list[dict[str, Any]]:
    """
    Export slide images and speaker notes from *pptx_path*.

    Returns a list of slide descriptor dicts (one per slide)::

        [
          {
            "index":      0,                       # 0-based position
            "slide_num":  1,                       # 1-based display number
            "image_path": "/…/images/slide-1.png",
            "notes_text": "narration text",
            "audio_out":  "/…/audio/slide_001.mp3",
            "part_out":   "/…/parts/part_001.mp4",
          },
          …
        ]

    Parameters
    ----------
    project_id: Unique project identifier (used for workspace layout).
    pptx_path:  Absolute path to the source .pptx file.
    """
    try:
        from scripts.pptx_extract import export_slide_images, extract_speaker_notes
        from scripts.text_segmentation import build_slide_page_structure
    except ImportError as exc:
        raise RuntimeError(
            f"pptx_extract not importable — check PYTHONPATH. Original error: {exc}"
        ) from exc

    logger.info("export_project START project=%s pptx=%s", project_id, pptx_path)
    self.update_state(state="PROGRESS", meta={"step": "export_start", "progress": 2})

    ws = _workspace(project_id)

    # Export slide images (PNG)
    image_paths = export_slide_images(pptx_path, str(ws["images"]))
    n_slides = len(image_paths)
    if n_slides == 0:
        raise ValueError(f"No slide images exported from {pptx_path!r}")
    logger.info("export_project: %d slide images exported", n_slides)
    self.update_state(state="PROGRESS", meta={"step": "images_done", "progress": 6})

    # Extract speaker notes (one .txt per slide)
    note_paths = extract_speaker_notes(pptx_path, str(ws["notes"]))
    logger.info("export_project: %d note files extracted", len(note_paths))
    self.update_state(state="PROGRESS", meta={"step": "notes_done", "progress": 10})

    # Build render descriptors with reusable long-slide splitting.
    slides: list[dict[str, Any]] = []
    display_index = 0
    for idx in range(n_slides):
        slide_num = idx + 1

        notes_text = ""
        if idx < len(note_paths):
            txt_file = Path(note_paths[idx])
            if txt_file.exists():
                notes_text = txt_file.read_text(encoding="utf-8").strip()
        if not notes_text:
            notes_text = f"Slide {slide_num}."  # minimal fallback narration

        page_items = build_slide_page_structure(idx, notes_text)
        for page in page_items:
            display_index += 1
            slides.append({
                "index": display_index - 1,
                "slide_num": display_index,
                "source_slide_index": idx,
                "source_slide_num": slide_num,
                "split_index": int(page.get("split_index") or 0),
                "page_key": page.get("page_key") or f"s{slide_num}-p1",
                "image_path": image_paths[idx],
                "notes_text": notes_text,
                "original_text": page.get("original_text") or notes_text,
                "narration_text": page.get("narration_text") or notes_text,
                "subtitle_chunks": page.get("subtitle_chunks") or [notes_text],
                "whiteboard_mode": bool(whiteboard_mode_all),
                "audio_out": str(ws["audio"] / f"slide_{display_index:03d}.mp3"),
                "part_out": str(ws["parts"] / f"part_{display_index:03d}.mp4"),
            })

    logger.info("export_project DONE: %d source slides -> %d render pages", n_slides, len(slides))
    return slides


# ---------------------------------------------------------------------------
# Pipeline step 2: Per-slide TTS + render (parallel via Celery group)
# ---------------------------------------------------------------------------

@app.task(
    bind=True,
    name="worker.tasks.synthesize_and_render_slide",
    max_retries=2,
    default_retry_delay=10,
)
def synthesize_and_render_slide(
    self,
    slide_meta: dict[str, Any],
    project_id: str,
    voice_id: str,
    pause_sec: float,
    lang_hint: str,
    tts_mode: str = "service",
    avatar_options: dict[str, Any] | None = None,
    tts_settings: dict[str, Any] | None = None,
    render_progress_index: int | None = None,
    render_progress_total: int | None = None,
) -> dict[str, Any]:
    """
    Synthesize TTS audio and render one slide to a part MP4.

    Designed to run in parallel with sibling slide tasks via a Celery
    ``group``.  Idempotent — output files are overwritten on retry.

    Parameters
    ----------
    slide_meta: Descriptor dict produced by :func:`export_project`.
    project_id: Used for logging; DB not updated here (finalizer handles it).
    voice_id:   TTS voice identifier passed to the TTS service.
    pause_sec:  Extra seconds appended to the audio duration.
    lang_hint:  Language hint for TTS (e.g. ``'en'``, ``'auto'``).
    tts_mode:   ``'service'`` (internal TTS microservice) or ``'eleven'``.

    Returns
    -------
    dict with keys: ``index``, ``slide_num``, ``part_path``, ``duration``, ``text``.
    """
    try:
        from scripts.tts_client import synthesize_text_with_metadata
        from scripts.ffmpeg_helpers import (
            create_slide_video,
            get_audio_duration,
            trim_trailing_silence,
        )
    except ImportError as exc:
        raise RuntimeError(
            f"Pipeline scripts not importable — check PYTHONPATH. Error: {exc}"
        ) from exc

    index      = slide_meta["index"]
    slide_num  = slide_meta["slide_num"]
    image_path = slide_meta["image_path"]
    notes_text = slide_meta["notes_text"]
    narration_text = slide_meta.get("narration_text") or notes_text
    original_text = slide_meta.get("original_text") or notes_text
    subtitle_chunks = list(slide_meta.get("subtitle_chunks") or [])
    whiteboard_mode = bool(slide_meta.get("whiteboard_mode"))
    rich_text_html = str(slide_meta.get("rich_text_html") or "")
    editor_document = slide_meta.get("editor_document") or {}
    if isinstance(editor_document, dict) and not narration_text:
        para_lines = [str(p.get("text") or "") for p in editor_document.get("paragraphs", []) if isinstance(p, dict)]
        if para_lines:
            narration_text = "\n".join(para_lines)
    if isinstance(editor_document, dict) and not rich_text_html:
        rich_text_html = str(editor_document.get("html") or "")
    notes_text_prepared = str(narration_text or "").strip() or f"Slide {slide_num}."
    audio_out  = slide_meta["audio_out"]
    part_out   = slide_meta["part_out"]

    logger.info(
        "synthesize_and_render_slide START slide=%d project=%s", slide_num, project_id
    )
    if _is_job_cancelled(project_id):
        raise RuntimeError(JOB_CANCELLED_MARKER)
    self.update_state(
        state="PROGRESS",
        meta={"step": "tts", "slide": slide_num, "project_id": project_id},
    )

    try:
        # TTS synthesis → MP3 audio file
        tts_meta = synthesize_text_with_metadata(
            voice_id,
            notes_text_prepared,
            audio_out,
            mode=tts_mode,
            lang=lang_hint,
            tts_settings=tts_settings,
        )
        spoken_text = str(tts_meta.get("spoken_text") or notes_text_prepared)
        tts_rules_applied = list(tts_meta.get("tts_normalization_rules_applied") or [])
        tts_settings_summary = _summarize_tts_settings(tts_settings)
        tts_applied_overrides = dict(tts_meta.get("applied_overrides") or tts_settings_summary.get("applied_overrides") or {})
        if tts_rules_applied:
            logger.info(
                "Slide %d TTS normalization lang=%s rules=%s",
                slide_num,
                tts_meta.get("tts_normalization_language") or lang_hint,
                json.dumps(tts_rules_applied, ensure_ascii=True),
            )

        render_image_path = image_path
        avatar_state = dict(avatar_options or {})
        avatar_state["composite_fallback_allowed"] = bool(avatar_state.get("composite_fallback_allowed", False))
        avatar_state["lipsync_engine"] = (
            str(os.environ.get("AVATAR_ENGINE") or avatar_state.get("lipsync_engine") or "musetalk")
            .strip()
            .lower()
        )
        avatar_required = bool(avatar_state.get("enabled"))

        if whiteboard_mode:
            render_image_path = _make_whiteboard_image(
                narration_text or original_text,
                str(Path(part_out).with_suffix(".whiteboard.png")),
            )
        elif rich_text_html or (str(narration_text or "").strip() and str(narration_text).strip() != str(notes_text).strip()):
            render_image_path = _render_transcript_overlay_image(
                image_path,
                narration_text,
                rich_text_html,
                str(Path(part_out).with_suffix(".overlay.png")),
            )

        if avatar_required:
            render_image_path = _render_avatar_safe_slide_image(
                render_image_path,
                str(Path(part_out).with_suffix(".avatar-safe.png")),
            )

        if WORKER_TRIM_TRAILING_SILENCE:
            try:
                trim_trailing_silence(audio_out)
            except Exception:
                logger.warning(
                    "Slide %d: trailing-silence trim failed, using original audio",
                    slide_num,
                    exc_info=True,
                )

        # Measure audio duration and add a small configurable slide hold.
        audio_duration = get_audio_duration(audio_out)
        total_duration = audio_duration + max(float(pause_sec), 0.0)
        logger.info(
            "  Slide %d: audio %.2fs + pause %.2fs = total %.2fs",
            slide_num, audio_duration, pause_sec, total_duration,
        )

        self.update_state(
            state="PROGRESS",
            meta={"step": "render", "slide": slide_num, "project_id": project_id},
        )
        if _is_job_cancelled(project_id):
            raise RuntimeError(JOB_CANCELLED_MARKER)

        # Avatar path: render local talking-head clip separately (never burn into slide video).
        avatar_applied = False
        avatar_engine_used = "none"
        avatar_fallback_chain: list[str] = []
        avatar_rel_path = ""
        avatar_failure_reason = ""
        avatar_validation = {}
        avatar_attempted = False
        avatar_skipped = False
        avatar_status_override = ""
        avatar_segment_path: Path | None = None

        try:
            avatar_has_source = bool(
                avatar_state.get("source_image_rel_path")
                or avatar_state.get("source_image_original_rel_path")
                or avatar_state.get("source_video_rel_path")
            )
            if avatar_state.get("enabled") and avatar_state.get("teacher_id") and avatar_has_source:
                source_valid_value = avatar_state.get("avatar_source_valid")
                preview_stale = bool(avatar_state.get("avatar_preview_stale"))
                if source_valid_value is False:
                    avatar_skipped = True
                    avatar_status_override = "avatar_source_invalid"
                    avatar_failure_reason = str(
                        avatar_state.get("avatar_source_validation_error")
                        or "avatar_input_face_not_detected"
                    )
                    avatar_validation = {
                        "avatar_source_valid": False,
                        "avatar_source_hash": str(avatar_state.get("avatar_source_hash") or ""),
                        "avatar_source_validation_error": avatar_failure_reason,
                    }
                elif preview_stale:
                    avatar_skipped = True
                    avatar_status_override = "avatar_preview_stale"
                    avatar_failure_reason = "avatar_preview_stale"
                    avatar_validation = {
                        "avatar_source_valid": True,
                        "avatar_source_hash": str(avatar_state.get("avatar_source_hash") or ""),
                        "avatar_preview_source_hash": str(avatar_state.get("avatar_preview_source_hash") or ""),
                        "avatar_preview_stale": True,
                    }
                else:
                    avatar_attempted = True
                    avatar_dir = Path(_avatar_storage_root()) / str(project_id) / "avatar_segments"
                    avatar_dir.mkdir(parents=True, exist_ok=True)
                    avatar_segment_path = avatar_dir / f"avatar_{slide_num:03d}.mp4"

                    avatar_result = render_avatar_segment.apply(
                        kwargs={
                            "project_id": int(project_id),
                            "teacher_id": int(avatar_state.get("teacher_id")),
                            "slide_index": int(index),
                            "audio_path": audio_out,
                            "output_path": str(avatar_segment_path),
                            "source_image_rel_path": str(avatar_state.get("source_image_rel_path")),
                            "source_image_original_rel_path": str(avatar_state.get("source_image_original_rel_path") or avatar_state.get("source_image_rel_path") or ""),
                            "source_video_rel_path": str(avatar_state.get("source_video_rel_path") or ""),
                            "avatar_reference_type": str(avatar_state.get("avatar_reference_type") or "image"),
                            "motion_preset": str(avatar_state.get("motion_preset") or "natural"),
                            "quality_preset": str(avatar_state.get("quality_preset") or "high"),
                            "lipsync_engine": str(avatar_state.get("lipsync_engine") or "musetalk"),
                            "cache_text_hash": hashlib.sha256(notes_text_prepared.encode("utf-8")).hexdigest(),
                        }
                    )

                    if avatar_result.failed():
                        raise RuntimeError(_concise_error_text(avatar_result.result, fallback=f"render_exception_slide_{slide_num}"))

                    avatar_payload = avatar_result.result
                    if not isinstance(avatar_payload, dict):
                        raise RuntimeError(f"render_avatar_segment returned unexpected type: {type(avatar_payload).__name__}")

                    avatar_engine_used = str(avatar_payload.get("engine_used") or "none")
                    avatar_fallback_chain = list(avatar_payload.get("fallback_chain_used") or [])
                    avatar_validation = dict(avatar_payload.get("motion_validation") or {})
                    avatar_output_path = str(avatar_payload.get("output_path") or "")
                    if avatar_output_path and Path(avatar_output_path).exists():
                        avatar_applied = True
                        avatar_rel_path = _safe_rel_path(_avatar_storage_root(), avatar_output_path)
                    else:
                        avatar_failure_reason = "missing_avatar_output"
        except Exception as exc:
            avatar_attempted = True
            avatar_failure_reason = _concise_error_text(exc, fallback=f"render_exception_slide_{slide_num}")
            logger.error(
                "Avatar overlay failed for project=%s slide=%s error=%s",
                project_id,
                slide_num,
                avatar_failure_reason,
                exc_info=True,
            )
            # Fallback: build a static avatar clip from teacher portrait + slide audio.
            # This keeps "render with avatar" usable when LivePortrait runtime fails.
            try:
                if avatar_required and avatar_segment_path is not None:
                    source_rel = str(
                        avatar_state.get("source_image_original_rel_path")
                        or avatar_state.get("source_image_rel_path")
                        or ""
                    ).strip()
                    source_abs = _resolve_storage_path(source_rel, _avatar_storage_root()) if source_rel else ""
                    if source_abs and Path(source_abs).exists():
                        subprocess.run(
                            [
                                "ffmpeg",
                                "-y",
                                "-loop",
                                "1",
                                "-i",
                                str(source_abs),
                                "-i",
                                str(audio_out),
                                "-c:v",
                                "libx264",
                                "-preset",
                                "veryfast",
                                "-tune",
                                "stillimage",
                                "-pix_fmt",
                                "yuv420p",
                                "-c:a",
                                "aac",
                                "-shortest",
                                str(avatar_segment_path),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        if avatar_segment_path.exists() and avatar_segment_path.stat().st_size > 0:
                            avatar_applied = True
                            avatar_engine_used = "static_fallback"
                            avatar_rel_path = _safe_rel_path(_avatar_storage_root(), str(avatar_segment_path))
                            avatar_fallback_chain = list(avatar_fallback_chain or []) + ["static_avatar_fallback"]
                            avatar_status_override = "warning"
                            avatar_validation = dict(avatar_validation or {})
                            avatar_validation["fallback_used"] = True
                            avatar_validation["fallback_reason"] = avatar_failure_reason
                            avatar_failure_reason = (
                                f"avatar_warning_fallback_used:{avatar_failure_reason}"
                            )
            except Exception:
                logger.warning(
                    "Avatar fallback segment build failed for project=%s slide=%s",
                    project_id,
                    slide_num,
                    exc_info=True,
                )

        if avatar_required and not avatar_applied and not avatar_failure_reason:
            avatar_failure_reason = "validation_rejected_or_no_usable_avatar"

        if avatar_required and not avatar_applied:
            logger.warning(
                "Avatar segment skipped for project=%s slide=%s reason=%s; continuing slide render without avatar",
                project_id,
                slide_num,
                avatar_failure_reason or "validation_rejected_or_no_usable_avatar",
            )

        # Slide and audio are always rendered as their own track; avatar remains separate.
        create_slide_video(render_image_path, audio_out, part_out, duration_sec=total_duration)

        try:
            if avatar_state.get("enabled") and avatar_state.get("teacher_id"):
                from avatar.hashing import sha256_file

                source_rel = str(avatar_state.get("source_image_rel_path") or "")
                source_abs = str(Path(_avatar_storage_root()) / source_rel) if source_rel else ""
                _record_avatar_render_job(
                    lesson_id=int(project_id),
                    teacher_id=int(avatar_state.get("teacher_id")),
                    source_image_hash=sha256_file(source_abs) if source_abs and Path(source_abs).exists() else "",
                    tts_audio_hash=sha256_file(audio_out),
                    lesson_text_hash=hashlib.sha256(notes_text_prepared.encode("utf-8")).hexdigest(),
                    slide_hash=sha256_file(Path(render_image_path)) if Path(render_image_path).exists() else "",
                    engine_used=avatar_engine_used,
                    render_status="done" if avatar_applied else ("failed" if avatar_required else "skipped"),
                    render_error=avatar_failure_reason,
                    output_path=avatar_rel_path,
                    fallback_chain_used=avatar_fallback_chain,
                    metadata={
                        "avatar_version": str(avatar_state.get("model_version") or "musetalk:v1"),
                        "avatar_reference_type": str(avatar_state.get("avatar_reference_type") or "image"),
                        "avatar_status": avatar_status_override or ("ready" if avatar_applied else ("avatar_failed" if avatar_required else "none")),
                        "avatar_skipped": bool(avatar_skipped),
                        "avatar_source_valid": avatar_state.get("avatar_source_valid"),
                        "avatar_source_validation_error": str(avatar_state.get("avatar_source_validation_error") or ""),
                        "avatar_source_hash": str(avatar_state.get("avatar_source_hash") or ""),
                        "avatar_preview_stale": bool(avatar_state.get("avatar_preview_stale")),
                        "slide_num": int(slide_num),
                        "page_key": slide_meta.get("page_key") or "",
                        "motion_validation": avatar_validation,
                    },
                )
        except Exception:
            logger.warning("Avatar render job telemetry failed for project=%s slide=%s", project_id, slide_num, exc_info=True)

        logger.info("  Slide %d: part video → %s", slide_num, part_out)
        if render_progress_index is not None and render_progress_total:
            completed = max(0, min(int(render_progress_index), int(render_progress_total)))
            total = max(1, int(render_progress_total))
            synth_progress = 10 + int((completed * 79) / total)
            _update_job_progress_floor(project_id, synth_progress)


        return {
            "index":     index,
            "slide_num": slide_num,
            "page_key":  slide_meta.get("page_key"),
            "source_slide_index": slide_meta.get("source_slide_index", index),
            "split_index": slide_meta.get("split_index", 0),
            "part_path": part_out,
            "duration":  total_duration,
            "pause_seconds": max(float(pause_sec), 0.0),
            "text":      notes_text_prepared,
            "original_text": original_text,
            "spoken_text": spoken_text,
            "tts_normalization_language": str(tts_meta.get("tts_normalization_language") or lang_hint),
            "tts_normalization_rules_applied": tts_rules_applied,
            "tts_provider": str(tts_meta.get("provider") or ""),
            "tts_provider_preference": str(tts_meta.get("provider_preference") or tts_settings_summary.get("provider_preference") or "auto"),
            "tts_normalization_enabled": tts_meta.get("normalization_enabled", tts_settings_summary.get("normalization_enabled")),
            "tts_normalization_mode": str(tts_meta.get("normalization_mode") or tts_settings_summary.get("normalization_mode") or ""),
            "tts_unknown_word_strategy": str(tts_meta.get("unknown_word_strategy") or tts_settings_summary.get("unknown_word_strategy") or ""),
            "tts_applied_overrides": tts_applied_overrides,
            "tts_fallback_used": bool(tts_meta.get("fallback_used", str(tts_meta.get("provider") or "").lower() in {"fallback", "local_fallback"})),
            "tts_fallback_reason": str(tts_meta.get("fallback_reason") or tts_meta.get("message") or ""),
            "tts_settings": tts_settings_summary,
            "tts_preprocessing_warnings": list(tts_meta.get("preprocessing_warnings") or []),
            "slide_path": render_image_path,
            "tts_audio_path": audio_out,
            "subtitle_chunks": subtitle_chunks or [notes_text_prepared],
            "whiteboard_mode": whiteboard_mode,
            "avatar_applied": avatar_applied,
            "avatar_engine_used": avatar_engine_used,
            "avatar_fallback_chain": avatar_fallback_chain,
            "avatar_segment_rel_path": avatar_rel_path,
            "avatar_attempted": bool(avatar_attempted),
            "avatar_skipped": bool(avatar_skipped),
            "avatar_failed": bool(avatar_required and not avatar_applied),
            "avatar_status": avatar_status_override or ("avatar_failed" if avatar_required and not avatar_applied else ("ready" if avatar_applied else "none")),
            "avatar_error": avatar_failure_reason if avatar_required and not avatar_applied else "",
            "avatar_warning": avatar_failure_reason if avatar_required and not avatar_applied else "",
            "avatar_failure_reason": avatar_failure_reason,
            "avatar_motion_validation": avatar_validation,
        }

    except Exception:
        logger.exception(
            "synthesize_and_render_slide FAILED slide=%d project=%s",
            slide_num, project_id,
        )
        # Re-raise so Celery retry/chord-failure logic kicks in.
        raise


# ---------------------------------------------------------------------------
# Pipeline step 3: Concatenate + finalise (chord callback)
# ---------------------------------------------------------------------------

@app.task(
    name="worker.tasks.concat_and_finalize",
    max_retries=0,
    # bind=True is intentionally absent: Celery passes the group results as
    # the first positional argument to the chord callback, so injecting
    # `self` would shift every parameter by one position.
)
def concat_and_finalize(
    results: list[dict[str, Any]],
    project_id: str,
) -> dict[str, Any]:
    """
    Chord callback: concatenate all per-slide part MP4s into the final video
    and generate an SRT subtitle file.  Updates the Job DB record.

    Called automatically by Celery once every task in the preceding ``group``
    has returned successfully.  If any slide task raises, Celery
    (``CELERY_CHORD_PROPAGATES=True`` by default) marks this callback as
    failed without calling it — no extra guard needed here.

    Parameters
    ----------
    results:    List of dicts from :func:`synthesize_and_render_slide`, one
                per slide.  Sorted defensively by ``index`` before use.
    project_id: Project identifier used for output paths and DB updates.

    Returns
    -------
    dict with keys: ``final_video``, ``srt``, ``parts``, ``durations``,
    ``n_slides``.

    Job model fields written
    ------------------------
    * ``status``    → ``'done'`` on success, ``'failed'`` on error.
    * ``result_url``→ relative path (e.g., "16/16.mp4") resolved by MediaStreamView in STORAGE_ROOT.
    * ``progress``  → 95 at concat, 100 at done (if field exists on model).
    * ``error_message`` → full traceback on failure.
    """
    try:
        from scripts.ffmpeg_helpers import concat_videos, generate_srt_from_cues, generate_vtt_from_cues, package_hls_stream
    except ImportError as exc:
        raise RuntimeError(
            f"ffmpeg_helpers not importable — check PYTHONPATH. Error: {exc}"
        ) from exc

    logger.info(
        "concat_and_finalize START project=%s slides=%d", project_id, len(results)
    )
    if _is_job_cancelled(project_id):
        raise RuntimeError(JOB_CANCELLED_MARKER)
    _update_job(project_id, progress=90)
    _upsert_job_checkpoint(
        project_id,
        stage_name="concat_finalize",
        stage_status="running",
        payload={"slides": len(results)},
    )

    try:
        # Sort by index — Celery preserves group order since v4, but defensive
        ordered         = sorted(results, key=lambda r: r["index"])
        part_paths      = [r["part_path"] for r in ordered]
        slide_durations = [r["duration"]  for r in ordered]
        _sync_lesson_segments(project_id, ordered)

        ws = _workspace(project_id)

        # Concatenate part MP4s → final video
        final_video = str(ws["final"] / f"{project_id}.mp4")
        logger.info("Concatenating %d clips → %s", len(part_paths), final_video)
        concat_videos(part_paths, final_video)
        _update_job(project_id, progress=95)

        # Persist the render timeline, then build canonical cue text from active
        # transcript rows so captions never consume provider-normalized TTS text.
        srt_path = str(ws["final"] / f"{project_id}.srt")
        page_timeline = _build_page_timeline_from_render_results(ordered)
        _update_transcript_timeline(project_id, page_timeline)
        subtitle_cues = build_subtitle_cues_from_transcript_pages(project_id, ordered, slide_durations)
        avatar_segments: list[dict[str, Any]] = []
        avatar_failures: list[dict[str, Any]] = []
        for result in ordered:
            duration = float(result.get("duration") or 0.0)
            if result.get("avatar_applied"):
                avatar_segments.append(
                    {
                        "index": int(result.get("index") or 0),
                        "engine": str(result.get("avatar_engine_used") or "none"),
                        "fallback_chain": list(result.get("avatar_fallback_chain") or []),
                        "segment_rel_path": str(result.get("avatar_segment_rel_path") or ""),
                        "duration": round(duration, 3),
                    }
                )
            elif result.get("avatar_failed"):
                avatar_failures.append(
                    {
                        "index": int(result.get("index") or 0),
                        "slide_num": int(result.get("slide_num") or 0),
                        "page_key": str(result.get("page_key") or ""),
                        "status": str(result.get("avatar_status") or "avatar_failed"),
                        "skipped": bool(result.get("avatar_skipped")),
                        "reason": str(result.get("avatar_error") or result.get("avatar_failure_reason") or "avatar_failed"),
                        "validation": dict(result.get("avatar_motion_validation") or {}),
                    }
                )

        generate_srt_from_cues(subtitle_cues, srt_path)
        logger.info("SRT written → %s", srt_path)

        vtt_path = str(ws["final"] / f"{project_id}.vtt")
        generate_vtt_from_cues(subtitle_cues, vtt_path)
        logger.info("WebVTT written → %s", vtt_path)
        playback_assets: dict[str, Any] = {
            "asset_id": f"{DRM_ASSET_ID_PREFIX}{project_id}",
            "content_id": f"{DRM_CONTENT_ID_PREFIX}{project_id}",
            "mp4_rel_path": f"{project_id}/{project_id}.mp4",
            "srt_rel_path": f"{project_id}/{project_id}.srt",
            "vtt_rel_path": f"{project_id}/{project_id}.vtt",
            "protection_mode": _worker_protection_mode(),
            "timeline": page_timeline,
            "hls": None,
            "avatar": None,
            "slides": [
                _safe_rel_path(STORAGE_ROOT, str(item.get("slide_path"))) if item.get("slide_path") else ""
                for item in ordered
            ],
            "transcript": [str(item.get("text") or "") for item in ordered],
            "tts_audio": [
                _safe_rel_path(STORAGE_ROOT, str(item.get("tts_audio_path"))) if item.get("tts_audio_path") else ""
                for item in ordered
            ],
            "tts_normalization": [
                {
                    "index": int(item.get("index") or 0),
                    "slide_num": int(item.get("slide_num") or 0),
                    "page_key": str(item.get("page_key") or ""),
                    "original_text": str(item.get("original_text") or item.get("text") or ""),
                    "spoken_text": str(item.get("spoken_text") or item.get("text") or ""),
                    "tts_normalization_language": str(item.get("tts_normalization_language") or ""),
                    "tts_normalization_rules_applied": list(item.get("tts_normalization_rules_applied") or []),
                    "tts_provider": str(item.get("tts_provider") or ""),
                    "provider_preference": str(item.get("tts_provider_preference") or ""),
                    "normalization_enabled": item.get("tts_normalization_enabled"),
                    "normalization_mode": str(item.get("tts_normalization_mode") or ""),
                    "unknown_word_strategy": str(item.get("tts_unknown_word_strategy") or ""),
                    "applied_overrides": dict(item.get("tts_applied_overrides") or {}),
                    "fallback_used": bool(item.get("tts_fallback_used", False)),
                    "fallback_reason": str(item.get("tts_fallback_reason") or ""),
                    "project_tts_settings": dict(item.get("tts_settings") or {}),
                    "tts_preprocessing_warnings": list(item.get("tts_preprocessing_warnings") or []),
                }
                for item in ordered
            ],
            "avatar_clips": [str(item.get("avatar_segment_rel_path") or "") for item in ordered],
            "avatar_failures": avatar_failures,
            "avatar_slide_metadata": [
                {
                    "index": int(item.get("index") or 0),
                    "slide_num": int(item.get("slide_num") or 0),
                    "page_key": str(item.get("page_key") or ""),
                    "avatar_attempted": bool(item.get("avatar_attempted")),
                    "avatar_skipped": bool(item.get("avatar_skipped")),
                    "avatar_applied": bool(item.get("avatar_applied")),
                    "avatar_failed": bool(item.get("avatar_failed")),
                    "avatar_status": str(item.get("avatar_status") or ("avatar_failed" if item.get("avatar_failed") else ("ready" if item.get("avatar_applied") else "none"))),
                    "avatar_error": str(item.get("avatar_error") or item.get("avatar_failure_reason") or ""),
                    "avatar_segment_rel_path": str(item.get("avatar_segment_rel_path") or ""),
                    "avatar_engine_used": str(item.get("avatar_engine_used") or "none"),
                    "avatar_fallback_chain": list(item.get("avatar_fallback_chain") or []),
                    "avatar_motion_validation": dict(item.get("avatar_motion_validation") or {}),
                }
                for item in ordered
            ],
            "avatar_status": (
                "avatar_partial_failed"
                if avatar_segments and avatar_failures
                else (
                    "avatar_source_invalid"
                    if avatar_failures and all(str(failure.get("status") or "") == "avatar_source_invalid" for failure in avatar_failures)
                    else (
                        "avatar_preview_stale"
                        if avatar_failures and all(str(failure.get("status") or "") == "avatar_preview_stale" for failure in avatar_failures)
                        else ("avatar_failed" if avatar_failures else ("ready" if avatar_segments else "none"))
                    )
                )
            ),
            "pause_durations": [float(item.get("pause_seconds") or 0.0) for item in ordered],
            "final_segments": [],
        }

        playback_assets["final_segments"] = [
            {
                "index": int(item.get("index") or 0),
                "slide": playback_assets["slides"][idx],
                "transcript": playback_assets["transcript"][idx],
                "tts_audio": playback_assets["tts_audio"][idx],
                "avatar_clip": playback_assets["avatar_clips"][idx],
                "avatar_attempted": bool(item.get("avatar_attempted")),
                "avatar_skipped": bool(item.get("avatar_skipped")),
                "avatar_applied": bool(item.get("avatar_applied")),
                "avatar_failed": bool(item.get("avatar_failed")),
                "avatar_status": str(item.get("avatar_status") or ("avatar_failed" if item.get("avatar_failed") else ("ready" if item.get("avatar_applied") else "none"))),
                "avatar_error": str(item.get("avatar_error") or item.get("avatar_failure_reason") or ""),
                "avatar_failure_reason": str(item.get("avatar_failure_reason") or item.get("avatar_error") or ""),
                "pause_seconds": playback_assets["pause_durations"][idx],
                "part_rel_path": _safe_rel_path(STORAGE_ROOT, str(item.get("part_path"))),
                "duration": float(item.get("duration") or 0.0),
            }
            for idx, item in enumerate(ordered)
        ]

        if avatar_segments:
            try:
                avatar_track_dir = ws["final"] / "avatar"
                avatar_track_dir.mkdir(parents=True, exist_ok=True)
                avatar_track_path = avatar_track_dir / "avatar_track.mp4"
                avatar_segment_paths: list[str] = []
                for item in sorted(avatar_segments, key=lambda seg: seg.get("index", 0)):
                    rel = str(item.get("segment_rel_path") or "")
                    if not rel:
                        continue
                    abs_path = Path(STORAGE_ROOT) / rel
                    if abs_path.exists():
                        avatar_segment_paths.append(str(abs_path))
                if avatar_segment_paths:
                    concat_videos(avatar_segment_paths, str(avatar_track_path))
                    playback_assets["avatar"] = {
                        "track_rel_path": f"{project_id}/avatar/avatar_track.mp4",
                        "default_position": "top-right",
                        "default_size": "medium",
                        "segments": avatar_segments,
                    }
            except Exception:
                logger.warning("Avatar track concat failed for project=%s", project_id, exc_info=True)

        if DRM_STREAMING_ENABLED:
            try:
                hls_dir = ws["final"] / "drm" / "hls"
                hls_rel_dir = f"{project_id}/drm/hls"
                package_result = package_hls_stream(
                    final_video,
                    str(hls_dir),
                    playlist_name="index.m3u8",
                    segment_pattern="seg_%05d.ts",
                    segment_time=6,
                    encrypt=DRM_HLS_ENCRYPTION_ENABLED,
                    key_hex=DRM_HLS_KEY_HEX,
                    key_uri="enc.key" if DRM_HLS_ENCRYPTION_ENABLED else None,
                    key_filename="enc.key",
                )
                playback_assets["hls"] = {
                    "manifest_rel_path": f"{hls_rel_dir}/index.m3u8",
                    "segment_glob": f"{hls_rel_dir}/seg_*.ts",
                    "encrypted": bool(package_result.get("encrypted")),
                    "key_rel_path": f"{hls_rel_dir}/enc.key" if package_result.get("encrypted") else None,
                    "drm_scheme": "hls-aes-128" if package_result.get("encrypted") else "none",
                }
                logger.info("HLS packaging complete for project=%s", project_id)
            except Exception as hls_exc:
                logger.warning("HLS packaging skipped for project=%s: %s", project_id, hls_exc)

        _write_playback_sidecar(project_id, playback_assets)
        avatar_warning_message = ""
        if avatar_failures:
            compact_failures = []
            for failure in avatar_failures[:5]:
                label = failure.get("slide_num") or (int(failure.get("index") or 0) + 1)
                compact_failures.append(f"slide {label}: {failure.get('reason') or 'avatar_failed'}")
            if len(avatar_failures) > 5:
                compact_failures.append(f"+{len(avatar_failures) - 5} more")
            avatar_warning_message = f"{playback_assets['avatar_status']}:" + "; ".join(compact_failures)
            logger.warning(
                "Lesson finalized with avatar warnings project=%s status=%s failures=%s",
                project_id,
                playback_assets["avatar_status"],
                json.dumps(avatar_failures, ensure_ascii=True, sort_keys=True),
            )

        # Store relative paths (not HTTP URLs) so MediaStreamView can find them in storage_root
        # result_url = "{project_id}/{project_id}.mp4" (relative path relative to STORAGE_ROOT)
        result_url_rel = f"{project_id}/{project_id}.mp4"
        srt_url_rel = f"{project_id}/{project_id}.srt"
        vtt_url_rel = f"{project_id}/{project_id}.vtt"

        # Mark the Job as done and record relative file paths
        _update_job(
            project_id,
            status="done",
            progress=100,
            result_url=result_url_rel,
            srt_url=srt_url_rel,
            error_message=avatar_warning_message,
        )
        existing_concat_payload = _checkpoint_payload(project_id, "concat_finalize")
        resume_source = str(existing_concat_payload.get("source") or "").strip().lower()
        _upsert_job_checkpoint(
            project_id,
            stage_name="concat_finalize",
            stage_status="done",
            payload={
                "result_url": result_url_rel,
                "srt_url": srt_url_rel,
                "parts_count": len(part_paths),
                "source": resume_source or "normal",
                "recovered_parts_count": len(part_paths) if resume_source == "resume_shortcut" else 0,
            },
        )
        _upsert_job_checkpoint(
            project_id,
            stage_name="pipeline",
            stage_status="done",
            payload={"status": "completed"},
        )

        result = {
            "final_video": final_video,
            "result_url":  result_url_rel,
            "srt":         srt_path,
            "srt_url":     srt_url_rel,
            "vtt":         vtt_path,
            "vtt_url":     vtt_url_rel,
            "playback_assets": playback_assets,
            "parts":       part_paths,
            "durations":   slide_durations,
            "timeline":    page_timeline,
            "avatar_segments": avatar_segments,
            "avatar_failures": avatar_failures,
            "avatar_status": playback_assets["avatar_status"],
            "n_slides":    len(ordered),
        }

        if avatar_segments:
            try:
                from django.utils import timezone
                from core.models import Project, UserProfile

                project = Project.objects.select_related("user").get(pk=int(project_id))
                if project.user_id:
                    UserProfile.objects.filter(user_id=project.user_id).update(avatar_last_rendered_at=timezone.now())
            except Exception:
                logger.warning("Avatar last-render timestamp update failed for project=%s", project_id, exc_info=True)

        logger.info(
            "=== concat_and_finalize DONE project=%s → %s ===", project_id, final_video
        )
        return result

    except Exception:
        error_trace = tb.format_exc()
        logger.exception("concat_and_finalize FAILED project=%s", project_id)
        if JOB_CANCELLED_MARKER in str(error_trace):
            _update_job(project_id, status="cancelled", error_message=JOB_CANCELLED_MARKER)
        else:
            _update_job(project_id, status="failed", error_message=error_trace)
        _upsert_job_checkpoint(
            project_id,
            stage_name="concat_finalize",
            stage_status="failed",
            payload={"error": _concise_error_text(error_trace, fallback="concat_failed", limit=800)},
        )
        raise


@app.task(
    name="worker.tasks.merge_and_finalize_segments",
    max_retries=0,
)
def merge_and_finalize_segments(
    changed_results: list[dict[str, Any]],
    project_id: str,
    slides: list[dict[str, Any]],
    rerender_page_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Merge rerendered segment outputs with unchanged artifacts, then finalize full lesson."""
    try:
        from scripts.ffmpeg_helpers import get_audio_duration
    except ImportError as exc:
        raise RuntimeError(f"ffmpeg_helpers not importable — check PYTHONPATH. Error: {exc}") from exc

    changed_by_key = {
        str(item.get("page_key") or ""): item
        for item in (changed_results or [])
        if isinstance(item, dict)
    }
    rerender_set = {str(key) for key in (rerender_page_keys or []) if str(key)}

    full_results: list[dict[str, Any]] = []
    for slide in slides:
        page_key = str(slide.get("page_key") or "")
        if page_key and page_key in changed_by_key:
            full_results.append(changed_by_key[page_key])
            continue

        part_path = str(slide.get("part_out") or "")
        audio_path = str(slide.get("audio_out") or "")
        pause_seconds = float(slide.get("pause_seconds") or 2.2)
        duration = 0.0
        if audio_path and Path(audio_path).exists():
            duration = float(get_audio_duration(audio_path)) + max(pause_seconds, 0.0)

        slide_index = int(slide.get("index") or 0)
        avatar_segment_abs = Path(_avatar_storage_root()) / str(project_id) / "avatar_segments" / f"avatar_{int(slide.get('slide_num') or 0):03d}.mp4"
        avatar_rel = _safe_rel_path(_avatar_storage_root(), str(avatar_segment_abs)) if avatar_segment_abs.exists() else ""

        full_results.append(
            {
                "index": slide_index,
                "slide_num": int(slide.get("slide_num") or 0),
                "page_key": page_key,
                "source_slide_index": slide.get("source_slide_index", slide_index),
                "split_index": slide.get("split_index", 0),
                "part_path": part_path,
                "duration": duration,
                "pause_seconds": pause_seconds,
                "text": str(slide.get("narration_text") or slide.get("notes_text") or ""),
                "original_text": str(slide.get("original_text") or slide.get("notes_text") or ""),
                "spoken_text": "",
                "tts_normalization_language": "",
                "tts_normalization_rules_applied": [],
                "tts_provider": "cached",
                "tts_provider_preference": "",
                "tts_normalization_enabled": None,
                "tts_normalization_mode": "",
                "tts_unknown_word_strategy": "",
                "tts_applied_overrides": {},
                "tts_fallback_used": False,
                "tts_fallback_reason": "",
                "tts_settings": {},
                "tts_preprocessing_warnings": [],
                "slide_path": str(slide.get("image_path") or ""),
                "tts_audio_path": audio_path,
                "subtitle_chunks": list(slide.get("subtitle_chunks") or []),
                "whiteboard_mode": bool(slide.get("whiteboard_mode")),
                "avatar_applied": bool(avatar_rel),
                "avatar_engine_used": "cached",
                "avatar_fallback_chain": [],
                "avatar_segment_rel_path": avatar_rel,
                "avatar_attempted": bool(avatar_rel),
                "avatar_failed": False,
                "avatar_status": "ready" if avatar_rel else "none",
                "avatar_error": "",
                "avatar_warning": "",
                "avatar_failure_reason": "",
            }
        )

    # Defensive sort keeps deterministic segment order for concatenation.
    full_results = sorted(full_results, key=lambda item: int(item.get("index") or 0))
    return concat_and_finalize.apply(args=[full_results, project_id]).result


# ---------------------------------------------------------------------------
# Entry-point — kept for backward compatibility
# ---------------------------------------------------------------------------

@app.task(name="worker.tasks.mark_project_render_failed", max_retries=0)
def mark_project_render_failed(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Errback for dispatched render chords so failed headers do not leave jobs running."""
    project_id = kwargs.get("project_id")
    if project_id is None and args:
        project_id = args[-1]
    error_parts = [
        _concise_error_text(arg, fallback="", limit=240)
        for arg in args[:-1]
        if _concise_error_text(arg, fallback="", limit=240)
    ]
    error_message = "render_pipeline_failed"
    if error_parts:
        error_message = f"{error_message}: {'; '.join(error_parts[:3])}"
    if project_id is not None:
        logger.error("Render pipeline failed for project=%s errback_args=%s", project_id, args[:-1])
        if _is_job_cancelled(project_id):
            _update_job(project_id, status="cancelled", progress=100, error_message=JOB_CANCELLED_MARKER)
        else:
            _update_job(project_id, status="failed", progress=100, error_message=error_message)
    return {"status": "failed", "project_id": project_id, "error_message": error_message}


@app.task(
    bind=True,
    name="worker.tasks.process_pptx_to_video",
    max_retries=0,
)
def process_pptx_to_video(
    self,
    project_id: str,
    pptx_path: str,
    voice_id: str,
    pause_sec: float = 2.2,
    lang_hint: str = "auto",
    tts_mode: str = "service",
    whiteboard_mode_all: bool = False,
    avatar_options: dict[str, Any] | None = None,
    rerender_page_keys: list[str] | None = None,
    tts_settings: dict[str, Any] | None = None,
    render_profile: str = "balanced",
) -> dict[str, Any]:
    """
    Orchestrate the full PPTX → lesson MP4 pipeline.

    Execution model
    ---------------
    1. Runs :func:`export_project` **inline** (same worker process via
       ``apply()``) to avoid a queue round-trip for a fast I/O step.
    2. Builds a Celery ``chord``:
       - **header**: ``group`` of :func:`synthesize_and_render_slide` tasks —
         one per slide, dispatched simultaneously to available workers.
       - **callback**: :func:`concat_and_finalize` — runs after all slides
         succeed; if any slide fails the chord propagates the error (fail-fast).
    3. Dispatches the chord and **returns immediately** with a lightweight
       status dict so the API caller is not blocked.

    The ``send_task`` name ``"worker.tasks.process_pptx_to_video"`` is
    unchanged for backward compatibility with existing API callers.

    Returns
    -------
    dict: ``status``, ``chord_id``, ``n_slides``, ``project_id``.
    Poll ``chord_id`` via the Celery result backend, or watch the Job record
    (updated by :func:`concat_and_finalize`).
    """
    logger.info("=== process_pptx_to_video START project=%s ===", project_id)
    if _is_job_cancelled(project_id):
        raise RuntimeError(JOB_CANCELLED_MARKER)
    _update_job(project_id, status="running", progress=0)
    _upsert_job_checkpoint(
        project_id,
        stage_name="pipeline",
        stage_status="running",
        payload={"step": "start"},
    )
    _upsert_job_checkpoint(
        project_id,
        stage_name="export",
        stage_status="running",
        payload={"step": "export_start"},
    )
    self.update_state(state="PROGRESS", meta={"step": "start", "progress": 0})

    try:
        # ------------------------------------------------------------------
        # Step 1: Export slides inline
        # ------------------------------------------------------------------
        export_status = _checkpoint_status(project_id, "export")
        manifest_slides = _read_export_manifest(project_id) if export_status == "done" else None
        if manifest_slides:
            logger.info("Step 1 resume: using export manifest for project=%s", project_id)
            slides = _sync_transcript_pages_from_export(project_id, manifest_slides)
        else:
            if _is_job_cancelled(project_id):
                raise RuntimeError(JOB_CANCELLED_MARKER)
            logger.info("Step 1: exporting slides for project=%s ...", project_id)
            export_result = export_project.apply(args=[project_id, pptx_path, whiteboard_mode_all])
            if export_result.failed():
                raise RuntimeError(f"export_project raised: {export_result.result}")
            slides = _sync_transcript_pages_from_export(project_id, export_result.result)
            _write_export_manifest(project_id, slides=slides, source_path=str(pptx_path))
        n_slides = len(slides)
        logger.info("Step 1 done: %d slides ready for parallel rendering", n_slides)
        _upsert_job_checkpoint(
            project_id,
            stage_name="export",
            stage_status="done",
            payload={"slides": n_slides, "source_path": str(pptx_path)},
        )

        language_detection = _detect_language_from_slides(slides, lang_hint=lang_hint)
        resolved_lang = language_detection.get("resolved_language") or "tr"
        _write_language_detection_sidecar(project_id, language_detection)
        logger.info(
            "Language resolved for project=%s -> %s (source=%s confidence=%s)",
            project_id,
            resolved_lang,
            language_detection.get("source"),
            language_detection.get("confidence"),
        )

        _update_job(project_id, progress=10)
        tts_settings_summary = _summarize_tts_settings(tts_settings)

        profile_name = str(render_profile or "balanced").strip().lower()
        if profile_name not in {"fast", "balanced", "quality"}:
            profile_name = "balanced"

        teacher_id: int | None = None
        avatar_cfg = dict(avatar_options or {})
        if not avatar_cfg:
            try:
                from core.models import Project

                project_row = Project.objects.filter(pk=int(project_id)).select_related("user").first()
                teacher_id = int(project_row.user_id) if project_row and project_row.user_id else None
            except Exception:
                teacher_id = None

            teacher_avatar_cfg = _get_teacher_avatar_config(teacher_id)
            avatar_cfg = {
                "enabled": bool(teacher_avatar_cfg.get("enabled")),
                "teacher_id": teacher_id,
                "source_image_rel_path": teacher_avatar_cfg.get("processed_rel_path", ""),
                "source_image_original_rel_path": teacher_avatar_cfg.get("source_rel_path", ""),
                "source_video_rel_path": teacher_avatar_cfg.get("video_rel_path", ""),
                "avatar_reference_type": teacher_avatar_cfg.get("reference_type", "image"),
                "motion_preset": teacher_avatar_cfg.get("motion_preset", "natural"),
                "quality_preset": teacher_avatar_cfg.get("quality_preset", "high"),
                "lipsync_engine": teacher_avatar_cfg.get("lipsync_engine", "musetalk"),
                "model_version": teacher_avatar_cfg.get("model_version", "musetalk:v1"),
                "avatar_source_valid": bool(teacher_avatar_cfg.get("avatar_source_valid")),
                "avatar_source_validation_error": str(teacher_avatar_cfg.get("avatar_source_validation_error") or ""),
                "avatar_source_hash": str(teacher_avatar_cfg.get("avatar_source_hash") or ""),
                "avatar_preview_stale": bool(teacher_avatar_cfg.get("avatar_preview_stale")),
                "avatar_preview_source_hash": str(teacher_avatar_cfg.get("avatar_preview_source_hash") or ""),
                "composite_fallback_allowed": False,
            }
        else:
            teacher_id = int(avatar_cfg.get("teacher_id")) if avatar_cfg.get("teacher_id") else None
        if profile_name == "fast":
            avatar_cfg["quality_preset"] = "low"
        elif profile_name == "quality":
            avatar_cfg["quality_preset"] = "high"
        elif not avatar_cfg.get("quality_preset"):
            avatar_cfg["quality_preset"] = "medium"
        avatar_cfg["composite_fallback_allowed"] = bool(avatar_cfg.get("composite_fallback_allowed", False))
        self.update_state(
            state="PROGRESS",
            meta={
                "step": "export_done",
                "progress": 10,
                "n_slides": n_slides,
                "language_detection": language_detection,
            },
        )

        # ------------------------------------------------------------------
        # Step 2: Build and dispatch parallel chord
        # ------------------------------------------------------------------
        rerender_set = {str(key) for key in (rerender_page_keys or []) if str(key)}
        target_slides = [slide for slide in slides if not rerender_set or str(slide.get("page_key") or "") in rerender_set]
        if not target_slides:
            target_slides = slides

        pipeline_queue = _queue_for_pipeline(avatar_cfg, profile_name)
        render_dispatch_status = _checkpoint_status(project_id, "render_dispatch")
        recovered_results: list[dict[str, Any]] = []
        missing_page_keys: list[str] = []

        # Resume shortcut: if a previous attempt already dispatched render tasks and all
        # part artifacts exist, skip redispatch and finalize directly.
        if not rerender_set and render_dispatch_status == "done":
            try:
                from scripts.ffmpeg_helpers import get_audio_duration
            except Exception:
                get_audio_duration = None
            missing_parts = 0
            for slide in slides:
                part_path = str(slide.get("part_out") or "")
                audio_path = str(slide.get("audio_out") or "")
                if not part_path or not Path(part_path).exists():
                    missing_parts += 1
                    missing_page_keys.append(str(slide.get("page_key") or ""))
                    continue
                pause_seconds = max(float(slide.get("pause_seconds") or pause_sec), 0.0)
                duration = 0.0
                if audio_path and Path(audio_path).exists() and callable(get_audio_duration):
                    try:
                        duration = float(get_audio_duration(audio_path)) + pause_seconds
                    except Exception:
                        duration = 0.0
                recovered_results.append(
                    {
                        "index": int(slide.get("index") or 0),
                        "slide_num": int(slide.get("slide_num") or 0),
                        "page_key": slide.get("page_key"),
                        "source_slide_index": slide.get("source_slide_index", slide.get("index", 0)),
                        "split_index": slide.get("split_index", 0),
                        "part_path": part_path,
                        "duration": duration,
                        "pause_seconds": pause_seconds,
                        "text": str(slide.get("narration_text") or slide.get("notes_text") or ""),
                        "original_text": str(slide.get("original_text") or slide.get("notes_text") or ""),
                        "spoken_text": "",
                        "tts_normalization_language": "",
                        "tts_normalization_rules_applied": [],
                        "tts_provider": "",
                        "tts_provider_preference": "",
                        "tts_normalization_enabled": True,
                        "tts_normalization_mode": "",
                        "tts_unknown_word_strategy": "",
                        "tts_applied_overrides": {},
                        "tts_fallback_used": False,
                        "tts_fallback_reason": "",
                        "tts_settings": tts_settings_summary,
                        "tts_preprocessing_warnings": [],
                        "slide_path": str(slide.get("image_path") or ""),
                        "tts_audio_path": audio_path,
                        "subtitle_chunks": list(slide.get("subtitle_chunks") or []),
                        "whiteboard_mode": bool(slide.get("whiteboard_mode")),
                        "avatar_applied": False,
                        "avatar_engine_used": "none",
                        "avatar_fallback_chain": [],
                        "avatar_segment_rel_path": "",
                        "avatar_attempted": False,
                        "avatar_skipped": False,
                        "avatar_failed": False,
                        "avatar_status": "none",
                        "avatar_error": "",
                        "avatar_warning": "",
                        "avatar_failure_reason": "",
                        "avatar_motion_validation": {},
                    }
                )
            if recovered_results and missing_parts == 0 and len(recovered_results) == len(slides):
                logger.info(
                    "Resume shortcut: finalizing from recovered part artifacts project=%s count=%s",
                    project_id,
                    len(recovered_results),
                )
                _upsert_job_checkpoint(
                    project_id,
                    stage_name="concat_finalize",
                    stage_status="running",
                    payload={"source": "resume_shortcut", "parts_count": len(recovered_results)},
                )
                finalized = concat_and_finalize.apply(args=[recovered_results, project_id]).result
                return {
                    "status": "resumed_finalized",
                    "project_id": project_id,
                    "n_slides": len(slides),
                    "resume_source": "render_dispatch_checkpoint+part_artifacts",
                    "finalize_result": finalized,
                    "language_detection": language_detection,
                    "render_profile": profile_name,
                }
            if missing_page_keys:
                rerender_set = {key for key in missing_page_keys if key}
                target_slides = [slide for slide in slides if str(slide.get("page_key") or "") in rerender_set]
                logger.info(
                    "Resume partial rerender: project=%s missing_parts=%s total_slides=%s",
                    project_id,
                    len(target_slides),
                    len(slides),
                )

        target_total = len(target_slides)
        if _is_job_cancelled(project_id):
            raise RuntimeError(JOB_CANCELLED_MARKER)

        def _slide_render_signature(slide: dict[str, Any], render_index: int):
            errback = mark_project_render_failed.s(project_id).set(queue=pipeline_queue)
            return synthesize_and_render_slide.s(
                slide,
                project_id,
                voice_id,
                pause_sec,
                resolved_lang,
                tts_mode,
                avatar_cfg,
                tts_settings,
                render_index,
                target_total,
            ).set(queue=pipeline_queue, link_error=errback)

        slide_tasks = group(
            _slide_render_signature(slide, i)
            for i, slide in enumerate(target_slides, start=1)
        )
        if rerender_set:
            callback = merge_and_finalize_segments.s(project_id, slides, list(rerender_set)).set(queue=pipeline_queue)
        else:
            callback = concat_and_finalize.s(project_id).set(queue=pipeline_queue)

        _upsert_job_checkpoint(
            project_id,
            stage_name="render_dispatch",
            stage_status="running",
            payload={"queue": pipeline_queue, "target_slides": len(target_slides)},
        )
        pipeline     = chord(slide_tasks, callback)
        async_result = pipeline.apply_async(queue=pipeline_queue)

        logger.info(
            "Chord dispatched: chord_id=%s n_slides=%d project=%s queue=%s",
            async_result.id, n_slides, project_id, pipeline_queue,
        )
        _upsert_job_checkpoint(
            project_id,
            stage_name="render_dispatch",
            stage_status="done",
            payload={
                "queue": pipeline_queue,
                "chord_id": str(async_result.id),
                "target_slides": len(target_slides),
                "render_profile": profile_name,
                "source": "resume_partial" if missing_page_keys else "normal",
                "missing_parts_count": len(missing_page_keys),
            },
        )

        return {
            "status":     "dispatched",
            "chord_id":   async_result.id,
            "n_slides":   len(target_slides),
            "project_id": project_id,
            "language_detection": language_detection,
            "rerender_page_keys": list(rerender_set),
            "tts_settings": tts_settings_summary,
            "render_profile": profile_name,
            "avatar": {
                "enabled": bool(avatar_cfg.get("enabled")),
                "teacher_id": teacher_id,
                "source_image_rel_path": avatar_cfg.get("source_image_rel_path", ""),
                "source_video_rel_path": avatar_cfg.get("source_video_rel_path", ""),
                "avatar_reference_type": avatar_cfg.get("avatar_reference_type", "image"),
                "avatar_source_valid": bool(avatar_cfg.get("avatar_source_valid")),
                "avatar_source_validation_error": str(avatar_cfg.get("avatar_source_validation_error") or ""),
                "avatar_preview_stale": bool(avatar_cfg.get("avatar_preview_stale")),
                "composite_fallback_allowed": bool(avatar_cfg.get("composite_fallback_allowed")),
            },
        }

    except Exception as exc:
        error_trace = tb.format_exc()
        logger.exception("process_pptx_to_video FAILED for project=%s", project_id)
        if JOB_CANCELLED_MARKER in str(error_trace):
            _update_job(project_id, status="cancelled", error_message=JOB_CANCELLED_MARKER)
        else:
            _update_job(project_id, status="failed", error_message=error_trace)
        _upsert_job_checkpoint(
            project_id,
            stage_name="pipeline",
            stage_status="failed",
            payload={
                "error": _concise_error_text(error_trace, fallback="pipeline_failed", limit=800),
                "exc_type": type(exc).__name__,
            },
        )
        self.update_state(
            state="FAILURE",
            meta={
                "exc_type":    type(exc).__name__,
                "exc_message": str(exc),
                "project_id":  project_id,
            },
        )
        raise
