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

_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS = int(float(os.environ.get("AVATAR_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS", "900") or 900))
_PREVIEW_TASK_HARD_TIMEOUT_SECONDS = int(
    float(
        os.environ.get(
            "AVATAR_PREVIEW_TASK_HARD_TIMEOUT_SECONDS",
            str(_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS + 60),
        )
        or (_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS + 60)
    )
)
if _PREVIEW_TASK_HARD_TIMEOUT_SECONDS <= _PREVIEW_TASK_SOFT_TIMEOUT_SECONDS:
    _PREVIEW_TASK_HARD_TIMEOUT_SECONDS = _PREVIEW_TASK_SOFT_TIMEOUT_SECONDS + 60


@worker_ready.connect
def _log_avatar_engine_startup_status(sender=None, **kwargs):
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

    existing = {page.page_key: page for page in TranscriptPage.objects.filter(project=project).order_by("order", "id")}
    seen_keys: set[str] = set()
    updated_slides: list[dict[str, Any]] = []

    for order, slide in enumerate(slides or []):
        slide_payload = dict(slide)
        page_key = str(slide_payload.get("page_key") or f"s{int(slide_payload.get('source_slide_num') or slide_payload.get('slide_num') or order + 1)}-p{int(slide_payload.get('split_index') or 0) + 1}")
        original_text = str(slide_payload.get("original_text") or slide_payload.get("notes_text") or "")
        subtitle_chunks = list(slide_payload.get("subtitle_chunks") or ([original_text] if original_text else []))
        page = existing.get(page_key)
        if page is None:
            page = TranscriptPage(project=project, page_key=page_key)

        page.order = int(slide_payload.get("index") or order)
        page.source_slide_index = int(slide_payload.get("source_slide_index") or slide_payload.get("index") or order)
        page.split_index = int(slide_payload.get("split_index") or 0)
        page.original_text = original_text
        if not str(page.narration_text or "").strip():
            page.narration_text = str(slide_payload.get("narration_text") or original_text)
        if not str(page.rich_text_html or "").strip():
            page.rich_text_html = str(slide_payload.get("rich_text_html") or "")
        if not dict(page.editor_document or {}):
            page.editor_document = dict(slide_payload.get("editor_document") or {})
        if not list(page.subtitle_chunks or []):
            page.subtitle_chunks = subtitle_chunks
        if not bool(page.whiteboard_mode):
            page.whiteboard_mode = bool(slide_payload.get("whiteboard_mode"))
        page.save()

        seen_keys.add(page_key)
        slide_payload.update(
            {
                "page_key": page_key,
                "original_text": str(page.original_text or original_text),
                "narration_text": str(page.narration_text or original_text),
                "rich_text_html": str(page.rich_text_html or ""),
                "editor_document": dict(page.editor_document or {}),
                "subtitle_chunks": list(page.subtitle_chunks or subtitle_chunks),
                "whiteboard_mode": bool(page.whiteboard_mode),
            }
        )
        updated_slides.append(slide_payload)

    stale_keys = [key for key in existing.keys() if key not in seen_keys]
    if stale_keys:
        TranscriptPage.objects.filter(project=project, page_key__in=stale_keys).delete()
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
        if page is None:
            page = TranscriptPage(
                project=project,
                page_key=page_key,
                order=int(item.get("order") or 0),
                source_slide_index=int(item.get("source_slide_index") or 0),
                split_index=int(item.get("split_index") or 0),
            )
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
            profile.avatar_lipsync_engine or profile.avatar_engine_primary or os.environ.get("AVATAR_ENGINE")
        ),
        "model_version": str(profile.avatar_model_version or "liveportrait+musetalk:v1"),
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

    return render_avatar_preview_canonical(self, teacher_id=teacher_id, job_id=job_id)

