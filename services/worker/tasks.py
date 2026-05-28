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
import html
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
SCENE_RENDER_CANVAS_SIZE = (1600, 900)
SCENE_RENDER_TEXT_FONT_SIZE = 60

_PREVIEW_TASK_TIME_LIMITS = resolve_preview_task_time_limits(logger=logger)
_PREVIEW_TASK_SOFT_TIMEOUT_SECONDS = _PREVIEW_TASK_TIME_LIMITS.soft_seconds
_PREVIEW_TASK_HARD_TIMEOUT_SECONDS = _PREVIEW_TASK_TIME_LIMITS.hard_seconds


def _render_queue_name() -> str:
    return str(os.environ.get("CELERY_RENDER_QUEUE", "render") or "render").strip() or "render"


def _avatar_queue_name() -> str:
    return str(os.environ.get("CELERY_AVATAR_QUEUE", "avatar") or "avatar").strip() or "avatar"


def _intelligence_queue_name() -> str:
    return str(
        os.environ.get("INTELLIGENCE_CELERY_QUEUE")
        or os.environ.get("CELERY_INTELLIGENCE_QUEUE")
        or os.environ.get("INTELLIGENCE_CELERY_QUEUE_DEFAULT")
        or _render_queue_name()
    ).strip() or _render_queue_name()


def _lesson_intelligence_queue_name() -> str:
    return str(os.environ.get("INTELLIGENCE_LESSON_CELERY_QUEUE") or _intelligence_queue_name()).strip() or _intelligence_queue_name()


def _analytics_intelligence_queue_name() -> str:
    return str(os.environ.get("INTELLIGENCE_ANALYTICS_CELERY_QUEUE") or _intelligence_queue_name()).strip() or _intelligence_queue_name()


def _queue_for_avatar_options(avatar_options: dict[str, Any] | None) -> str:
    return _avatar_queue_name() if bool((avatar_options or {}).get("enabled")) else _render_queue_name()


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_explicit(name: str) -> bool:
    return name in os.environ


def _avatar_feature_enabled() -> bool:
    if _env_explicit("ENABLE_AVATAR"):
        return _env_enabled("ENABLE_AVATAR", False)
    try:
        from django.conf import settings

        if bool(getattr(settings, "ENABLE_AVATAR", False)):
            return True
    except Exception:
        pass
    legacy_names = (
        "AVATAR_ENGINE",
        "AVATAR_LIVEPORTRAIT_CMD",
        "AVATAR_MUSETALK_CMD",
        "AVATAR_ENABLE_COMPOSITE_LESSON",
        "AVATAR_BOOTSTRAP_ON_WORKER_STARTUP",
    )
    return any(str(os.environ.get(name, "")).strip() for name in legacy_names)


def _intelligence_feature_enabled() -> bool:
    if _env_explicit("ENABLE_INTELLIGENCE"):
        return _env_enabled("ENABLE_INTELLIGENCE", False)
    try:
        from django.conf import settings

        if bool(getattr(settings, "ENABLE_INTELLIGENCE", False)):
            return True
        if bool(getattr(settings, "LESSON_INTELLIGENCE_ENABLED", False)):
            return True
        if bool(getattr(settings, "ANALYTICS_INTELLIGENCE_ENABLED", False)):
            return True
    except Exception:
        pass
    return (
        _env_enabled("LESSON_INTELLIGENCE_ENABLED", False)
        or _env_enabled("ANALYTICS_INTELLIGENCE_ENABLED", False)
    )


def _visual_moderation_feature_enabled() -> bool:
    if _env_explicit("ENABLE_VISUAL_MODERATION"):
        return _env_enabled("ENABLE_VISUAL_MODERATION", False)
    try:
        from django.conf import settings

        if bool(getattr(settings, "ENABLE_VISUAL_MODERATION", False)):
            return True
    except Exception:
        pass
    return (
        _env_enabled("VISUAL_MODERATION_AUTO_ENABLED", False)
        or _env_enabled("OCR_MODERATION_AUTO_ENABLED", False)
        or _env_enabled("VIDEO_FRAME_AUDIT_AUTO_ENABLED", False)
        or _env_enabled("VISUAL_SAFETY_CLASSIFIER_ENABLED", False)
        or _env_enabled("AZURE_CONTENT_SAFETY_ENABLED", False)
        or _env_enabled("AZURE_OCR_ENABLED", False)
    )


def _settings_bool(name: str, default: bool = False) -> bool:
    try:
        from django.conf import settings

        if hasattr(settings, name):
            return bool(getattr(settings, name))
    except Exception:
        pass
    return _env_enabled(name, default)


def _settings_str(name: str, default: str = "") -> str:
    try:
        from django.conf import settings

        if hasattr(settings, name):
            value = str(getattr(settings, name) or "").strip()
            return value or default
    except Exception:
        pass
    return str(os.environ.get(name, default) or default).strip() or default


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
    if not _avatar_feature_enabled():
        logger.info("Worker avatar bootstrap skipped because ENABLE_AVATAR is disabled")
        return
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
        try:
            from avatar.canonical_adapters import normalize_avatar_engine

            selected_engine = normalize_avatar_engine(os.environ.get("AVATAR_ENGINE"))
        except Exception:
            selected_engine = "liveportrait+musetalk"
        logger.info("Worker avatar bootstrap ready selected_engine=%s", selected_engine)
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
        "source_backgrounds": root / "source_backgrounds",
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

    job = (
        Job.objects.filter(project_id=int(project_id), job_type="video_export")
        .order_by("-created_at", "-id")
        .first()
    )
    if job is None:
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


def _update_job_by_id(
    job_id: str | int | None,
    *,
    status: str | None = None,
    progress: int | None = None,
    result_url: str | None = None,
    srt_url: str | None = None,
    error_message: str | None = None,
) -> bool:
    if not job_id:
        return False
    try:
        from core.models import Job
    except Exception:
        logger.warning("_update_job_by_id skipped for job=%s because core.models.Job is unavailable", job_id, exc_info=True)
        return False

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
    if not updates:
        return False
    return bool(Job.objects.filter(id=int(job_id)).update(**updates))


def _update_render_job(
    project_id: str | int,
    job_id: str | int | None = None,
    **updates: Any,
) -> None:
    if _update_job_by_id(job_id, **updates):
        return
    _update_job(project_id, **updates)


def _resolve_render_job_id(project_id: str | int, celery_task_id: str = "") -> int | None:
    try:
        from core.models import Job

        task_id = str(celery_task_id or "").strip()
        if task_id:
            row = (
                Job.objects.filter(project_id=int(project_id), job_type="video_export", celery_task_id=task_id)
                .order_by("-created_at", "-id")
                .values("id")
                .first()
            )
            if row:
                return int(row["id"])
        return _latest_project_job_id(project_id, job_type="video_export")
    except Exception:
        logger.warning("Render job id resolution failed for project=%s task=%s", project_id, celery_task_id, exc_info=True)
        return None


def _is_current_render_job(project_id: str | int, job_id: str | int | None) -> bool:
    if not job_id:
        return True
    latest_id = _latest_project_job_id(project_id, job_type="video_export")
    return latest_id is None or int(latest_id) == int(job_id)


def _mark_project_ready_after_successful_render(project_id: str | int) -> None:
    try:
        from django.utils import timezone
        from core.models import Project

        Project.objects.filter(pk=int(project_id)).update(
            status="ready",
            updated_at=timezone.now(),
        )
        logger.info("Project status set to ready for project=%s", project_id)
    except Exception:
        logger.warning(
            "Failed to update Project.status to ready for project=%s",
            project_id,
            exc_info=True,
        )


def _mark_project_avatar_state(
    project_id: str | int,
    *,
    status: str,
    message: str = "",
    job_id: str | int | None = None,
    output_path: str | None = None,
    clear_output: bool = False,
) -> None:
    try:
        from django.utils import timezone
        from core.models import Project

        updates: dict[str, Any] = {
            "avatar_processing_status": str(status or "none"),
            "avatar_processing_message": str(message or ""),
            "avatar_updated_at": timezone.now(),
            "updated_at": timezone.now(),
        }
        if job_id is not None:
            updates["avatar_last_job_id"] = str(job_id or "")
        if output_path is not None:
            updates["avatar_output_path"] = str(output_path or "")
        elif clear_output:
            updates["avatar_output_path"] = ""
        Project.objects.filter(pk=int(project_id)).update(**updates)
    except Exception:
        logger.warning("Failed to update avatar state for project=%s", project_id, exc_info=True)


def _notify_render_completed(project_id: str | int) -> None:
    try:
        from core.notifications import notify_render_completed

        notify_render_completed(project_id, _latest_video_export_job_id(project_id))
    except Exception:
        logger.warning("Render completion notification hook failed for project=%s", project_id, exc_info=True)


def _schedule_lesson_intelligence_after_worker_event(project_id: str | int, *, reason: str, force: bool = False) -> None:
    if not _intelligence_feature_enabled():
        logger.info("Lesson intelligence schedule skipped project=%s because ENABLE_INTELLIGENCE is disabled", project_id)
        return
    try:
        schedule_lesson_intelligence.apply_async(
            args=[int(project_id)],
            kwargs={"reason": str(reason or "auto"), "force": bool(force)},
            queue=_lesson_intelligence_queue_name(),
        )
    except Exception:
        logger.warning("Lesson intelligence worker schedule dispatch failed for project=%s", project_id, exc_info=True)


def _schedule_creator_analytics_after_worker_event(project_id: str | int, *, reason: str, force: bool = False) -> None:
    if not _intelligence_feature_enabled():
        logger.info("Analytics intelligence schedule skipped project=%s because ENABLE_INTELLIGENCE is disabled", project_id)
        return
    try:
        from core.models import Project

        project = Project.objects.filter(pk=int(project_id)).only("user_id").first()
        if project and project.user_id:
            schedule_creator_analytics_intelligence.apply_async(
                args=[int(project.user_id)],
                kwargs={"reason": str(reason or "auto"), "force": bool(force)},
                queue=_analytics_intelligence_queue_name(),
            )
    except Exception:
        logger.warning("Analytics intelligence worker schedule dispatch failed for project=%s", project_id, exc_info=True)


def _notify_render_failed(project_id: str | int) -> None:
    try:
        from core.notifications import notify_render_failed

        notify_render_failed(project_id, _latest_project_job_id(project_id, job_type="video_export"))
    except Exception:
        logger.warning("Render failure notification hook failed for project=%s", project_id, exc_info=True)


def _notify_avatar_completed(project_id: str | int, avatar_job_id: str | int | None = None) -> None:
    try:
        from core.notifications import notify_avatar_completed

        notify_avatar_completed(
            project_id,
            avatar_job_id or _latest_project_job_id(project_id, job_type="avatar_render"),
        )
    except Exception:
        logger.warning("Avatar completion notification hook failed for project=%s", project_id, exc_info=True)


def _notify_avatar_failed(project_id: str | int, avatar_job_id: str | int | None = None) -> None:
    try:
        from core.notifications import notify_avatar_failed

        notify_avatar_failed(
            project_id,
            avatar_job_id or _latest_project_job_id(project_id, job_type="avatar_render"),
        )
    except Exception:
        logger.warning("Avatar failure notification hook failed for project=%s", project_id, exc_info=True)


def _avatar_job_is_current(project_id: str | int, avatar_job_id: str | int | None) -> bool:
    if not avatar_job_id:
        return True
    try:
        from core.models import Project

        row = Project.objects.filter(pk=int(project_id)).values("avatar_last_job_id").first()
        if not row:
            return True
        current = str(row.get("avatar_last_job_id") or "").strip()
        return not current or current == str(avatar_job_id)
    except Exception:
        logger.warning("Avatar job freshness check skipped for project=%s job=%s", project_id, avatar_job_id, exc_info=True)
        return True


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


def _normalise_protection_mode(protection_mode: str | None) -> str:
    mode = str(protection_mode or "").strip().lower()
    return mode if mode in {"public", "secure_stream", "drm_protected"} else "secure_stream"


def should_package_hls_for_lesson(protection_mode: str | None, *, streaming_enabled: bool | None = None) -> bool:
    mode = _normalise_protection_mode(protection_mode)
    if mode in {"secure_stream", "drm_protected"}:
        return True
    configured_streaming = DRM_STREAMING_ENABLED if streaming_enabled is None else bool(streaming_enabled)
    return bool(configured_streaming)


def _drm_metadata_configured_for_worker() -> bool:
    legacy_enabled = _settings_bool("DRM_ENABLED", False)
    legacy_key_system = _settings_str("DRM_KEY_SYSTEM", "")
    legacy_license_url = _settings_str("DRM_LICENSE_URL", "")
    legacy_certificate_url = _settings_str("DRM_CERTIFICATE_URL", "")
    preferred_system = _settings_str("DRM_PREFERRED_SYSTEM", "").lower()
    inferred_legacy_system = {
        "com.widevine.alpha": "widevine",
        "com.microsoft.playready": "playready",
        "com.apple.fps.1_0": "fairplay",
    }.get(legacy_key_system.lower(), "")

    for name, requires_certificate in (("widevine", False), ("playready", False), ("fairplay", True)):
        env_prefix = f"DRM_{name.upper()}"
        system_enabled = _settings_bool(f"{env_prefix}_ENABLED", False)
        key_system = _settings_str(f"{env_prefix}_KEY_SYSTEM", "")
        license_url = _settings_str(f"{env_prefix}_LICENSE_URL", "")
        certificate_url = _settings_str(f"{env_prefix}_CERTIFICATE_URL", "")

        if not any((key_system, license_url, certificate_url)) and legacy_enabled:
            if preferred_system == name or inferred_legacy_system == name:
                system_enabled = True
                key_system = legacy_key_system
                license_url = legacy_license_url
                certificate_url = legacy_certificate_url

        if not system_enabled and legacy_enabled and (preferred_system == name or inferred_legacy_system == name):
            system_enabled = bool(key_system or license_url or certificate_url)

        if system_enabled and key_system and license_url and (not requires_certificate or certificate_url):
            return True
    return False


def _dedupe_warnings(warnings: list[str] | tuple[str, ...] | None) -> list[str]:
    return [
        warning
        for warning in dict.fromkeys(str(item or "").strip() for item in (warnings or []))
        if warning
    ]


def _hls_sidecar_payload(
    *,
    enabled: bool,
    manifest_rel_path: str = "",
    encrypted: bool = False,
    packaging_status: str,
    warnings: list[str] | None = None,
    segment_glob: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": bool(enabled),
        "manifest_rel_path": str(manifest_rel_path or ""),
        "encrypted": bool(encrypted),
        "packaging_status": str(packaging_status or "unknown"),
        "warnings": _dedupe_warnings(warnings),
    }
    if segment_glob:
        payload["segment_glob"] = segment_glob
    if encrypted:
        payload["drm_scheme"] = "hls-aes-128"
    elif enabled:
        payload["drm_scheme"] = "none"
    return payload


def _package_hls_assets_for_playback(
    *,
    project_id: str | int,
    final_video: str,
    output_dir: Path,
    output_rel_prefix: str,
    protection_mode: str | None,
    package_hls_stream_func,
    streaming_enabled: bool | None = None,
    hls_encryption_enabled: bool | None = None,
    hls_key_hex: str | None = None,
) -> dict[str, Any]:
    mode = _normalise_protection_mode(protection_mode)
    hls_required = mode in {"secure_stream", "drm_protected"}
    encrypt_hls = DRM_HLS_ENCRYPTION_ENABLED if hls_encryption_enabled is None else bool(hls_encryption_enabled)
    warnings: list[str] = []

    if mode == "drm_protected":
        if _settings_bool("LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM", True) and not encrypt_hls:
            warnings.append("drm_hls_encryption_required_but_disabled")
        if _settings_bool("LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM", True) and not _drm_metadata_configured_for_worker():
            warnings.append("drm_metadata_required_but_missing")

    if not should_package_hls_for_lesson(mode, streaming_enabled=streaming_enabled):
        return _hls_sidecar_payload(
            enabled=False,
            encrypted=False,
            packaging_status="not_required",
            warnings=warnings,
        )

    hls_dir = output_dir / "drm" / "hls"
    hls_rel_dir = f"{output_rel_prefix}/drm/hls"
    try:
        package_result = package_hls_stream_func(
            final_video,
            str(hls_dir),
            playlist_name="index.m3u8",
            segment_pattern="seg_%05d.ts",
            segment_time=6,
            encrypt=encrypt_hls,
            key_hex=DRM_HLS_KEY_HEX if hls_key_hex is None else hls_key_hex,
            key_uri="enc.key" if encrypt_hls else None,
            key_filename="enc.key",
        )
    except Exception as hls_exc:
        warnings.append("hls_packaging_failed")
        if hls_required:
            warnings.append("hls_required_but_missing")
        logger.warning("HLS packaging failed for project=%s: %s", project_id, hls_exc, exc_info=True)
        return _hls_sidecar_payload(
            enabled=False,
            encrypted=False,
            packaging_status="failed",
            warnings=warnings,
        )

    encrypted = bool(package_result.get("encrypted"))
    if mode == "drm_protected" and _settings_bool("LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM", True) and not encrypted:
        warnings.append("drm_hls_encryption_required_but_disabled")

    logger.info("HLS packaging complete for project=%s", project_id)
    return _hls_sidecar_payload(
        enabled=True,
        manifest_rel_path=f"{hls_rel_dir}/index.m3u8",
        encrypted=encrypted,
        packaging_status="packaged",
        warnings=warnings,
        segment_glob=f"{hls_rel_dir}/seg_*.ts",
    )


def _write_json_sidecar(project_id: str | int, file_name: str, payload: dict[str, Any]) -> str:
    target = Path(STORAGE_ROOT) / str(project_id) / str(file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(json.dumps(payload, ensure_ascii=True, indent=2))
    temp_path.replace(target)
    return str(target)


def _read_json_sidecar(project_id: str | int, file_name: str) -> dict[str, Any]:
    path = Path(STORAGE_ROOT) / str(project_id) / str(file_name)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read sidecar project=%s file=%s", project_id, file_name, exc_info=True)
        return {}
    return payload if isinstance(payload, dict) else {}


def _avatar_handoff_manifest_dir(project_id: str | int, job_id: str | int) -> Path:
    job_part = str(job_id or "unknown").strip() or "unknown"
    return Path(STORAGE_ROOT) / "projects" / str(project_id) / "renders" / job_part


def _write_avatar_handoff_manifest(project_id: str | int, job_id: str | int, payload: dict[str, Any]) -> str:
    target = _avatar_handoff_manifest_dir(project_id, job_id) / "avatar_handoff.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(json.dumps(payload, ensure_ascii=True, indent=2))
    temp_path.replace(target)
    return str(target)


def _read_avatar_handoff_manifest(path: str | Path) -> dict[str, Any]:
    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("avatar_handoff_manifest_path_missing")
    manifest_path = Path(raw_path)
    if not manifest_path.is_absolute() and not manifest_path.exists():
        manifest_path = Path(STORAGE_ROOT) / manifest_path
    if not manifest_path.exists() or not manifest_path.is_file():
        raise FileNotFoundError("avatar_handoff_manifest_missing")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("avatar_handoff_manifest_unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("avatar_handoff_manifest_invalid")
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("avatar_handoff_manifest_schema_unsupported")
    return payload


def _write_language_detection_sidecar(project_id: str | int, payload: dict[str, Any]) -> str:
    return _write_json_sidecar(project_id, "language_detection.json", payload)


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
    original_text = _clean_caption_text(getattr(page, "original_text", ""))
    if original_text:
        return original_text
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
        raw_text_value = editor_document.get("text")
        html_text = _clean_caption_text(
            editor_document.get("plain_text")
            or (raw_text_value if isinstance(raw_text_value, str) else "")
        )
        if html_text:
            return html_text
    return _clean_caption_text(getattr(page, "narration_text", ""))


def _page_caption_text(page: Any) -> str:
    return _clean_caption_text(getattr(page, "narration_text", "") or getattr(page, "original_text", ""))


def _caption_chunks_match_text(chunks: list[str], caption_text: str) -> bool:
    if not caption_text:
        return True
    joined = _normalize_caption_compare(" ".join(chunks))
    return joined == _normalize_caption_compare(caption_text)


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
    caption_text = _page_caption_text(page)
    if chunks and _caption_chunks_match_text(chunks, caption_text):
        return chunks
    if chunks and not caption_text:
        return chunks
    if chunks:
        logger.warning(
            "Subtitle chunks ignored because they do not match narration text: project=%s page_key=%s",
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
    caption_text = _page_caption_text(page)
    if not chunks and caption_text:
        chunks = [caption_text]
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
    caption_text = _page_caption_text(page)
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
        elif caption_text and len(raw_timeline) == 1:
            text = caption_text
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

    if caption_text and not safe_chunks:
        joined = _normalize_caption_compare(" ".join(cue["text"] for cue in cues))
        if joined != _normalize_caption_compare(caption_text):
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


def _read_playback_sidecar(project_id: str | int) -> dict[str, Any]:
    return _read_json_sidecar(project_id, "playback_assets.json")


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


def _text_is_probably_rtl(text: str) -> bool:
    return bool(re.search(r"[\u0591-\u07FF\uFB1D-\uFDFD\uFE70-\uFEFC]", str(text or "")))


def _text_width(draw: Any, text: str, font: Any) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return max(0, int(bbox[2] - bbox[0]))
    except Exception:
        return max(0, len(text) * 18)


def _split_word_for_width(draw: Any, word: str, font: Any, max_width: int) -> list[str]:
    if _text_width(draw, word, font) <= max_width:
        return [word]
    chunks: list[str] = []
    current = ""
    for char in word:
        candidate = f"{current}{char}"
        if current and _text_width(draw, candidate, font) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [word]


def _wrap_text_lines_for_width(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    normalized = _prepare_narration_for_tts(text)
    if not normalized:
        return []
    lines: list[str] = []
    for paragraph in normalized.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if _text_width(draw, candidate, font) <= max_width or not current:
                if not current and _text_width(draw, word, font) > max_width:
                    chunks = _split_word_for_width(draw, word, font, max_width)
                    lines.extend(chunks[:-1])
                    current = chunks[-1]
                    continue
                current = candidate
                continue
            lines.append(current)
            if _text_width(draw, word, font) > max_width:
                chunks = _split_word_for_width(draw, word, font, max_width)
                lines.extend(chunks[:-1])
                current = chunks[-1]
            else:
                current = word
        if current:
            lines.append(current)
    return lines


def _scene_text_scale(text_scale: float) -> float:
    try:
        scale = float(text_scale)
    except (TypeError, ValueError):
        scale = 1.0
    return max(0.75, min(scale, 2.0))


def _font_size_value(font: Any, fallback: int) -> int:
    return int(getattr(font, "size", fallback) or fallback)


def _compute_scene_text_overlay_layout(
    draw: Any,
    text: str,
    canvas_size: tuple[int, int],
    text_scale: float = 1.0,
    *,
    boxed: bool = False,
    min_font_size: int = 24,
) -> dict[str, Any]:
    canvas_width, canvas_height = canvas_size
    normalized = _prepare_narration_for_tts(text)
    rtl = _text_is_probably_rtl(normalized)
    scale = _scene_text_scale(text_scale)
    preferred_font_size = max(36, min(120, int(SCENE_RENDER_TEXT_FONT_SIZE * scale)))
    safe_top = int(canvas_height * 0.1)
    safe_bottom = canvas_height - safe_top
    safe_height = max(1, safe_bottom - safe_top)
    min_box_width = int(canvas_width * (0.34 if boxed else 0.18))
    best_layout: dict[str, Any] | None = None
    density = max(len(normalized) / 520, len(normalized.split()) / 85, 1.0)
    if density > 1.8:
        font_size = max(min_font_size, int(preferred_font_size / math.sqrt(density / 1.45)))
    else:
        font_size = preferred_font_size
    profile_options = (
        ((0.1, 0.035, 0.75, 1.26), (0.075, 0.025, 0.58, 1.18), (0.05, 0.015, 0.42, 1.1), (0.025, 0.0, 0.2, 1.04), (0.0, 0.0, 0.0, 1.02))
        if boxed
        else ((0.15, 0.0, 0.0, 1.26), (0.1, 0.0, 0.0, 1.18), (0.05, 0.0, 0.0, 1.1), (0.0, 0.0, 0.0, 1.04))
    )

    while font_size >= min_font_size:
        for outer_ratio, inner_x_ratio, inner_y_factor, line_factor in profile_options:
            outer_margin = int(canvas_width * outer_ratio)
            max_box_width = max(1, canvas_width - (outer_margin * 2))
            padding_x = int(canvas_width * inner_x_ratio) if boxed else 0
            padding_y = max(0, int(font_size * inner_y_factor)) if boxed else 0
            text_width = max(1, max_box_width - (padding_x * 2))
            font = _load_font(font_size)
            line_height = max(24, int(_font_size_value(font, font_size) * line_factor))
            lines = _wrap_text_lines_for_width(draw, normalized, font, text_width)
            if not lines:
                lines = [""]
            text_total_height = len(lines) * line_height
            box_height = text_total_height + (padding_y * 2)
            max_line_width = max((_text_width(draw, line, font) for line in lines), default=0)
            box_width = min(max_box_width, max(min_box_width, max_line_width + (padding_x * 2)))
            box_left = int((canvas_width - box_width) / 2)
            box_top = int(safe_top + max(0, (safe_height - box_height) / 2))
            layout = {
                "font": font,
                "font_size": font_size,
                "preferred_font_size": preferred_font_size,
                "line_height": line_height,
                "lines": lines,
                "rtl": rtl,
                "text_scale": scale,
                "padding_x": padding_x,
                "padding_y": padding_y,
                "box_left": box_left,
                "box_top": box_top,
                "box_right": box_left + box_width,
                "box_bottom": box_top + box_height,
                "box_width": box_width,
                "box_height": box_height,
                "content_left": box_left + padding_x,
                "content_right": box_left + box_width - padding_x,
                "content_top": box_top + padding_y,
                "safe_top": safe_top,
                "safe_bottom": safe_bottom,
                "truncated": False,
            }
            best_layout = layout
            if box_height <= safe_height:
                return layout
        font_size = int(font_size * 0.9)

    layout = best_layout or {
        "font": _load_font(min_font_size),
        "font_size": min_font_size,
        "preferred_font_size": preferred_font_size,
        "line_height": max(24, int(min_font_size * 1.04)),
        "lines": [normalized] if normalized else [""],
        "rtl": rtl,
        "text_scale": scale,
        "padding_x": 0,
        "padding_y": 0,
        "box_left": 0,
        "box_top": safe_top,
        "box_right": canvas_width,
        "box_bottom": safe_bottom,
        "box_width": canvas_width,
        "box_height": safe_height,
        "content_left": 0,
        "content_right": canvas_width,
        "content_top": safe_top,
        "safe_top": safe_top,
        "safe_bottom": safe_bottom,
        "truncated": True,
    }
    max_lines = max(1, int((safe_height - (layout["padding_y"] * 2)) / max(1, layout["line_height"])))
    layout["lines"] = list(layout["lines"])[:max_lines]
    text_total_height = len(layout["lines"]) * layout["line_height"]
    layout["box_height"] = min(safe_height, text_total_height + (layout["padding_y"] * 2))
    layout["box_top"] = int(safe_top + max(0, (safe_height - layout["box_height"]) / 2))
    layout["box_bottom"] = layout["box_top"] + layout["box_height"]
    layout["content_top"] = layout["box_top"] + layout["padding_y"]
    layout["truncated"] = True
    return layout


def _make_whiteboard_image(text: str, output_path: str, text_scale: float = 1.0) -> str:
    if Image is None or ImageDraw is None:
        raise RuntimeError("whiteboard_render_requires_pillow")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", SCENE_RENDER_CANVAS_SIZE, color="white")
    draw = ImageDraw.Draw(image)
    layout = _compute_scene_text_overlay_layout(draw, text, image.size, text_scale, boxed=False)
    if layout["truncated"]:
        logger.warning("Whiteboard text truncated to fit scene canvas")
    y = layout["content_top"]
    for line in layout["lines"]:
        line_width = min(layout["box_width"], _text_width(draw, line, layout["font"]))
        x = layout["content_right"] - line_width if layout["rtl"] else layout["content_left"]
        draw.text((x, y), line, fill="black", font=layout["font"])
        y += layout["line_height"]
    image.save(output, format="PNG")
    return str(output)


def _image_resample_lanczos() -> Any:
    resampling = getattr(Image, "Resampling", Image)
    return getattr(resampling, "LANCZOS", 1)


def _fit_image_to_scene_canvas(image: Any, fit: str = "contain") -> Any:
    if Image is None:
        return image
    target_width, target_height = SCENE_RENDER_CANVAS_SIZE
    fit_mode = str(fit or "contain").strip().lower()
    if fit_mode not in {"contain", "cover", "stretch"}:
        fit_mode = "contain"
    if fit_mode == "stretch":
        return image.resize(SCENE_RENDER_CANVAS_SIZE, _image_resample_lanczos())

    source_width, source_height = image.size
    if source_width <= 0 or source_height <= 0:
        return Image.new("RGBA", SCENE_RENDER_CANVAS_SIZE, (5, 8, 14, 255))

    scale = (
        max(target_width / source_width, target_height / source_height)
        if fit_mode == "cover"
        else min(target_width / source_width, target_height / source_height)
    )
    if fit_mode == "cover":
        resized_width = max(1, int(math.ceil(source_width * scale)))
        resized_height = max(1, int(math.ceil(source_height * scale)))
    else:
        resized_width = max(1, int(round(source_width * scale)))
        resized_height = max(1, int(round(source_height * scale)))
    resized = image.resize((resized_width, resized_height), _image_resample_lanczos())

    if fit_mode == "cover":
        left = max(0, int((resized_width - target_width) / 2))
        top = max(0, int((resized_height - target_height) / 2))
        return resized.crop((left, top, left + target_width, top + target_height))

    canvas = Image.new("RGBA", SCENE_RENDER_CANVAS_SIZE, (5, 8, 14, 255))
    left = int((target_width - resized_width) / 2)
    top = int((target_height - resized_height) / 2)
    canvas.alpha_composite(resized, (left, top))
    return canvas


def _render_transcript_overlay_image(
    base_image_path: str,
    display_text: str,
    rich_text_html: str,
    output_path: str,
    text_scale: float = 1.0,
    background_fit: str = "contain",
) -> str:
    source = Path(base_image_path)
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"transcript_overlay_source_missing:{source}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if Image is None or ImageDraw is None:
        shutil.copy2(str(source), str(output))
        return str(output)

    text = _prepare_narration_for_tts(display_text)
    if not text and rich_text_html:
        text = _prepare_narration_for_tts(re.sub(r"<[^>]+>", " ", rich_text_html))
    if not text:
        shutil.copy2(str(source), str(output))
        return str(output)

    image = _fit_image_to_scene_canvas(Image.open(source).convert("RGBA"), background_fit)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    layout = _compute_scene_text_overlay_layout(draw, text, image.size, text_scale, boxed=True)
    if layout["truncated"]:
        logger.warning("Custom background overlay text truncated to fit scene canvas")
    draw.rounded_rectangle(
        [(layout["box_left"], layout["box_top"]), (layout["box_right"], layout["box_bottom"])],
        radius=max(18, int(layout["font_size"] * 0.45)),
        fill=(0, 0, 0, 175),
    )
    text_total_height = len(layout["lines"]) * layout["line_height"]
    y = layout["box_top"] + max(layout["padding_y"], int((layout["box_height"] - text_total_height) / 2))
    for line in layout["lines"]:
        line_width = _text_width(draw, line, layout["font"])
        x = layout["content_right"] - line_width if layout["rtl"] else layout["content_left"]
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=layout["font"])
        y += layout["line_height"]

    combined = Image.alpha_composite(image, overlay).convert("RGB")
    combined.save(output, format="PNG")
    return str(output)


def _source_background_overflow_warnings(
    base_image_path: str,
    display_text: str,
    rich_text_html: str,
    *,
    text_scale: float,
    background_fit: str,
) -> list[str]:
    if Image is None or ImageDraw is None:
        return []
    source = Path(base_image_path)
    if not source.exists() or not source.is_file():
        return []
    text = _prepare_narration_for_tts(display_text)
    if not text and rich_text_html:
        text = _prepare_narration_for_tts(re.sub(r"<[^>]+>", " ", rich_text_html))
    if not text:
        return []
    try:
        image = _fit_image_to_scene_canvas(Image.open(source).convert("RGBA"), background_fit)
        draw = ImageDraw.Draw(Image.new("RGBA", image.size, (0, 0, 0, 0)))
        layout = _compute_scene_text_overlay_layout(draw, text, image.size, text_scale, boxed=True)
    except Exception:
        return []
    return ["source_background_text_overflow"] if layout.get("truncated") else []


def _render_avatar_safe_slide_image(source_image_path: str, output_path: str) -> str:
    source = Path(source_image_path)
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"avatar_safe_slide_source_missing:{source}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(output))
    return str(output)


_SCENE_BACKGROUND_MODES = {"original", "whiteboard", "custom", "source_background"}
_SCENE_BACKGROUND_FITS = {"contain", "cover", "stretch"}
_SOURCE_BACKGROUND_SUPPORTED_TYPES = {"pptx"}


def _scene_storage_rel_path(path_value: Any) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/").lstrip("/")
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return ""
    candidate = Path(raw)
    if not candidate.is_absolute():
        return normalized
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(Path(STORAGE_ROOT).resolve())
        return str(relative).replace("\\", "/")
    except Exception:
        return ""


def _scene_mode_from_value(value: Any, *, fallback: str) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in _SCENE_BACKGROUND_MODES else fallback


def _scene_fit_from_value(value: Any) -> str:
    fit = str(value or "").strip().lower()
    return fit if fit in _SCENE_BACKGROUND_FITS else "contain"


def _scene_text_scale_from_value(value: Any, *, fallback: float = 1.0) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        scale = fallback
    return max(0.75, min(scale, 2.0))


def _source_type_from_value(value: Any) -> str:
    return str(value or "").strip().lower().lstrip(".")


def _source_type_uses_visual_mapping(source_type: Any) -> bool:
    normalized = _source_type_from_value(source_type)
    return bool(normalized and normalized != "txt")


def _source_background_supported_for_source_type(source_type: Any) -> bool:
    return _source_type_from_value(source_type) in _SOURCE_BACKGROUND_SUPPORTED_TYPES


def _warning_list_from_value(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    elif value:
        items = [value]
    else:
        items = []
    return list(dict.fromkeys(str(item).strip() for item in items if str(item or "").strip()))


def _details_list_from_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    elif value:
        items = [{"message": str(value)}]
    else:
        items = []

    details: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            cleaned = {str(key): item[key] for key in item if str(key or "").strip()}
        else:
            cleaned = {"message": str(item)}
        if not cleaned:
            continue
        try:
            dedupe_key = json.dumps(cleaned, sort_keys=True, ensure_ascii=True, default=str)
        except TypeError:
            cleaned = {key: str(val) for key, val in cleaned.items()}
            dedupe_key = json.dumps(cleaned, sort_keys=True, ensure_ascii=True)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        details.append(cleaned)
    return details


def _single_source_page_structure(source_slide_index: int, text: str) -> dict[str, Any]:
    from scripts.text_segmentation import SegmentationConfig, normalize_source_text, split_narration_chunks

    cfg = SegmentationConfig()
    display_text = normalize_source_text(text)
    chunks = split_narration_chunks(
        display_text,
        max_chars=cfg.max_chunk_chars,
        min_chars=cfg.min_chunk_chars,
    ) if display_text else []
    return {
        "source_slide_index": source_slide_index,
        "split_index": 0,
        "page_key": f"s{source_slide_index + 1}-p1",
        "original_text": display_text,
        "narration_text": display_text,
        "subtitle_chunks": chunks,
    }


def _split_text_on_blank_lines(value: Any) -> list[str]:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]


def _normalized_text_for_compare(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _rich_text_html_from_display_text(value: Any) -> str:
    return html.escape(str(value or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br />")


def _plain_text_from_html(value: Any) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _split_consistent_display_text(display_text: Any, narration_text: Any) -> str:
    display = str(display_text or "")
    narration = str(narration_text or "")
    display_parts = _split_text_on_blank_lines(display)
    if len(display_parts) > 1 and _normalized_text_for_compare(narration):
        normalized_narration = _normalized_text_for_compare(narration)
        for part in display_parts:
            if _normalized_text_for_compare(part) == normalized_narration:
                return part
        return narration
    if not display.strip() and narration.strip():
        return narration
    return display


def _split_consistent_rich_text_html(display_text: Any, rich_text_html: Any) -> str:
    display = str(display_text or "")
    rich = str(rich_text_html or "")
    if rich and _normalized_text_for_compare(_plain_text_from_html(rich)) == _normalized_text_for_compare(display):
        return rich
    return _rich_text_html_from_display_text(display)


def _subtitle_chunks_for_render(chunks: Any, narration_text: Any) -> list[str]:
    narration = str(narration_text or "")
    safe_chunks = [str(chunk).strip() for chunk in (chunks or []) if str(chunk or "").strip()] if isinstance(chunks, list) else []
    if safe_chunks and _normalized_text_for_compare(" ".join(safe_chunks)) == _normalized_text_for_compare(narration):
        return safe_chunks
    return [narration] if narration.strip() else []


def _merge_scene_from_export(page: Any, slide_payload: dict[str, Any]) -> None:
    editor_document = dict(getattr(page, "editor_document", None) or {})
    scene = dict(editor_document.get("scene") or {})
    source_type = _source_type_from_value(slide_payload.get("source_type") or slide_payload.get("source_ext"))
    is_text_only_source = source_type == "txt"
    if source_type:
        scene["source_type"] = source_type
    original_background_path = _scene_storage_rel_path(
        slide_payload.get("original_background_path")
        or slide_payload.get("image_path")
        or slide_payload.get("slide_path")
    )
    has_original_background = bool(original_background_path)
    if is_text_only_source:
        scene.pop("original_background_path", None)
        if str(scene.get("background_mode") or "").strip().lower() == "original":
            scene.pop("background_mode", None)
    elif original_background_path:
        scene["original_background_path"] = original_background_path

    source_background_path = _scene_storage_rel_path(slide_payload.get("source_background_path"))
    source_background_warnings = _warning_list_from_value(slide_payload.get("source_background_warnings"))
    if source_type == "pptx" and source_background_path:
        scene["source_background_path"] = source_background_path
        scene["source_background_generated"] = True
    else:
        scene.pop("source_background_path", None)
        scene["source_background_generated"] = False
    if source_background_warnings:
        scene["source_background_warnings"] = source_background_warnings
    elif source_type != "pptx":
        scene.pop("source_background_warnings", None)

    fallback_mode = (
        "whiteboard"
        if bool(is_text_only_source or getattr(page, "whiteboard_mode", False) or slide_payload.get("whiteboard_mode"))
        else ("original" if has_original_background else "whiteboard")
    )
    mode = _scene_mode_from_value(scene.get("background_mode"), fallback=fallback_mode)
    if mode == "source_background" and not _source_background_supported_for_source_type(source_type):
        mode = "whiteboard"
        source_background_warnings = list(
            dict.fromkeys([*source_background_warnings, "source_background_unsupported_for_source_type"])
        )
    scene["background_mode"] = mode
    if source_background_warnings:
        scene["source_background_warnings"] = source_background_warnings
    scene["background_fit"] = _scene_fit_from_value(scene.get("background_fit"))
    scene["text_scale"] = _scene_text_scale_from_value(
        scene.get("text_scale"),
        fallback=1.0,
    )
    editor_document["scene"] = scene
    page.editor_document = editor_document


def _scene_background_image_for_render(scene: dict[str, Any], fallback_image_path: Any) -> str:
    mode = _scene_mode_from_value(scene.get("background_mode"), fallback="original")
    if mode == "whiteboard":
        return str(fallback_image_path or "")
    if mode == "custom":
        key = "custom_background_path"
    elif mode == "source_background":
        key = "source_background_path"
    else:
        key = "original_background_path"
    raw = str(scene.get(key) or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path(STORAGE_ROOT) / raw.lstrip("/\\")
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    if mode in {"custom", "source_background"}:
        return ""
    return str(fallback_image_path or "")


def _scene_render_warning_list(slide_payload: dict[str, Any], scene: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *_warning_list_from_value(slide_payload.get("source_background_warnings")),
                *_warning_list_from_value(scene.get("source_background_warnings")),
            ]
        )
    )


def _normalize_scene_mode_for_render(
    scene_mode: str,
    *,
    source_type: Any,
    render_image_path: str,
    warnings: list[str],
) -> tuple[str, list[str], bool]:
    normalized_source_type = _source_type_from_value(source_type)
    if scene_mode == "source_background" and not _source_background_supported_for_source_type(normalized_source_type):
        return (
            "whiteboard",
            list(dict.fromkeys([*warnings, "source_background_unsupported_for_source_type"])),
            True,
        )
    if scene_mode == "source_background" and not str(render_image_path or "").strip():
        return (
            scene_mode,
            list(dict.fromkeys([*warnings, "source_background_missing_fallback_whiteboard"])),
            True,
        )
    if scene_mode == "custom" and not str(render_image_path or "").strip():
        return (
            "whiteboard",
            list(dict.fromkeys([*warnings, "custom_background_missing_fallback_whiteboard"])),
            True,
        )
    return scene_mode, warnings, scene_mode == "whiteboard"


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
        _merge_scene_from_export(page, slide_payload)
        subtitle_source = str(page.narration_text or page.original_text or original_text)
        subtitle_chunks = list(slide_payload.get("subtitle_chunks") or ([subtitle_source] if subtitle_source else []))
        if not list(page.subtitle_chunks or []):
            page.subtitle_chunks = subtitle_chunks
        if is_new_page and not bool(page.whiteboard_mode):
            stored_scene = dict(page.editor_document or {}).get("scene")
            stored_scene = stored_scene if isinstance(stored_scene, dict) else {}
            page.whiteboard_mode = _scene_mode_from_value(
                stored_scene.get("background_mode"),
                fallback="whiteboard" if bool(slide_payload.get("whiteboard_mode")) else "original",
            ) == "whiteboard"
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
        editor_document = dict(page.editor_document or {})
        scene = dict(editor_document.get("scene") or {})
        source_type = _source_type_from_value(
            slide_payload.get("source_type") or scene.get("source_type") or slide_payload.get("source_ext")
        )
        scene_mode = _scene_mode_from_value(
            scene.get("background_mode"),
            fallback="whiteboard" if bool(page.whiteboard_mode) else "original",
        )
        if (
            scene_mode == "source_background"
            and _source_background_supported_for_source_type(source_type)
            and not scene.get("source_background_path")
        ):
            payload_source_background = _scene_storage_rel_path(slide_payload.get("source_background_path"))
            if payload_source_background:
                scene["source_background_path"] = payload_source_background
        render_image_path = _scene_background_image_for_render(scene, slide_payload.get("image_path") or slide_payload.get("slide_path") or "")
        source_background_warnings = _scene_render_warning_list(slide_payload, scene)
        scene_mode, source_background_warnings, effective_whiteboard_mode = _normalize_scene_mode_for_render(
            scene_mode,
            source_type=source_type,
            render_image_path=render_image_path,
            warnings=source_background_warnings,
        )
        if effective_whiteboard_mode:
            render_image_path = "" if scene_mode == "whiteboard" else render_image_path
        narration_text = str(page.narration_text or slide_payload.get("narration_text") or slide_payload.get("notes_text") or "")
        raw_display_text = str(
            page.original_text
            or slide_payload.get("display_text")
            or slide_payload.get("original_text")
            or slide_payload.get("notes_text")
            or ""
        )
        display_text = _split_consistent_display_text(raw_display_text, narration_text)
        rich_text_html = _split_consistent_rich_text_html(display_text, page.rich_text_html)
        subtitle_chunks = _subtitle_chunks_for_render(page.subtitle_chunks, narration_text)
        slide_payload.update(
            {
                "index": display_index,
                "slide_num": display_index + 1,
                "source_slide_index": int(page.source_slide_index or 0),
                "source_slide_num": int(page.source_slide_index or 0) + 1,
                "split_index": int(page.split_index or 0),
                "page_key": str(page.page_key or ""),
                "notes_text": narration_text,
                "original_text": display_text,
                "display_text": display_text,
                "narration_text": narration_text,
                "rich_text_html": rich_text_html,
                "editor_document": editor_document,
                "subtitle_chunks": subtitle_chunks,
                "source_type": source_type,
                "whiteboard_mode": effective_whiteboard_mode,
                "scene_background_mode": scene_mode,
                "source_background_warnings": source_background_warnings,
                "custom_background_path": scene.get("custom_background_path") or "",
                "scene_background_fit": _scene_fit_from_value(scene.get("background_fit")),
                "scene_text_scale": _scene_text_scale_from_value(
                    scene.get("text_scale"),
                    fallback=1.0,
                ),
                "image_path": render_image_path,
                "audio_out": str(ws["audio"] / f"slide_{display_index + 1:03d}.mp3"),
                "part_out": str(ws["parts"] / f"part_{display_index + 1:03d}.mp4"),
            }
        )
        updated_slides.append(slide_payload)

    return updated_slides


def _draft_render_tts_settings(project_id: str | int, fallback: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        from core.models import Project
        from core.drafts import get_draft_project_fields
        from core.serializers import canonical_project_tts_settings
    except Exception:
        logger.warning("Draft TTS settings unavailable for project=%s", project_id, exc_info=True)
        return fallback

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return fallback
    draft_fields = get_draft_project_fields(project)
    if isinstance(draft_fields.get("tts_settings"), dict):
        return canonical_project_tts_settings(draft_fields.get("tts_settings"))
    return fallback


def _build_render_slides_from_draft(project_id: str | int, exported_slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from core.models import Project
        from core.drafts import get_draft_transcript_pages, has_dirty_draft
    except Exception as exc:
        raise RuntimeError(f"draft_render_helpers_unavailable:{exc}") from exc

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        raise RuntimeError("draft_render_project_missing")
    if not has_dirty_draft(project):
        raise RuntimeError("draft_render_missing_dirty_draft")

    draft_pages = get_draft_transcript_pages(project)
    if not draft_pages:
        raise RuntimeError("draft_render_no_pages")

    source_templates: dict[int, dict[str, Any]] = {}
    first_template: dict[str, Any] = {}
    for order, slide in enumerate(exported_slides or []):
        slide_payload = dict(slide)
        source_index = int(slide_payload.get("source_slide_index") or slide_payload.get("index") or order)
        source_templates.setdefault(source_index, slide_payload)
        if not first_template:
            first_template = slide_payload

    ws = _workspace(project_id)
    rendered: list[dict[str, Any]] = []
    for display_index, page in enumerate(draft_pages):
        source_index = int(page.get("source_slide_index") or display_index)
        slide_payload = dict(source_templates.get(source_index) or first_template or {})
        editor_document = dict(page.get("editor_document") or {})
        scene = dict(editor_document.get("scene") or {})
        source_type = _source_type_from_value(
            slide_payload.get("source_type") or scene.get("source_type") or slide_payload.get("source_ext")
        )
        scene_mode = _scene_mode_from_value(
            scene.get("background_mode"),
            fallback="whiteboard" if bool(page.get("whiteboard_mode")) else "original",
        )
        if (
            scene_mode == "source_background"
            and _source_background_supported_for_source_type(source_type)
            and not scene.get("source_background_path")
        ):
            payload_source_background = _scene_storage_rel_path(slide_payload.get("source_background_path"))
            if payload_source_background:
                scene["source_background_path"] = payload_source_background
        render_image_path = _scene_background_image_for_render(
            scene,
            slide_payload.get("image_path") or slide_payload.get("slide_path") or "",
        )
        source_background_warnings = _scene_render_warning_list(slide_payload, scene)
        scene_mode, source_background_warnings, effective_whiteboard_mode = _normalize_scene_mode_for_render(
            scene_mode,
            source_type=source_type,
            render_image_path=render_image_path,
            warnings=source_background_warnings,
        )
        if effective_whiteboard_mode:
            render_image_path = "" if scene_mode == "whiteboard" else render_image_path
        narration_text = str(page.get("narration_text") or slide_payload.get("narration_text") or slide_payload.get("notes_text") or "")
        raw_display_text = str(
            page.get("original_text")
            or slide_payload.get("display_text")
            or slide_payload.get("original_text")
            or slide_payload.get("notes_text")
            or ""
        )
        display_text = _split_consistent_display_text(raw_display_text, narration_text)
        rich_text_html = _split_consistent_rich_text_html(display_text, page.get("rich_text_html") or "")
        subtitle_chunks = _subtitle_chunks_for_render(page.get("subtitle_chunks"), narration_text)
        slide_payload.update(
            {
                "index": display_index,
                "slide_num": display_index + 1,
                "source_slide_index": source_index,
                "source_slide_num": source_index + 1,
                "split_index": int(page.get("split_index") or 0),
                "page_key": str(page.get("page_key") or f"draft-page-{display_index + 1}"),
                "notes_text": narration_text,
                "original_text": display_text,
                "display_text": display_text,
                "narration_text": narration_text,
                "rich_text_html": rich_text_html,
                "editor_document": editor_document,
                "subtitle_chunks": subtitle_chunks,
                "source_type": source_type,
                "whiteboard_mode": effective_whiteboard_mode,
                "scene_background_mode": scene_mode,
                "source_background_warnings": source_background_warnings,
                "custom_background_path": scene.get("custom_background_path") or "",
                "scene_background_fit": _scene_fit_from_value(scene.get("background_fit")),
                "scene_text_scale": _scene_text_scale_from_value(scene.get("text_scale"), fallback=1.0),
                "image_path": render_image_path,
                "audio_out": str(ws["audio"] / f"slide_{display_index + 1:03d}.mp3"),
                "part_out": str(ws["parts"] / f"part_{display_index + 1:03d}.mp4"),
                "draft_page_id": page.get("id"),
            }
        )
        rendered.append(slide_payload)
    return rendered


class _DraftPageList(list):
    def all(self):
        return self

    def filter(self, **_kwargs):
        return self

    def order_by(self, *_fields):
        return self


class _DraftProjectProxy:
    def __init__(self, project, *, title: str, description: str, pages: list[dict[str, Any]]) -> None:
        self.id = project.id
        self.title = title
        self.description = description
        self.transcript_pages = _DraftPageList(
            [
                type(
                    "DraftTranscriptPage",
                    (),
                    {
                        "id": int(page.get("id") or 0),
                        "page_key": str(page.get("page_key") or ""),
                        "order": int(page.get("order") or index),
                        "original_text": str(page.get("original_text") or ""),
                        "narration_text": str(page.get("narration_text") or ""),
                    },
                )()
                for index, page in enumerate(pages)
            ]
        )


def _run_auto_source_moderation_for_draft(
    project_id: str | int,
    *,
    triggered_by_user_id: int | None = None,
) -> dict[str, Any]:
    if not _source_moderation_auto_enabled():
        return {
            "enabled": False,
            "status": "skipped_disabled",
            "project_id": int(project_id),
            "block_render": False,
        }

    try:
        from django.contrib.auth.models import User
        from django.utils import timezone
        from ai_agents.models import AgentRun
        from core.models import Project
        from core.drafts import get_draft_project_fields, get_draft_transcript_pages
        from .ai_agents.orchestrator import ModerationOrchestrator
    except Exception as exc:  # noqa: BLE001
        logger.exception("Draft source moderation unavailable for project=%s", project_id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project_id),
            "moderation_status": "failed",
            "error_message": _concise_error_text(exc, fallback="draft_source_moderation_import_failed"),
            "block_render": False,
        }

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return {
            "enabled": True,
            "status": "skipped_missing_project",
            "project_id": int(project_id),
            "block_render": False,
        }
    if triggered_by_user_id is None:
        triggered_by_user_id = int(project.user_id) if project.user_id else None

    draft_fields = get_draft_project_fields(project)
    draft_pages = get_draft_transcript_pages(project)
    draft_project = _DraftProjectProxy(
        project,
        title=str(draft_fields.get("title") or project.title or ""),
        description=str(draft_fields.get("description") or project.description or ""),
        pages=draft_pages,
    )
    orchestrator = ModerationOrchestrator()
    triggered_by = User.objects.filter(pk=triggered_by_user_id).first() if triggered_by_user_id else None
    phase = f"{_source_moderation_phase()}_draft"
    run = AgentRun.objects.create(
        project=project,
        triggered_by=triggered_by,
        purpose="moderation",
        phase=phase,
        status="running",
    )

    try:
        result = orchestrator.text_agent.scan_project(draft_project)
        final_decision = orchestrator.policy_engine.combine_results([result])
        project_status = orchestrator.policy_engine.project_status_for_decision(final_decision)
        persisted_count = orchestrator._persist_findings(run, result)
        summary = orchestrator._frontend_safe_summary(
            run_id=run.id,
            moderation_status=project_status,
            final_decision=final_decision,
            result=result,
        )
        run.status = "done"
        run.final_decision = final_decision
        run.input_hash = str(result.metadata.get("input_hash") or "")
        run.summary = summary
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "final_decision", "input_hash", "summary", "completed_at"])

        summary_payload = dict(project.moderation_summary or {})
        summary_payload["draft_moderation"] = summary
        Project.objects.filter(pk=project.id).update(
            moderation_summary=summary_payload,
            last_moderation_run_id=run.id,
        )

        block_render = project_status in SOURCE_MODERATION_REVIEW_STATUSES
        return {
            "enabled": True,
            "status": "done",
            "phase": phase,
            "project_id": project.id,
            "run_id": run.id,
            "final_decision": final_decision,
            "moderation_status": project_status,
            "finding_count": persisted_count,
            "input_hash": run.input_hash,
            "block_render": block_render,
            "message": _source_moderation_message(project_status),
            "moderation_summary": summary,
            "findings": summary.get("findings") or [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Draft source moderation failed for project=%s run=%s", project.id, run.id)
        error_text = _concise_error_text(exc, fallback="draft_source_moderation_failed")
        failure_summary = {
            "moderation_status": "failed",
            "message": "Draft moderation scan failed. Please try again or contact support.",
            "run_id": run.id,
        }
        run.status = "failed"
        run.final_decision = "needs_admin_review"
        run.error_message = error_text
        run.summary = failure_summary
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "final_decision", "error_message", "summary", "completed_at"])
        return {
            "enabled": True,
            "status": "failed",
            "project_id": project.id,
            "run_id": run.id,
            "moderation_status": "failed",
            "error_message": error_text,
            "block_render": False,
            "moderation_summary": failure_summary,
        }


def _mark_draft_render_blocked(project_id: str | int, moderation_result: dict[str, Any]) -> None:
    try:
        from core.models import Project
        from core.drafts import mark_draft_moderation_failed
    except Exception:
        logger.warning("Draft moderation-block update skipped project=%s", project_id, exc_info=True)
        return

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is not None:
        mark_draft_moderation_failed(project, moderation_result)
    message = str(moderation_result.get("message") or "Draft blocked by moderation.")
    _update_job(project_id, status="failed", progress=100, error_message=message)


SOURCE_MODERATION_REVIEW_STATUSES = {"revision_required", "needs_admin_review"}
SOURCE_MODERATION_APPROVED_STATUSES = {"approved", "admin_approved"}


def _source_moderation_auto_enabled() -> bool:
    return _settings_bool("SOURCE_MODERATION_AUTO_ENABLED", False)


def _source_moderation_block_render_on_rejection() -> bool:
    return _settings_bool("SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION", True)


def _source_moderation_phase() -> str:
    return _settings_str("SOURCE_MODERATION_PHASE", "source_scan")


def _run_auto_source_moderation_after_transcript_sync(
    project_id: str | int,
    *,
    triggered_by_user_id: int | None = None,
) -> dict[str, Any]:
    if not _source_moderation_auto_enabled():
        return {
            "enabled": False,
            "status": "skipped_disabled",
            "project_id": int(project_id),
        }

    try:
        from ai_agents.models import AgentRun
        from core.models import Project
        from .ai_agents.orchestrator import ModerationOrchestrator
        from .ai_agents.text_moderation import project_text_input_hash
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto source moderation unavailable for project=%s", project_id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project_id),
            "moderation_status": "failed",
            "error_message": _concise_error_text(exc, fallback="auto_source_moderation_import_failed"),
            "block_render": False,
        }

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return {
            "enabled": True,
            "status": "skipped_missing_project",
            "project_id": int(project_id),
            "block_render": False,
        }

    if triggered_by_user_id is None:
        triggered_by_user_id = int(project.user_id) if project.user_id else None

    current_hash = project_text_input_hash(project)
    if project.moderation_status in SOURCE_MODERATION_APPROVED_STATUSES and project.last_moderation_run_id:
        latest_run = AgentRun.objects.filter(pk=project.last_moderation_run_id, project=project).first()
        if latest_run is not None and str(latest_run.input_hash or "") == current_hash:
            return {
                "enabled": True,
                "status": "skipped_unchanged_approved",
                "project_id": project.id,
                "moderation_status": project.moderation_status,
                "run_id": latest_run.id,
                "input_hash": current_hash,
                "block_render": False,
            }

    phase = _source_moderation_phase()
    try:
        result = ModerationOrchestrator().run(
            project_id=project.id,
            triggered_by_user_id=triggered_by_user_id,
            phase=phase,
        )
        moderation_status = str(result.get("moderation_status") or "")
        block_render = (
            _source_moderation_block_render_on_rejection()
            and moderation_status in SOURCE_MODERATION_REVIEW_STATUSES
        )
        result.update(
            {
                "enabled": True,
                "phase": phase,
                "input_hash": current_hash,
                "block_render": block_render,
                "message": _source_moderation_message(moderation_status),
            }
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto source moderation failed for project=%s", project.id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": project.id,
            "moderation_status": "failed",
            "error_message": _concise_error_text(exc, fallback="auto_source_moderation_failed"),
            "block_render": False,
        }


def _source_moderation_message(moderation_status: str) -> str:
    if moderation_status == "revision_required":
        return "Source moderation requires revisions before rendering."
    if moderation_status == "needs_admin_review":
        return "Source moderation requires admin review before rendering."
    if moderation_status == "approved":
        return "Source moderation approved this lesson."
    if moderation_status == "failed":
        return "Source moderation failed. Rendering was not blocked by the source moderation gate."
    return f"Source moderation status: {moderation_status or 'unknown'}."


VISUAL_MODERATION_REVIEW_DECISIONS = {"block", "needs_admin_review"}


def _mark_project_scan_disabled(project_id: str | int, *, key: str, phase: str, message: str) -> None:
    try:
        from django.utils import timezone
        from core.models import Project

        project = Project.objects.filter(pk=int(project_id)).first()
        if project is None:
            return
        summary = dict(project.moderation_summary or {})
        summary[str(key)] = {
            "enabled": False,
            "status": "skipped_disabled",
            "final_decision": "skipped",
            "phase": str(phase or ""),
            "disabled": True,
            "message": str(message or "Visual scan disabled by environment."),
            "updated_at": timezone.now().isoformat(),
        }
        project.moderation_summary = summary
        project.save(update_fields=["moderation_summary", "updated_at"])
    except Exception:
        logger.warning("Could not mark moderation scan disabled project=%s key=%s", project_id, key, exc_info=True)


def _visual_moderation_auto_enabled() -> bool:
    return _visual_moderation_feature_enabled() and _settings_bool("VISUAL_MODERATION_AUTO_ENABLED", False)


def _visual_moderation_block_render_on_rejection() -> bool:
    return _settings_bool("VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", False)


def _visual_moderation_phase() -> str:
    return _settings_str("VISUAL_MODERATION_PHASE", "visual_asset_scan")


def _visual_moderation_scan_cover() -> bool:
    return _settings_bool("VISUAL_MODERATION_SCAN_COVER", True)


def _visual_moderation_scan_slides() -> bool:
    return _settings_bool("VISUAL_MODERATION_SCAN_SLIDES", True)


def _run_auto_visual_asset_moderation_after_export(
    project_id: str | int,
    export_result: list[dict[str, Any]] | None,
    job_id: str | int | None = None,
    use_draft: bool = False,
) -> dict[str, Any]:
    if not _visual_moderation_auto_enabled():
        phase = _visual_moderation_phase()
        _mark_project_scan_disabled(
            project_id,
            key="visual_asset_scan",
            phase=phase,
            message="Visual scan disabled by environment.",
        )
        return {
            "enabled": False,
            "status": "skipped_disabled",
            "project_id": int(project_id),
            "phase": phase,
            "final_decision": "skipped",
            "message": "Visual scan disabled by environment.",
            "block_render": False,
        }

    try:
        from core.models import Project
        from .ai_agents.policy_engine import PolicyEngine
        from .ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from .ai_agents.providers.visual_safety_provider import (
            build_visual_safety_provider,
            visual_safety_classifier_should_run,
        )
        from .ai_agents.visual_moderation import VisualModerationAgent
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto visual moderation unavailable for project=%s", project_id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project_id),
            "phase": _visual_moderation_phase(),
            "final_decision": "allow",
            "error_message": _concise_error_text(exc, fallback="auto_visual_moderation_import_failed"),
            "block_render": False,
        }

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return {
            "enabled": True,
            "status": "skipped_missing_project",
            "project_id": int(project_id),
            "phase": _visual_moderation_phase(),
            "block_render": False,
        }

    phase = _visual_moderation_phase()
    agent = VisualModerationAgent(provider=LocalImageRulesProvider())
    safety_agent = (
        VisualModerationAgent(provider=build_visual_safety_provider())
        if visual_safety_classifier_should_run()
        else None
    )
    results = []

    try:
        if _visual_moderation_scan_cover():
            cover_rel_path = getattr(project, "cover_image_processed", "") or getattr(project, "cover_image_original", "")
            if use_draft:
                try:
                    from core.drafts import get_draft_project_fields

                    draft_fields = get_draft_project_fields(project)
                    cover_rel_path = (
                        draft_fields.get("cover_image_processed")
                        or draft_fields.get("cover_image_original")
                        or cover_rel_path
                    )
                except Exception:
                    logger.warning("Could not load draft cover path for project=%s", project.id, exc_info=True)
            cover_path = _visual_asset_path(cover_rel_path)
            results.append(agent.scan_cover_image(project, image_path=cover_path))
            if safety_agent is not None:
                results.append(safety_agent.scan_cover_image(project, image_path=cover_path))

        if _visual_moderation_scan_slides():
            for asset in _visual_slide_assets_from_export(export_result or []):
                results.append(
                    agent.scan_slide_image(
                        project_id=int(project.id),
                        image_path=asset["image_path"],
                        slide_order=asset["slide_order"],
                        page_key=asset["page_key"],
                        ui_anchor=asset["ui_anchor"],
                    )
                )
                if safety_agent is not None:
                    results.append(
                        safety_agent.scan_slide_image(
                            project_id=int(project.id),
                            image_path=asset["image_path"],
                            slide_order=asset["slide_order"],
                            page_key=asset["page_key"],
                            ui_anchor=asset["ui_anchor"],
                        )
                    )

        if not results:
            return {
                "enabled": True,
                "status": "skipped_no_assets",
                "project_id": int(project.id),
                "phase": phase,
                "block_render": False,
            }

        final_decision = PolicyEngine().combine_results(results)
        if _visual_provider_unavailable(results) and final_decision != "block":
            final_decision = "needs_admin_review"
        run = _persist_auto_visual_moderation_results(
            project=project,
            results=results,
            final_decision=final_decision,
            phase=phase,
            job_id=job_id,
            use_draft=use_draft,
        )
        finding_count = sum(len(result.findings) for result in results)
        block_render = (
            _visual_moderation_block_render_on_rejection()
            and final_decision in VISUAL_MODERATION_REVIEW_DECISIONS
        )
        return {
            "enabled": True,
            "status": "done",
            "project_id": int(project.id),
            "phase": phase,
            "run_id": run.id,
            "final_decision": final_decision,
            "finding_count": finding_count,
            "scanned_asset_count": len(results),
            "block_render": block_render,
            "message": _visual_moderation_message(final_decision),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto visual moderation failed for project=%s", project.id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project.id),
            "phase": phase,
            "final_decision": "allow",
            "error_message": _concise_error_text(exc, fallback="auto_visual_moderation_failed"),
            "block_render": False,
        }


def _visual_slide_assets_from_export(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for order, slide in enumerate(slides or []):
        if not isinstance(slide, dict):
            continue
        image_path = _visual_asset_path(slide.get("image_path") or slide.get("slide_path") or "")
        if image_path and image_path in seen_paths:
            continue
        if image_path:
            seen_paths.add(image_path)
        slide_order = _safe_int_value(slide.get("source_slide_index"), fallback=_safe_int_value(slide.get("index"), fallback=order))
        assets.append(
            {
                "image_path": image_path,
                "slide_order": slide_order,
                "page_key": str(slide.get("page_key") or ""),
                "ui_anchor": f"export-slide-{slide_order}-image",
            }
        )
    return assets


def _visual_asset_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    direct = Path(raw)
    if direct.is_file():
        return str(direct)
    if ".." in raw.replace("\\", "/").split("/"):
        return raw
    storage_path = Path(STORAGE_ROOT) / raw.lstrip("/\\")
    if storage_path.is_file():
        return str(storage_path)
    return raw


def _safe_int_value(value: Any, *, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _persist_auto_visual_moderation_results(
    *,
    project,
    results: list,
    final_decision: str,
    phase: str,
    job_id: str | int | None = None,
    use_draft: bool = False,
):
    from ai_agents.models import AgentFinding, AgentRun
    from django.utils import timezone

    finding_count = sum(len(result.findings) for result in results)
    summary = _visual_moderation_summary(
        final_decision=final_decision,
        results=results,
        finding_count=finding_count,
        job_id=job_id,
    )
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user if getattr(project, "user_id", None) else None,
        purpose="moderation",
        phase=phase,
        status="done",
        final_decision=final_decision,
        summary=summary,
        completed_at=timezone.now(),
    )

    rows = []
    for result in results:
        provider_raw = _visual_provider_raw(result.metadata or {})
        for finding in result.findings:
            location = finding.location.model_dump(exclude_none=True)
            rows.append(
                AgentFinding(
                    run=run,
                    agent_slug=result.agent_slug,
                    agent_version=result.agent_version,
                    content_type="image",
                    object_type=str(location.get("asset_type") or ""),
                    object_id=_visual_object_id(location),
                    location=location,
                    category=finding.category,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    decision=finding.decision,
                    user_message=finding.user_message,
                    admin_message=finding.admin_message,
                    evidence_excerpt=finding.evidence_excerpt,
                    provider=result.provider,
                    provider_raw=provider_raw,
                )
            )
    if rows:
        AgentFinding.objects.bulk_create(rows)

    try:
        from ai_agents.policies import manual_moderation_prevents_auto_override

        project.refresh_from_db(
            fields=[
                "moderation_status",
                "moderation_summary",
                "manual_moderation_status",
                "moderation_blocked_until_review",
                "manual_moderation_at",
                "latest_publisher_change_at",
            ]
        )
        existing_summary = dict(project.moderation_summary or {})
        summary_key = "draft_visual_asset_scan" if use_draft else "visual_asset_scan"
        existing_summary[summary_key] = {
            **summary,
            "run_id": run.id,
            "phase": phase,
        }
        update_fields = ["moderation_summary", "updated_at"]
        if (
            not use_draft
            and final_decision in {"block", "needs_admin_review"}
            and not manual_moderation_prevents_auto_override(project)
        ):
            project.moderation_status = "revision_required" if final_decision == "block" else "needs_admin_review"
            existing_summary["moderation_status"] = project.moderation_status
            if final_decision == "block":
                existing_summary["message"] = "Visual moderation blocked this lesson pending revision."
            else:
                existing_summary["message"] = "Visual moderation requires admin review before publication."
            update_fields.append("moderation_status")
        project.moderation_summary = existing_summary
        project.save(update_fields=[*dict.fromkeys(update_fields)])
        if (
            not use_draft
            and final_decision in {"block", "needs_admin_review"}
            and not manual_moderation_prevents_auto_override(project)
        ):
            _ensure_auto_project_review_request(
                project,
                run,
                "Visual moderation flagged this lesson for admin review.",
            )
    except Exception:
        logger.warning("Visual moderation summary update failed for project=%s", project.id, exc_info=True)

    return run


def _visual_moderation_summary(*, final_decision: str, results: list, finding_count: int, job_id: str | int | None) -> dict[str, Any]:
    categories = sorted({finding.category for result in results for finding in result.findings})
    severities = sorted({finding.severity for result in results for finding in result.findings})
    provider_errors = _provider_error_metadata(results)
    return {
        "final_decision": final_decision,
        "message": _visual_moderation_message(final_decision),
        "finding_count": finding_count,
        "scanned_asset_count": len(results),
        "categories": categories,
        "severities": severities,
        "job_id": int(job_id) if job_id is not None else None,
        "providers": sorted({str(getattr(result, "provider", "") or "") for result in results if getattr(result, "provider", "")}),
        "provider_skipped_count": sum(1 for result in results if bool((result.metadata or {}).get("skipped"))),
        "provider_errors": provider_errors,
    }


def _visual_provider_raw(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in (
            "width",
            "height",
            "format",
            "mode",
            "file_size_bytes",
            "missing",
            "error",
            "provider",
            "reason",
            "provider_error",
            "response_category_count",
            "block_severity",
        )
        if key in metadata
    }


def _provider_error_metadata(results: list) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for result in results or []:
        metadata = dict(getattr(result, "metadata", {}) or {})
        if not metadata.get("provider_error"):
            continue
        errors.append(
            {
                "provider": str(getattr(result, "provider", "") or metadata.get("provider") or ""),
                "reason": str(metadata.get("reason") or ""),
                "error": str(metadata.get("error") or "")[:240],
            }
        )
    return errors


def _visual_provider_unavailable(results: list) -> bool:
    for result in results or []:
        provider = str(getattr(result, "provider", "") or "")
        if provider in {"", "local_image_rules"}:
            continue
        metadata = dict(getattr(result, "metadata", {}) or {})
        if metadata.get("provider_error"):
            return True
        if metadata.get("skipped"):
            reason = str(metadata.get("reason") or "")
            if reason in {
                "visual_safety_classifier_disabled",
                "azure_content_safety_disabled",
                "azure_content_safety_missing_config",
                "azure_content_safety_timeout",
                "azure_content_safety_request_error",
                "azure_content_safety_invalid_response",
            }:
                return True
    return False


def _visual_object_id(location: dict[str, Any]) -> str:
    if location.get("slide_order") is not None:
        return str(location["slide_order"])
    return str(location.get("project_id") or "")


def _visual_moderation_message(final_decision: str) -> str:
    if final_decision == "allow":
        return "Visual asset validation passed."
    if final_decision == "warn":
        return "Visual asset validation completed with warnings."
    if final_decision == "needs_admin_review":
        return "Visual asset validation found assets that should be reviewed."
    if final_decision == "block":
        return "Visual asset validation blocked rendering."
    return f"Visual asset validation status: {final_decision or 'unknown'}."


def _mark_project_visual_moderation_blocked(project_id: str | int, moderation_result: dict[str, Any]) -> None:
    try:
        from core.models import Project
    except Exception:
        logger.warning("Project visual moderation-block status update skipped project=%s", project_id, exc_info=True)
        return

    message = str(moderation_result.get("message") or "Visual asset moderation blocked rendering.")
    Project.objects.filter(pk=int(project_id)).update(
        status="draft",
        is_published=False,
    )
    _update_job(
        project_id,
        status="failed",
        progress=100,
        error_message=message,
    )


OCR_MODERATION_REVIEW_DECISIONS = {"block", "needs_admin_review"}
OCR_TEXT_AGENT_SLUG = "ocr_slide_text_local_rules"
OCR_TEXT_AGENT_VERSION = "local-rules:v1"


def _ocr_moderation_auto_enabled() -> bool:
    return _visual_moderation_feature_enabled() and _settings_bool("OCR_MODERATION_AUTO_ENABLED", False)


def _ocr_moderation_block_render_on_rejection() -> bool:
    return _settings_bool("OCR_MODERATION_BLOCK_RENDER_ON_REJECTION", False)


def _ocr_moderation_phase() -> str:
    return _settings_str("OCR_MODERATION_PHASE", "ocr_slide_scan")


def _ocr_moderation_scan_slides() -> bool:
    return _settings_bool("OCR_MODERATION_SCAN_SLIDES", True)


def _ocr_moderation_provider_name() -> str:
    return _settings_str("OCR_MODERATION_PROVIDER", "noop").strip().lower() or "noop"


def _run_auto_ocr_slide_moderation_after_export(
    project_id: str | int,
    export_result: list[dict[str, Any]] | None,
    job_id: str | int | None = None,
) -> dict[str, Any]:
    if not _ocr_moderation_auto_enabled():
        phase = _ocr_moderation_phase()
        _mark_project_scan_disabled(
            project_id,
            key="ocr_slide_scan",
            phase=phase,
            message="OCR visual scan disabled by environment.",
        )
        return {
            "enabled": False,
            "status": "skipped_disabled",
            "project_id": int(project_id),
            "phase": phase,
            "final_decision": "skipped",
            "message": "OCR visual scan disabled by environment.",
            "block_render": False,
        }

    try:
        from core.models import Project
        from .ai_agents.ocr_bridge import OCRBridge, OCRTextResult, build_ocr_provider
        from .ai_agents.policy_engine import PolicyEngine
        from .ai_agents.providers.local_rules_provider import LocalRulesProvider
        from .ai_agents.schemas import FindingLocation
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto OCR moderation unavailable for project=%s", project_id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project_id),
            "phase": _ocr_moderation_phase(),
            "final_decision": "allow",
            "error_message": _concise_error_text(exc, fallback="auto_ocr_moderation_import_failed"),
            "block_render": False,
        }

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return {
            "enabled": True,
            "status": "skipped_missing_project",
            "project_id": int(project_id),
            "phase": _ocr_moderation_phase(),
            "block_render": False,
        }

    phase = _ocr_moderation_phase()
    if not _ocr_moderation_scan_slides():
        return {
            "enabled": True,
            "status": "skipped_no_assets",
            "project_id": int(project.id),
            "phase": phase,
            "block_render": False,
        }

    assets = _visual_slide_assets_from_export(export_result or [])
    if not assets:
        return {
            "enabled": True,
            "status": "skipped_no_assets",
            "project_id": int(project.id),
            "phase": phase,
            "block_render": False,
        }

    provider_name = _ocr_moderation_provider_name()
    ocr_bridge = OCRBridge(provider=build_ocr_provider(provider_name))
    text_provider = LocalRulesProvider()
    policy_engine = PolicyEngine()
    ocr_results = []
    findings = []

    try:
        for asset in assets:
            location = FindingLocation(
                project_id=int(project.id),
                page_key=asset["page_key"],
                slide_order=asset["slide_order"],
                asset_type="ocr_text",
                image_path=asset["image_path"],
                field_name="ocr_text",
                ui_anchor=f"export-slide-{asset['slide_order']}-ocr",
            )
            try:
                ocr_result = ocr_bridge.extract(
                    image_path=asset["image_path"],
                    location=location,
                    asset_type="ocr_text",
                    slide_order=asset["slide_order"],
                    project_id=int(project.id),
                    ui_anchor=location.ui_anchor,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Auto OCR extraction failed project=%s slide_order=%s image=%s",
                    project.id,
                    asset["slide_order"],
                    asset["image_path"],
                    exc_info=True,
                )
                ocr_result = OCRTextResult(
                    text="",
                    location=location,
                    provider=provider_name or "noop_ocr",
                    success=False,
                    error_message=_concise_error_text(exc, fallback="auto_ocr_extract_failed"),
                    image_path=asset["image_path"],
                    asset_type="ocr_text",
                    slide_order=asset["slide_order"],
                    metadata={"error": exc.__class__.__name__},
                )
            ocr_results.append(ocr_result)

            text = str(getattr(ocr_result, "text", "") or "").strip()
            if not text:
                continue
            findings.extend(text_provider.scan_text(text, ocr_result.location))

        final_decision = policy_engine.combine_findings(findings)
        if _ocr_provider_unavailable(provider_name, ocr_results) and final_decision != "block":
            final_decision = "needs_admin_review"
        run = _persist_auto_ocr_moderation_results(
            project=project,
            ocr_results=ocr_results,
            findings=findings,
            final_decision=final_decision,
            phase=phase,
            job_id=job_id,
        )
        text_asset_count = sum(1 for result in ocr_results if str(getattr(result, "text", "") or "").strip())
        block_render = (
            _ocr_moderation_block_render_on_rejection()
            and final_decision in OCR_MODERATION_REVIEW_DECISIONS
        )
        return {
            "enabled": True,
            "status": "done",
            "project_id": int(project.id),
            "phase": phase,
            "run_id": run.id,
            "final_decision": final_decision,
            "finding_count": len(findings),
            "scanned_asset_count": len(ocr_results),
            "text_asset_count": text_asset_count,
            "block_render": block_render,
            "message": _ocr_moderation_message(final_decision),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto OCR moderation failed for project=%s", project.id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project.id),
            "phase": phase,
            "final_decision": "allow",
            "error_message": _concise_error_text(exc, fallback="auto_ocr_moderation_failed"),
            "block_render": False,
        }


def _persist_auto_ocr_moderation_results(
    *,
    project,
    ocr_results: list,
    findings: list,
    final_decision: str,
    phase: str,
    job_id: str | int | None = None,
):
    from ai_agents.models import AgentFinding, AgentRun
    from django.utils import timezone

    summary = _ocr_moderation_summary(
        final_decision=final_decision,
        ocr_results=ocr_results,
        findings=findings,
        job_id=job_id,
    )
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user if getattr(project, "user_id", None) else None,
        purpose="moderation",
        phase=phase,
        status="done",
        final_decision=final_decision,
        summary=summary,
        completed_at=timezone.now(),
    )

    rows = []
    for finding in findings:
        location = finding.location.model_dump(exclude_none=True)
        rows.append(
            AgentFinding(
                run=run,
                agent_slug=OCR_TEXT_AGENT_SLUG,
                agent_version=OCR_TEXT_AGENT_VERSION,
                content_type="ocr",
                object_type="slide_image_ocr",
                object_id=_visual_object_id(location),
                location=location,
                category=finding.category,
                severity=finding.severity,
                confidence=finding.confidence,
                decision=finding.decision,
                user_message=finding.user_message,
                admin_message=finding.admin_message,
                evidence_excerpt=str(finding.evidence_excerpt or "")[:220],
                provider="ocr_slide_moderation:local_rules",
                provider_raw=_ocr_provider_raw_for_location(ocr_results, location),
            )
        )
    if rows:
        AgentFinding.objects.bulk_create(rows)

    try:
        from ai_agents.policies import manual_moderation_prevents_auto_override

        project.refresh_from_db(
            fields=[
                "moderation_status",
                "moderation_summary",
                "manual_moderation_status",
                "moderation_blocked_until_review",
                "manual_moderation_at",
                "latest_publisher_change_at",
            ]
        )
        existing_summary = dict(project.moderation_summary or {})
        existing_summary["ocr_slide_scan"] = {
            **summary,
            "run_id": run.id,
            "phase": phase,
        }
        update_fields = ["moderation_summary", "updated_at"]
        if final_decision in {"block", "needs_admin_review"} and not manual_moderation_prevents_auto_override(project):
            project.moderation_status = "revision_required" if final_decision == "block" else "needs_admin_review"
            existing_summary["moderation_status"] = project.moderation_status
            if final_decision == "block":
                existing_summary["message"] = "OCR moderation blocked this lesson pending revision."
            else:
                existing_summary["message"] = "OCR moderation requires admin review before publication."
            update_fields.append("moderation_status")
        project.moderation_summary = existing_summary
        project.save(update_fields=[*dict.fromkeys(update_fields)])
        if final_decision in {"block", "needs_admin_review"} and not manual_moderation_prevents_auto_override(project):
            _ensure_auto_project_review_request(
                project,
                run,
                "OCR moderation flagged this lesson for admin review.",
            )
    except Exception:
        logger.warning("OCR moderation summary update failed for project=%s", project.id, exc_info=True)

    return run


def _ocr_moderation_summary(*, final_decision: str, ocr_results: list, findings: list, job_id: str | int | None) -> dict[str, Any]:
    categories = sorted({finding.category for finding in findings})
    severities = sorted({finding.severity for finding in findings})
    text_asset_count = sum(1 for result in ocr_results if str(getattr(result, "text", "") or "").strip())
    failed_asset_count = sum(1 for result in ocr_results if not bool(getattr(result, "success", False)))
    return {
        "final_decision": final_decision,
        "message": _ocr_moderation_message(final_decision),
        "finding_count": len(findings),
        "scanned_asset_count": len(ocr_results),
        "text_asset_count": text_asset_count,
        "failed_asset_count": failed_asset_count,
        "categories": categories,
        "severities": severities,
        "provider": _ocr_moderation_provider_name(),
        "job_id": int(job_id) if job_id is not None else None,
    }


def _ensure_auto_project_review_request(project, run, message: str) -> None:
    try:
        from ai_agents.models import AdminReviewRequest

        AdminReviewRequest.objects.get_or_create(
            project=project,
            status="open",
            defaults={
                "run": run,
                "requested_by": None,
                "publisher_message": str(message or "Automatic moderation flagged this lesson for admin review."),
            },
        )
    except Exception:
        logger.warning("Auto moderation review request creation failed project=%s", getattr(project, "id", None), exc_info=True)


def _ocr_provider_raw_for_location(ocr_results: list, location: dict[str, Any]) -> dict[str, Any]:
    slide_order = location.get("slide_order")
    image_path = str(location.get("image_path") or "")
    for result in ocr_results:
        result_location = getattr(result, "location", None)
        if result_location is None:
            continue
        if result_location.slide_order == slide_order and str(result_location.image_path or "") == image_path:
            metadata = dict(getattr(result, "metadata", {}) or {})
            return {
                "ocr_provider": str(getattr(result, "provider", "") or ""),
                "ocr_success": bool(getattr(result, "success", False)),
                "ocr_error_message": str(getattr(result, "error_message", "") or "")[:240],
                "ocr_text_length": len(str(getattr(result, "text", "") or "")),
                "ocr_metadata": {
                    key: metadata[key]
                    for key in ("noop", "asset_missing", "error")
                    if key in metadata
                },
            }
    return {}


def _ocr_provider_unavailable(provider_name: str, ocr_results: list) -> bool:
    normalized = str(provider_name or "").strip().lower()
    if normalized in {"", "none", "noop"}:
        return True
    for result in ocr_results or []:
        if bool(getattr(result, "success", False)):
            continue
        metadata = dict(getattr(result, "metadata", {}) or {})
        reason = str(metadata.get("reason") or "")
        if metadata.get("skipped") or reason or getattr(result, "error_message", ""):
            return True
    return False


def _ocr_moderation_message(final_decision: str) -> str:
    if final_decision == "allow":
        return "OCR slide text validation passed."
    if final_decision == "warn":
        return "OCR slide text validation completed with warnings."
    if final_decision == "needs_admin_review":
        return "OCR slide text validation found text that should be reviewed."
    if final_decision == "block":
        return "OCR slide text validation blocked rendering."
    return f"OCR slide text validation status: {final_decision or 'unknown'}."


def _mark_project_ocr_moderation_blocked(project_id: str | int, moderation_result: dict[str, Any]) -> None:
    try:
        from core.models import Project
    except Exception:
        logger.warning("Project OCR moderation-block status update skipped project=%s", project_id, exc_info=True)
        return

    message = str(moderation_result.get("message") or "OCR slide text moderation blocked rendering.")
    Project.objects.filter(pk=int(project_id)).update(
        status="draft",
        is_published=False,
    )
    _update_job(
        project_id,
        status="failed",
        progress=100,
        error_message=message,
    )


VIDEO_FRAME_OCR_AGENT_SLUG = "video_frame_ocr_local_rules"
VIDEO_FRAME_OCR_AGENT_VERSION = "local-rules:v1"
VIDEO_FRAME_AUDIT_SUMMARY_KEY = "video_frame_audit"


def _video_frame_audit_auto_enabled() -> bool:
    return _visual_moderation_feature_enabled() and _settings_bool("VIDEO_FRAME_AUDIT_AUTO_ENABLED", False)


def _video_frame_audit_phase() -> str:
    return _settings_str("VIDEO_FRAME_AUDIT_PHASE", "video_frame_audit")


def _video_frame_audit_every_seconds() -> float:
    try:
        from django.conf import settings

        return float(getattr(settings, "VIDEO_FRAME_AUDIT_EVERY_SECONDS", 10) or 10)
    except Exception:
        return float(os.environ.get("VIDEO_FRAME_AUDIT_EVERY_SECONDS", "10") or 10)


def _video_frame_audit_max_frames() -> int:
    try:
        from django.conf import settings

        return int(getattr(settings, "VIDEO_FRAME_AUDIT_MAX_FRAMES", 5) or 5)
    except Exception:
        return int(os.environ.get("VIDEO_FRAME_AUDIT_MAX_FRAMES", "5") or 5)


def _video_frame_audit_run_visual_check() -> bool:
    return _visual_moderation_feature_enabled() and _settings_bool("VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK", True)


def _video_frame_audit_run_ocr() -> bool:
    return _visual_moderation_feature_enabled() and _settings_bool("VIDEO_FRAME_AUDIT_RUN_OCR", False)


def _video_frame_audit_retain_frames() -> bool:
    return _settings_bool("VIDEO_FRAME_AUDIT_RETAIN_FRAMES", False)


def _video_frame_audit_cleanup_on_success() -> bool:
    return _settings_bool("VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS", True)


def _run_auto_video_frame_audit_after_render(
    project_id: str | int,
    job_id: str | int | None,
    video_path: str | Path,
) -> dict[str, Any]:
    if not _video_frame_audit_auto_enabled():
        phase = _video_frame_audit_phase()
        _mark_project_scan_disabled(
            project_id,
            key=VIDEO_FRAME_AUDIT_SUMMARY_KEY,
            phase=phase,
            message="Video frame visual scan disabled by environment.",
        )
        return {
            "enabled": False,
            "status": "skipped_disabled",
            "project_id": int(project_id),
            "phase": phase,
            "final_decision": "skipped",
            "message": "Video frame visual scan disabled by environment.",
            "block_render": False,
        }

    phase = _video_frame_audit_phase()
    try:
        from core.models import Project
        from .ai_agents.ocr_bridge import OCRBridge, build_ocr_provider
        from .ai_agents.policy_engine import PolicyEngine
        from .ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from .ai_agents.providers.local_rules_provider import LocalRulesProvider
        from .ai_agents.providers.visual_safety_provider import (
            build_visual_safety_provider,
            visual_safety_classifier_should_run,
        )
        from .ai_agents.schemas import FindingLocation
        from .ai_agents.video_frame_moderation import VideoFrameModerationAgent, sample_video_frames
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto video frame audit unavailable for project=%s", project_id)
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project_id),
            "phase": phase,
            "final_decision": "allow",
            "error_message": _concise_error_text(exc, fallback="auto_video_frame_audit_import_failed"),
            "block_render": False,
        }

    project = Project.objects.filter(pk=int(project_id)).first()
    if project is None:
        return {
            "enabled": True,
            "status": "skipped_missing_project",
            "project_id": int(project_id),
            "phase": phase,
            "block_render": False,
        }

    sampling_result = None
    try:
        output_dir = _video_frame_audit_output_dir(project_id=project.id, job_id=job_id)
        sampling_result = sample_video_frames(
            video_path=video_path,
            output_dir=output_dir,
            every_seconds=_video_frame_audit_every_seconds(),
            max_frames=_video_frame_audit_max_frames(),
            include_first_frame=True,
        )
        if not sampling_result.success:
            run = _persist_auto_video_frame_audit_results(
                project=project,
                sampling_result=sampling_result,
                visual_results=[],
                ocr_results=[],
                ocr_findings=[],
                final_decision="allow",
                phase=phase,
                run_status="failed",
                job_id=job_id,
                error_message=sampling_result.error_message,
            )
            return {
                "enabled": True,
                "status": "failed",
                "project_id": int(project.id),
                "phase": phase,
                "run_id": run.id,
                "final_decision": "allow",
                "finding_count": 0,
                "sampled_frame_count": 0,
                "error_message": sampling_result.error_message,
                "block_render": False,
            }

        visual_results = []
        if _video_frame_audit_run_visual_check():
            visual_agents = [VideoFrameModerationAgent(provider=LocalImageRulesProvider())]
            if visual_safety_classifier_should_run():
                visual_agents.append(VideoFrameModerationAgent(provider=build_visual_safety_provider()))
            for index, frame in enumerate(sampling_result.sampled_frames):
                for visual_agent in visual_agents:
                    visual_results.append(
                        visual_agent.scan_frame(
                            project_id=int(project.id),
                            frame_path=frame.frame_path,
                            timestamp_seconds=frame.timestamp_seconds,
                            timestamp_label=frame.timestamp_label,
                            ui_anchor=f"auto-video-frame-{index}",
                        )
                    )

        ocr_results = []
        ocr_findings = []
        if _video_frame_audit_run_ocr():
            ocr_bridge = OCRBridge(provider=build_ocr_provider())
            text_provider = LocalRulesProvider()
            for index, frame in enumerate(sampling_result.sampled_frames):
                location = FindingLocation(
                    project_id=int(project.id),
                    asset_type="ocr_text",
                    frame_path=frame.frame_path,
                    timestamp_seconds=frame.timestamp_seconds,
                    timestamp_label=frame.timestamp_label,
                    field_name="ocr_text",
                    ui_anchor=f"auto-video-frame-{index}-ocr",
                )
                try:
                    ocr_result = ocr_bridge.extract(
                        image_path=frame.frame_path,
                        location=location,
                        asset_type="ocr_text",
                        project_id=int(project.id),
                        ui_anchor=location.ui_anchor,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Auto video frame OCR failed project=%s frame=%s",
                        project.id,
                        frame.frame_path,
                        exc_info=True,
                    )
                    from .ai_agents.ocr_bridge import OCRTextResult

                    ocr_result = OCRTextResult(
                        text="",
                        location=location,
                        provider="video_frame_ocr",
                        success=False,
                        error_message=_concise_error_text(exc, fallback="auto_video_frame_ocr_failed"),
                        image_path=frame.frame_path,
                        asset_type="ocr_text",
                        metadata={"error": exc.__class__.__name__},
                    )
                ocr_results.append(ocr_result)
                text = str(getattr(ocr_result, "text", "") or "").strip()
                if text:
                    ocr_findings.extend(text_provider.scan_text(text, ocr_result.location))

        policy_engine = PolicyEngine()
        findings = [finding for result in visual_results for finding in result.findings] + list(ocr_findings)
        final_decision = policy_engine.combine_findings(findings)
        run = _persist_auto_video_frame_audit_results(
            project=project,
            sampling_result=sampling_result,
            visual_results=visual_results,
            ocr_results=ocr_results,
            ocr_findings=ocr_findings,
            final_decision=final_decision,
            phase=phase,
            run_status="done",
            job_id=job_id,
        )
        cleanup_result = _cleanup_successful_video_frame_audit(sampling_result)
        return {
            "enabled": True,
            "status": "done",
            "project_id": int(project.id),
            "phase": phase,
            "run_id": run.id,
            "final_decision": final_decision,
            "finding_count": len(findings),
            "sampled_frame_count": len(sampling_result.sampled_frames),
            "ocr_frame_count": len(ocr_results),
            "cleanup": cleanup_result,
            "block_render": False,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto video frame audit failed for project=%s", project.id)
        cleanup_result = None
        if sampling_result is not None and getattr(sampling_result, "sampled_frames", None):
            cleanup_result = _cleanup_successful_video_frame_audit(sampling_result)
        try:
            from .ai_agents.video_frame_moderation import VideoFrameSamplingResult

            failed_sampling_result = VideoFrameSamplingResult(
                video_path=str(video_path or ""),
                output_dir=str(_video_frame_audit_output_dir(project_id=project.id, job_id=job_id)),
                sampled_frames=[],
                success=False,
                error_message=_concise_error_text(exc, fallback="auto_video_frame_audit_failed"),
            )
            run = _persist_auto_video_frame_audit_results(
                project=project,
                sampling_result=failed_sampling_result,
                visual_results=[],
                ocr_results=[],
                ocr_findings=[],
                final_decision="allow",
                phase=phase,
                run_status="failed",
                job_id=job_id,
                error_message=failed_sampling_result.error_message,
            )
            run_id = run.id
        except Exception:
            logger.warning("Auto video frame audit failure summary could not be persisted project=%s", project.id, exc_info=True)
            run_id = None
        return {
            "enabled": True,
            "status": "failed",
            "project_id": int(project.id),
            "phase": phase,
            "run_id": run_id,
            "final_decision": "allow",
            "error_message": _concise_error_text(exc, fallback="auto_video_frame_audit_failed"),
            "cleanup": cleanup_result,
            "block_render": False,
        }


def _video_frame_audit_output_dir(*, project_id: str | int, job_id: str | int | None) -> Path:
    job_part = str(job_id or "latest")
    return Path(STORAGE_ROOT) / "moderation" / "video_frames" / str(project_id) / job_part


def _video_frame_audit_storage_base() -> Path:
    return Path(STORAGE_ROOT) / "moderation" / "video_frames"


def _cleanup_successful_video_frame_audit(sampling_result) -> dict[str, Any]:
    if _video_frame_audit_retain_frames() or not _video_frame_audit_cleanup_on_success():
        return {
            "enabled": False,
            "reason": "retained" if _video_frame_audit_retain_frames() else "cleanup_disabled",
            "deleted_files": 0,
            "deleted_dirs": 0,
            "skipped": 0,
        }
    try:
        frame_paths = [frame.frame_path for frame in getattr(sampling_result, "sampled_frames", []) or []]
        output_dir = str(getattr(sampling_result, "output_dir", "") or "")
        targets: list[Any] = list(frame_paths)
        if output_dir:
            targets.append(output_dir)
        return _cleanup_video_frame_audit_files(targets, reason="success")
    except Exception:
        logger.warning("Video frame audit cleanup failed after successful audit", exc_info=True)
        return {
            "enabled": True,
            "reason": "cleanup_failed",
            "deleted_files": 0,
            "deleted_dirs": 0,
            "skipped": 0,
        }


def _cleanup_video_frame_audit_files(frame_paths_or_dir: Any, reason: str = "success") -> dict[str, Any]:
    base = _video_frame_audit_storage_base().resolve()
    targets = _normalize_cleanup_targets(frame_paths_or_dir)
    deleted_files = 0
    deleted_dirs = 0
    skipped = 0
    errors: list[str] = []

    for target in targets:
        path = Path(str(target or "")).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            skipped += 1
            continue

        if not _path_is_within(resolved, base):
            skipped += 1
            continue
        if not resolved.exists():
            skipped += 1
            continue

        try:
            if resolved.is_dir():
                file_count, dir_count = _delete_directory_tree(resolved)
                deleted_files += file_count
                deleted_dirs += dir_count
            else:
                resolved.unlink()
                deleted_files += 1
                deleted_dirs += _remove_empty_audit_parents(resolved.parent, base)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to cleanup video frame audit path=%s reason=%s", resolved, reason, exc_info=True)
            errors.append(f"{resolved}: {exc.__class__.__name__}")
            skipped += 1

    return {
        "enabled": True,
        "reason": reason,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "skipped": skipped,
        "errors": errors,
    }


def _normalize_cleanup_targets(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _path_is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _delete_directory_tree(path: Path) -> tuple[int, int]:
    file_count = sum(1 for item in path.rglob("*") if item.is_file())
    dir_count = sum(1 for item in path.rglob("*") if item.is_dir()) + 1
    shutil.rmtree(path)
    return file_count, dir_count


def _remove_empty_audit_parents(start: Path, base: Path) -> int:
    removed = 0
    current = start
    while current != base and _path_is_within(current, base):
        try:
            current.rmdir()
        except OSError:
            break
        removed += 1
        current = current.parent
    return removed


def _latest_video_export_job_id(project_id: str | int) -> int | None:
    try:
        from core.models import Job

        row = (
            Job.objects.filter(project_id=int(project_id), job_type="video_export", status="done")
            .order_by("-updated_at", "-id")
            .values("id")
            .first()
        )
        return int(row["id"]) if row else None
    except Exception:
        logger.warning("Latest video export job lookup failed for project=%s", project_id, exc_info=True)
        return None


def _latest_project_job_id(project_id: str | int, *, job_type: str | None = None) -> int | None:
    try:
        from core.models import Job

        queryset = Job.objects.filter(project_id=int(project_id))
        if job_type:
            queryset = queryset.filter(job_type=str(job_type))
        row = queryset.order_by("-created_at", "-id").values("id").first()
        return int(row["id"]) if row else None
    except Exception:
        logger.warning("Latest project job lookup failed for project=%s type=%s", project_id, job_type, exc_info=True)
        return None


def _persist_auto_video_frame_audit_results(
    *,
    project,
    sampling_result,
    visual_results: list,
    ocr_results: list,
    ocr_findings: list,
    final_decision: str,
    phase: str,
    run_status: str,
    job_id: str | int | None = None,
    error_message: str = "",
):
    from ai_agents.models import AgentFinding, AgentRun
    from django.utils import timezone

    summary = _video_frame_audit_summary(
        final_decision=final_decision,
        sampling_result=sampling_result,
        visual_results=visual_results,
        ocr_results=ocr_results,
        ocr_findings=ocr_findings,
        job_id=job_id,
        status=run_status,
        error_message=error_message,
    )
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user if getattr(project, "user_id", None) else None,
        purpose="moderation",
        phase=phase,
        status=run_status,
        final_decision=final_decision,
        summary=summary,
        error_message=str(error_message or ""),
        completed_at=timezone.now(),
    )

    rows = []
    for result in visual_results:
        provider_raw = _video_frame_provider_raw(result.metadata or {})
        for finding in result.findings:
            location = finding.location.model_dump(exclude_none=True)
            rows.append(
                AgentFinding(
                    run=run,
                    agent_slug=result.agent_slug,
                    agent_version=result.agent_version,
                    content_type="video_frame",
                    object_type="video_frame",
                    object_id=_video_frame_object_id(location),
                    location=location,
                    category=finding.category,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    decision=finding.decision,
                    user_message=finding.user_message,
                    admin_message=finding.admin_message,
                    evidence_excerpt=finding.evidence_excerpt,
                    provider=result.provider,
                    provider_raw=provider_raw,
                )
            )

    for finding in ocr_findings:
        location = finding.location.model_dump(exclude_none=True)
        rows.append(
            AgentFinding(
                run=run,
                agent_slug=VIDEO_FRAME_OCR_AGENT_SLUG,
                agent_version=VIDEO_FRAME_OCR_AGENT_VERSION,
                content_type="ocr",
                object_type="video_frame_ocr",
                object_id=_video_frame_object_id(location),
                location=location,
                category=finding.category,
                severity=finding.severity,
                confidence=finding.confidence,
                decision=finding.decision,
                user_message=finding.user_message,
                admin_message=finding.admin_message,
                evidence_excerpt=str(finding.evidence_excerpt or "")[:220],
                provider="video_frame_ocr:local_rules",
                provider_raw=_video_frame_ocr_provider_raw_for_location(ocr_results, location),
            )
        )

    if rows:
        AgentFinding.objects.bulk_create(rows)

    try:
        project.refresh_from_db(fields=["moderation_summary"])
        existing_summary = dict(project.moderation_summary or {})
        existing_summary[VIDEO_FRAME_AUDIT_SUMMARY_KEY] = {
            **summary,
            "run_id": run.id,
            "phase": phase,
        }
        project.moderation_summary = existing_summary
        project.save(update_fields=["moderation_summary", "updated_at"])
    except Exception:
        logger.warning("Video frame audit summary update failed for project=%s", project.id, exc_info=True)

    return run


def _video_frame_audit_summary(
    *,
    final_decision: str,
    sampling_result,
    visual_results: list,
    ocr_results: list,
    ocr_findings: list,
    job_id: str | int | None,
    status: str,
    error_message: str,
) -> dict[str, Any]:
    visual_findings = [finding for result in visual_results for finding in result.findings]
    findings = visual_findings + list(ocr_findings)
    text_frame_count = sum(1 for result in ocr_results if str(getattr(result, "text", "") or "").strip())
    return {
        "status": status,
        "final_decision": final_decision,
        "message": _video_frame_audit_message(final_decision, status=status),
        "finding_count": len(findings),
        "visual_finding_count": len(visual_findings),
        "ocr_finding_count": len(ocr_findings),
        "sampled_frame_count": len(getattr(sampling_result, "sampled_frames", []) or []),
        "ocr_frame_count": len(ocr_results),
        "ocr_text_frame_count": text_frame_count,
        "categories": sorted({finding.category for finding in findings}),
        "severities": sorted({finding.severity for finding in findings}),
        "video_path": str(getattr(sampling_result, "video_path", "") or ""),
        "output_dir": str(getattr(sampling_result, "output_dir", "") or ""),
        "ffmpeg_path": str(getattr(sampling_result, "ffmpeg_path", "") or ""),
        "error_message": str(error_message or getattr(sampling_result, "error_message", "") or "")[:240],
        "run_visual_check": _video_frame_audit_run_visual_check(),
        "run_ocr": _video_frame_audit_run_ocr(),
        "every_seconds": _video_frame_audit_every_seconds(),
        "max_frames": _video_frame_audit_max_frames(),
        "job_id": int(job_id) if job_id is not None else None,
        "visual_providers": sorted({str(getattr(result, "provider", "") or "") for result in visual_results if getattr(result, "provider", "")}),
        "provider_skipped_count": sum(1 for result in visual_results if bool((result.metadata or {}).get("skipped"))),
        "provider_errors": _provider_error_metadata(visual_results),
    }


def _video_frame_provider_raw(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in (
            "width",
            "height",
            "format",
            "mode",
            "file_size_bytes",
            "missing",
            "error",
            "provider",
            "reason",
            "provider_error",
            "response_category_count",
            "block_severity",
        )
        if key in metadata
    }


def _video_frame_ocr_provider_raw_for_location(ocr_results: list, location: dict[str, Any]) -> dict[str, Any]:
    frame_path = str(location.get("frame_path") or "")
    timestamp_seconds = location.get("timestamp_seconds")
    for result in ocr_results:
        result_location = getattr(result, "location", None)
        if result_location is None:
            continue
        if str(result_location.frame_path or "") == frame_path and result_location.timestamp_seconds == timestamp_seconds:
            metadata = dict(getattr(result, "metadata", {}) or {})
            return {
                "ocr_provider": str(getattr(result, "provider", "") or ""),
                "ocr_success": bool(getattr(result, "success", False)),
                "ocr_error_message": str(getattr(result, "error_message", "") or "")[:240],
                "ocr_text_length": len(str(getattr(result, "text", "") or "")),
                "ocr_metadata": {
                    key: metadata[key]
                    for key in ("noop", "asset_missing", "error", "model", "text_length")
                    if key in metadata
                },
            }
    return {}


def _video_frame_object_id(location: dict[str, Any]) -> str:
    timestamp = location.get("timestamp_seconds")
    if timestamp is not None:
        return f"{float(timestamp):.3f}"
    return str(location.get("project_id") or "")


def _video_frame_audit_message(final_decision: str, *, status: str) -> str:
    if status == "failed":
        return "Video frame audit could not complete. Rendering was not blocked."
    if final_decision == "allow":
        return "Video frame audit completed without findings."
    if final_decision == "warn":
        return "Video frame audit completed with warnings."
    if final_decision == "needs_admin_review":
        return "Video frame audit found frames that should be reviewed."
    if final_decision == "block":
        return "Video frame audit found blocking frame findings."
    return f"Video frame audit status: {final_decision or 'unknown'}."


def _mark_project_source_moderation_blocked(project_id: str | int, moderation_result: dict[str, Any]) -> None:
    try:
        from core.models import Project
    except Exception:
        logger.warning("Project moderation-block status update skipped project=%s", project_id, exc_info=True)
        return

    message = str(moderation_result.get("message") or "Source moderation blocked rendering.")
    Project.objects.filter(pk=int(project_id)).update(
        status="draft",
        is_published=False,
    )
    _update_job(
        project_id,
        status="failed",
        progress=100,
        error_message=message,
    )


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
    if not _avatar_feature_enabled():
        return {"enabled": False, "disabled_reason": "Avatar disabled by environment."}
    if not teacher_id:
        return {"enabled": False}

    try:
        from avatar.canonical_adapters import normalize_avatar_engine
        from core.avatar_image_moderation import avatar_image_moderation_gate
        from core.avatar_runtime_settings import default_avatar_runtime_settings
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

    moderation_gate = avatar_image_moderation_gate(profile)
    enabled = bool(
        profile.avatar_enabled
        and profile.avatar_consent_confirmed
        and not bool(moderation_gate.get("blocked"))
        and (processed_rel_path or original_rel_path or video_rel_path)
    )
    runtime_settings = default_avatar_runtime_settings()

    return {
        "enabled": enabled,
        "processed_rel_path": processed_rel_path,
        "source_rel_path": original_rel_path,
        "video_rel_path": video_rel_path,
        "reference_type": reference_type,
        "motion_preset": str(runtime_settings["motion_preset"]),
        "restoration_enabled": bool(runtime_settings["restoration_enabled"]),
        "liveportrait_enabled": bool(runtime_settings["liveportrait_enabled"]),
        "avatar_runtime_settings": runtime_settings,
        "quality_preset": str(profile.avatar_quality_preset or "high"),
        "lipsync_engine": normalize_avatar_engine(
            profile.avatar_lipsync_engine or profile.avatar_engine_primary or os.environ.get("AVATAR_ENGINE")
        ),
        "avatar_engine_selected": normalize_avatar_engine(
            profile.avatar_lipsync_engine or profile.avatar_engine_primary or os.environ.get("AVATAR_ENGINE")
        ),
        "model_version": str(profile.avatar_model_version or "liveportrait+musetalk:v1"),
        "avatar_source_valid": bool(source_state.get("valid")),
        "avatar_source_validation_error": str(source_state.get("error") or profile.avatar_source_validation_error or ""),
        "avatar_source_hash": str(source_state.get("source_hash") or profile.avatar_source_hash or ""),
        "avatar_preview_stale": bool(source_state.get("preview_stale")),
        "avatar_preview_source_hash": str(source_state.get("preview_source_hash") or profile.avatar_preview_source_hash or ""),
        "avatar_moderation_status": str(profile.avatar_moderation_status or "not_scanned"),
        "avatar_moderation_blocked": bool(moderation_gate.get("blocked")),
        "avatar_moderation_error_code": str(moderation_gate.get("error_code") or ""),
        "avatar_moderation_summary": dict(profile.avatar_moderation_summary or {}),
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
    restoration_enabled: bool | None = None,
    liveportrait_enabled: bool | None = None,
    cache_text_hash: str = "",
    avatar_job_id: int | None = None,
) -> dict[str, Any]:
    if not _avatar_feature_enabled():
        raise RuntimeError("avatar_disabled_by_environment")
    from avatar.canonical_adapters import normalize_avatar_engine
    from avatar.hashing import sha256_file
    from avatar.pipeline import AvatarRenderRequest, render_avatar_segment_local
    from core.avatar_runtime_settings import normalize_avatar_runtime_settings

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

    raw_lipsync_engine = str(lipsync_engine or os.environ.get("AVATAR_ENGINE") or "").strip()
    normalized_lipsync_engine = normalize_avatar_engine(raw_lipsync_engine)
    runtime_settings = normalize_avatar_runtime_settings(
        {
            "motion_preset": motion_preset,
            "restoration_enabled": restoration_enabled,
            "liveportrait_enabled": liveportrait_enabled,
        }
    )
    request = AvatarRenderRequest(
        source_image_path=(source_image_abs or source_image_original_abs),
        source_image_original_path=(source_image_original_abs or source_image_abs),
        source_video_path=source_video_abs,
        avatar_reference_type=reference_type,
        audio_path=audio_abs,
        output_path=output_abs,
        motion_preset=str(runtime_settings["motion_preset"]),
        quality_preset=str(quality_preset or "high"),
        lipsync_engine=normalized_lipsync_engine,
        restoration_enabled=bool(runtime_settings["restoration_enabled"]),
        liveportrait_enabled=bool(runtime_settings["liveportrait_enabled"]),
        cache_text_hash=str(cache_text_hash or ""),
    )
    setattr(request, "_requested_engine_raw", raw_lipsync_engine)
    setattr(request, "_project_id", int(project_id or 0))
    setattr(request, "_avatar_job_id", int(avatar_job_id or 0))
    setattr(request, "_segment_index", int(slide_index or 0))

    logger.info(
        "Avatar segment dispatch project_id=%s teacher_id=%s slide_index=%s source_image_path=%s source_image_original_path=%s source_video_path=%s audio_path=%s output_path=%s text_hash=%s requested_engine_raw=%s normalized_engine=%s",
        int(project_id or 0),
        int(teacher_id or 0),
        int(slide_index or 0),
        request.source_image_path,
        request.source_image_original_path,
        request.source_video_path,
        request.audio_path,
        request.output_path,
        request.cache_text_hash,
        raw_lipsync_engine,
        request.lipsync_engine,
    )

    with _avatar_gpu_serial_section(stage_name="render_avatar_segment"):
        render_info = render_avatar_segment_local(request)
    render_info["project_id"] = int(project_id) if project_id is not None else None
    render_info["teacher_id"] = int(teacher_id) if teacher_id is not None else None
    render_info["slide_index"] = int(slide_index or 0)
    render_info["avatar_reference_type"] = reference_type
    render_info["avatar_engine_selected"] = normalized_lipsync_engine
    render_info["avatar_runtime_settings"] = runtime_settings
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
    if not _avatar_feature_enabled():
        return {
            "status": "disabled",
            "teacher_id": int(teacher_id),
            "job_id": int(job_id or 0) or None,
            "error": "Avatar disabled by environment.",
        }
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
    if not _avatar_feature_enabled():
        return {"project_id": int(project_id), "teacher_id": int(teacher_id), "segments": [], "status": "skipped_disabled"}
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
                "restoration_enabled": bool(avatar_cfg.get("restoration_enabled")),
                "liveportrait_enabled": bool(avatar_cfg.get("liveportrait_enabled", True)),
                "cache_text_hash": str(segment.get("text_hash") or ""),
            }
        ).result
        outputs.append(result)
    return {"project_id": int(project_id), "teacher_id": int(teacher_id), "segments": outputs, "status": "done"}


@app.task(bind=True, name="worker.tasks.render_lesson_avatar_overlay", max_retries=0)
def render_lesson_avatar_overlay(
    self,
    project_id: int,
    teacher_id: int | None = None,
    render_results: list[dict[str, Any]] | None = None,
    avatar_options: dict[str, Any] | None = None,
    output_rel_prefix: str = "",
    avatar_job_id: int | None = None,
    handoff_manifest_path: str | None = None,
    base_job_id: int | None = None,
) -> dict[str, Any]:
    """Render lesson avatar artifacts after the base lesson video is available."""
    if not _avatar_feature_enabled():
        _mark_project_avatar_state(
            project_id,
            status="none",
            message="",
            job_id="",
            clear_output=True,
        )
        return {"status": "skipped_disabled", "project_id": int(project_id), "teacher_id": int(teacher_id or 0)}
    try:
        from django.utils import timezone
        from core.models import Job, Project, UserProfile
        from core.avatar_runtime_settings import normalize_avatar_runtime_settings
        from avatar.canonical_adapters import run_restoration
        from avatar.hashing import sha256_file
        from avatar import pipeline as avatar_pipeline
        from scripts.ffmpeg_helpers import concat_videos
    except ImportError as exc:
        raise RuntimeError(f"Avatar overlay dependencies not importable: {exc}") from exc

    project_id_int = int(project_id)
    teacher_id_int = int(teacher_id or 0)
    job_id = int(avatar_job_id or 0) or None
    avatar_cfg = dict(avatar_options or {})
    output_rel_prefix = str(output_rel_prefix or str(project_id_int)).strip().replace("\\", "/").strip("/") or str(project_id_int)
    base_job_id_int = int(base_job_id or 0) or None

    def _set_job(**updates: Any) -> None:
        if job_id:
            Job.objects.filter(pk=job_id).update(**updates, updated_at=timezone.now())

    def _fail(message: str, failures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        message = str(message or "Avatar failed. Base video is still published.")
        _set_job(status="failed", progress=100, error_message=message)
        if _avatar_job_is_current(project_id_int, job_id):
            _mark_project_avatar_state(
                project_id_int,
                status="failed",
                message=message,
                job_id=job_id,
                clear_output=True,
            )
            sidecar = _read_playback_sidecar(project_id_int)
            if sidecar:
                sidecar["avatar"] = None
                sidecar["avatar_status"] = "failed"
                sidecar["avatar_failures"] = list(failures or [])
                _write_playback_sidecar(project_id_int, sidecar)
            _notify_avatar_failed(project_id_int, job_id)
        return {
            "status": "failed",
            "project_id": project_id_int,
            "teacher_id": teacher_id_int,
            "avatar_failures": list(failures or []),
            "message": message,
        }

    if not _avatar_job_is_current(project_id_int, job_id):
        message = "Stale avatar job ignored."
        _set_job(status="failed", progress=100, error_message=message)
        return {"status": "stale", "project_id": project_id_int, "teacher_id": teacher_id_int, "message": message}

    if handoff_manifest_path:
        try:
            handoff = _read_avatar_handoff_manifest(handoff_manifest_path)
        except Exception as exc:  # noqa: BLE001
            reason = _concise_error_text(exc, fallback="avatar_handoff_manifest_unavailable")
            return _fail("Avatar failed. Base video is still published.", [{"status": "avatar_handoff_unavailable", "reason": reason}])
        try:
            manifest_project_id = int(handoff.get("project_id") or 0)
            manifest_base_job_id = int(handoff.get("base_job_id") or 0) or None
        except Exception:
            return _fail(
                "Avatar failed. Base video is still published.",
                [{"status": "avatar_handoff_invalid", "reason": "invalid_manifest_ids"}],
            )
        if manifest_project_id != project_id_int:
            return _fail(
                "Avatar failed. Base video is still published.",
                [{"status": "avatar_handoff_project_mismatch", "reason": "project_id_mismatch"}],
            )
        if base_job_id_int and manifest_base_job_id and manifest_base_job_id != base_job_id_int:
            return _fail(
                "Avatar failed. Base video is still published.",
                [{"status": "avatar_handoff_stale", "reason": "base_job_id_mismatch"}],
            )
        base_job_id_int = manifest_base_job_id or base_job_id_int
        render_results = list(handoff.get("ordered_results") or [])
        avatar_cfg = dict(handoff.get("avatar_settings") or {})
        if not teacher_id_int:
            teacher_id_int = int(avatar_cfg.get("teacher_id") or 0)
        render_meta = handoff.get("render_metadata") if isinstance(handoff.get("render_metadata"), dict) else {}
        output_rel_prefix = str(render_meta.get("output_rel_prefix") or output_rel_prefix or str(project_id_int)).strip().replace("\\", "/").strip("/") or str(project_id_int)

    runtime_settings = normalize_avatar_runtime_settings(avatar_cfg.get("avatar_runtime_settings") or avatar_cfg)
    avatar_cfg["avatar_runtime_settings"] = runtime_settings
    avatar_cfg["motion_preset"] = runtime_settings["motion_preset"]
    avatar_cfg["restoration_enabled"] = bool(runtime_settings["restoration_enabled"])
    avatar_cfg["liveportrait_enabled"] = bool(runtime_settings["liveportrait_enabled"])
    restoration_requested = bool(avatar_cfg.get("restoration_enabled"))
    progressive_restoration_enabled = bool(
        restoration_requested
        and _env_enabled("AVATAR_PROGRESSIVE_RESTORATION_ENABLED", False)
    )
    avatar_cfg["progressive_restoration_enabled"] = progressive_restoration_enabled

    ordered = sorted(list(render_results or []), key=lambda item: int(item.get("index") or 0))
    logger.info("Lesson avatar overlay START project=%s teacher=%s slides=%d", project_id_int, teacher_id_int, len(ordered))
    _set_job(status="running", progress=5)
    _mark_project_avatar_state(
        project_id_int,
        status="processing",
        message="Avatar is still processing and will be added when ready.",
        job_id=job_id,
        clear_output=True,
    )
    self.update_state(state="PROGRESS", meta={"step": "avatar_processing", "project_id": project_id_int, "progress": 5})

    if not ordered:
        return _fail("Avatar failed. Base video is still published.", [{"status": "no_segments", "reason": "no_render_segments"}])

    if bool(avatar_cfg.get("avatar_moderation_blocked")):
        reason = str(avatar_cfg.get("disabled_reason") or avatar_cfg.get("avatar_moderation_error_code") or "avatar_image_moderation_blocked")
        return _fail("Avatar failed. Base video is still published.", [{"status": "avatar_moderation_blocked", "reason": reason}])
    if avatar_cfg.get("avatar_source_valid") is False:
        reason = str(avatar_cfg.get("avatar_source_validation_error") or "avatar_input_face_not_detected")
        return _fail("Avatar failed. Base video is still published.", [{"status": "avatar_source_invalid", "reason": reason}])
    if bool(avatar_cfg.get("avatar_preview_stale")):
        return _fail("Avatar failed. Base video is still published.", [{"status": "avatar_preview_stale", "reason": "avatar_preview_stale"}])

    output_dir = Path(_avatar_storage_root()) / output_rel_prefix
    segment_dir = output_dir / "avatar_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    avatar_segments: list[dict[str, Any]] = []
    avatar_failures: list[dict[str, Any]] = []
    avatar_slide_metadata: list[dict[str, Any]] = []
    final_avatar_engine_chain: list[str] = []
    segment_rel_by_index: dict[int, str] = {}
    restored_segment_rel_by_index: dict[int, str] = {}

    source_rel = str(avatar_cfg.get("source_image_rel_path") or "")
    source_abs = str(Path(_avatar_storage_root()) / source_rel) if source_rel else ""
    source_hash = sha256_file(source_abs) if source_abs and Path(source_abs).exists() else ""

    for position, item in enumerate(ordered, start=1):
        index = int(item.get("index") or 0)
        slide_num = int(item.get("slide_num") or position)
        audio_path = str(item.get("tts_audio_path") or "")
        avatar_output = segment_dir / f"avatar_{slide_num:03d}.mp4"
        metadata_payload: dict[str, Any] = {
            "index": index,
            "slide_num": slide_num,
            "page_key": str(item.get("page_key") or ""),
            "avatar_attempted": True,
            "avatar_skipped": False,
            "avatar_applied": False,
            "avatar_failed": True,
            "avatar_status": "failed",
            "avatar_error": "",
            "avatar_segment_rel_path": "",
            "avatar_engine_used": "none",
            "avatar_engine_selected": str(avatar_cfg.get("avatar_engine_selected") or avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk"),
            "avatar_fallback_chain": [],
            "avatar_motion_validation": {},
        }
        try:
            if not audio_path or not Path(audio_path).exists():
                raise RuntimeError("avatar_render_audio_missing")
            avatar_result = render_avatar_segment.apply(
                kwargs={
                    "project_id": project_id_int,
                    "teacher_id": teacher_id_int,
                    "slide_index": slide_num,
                    "audio_path": audio_path,
                    "output_path": str(avatar_output),
                    "source_image_rel_path": str(avatar_cfg.get("source_image_rel_path") or ""),
                    "source_image_original_rel_path": str(
                        avatar_cfg.get("source_image_original_rel_path")
                        or avatar_cfg.get("source_rel_path")
                        or avatar_cfg.get("source_image_rel_path")
                        or ""
                    ),
                    "source_video_rel_path": str(avatar_cfg.get("source_video_rel_path") or ""),
                    "avatar_reference_type": str(avatar_cfg.get("avatar_reference_type") or "image"),
                    "motion_preset": str(avatar_cfg.get("motion_preset") or "natural"),
                    "quality_preset": str(avatar_cfg.get("quality_preset") or "high"),
                    "lipsync_engine": str(avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk"),
                    "restoration_enabled": False,
                    "liveportrait_enabled": bool(avatar_cfg.get("liveportrait_enabled", True)),
                    "cache_text_hash": hashlib.sha256(str(item.get("text") or "").encode("utf-8")).hexdigest(),
                    "avatar_job_id": int(job_id or 0),
                }
            )
            if avatar_result.failed():
                raise RuntimeError(_concise_error_text(avatar_result.result, fallback="avatar_segment_failed"))
            payload = avatar_result.result
            if not isinstance(payload, dict):
                raise RuntimeError(f"render_avatar_segment returned unexpected type: {type(payload).__name__}")
            avatar_output_path = str(payload.get("output_path") or "")
            if not avatar_output_path or not Path(avatar_output_path).exists():
                raise RuntimeError("missing_avatar_output")
            rel_path = _safe_rel_path(_avatar_storage_root(), avatar_output_path)
            fallback_chain = list(payload.get("fallback_chain_used") or payload.get("final_avatar_engine_chain") or [])
            engine_used = str(payload.get("engine_used") or "liveportrait+musetalk")
            avatar_engine_selected = str(payload.get("avatar_engine_selected") or payload.get("normalized_engine") or engine_used)
            runtime_observability = {
                "liveportrait_enabled": bool(payload.get("liveportrait_enabled")),
                "liveportrait_started": bool(payload.get("liveportrait_started")),
                "liveportrait_succeeded": bool(payload.get("liveportrait_succeeded")),
                "liveportrait_failed": bool(payload.get("liveportrait_failed")),
                "liveportrait_failure_reason": str(payload.get("liveportrait_failure_reason") or ""),
                "liveportrait_quality_warning": str(payload.get("liveportrait_quality_warning") or ""),
                "liveportrait_fallback_used": bool(payload.get("liveportrait_fallback_used")),
                "liveportrait_fallback_reason": str(payload.get("liveportrait_fallback_reason") or ""),
                "musetalk_source_kind": str(payload.get("musetalk_source_kind") or ""),
                "restoration_requested": bool(restoration_requested),
                "restoration_enabled": bool(progressive_restoration_enabled),
                "progressive_restoration_enabled": bool(progressive_restoration_enabled),
                "restoration_pending": bool(progressive_restoration_enabled),
                "restoration_succeeded": bool(payload.get("restoration_succeeded")),
                "restoration_failed": bool(payload.get("restoration_failed")),
            }
            if not final_avatar_engine_chain:
                final_avatar_engine_chain = list(payload.get("final_avatar_engine_chain") or fallback_chain or ["liveportrait", "musetalk"])
            metadata_payload.update(
                {
                    "avatar_applied": True,
                    "avatar_failed": False,
                    "avatar_status": "ready",
                    "avatar_error": "",
                    "avatar_segment_rel_path": rel_path,
                    "avatar_segment_fast_rel_path": rel_path,
                    "avatar_segment_restored_rel_path": "",
                    "avatar_quality": "fast",
                    "avatar_enhanced_pending": bool(progressive_restoration_enabled),
                    "avatar_enhanced_available": False,
                    "avatar_engine_used": engine_used,
                    "avatar_engine_selected": avatar_engine_selected,
                    "avatar_fallback_chain": fallback_chain,
                    "avatar_motion_validation": dict(payload.get("motion_validation") or {}),
                    **runtime_observability,
                }
            )
            avatar_segments.append(
                {
                    "index": index,
                    "engine": engine_used,
                    "avatar_engine_selected": avatar_engine_selected,
                    "fallback_chain": fallback_chain,
                    "segment_rel_path": rel_path,
                    "fast_segment_rel_path": rel_path,
                    "restored_segment_rel_path": "",
                    "quality": "fast",
                    "enhanced_pending": bool(progressive_restoration_enabled),
                    "enhanced_available": False,
                    **runtime_observability,
                    "duration": round(float(item.get("duration") or 0.0), 3),
                }
            )
            segment_rel_by_index[index] = rel_path
            try:
                _record_avatar_render_job(
                    lesson_id=project_id_int,
                    teacher_id=teacher_id_int,
                    source_image_hash=source_hash,
                    tts_audio_hash=sha256_file(audio_path),
                    lesson_text_hash=hashlib.sha256(str(item.get("text") or "").encode("utf-8")).hexdigest(),
                    slide_hash=sha256_file(str(item.get("slide_path"))) if item.get("slide_path") and Path(str(item.get("slide_path"))).exists() else "",
                    engine_used=engine_used,
                    render_status="done",
                    render_error="",
                    output_path=rel_path,
                    fallback_chain_used=fallback_chain,
                    metadata={
                        "avatar_version": str(avatar_cfg.get("model_version") or "liveportrait+musetalk:v1"),
                        "avatar_reference_type": str(avatar_cfg.get("avatar_reference_type") or "image"),
                        "avatar_status": "ready",
                        "slide_num": slide_num,
                        "page_key": str(item.get("page_key") or ""),
                        "motion_validation": dict(payload.get("motion_validation") or {}),
                        "normalized_engine": str(payload.get("normalized_engine") or avatar_engine_selected),
                        "avatar_engine_selected": avatar_engine_selected,
                        "final_avatar_engine_chain": final_avatar_engine_chain,
                        "avatar_runtime_settings": runtime_settings,
                        **runtime_observability,
                        "background_avatar_job_id": job_id,
                    },
                )
            except Exception:
                logger.warning("Avatar render telemetry failed for project=%s slide=%s", project_id_int, slide_num, exc_info=True)
        except Exception as exc:  # noqa: BLE001
            reason = _concise_error_text(exc, fallback="avatar_segment_failed")
            metadata_payload["avatar_error"] = reason
            avatar_failures.append(
                {
                    "index": index,
                    "slide_num": slide_num,
                    "page_key": str(item.get("page_key") or ""),
                    "status": "failed",
                    "skipped": False,
                    "reason": reason,
                    "validation": {},
                }
            )
            try:
                _record_avatar_render_job(
                    lesson_id=project_id_int,
                    teacher_id=teacher_id_int,
                    source_image_hash=source_hash,
                    tts_audio_hash=sha256_file(audio_path) if audio_path and Path(audio_path).exists() else "",
                    lesson_text_hash=hashlib.sha256(str(item.get("text") or "").encode("utf-8")).hexdigest(),
                    slide_hash="",
                    engine_used="none",
                    render_status="failed",
                    render_error=reason,
                    output_path="",
                    fallback_chain_used=[],
                    metadata={
                        "avatar_version": str(avatar_cfg.get("model_version") or "liveportrait+musetalk:v1"),
                        "avatar_reference_type": str(avatar_cfg.get("avatar_reference_type") or "image"),
                        "avatar_status": "failed",
                        "normalized_engine": str(avatar_cfg.get("avatar_engine_selected") or avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk"),
                        "avatar_engine_selected": str(avatar_cfg.get("avatar_engine_selected") or avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk"),
                        "avatar_runtime_settings": runtime_settings,
                        "slide_num": slide_num,
                        "page_key": str(item.get("page_key") or ""),
                        "background_avatar_job_id": job_id,
                    },
                )
            except Exception:
                logger.warning("Failed avatar telemetry skipped for project=%s slide=%s", project_id_int, slide_num, exc_info=True)
            logger.warning("Avatar segment failed project=%s slide=%s reason=%s", project_id_int, slide_num, reason)
        avatar_slide_metadata.append(metadata_payload)
        progress = min(90, 5 + int((position / max(len(ordered), 1)) * 75))
        _set_job(progress=progress)
        self.update_state(state="PROGRESS", meta={"step": "avatar_processing", "project_id": project_id_int, "progress": progress})

    if avatar_failures or len(avatar_segments) != len(ordered):
        return _fail("Avatar failed. Base video is still published.", avatar_failures)

    avatar_track_dir = output_dir / "avatar"
    avatar_track_dir.mkdir(parents=True, exist_ok=True)
    artifact_version_token = f"job{job_id}" if job_id else f"run{int(time.time() * 1000)}"

    def _non_overwriting_path(directory: Path, filename: str) -> Path:
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        return candidate.with_name(f"{candidate.stem}.{artifact_version_token}{candidate.suffix}")

    avatar_track_path = avatar_track_dir / "avatar_track.mp4"
    avatar_fast_track_path = _non_overwriting_path(avatar_track_dir, "avatar_track_fast.mp4")
    avatar_restored_track_path = _non_overwriting_path(avatar_track_dir, "avatar_track_restored.mp4")
    segment_paths = [
        str(Path(_avatar_storage_root()) / segment["segment_rel_path"])
        for segment in sorted(avatar_segments, key=lambda seg: seg.get("index", 0))
    ]
    try:
        concat_videos(segment_paths, str(avatar_fast_track_path))
    except Exception as exc:  # noqa: BLE001
        return _fail("Avatar failed. Base video is still published.", [{"status": "avatar_concat_failed", "reason": _concise_error_text(exc, fallback="avatar_concat_failed")}])

    if not avatar_track_path.exists():
        try:
            shutil.copy2(str(avatar_fast_track_path), str(avatar_track_path))
        except Exception:
            logger.warning("Could not create compatibility avatar_track.mp4 for project=%s", project_id_int, exc_info=True)

    if not _avatar_job_is_current(project_id_int, job_id):
        message = "Stale avatar job ignored."
        _set_job(status="failed", progress=100, error_message=message)
        return {"status": "stale", "project_id": project_id_int, "teacher_id": teacher_id_int, "message": message}

    avatar_fast_track_rel = _safe_rel_path(_avatar_storage_root(), avatar_fast_track_path)
    avatar_track_rel = avatar_fast_track_rel

    def _track_version(path: Path) -> str:
        try:
            stat = path.stat()
            return hashlib.sha256(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
        except Exception:
            return ""

    def _publish_avatar_sidecar(
        *,
        preferred_track_rel: str,
        quality: str,
        enhanced_available: bool,
        enhanced_pending: bool,
        restored_track_rel: str = "",
        restoration_failures: list[dict[str, Any]] | None = None,
    ) -> None:
        sidecar = _read_playback_sidecar(project_id_int) or {}
        preferred_abs = Path(_avatar_storage_root()) / preferred_track_rel
        now_iso = timezone.now().isoformat()
        sidecar["avatar"] = {
            "track_rel_path": preferred_track_rel,
            "track_fast_rel_path": avatar_fast_track_rel,
            "track_restored_rel_path": restored_track_rel,
            "quality": quality,
            "enhanced_available": bool(enhanced_available),
            "enhanced_pending": bool(enhanced_pending),
            "version": _track_version(preferred_abs),
            "updated_at": now_iso,
            "progressive_restoration_enabled": bool(progressive_restoration_enabled),
            "default_position": "top-right",
            "default_size": "medium",
            "segments": avatar_segments,
        }
        sidecar["avatar_status"] = "ready"
        sidecar["avatar_restoration_status"] = (
            "restored" if enhanced_available else ("restoring" if enhanced_pending else ("failed" if restoration_failures else "not_requested"))
        )
        sidecar["avatar_engine_selected"] = str(avatar_cfg.get("avatar_engine_selected") or avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk")
        sidecar["normalized_engine"] = sidecar["avatar_engine_selected"]
        sidecar["final_avatar_engine_chain"] = final_avatar_engine_chain
        sidecar["avatar_runtime_settings"] = runtime_settings
        sidecar["avatar_failures"] = []
        sidecar["avatar_restoration_failures"] = list(restoration_failures or [])
        sidecar["avatar_clips"] = [segment_rel_by_index.get(int(item.get("index") or 0), "") for item in ordered]
        sidecar["avatar_clips_fast"] = list(sidecar["avatar_clips"])
        sidecar["avatar_clips_restored"] = [restored_segment_rel_by_index.get(int(item.get("index") or 0), "") for item in ordered]
        sidecar["avatar_slide_metadata"] = avatar_slide_metadata
        final_segments = sidecar.get("final_segments")
        if isinstance(final_segments, list):
            metadata_by_index = {int(item.get("index") or 0): item for item in avatar_slide_metadata}
            for final_segment in final_segments:
                if not isinstance(final_segment, dict):
                    continue
                index = int(final_segment.get("index") or 0)
                meta = metadata_by_index.get(index, {})
                final_segment["avatar_clip"] = str(meta.get("avatar_segment_rel_path") or "")
                final_segment["avatar_clip_fast"] = str(meta.get("avatar_segment_fast_rel_path") or meta.get("avatar_segment_rel_path") or "")
                final_segment["avatar_clip_restored"] = str(meta.get("avatar_segment_restored_rel_path") or "")
                final_segment["avatar_attempted"] = bool(meta.get("avatar_attempted"))
                final_segment["avatar_skipped"] = bool(meta.get("avatar_skipped"))
                final_segment["avatar_applied"] = bool(meta.get("avatar_applied"))
                final_segment["avatar_failed"] = bool(meta.get("avatar_failed"))
                final_segment["avatar_status"] = str(meta.get("avatar_status") or "none")
                final_segment["avatar_quality"] = str(meta.get("avatar_quality") or quality)
                final_segment["avatar_enhanced_pending"] = bool(meta.get("avatar_enhanced_pending"))
                final_segment["avatar_enhanced_available"] = bool(meta.get("avatar_enhanced_available"))
                final_segment["avatar_error"] = str(meta.get("avatar_error") or "")
                final_segment["avatar_failure_reason"] = str(meta.get("avatar_error") or "")
                final_segment["avatar_engine_selected"] = str(meta.get("avatar_engine_selected") or meta.get("avatar_engine_used") or "none")
                final_segment["musetalk_source_kind"] = str(meta.get("musetalk_source_kind") or "")
                final_segment["liveportrait_fallback_used"] = bool(meta.get("liveportrait_fallback_used"))
                final_segment["liveportrait_failure_reason"] = str(meta.get("liveportrait_failure_reason") or "")
        _write_playback_sidecar(project_id_int, sidecar)

    fast_message = (
        "Avatar ready. Enhanced avatar restoration is still processing."
        if progressive_restoration_enabled
        else "Avatar ready."
    )
    _publish_avatar_sidecar(
        preferred_track_rel=avatar_fast_track_rel,
        quality="fast",
        enhanced_available=False,
        enhanced_pending=bool(progressive_restoration_enabled),
    )
    _set_job(status="running" if progressive_restoration_enabled else "done", progress=92 if progressive_restoration_enabled else 100, result_url=avatar_fast_track_rel, error_message="")
    _mark_project_avatar_state(
        project_id_int,
        status="ready",
        message=fast_message,
        job_id=job_id,
        output_path=avatar_fast_track_rel,
    )
    UserProfile.objects.filter(user_id=teacher_id_int).update(avatar_last_rendered_at=timezone.now())
    Project.objects.filter(pk=project_id_int).update(updated_at=timezone.now())

    restoration_failures: list[dict[str, Any]] = []
    if progressive_restoration_enabled:
        self.update_state(state="PROGRESS", meta={"step": "avatar_restoration", "project_id": project_id_int, "progress": 92})
        restored_segments_by_index: dict[int, dict[str, Any]] = {}
        for position, item in enumerate(ordered, start=1):
            index = int(item.get("index") or 0)
            slide_num = int(item.get("slide_num") or position)
            fast_rel = segment_rel_by_index.get(index, "")
            audio_path = str(item.get("tts_audio_path") or "")
            fast_abs = Path(_avatar_storage_root()) / fast_rel if fast_rel else Path("")
            restored_path = _non_overwriting_path(segment_dir, f"avatar_{slide_num:03d}.restored.mp4")
            temp_restored_path = restored_path.with_name(f"{restored_path.name}.tmp-{job_id or os.getpid()}")
            try:
                if not fast_abs.exists() or not fast_abs.is_file():
                    raise RuntimeError("fast_avatar_segment_missing")
                restoration_result = run_restoration(
                    input_video=str(fast_abs),
                    output_path=str(temp_restored_path),
                    source_image=source_abs,
                    audio_path=audio_path,
                    timeout_seconds=float(os.environ.get("AVATAR_STAGE_TIMEOUT_RESTORATION_SECONDS", "180") or 180),
                )
                if not restoration_result.success:
                    raise RuntimeError(restoration_result.error or "restoration_failed")
                avatar_pipeline._assert_video_contract(str(temp_restored_path), stage_name="lesson_avatar_restoration")
                temp_restored_path.replace(restored_path)
                restored_rel = _safe_rel_path(_avatar_storage_root(), restored_path)
                restored_segment_rel_by_index[index] = restored_rel
                restored_segments_by_index[index] = {
                    "segment_rel_path": restored_rel,
                    "restored_segment_rel_path": restored_rel,
                    "fast_segment_rel_path": fast_rel,
                }
            except Exception as exc:  # noqa: BLE001
                temp_restored_path.unlink(missing_ok=True)
                reason = _concise_error_text(exc, fallback="avatar_restoration_failed")
                restoration_failures.append({"index": index, "slide_num": slide_num, "status": "failed", "reason": reason})
                logger.warning("Avatar restoration failed project=%s slide=%s reason=%s", project_id_int, slide_num, reason)
            finally:
                progress = min(99, 92 + int((position / max(len(ordered), 1)) * 6))
                _set_job(progress=progress)
                self.update_state(state="PROGRESS", meta={"step": "avatar_restoration", "project_id": project_id_int, "progress": progress})

        if not restoration_failures and len(restored_segment_rel_by_index) == len(ordered):
            if "restoration" not in final_avatar_engine_chain:
                final_avatar_engine_chain = list(final_avatar_engine_chain or ["liveportrait", "musetalk"]) + ["restoration"]
            for segment in avatar_segments:
                restored_info = restored_segments_by_index.get(int(segment.get("index") or 0), {})
                if restored_info:
                    segment["segment_rel_path"] = str(restored_info["segment_rel_path"])
                    segment["restored_segment_rel_path"] = str(restored_info["restored_segment_rel_path"])
                    segment["quality"] = "restored"
                    segment["enhanced_pending"] = False
                    segment["enhanced_available"] = True
                    segment["restoration_succeeded"] = True
                    segment["restoration_pending"] = False
                    segment["fallback_chain"] = list(final_avatar_engine_chain)
            metadata_by_index = {int(meta.get("index") or 0): meta for meta in avatar_slide_metadata}
            for index, restored_rel in restored_segment_rel_by_index.items():
                meta = metadata_by_index.get(index)
                if meta is None:
                    continue
                meta["avatar_segment_restored_rel_path"] = restored_rel
                meta["avatar_segment_rel_path"] = restored_rel
                meta["avatar_quality"] = "restored"
                meta["avatar_enhanced_pending"] = False
                meta["avatar_enhanced_available"] = True
                meta["restoration_succeeded"] = True
                meta["restoration_pending"] = False
                meta["avatar_fallback_chain"] = list(final_avatar_engine_chain)
            restored_segment_paths = [
                str(Path(_avatar_storage_root()) / restored_segment_rel_by_index[int(item.get("index") or 0)])
                for item in ordered
            ]
            temp_restored_track_path = avatar_restored_track_path.with_name(f"{avatar_restored_track_path.name}.tmp-{job_id or os.getpid()}")
            try:
                concat_videos(restored_segment_paths, str(temp_restored_track_path))
                temp_restored_track_path.replace(avatar_restored_track_path)
            except Exception as exc:  # noqa: BLE001
                temp_restored_track_path.unlink(missing_ok=True)
                restoration_failures.append({"status": "avatar_restored_concat_failed", "reason": _concise_error_text(exc, fallback="avatar_restored_concat_failed")})

        if restoration_failures:
            _publish_avatar_sidecar(
                preferred_track_rel=avatar_fast_track_rel,
                quality="fast",
                enhanced_available=False,
                enhanced_pending=False,
                restoration_failures=restoration_failures,
            )
            _set_job(status="done", progress=100, result_url=avatar_fast_track_rel, error_message="")
            _mark_project_avatar_state(
                project_id_int,
                status="ready",
                message="Avatar ready. Enhanced restoration failed; fast avatar is available.",
                job_id=job_id,
                output_path=avatar_fast_track_rel,
            )
            avatar_track_rel = avatar_fast_track_rel
        else:
            avatar_restored_track_rel = _safe_rel_path(_avatar_storage_root(), avatar_restored_track_path)
            _publish_avatar_sidecar(
                preferred_track_rel=avatar_restored_track_rel,
                quality="restored",
                enhanced_available=True,
                enhanced_pending=False,
                restored_track_rel=avatar_restored_track_rel,
            )
            _set_job(status="done", progress=100, result_url=avatar_restored_track_rel, error_message="")
            _mark_project_avatar_state(
                project_id_int,
                status="ready",
                message="Enhanced avatar ready.",
                job_id=job_id,
                output_path=avatar_restored_track_rel,
            )
            avatar_track_rel = avatar_restored_track_rel
    else:
        _set_job(status="done", progress=100, result_url=avatar_track_rel, error_message="")

    logger.info("Lesson avatar overlay DONE project=%s track=%s progressive_restoration=%s", project_id_int, avatar_track_rel, bool(progressive_restoration_enabled))
    _notify_avatar_completed(project_id_int, job_id)
    return {
        "status": "ready",
        "project_id": project_id_int,
        "teacher_id": teacher_id_int,
        "avatar_track_rel_path": avatar_track_rel,
        "avatar_fast_track_rel_path": avatar_fast_track_rel,
        "avatar_restored_track_rel_path": _safe_rel_path(_avatar_storage_root(), avatar_restored_track_path) if avatar_restored_track_path.exists() else "",
        "avatar_segments": avatar_segments,
        "avatar_engine_selected": str(avatar_cfg.get("avatar_engine_selected") or avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk"),
        "normalized_engine": str(avatar_cfg.get("avatar_engine_selected") or avatar_cfg.get("lipsync_engine") or "liveportrait+musetalk"),
        "final_avatar_engine_chain": final_avatar_engine_chain,
        "progressive_restoration": bool(progressive_restoration_enabled),
        "restoration_failures": restoration_failures,
    }


def _queue_lesson_avatar_overlay_after_base_render(
    *,
    project_id: str | int,
    ordered_results: list[dict[str, Any]],
    avatar_options: dict[str, Any] | None,
    output_rel_prefix: str,
    base_job_id: int | None = None,
) -> dict[str, Any]:
    avatar_cfg = dict(avatar_options or {})
    if not _avatar_feature_enabled():
        _mark_project_avatar_state(
            project_id,
            status="none",
            message="",
            job_id="",
            clear_output=True,
        )
        return {"status": "skipped_disabled", "queued": False, "reason": "Avatar disabled by environment."}
    try:
        from core.avatar_runtime_settings import normalize_avatar_runtime_settings

        runtime_settings = normalize_avatar_runtime_settings(avatar_cfg.get("avatar_runtime_settings") or avatar_cfg)
        avatar_cfg["avatar_runtime_settings"] = runtime_settings
        avatar_cfg["motion_preset"] = runtime_settings["motion_preset"]
        avatar_cfg["restoration_enabled"] = bool(runtime_settings["restoration_enabled"])
        avatar_cfg["liveportrait_enabled"] = bool(runtime_settings["liveportrait_enabled"])
    except Exception:
        logger.warning("Avatar runtime settings normalization skipped for project=%s", project_id, exc_info=True)
    requested = bool(avatar_cfg.get("requested", avatar_cfg.get("enabled", False)))
    enabled = bool(avatar_cfg.get("enabled"))
    teacher_id = int(avatar_cfg.get("teacher_id") or 0)
    if not requested:
        _mark_project_avatar_state(
            project_id,
            status="none",
            message="",
            job_id="",
            clear_output=True,
        )
        return {"status": "none", "queued": False}
    if not enabled or not teacher_id:
        reason = str(
            avatar_cfg.get("disabled_reason")
            or avatar_cfg.get("avatar_source_validation_error")
            or avatar_cfg.get("avatar_moderation_error_code")
            or "avatar_not_available"
        )
        _mark_project_avatar_state(
            project_id,
            status="failed",
            message="Avatar failed. Base video is still published.",
            job_id="",
            clear_output=True,
        )
        _notify_avatar_failed(project_id)
        return {"status": "failed", "queued": False, "reason": reason}

    try:
        from django.utils import timezone
        from core.models import Job, Project

        resolved_base_job_id = int(base_job_id or avatar_cfg.get("base_job_id") or 0) or _latest_project_job_id(project_id, job_type="video_export")
        job = Job.objects.create(project_id=int(project_id), job_type="avatar_render", status="pending", progress=0)
        handoff_job_id = resolved_base_job_id or job.id
        handoff_manifest_path = _write_avatar_handoff_manifest(
            project_id,
            handoff_job_id,
            {
                "schema_version": 1,
                "project_id": int(project_id),
                "base_job_id": resolved_base_job_id,
                "avatar_job_id": job.id,
                "created_at": timezone.now().isoformat(),
                "ordered_results": list(ordered_results or []),
                "avatar_settings": avatar_cfg,
                "source_hashes": {
                    "avatar_source_hash": str(avatar_cfg.get("avatar_source_hash") or ""),
                    "avatar_preview_source_hash": str(avatar_cfg.get("avatar_preview_source_hash") or ""),
                },
                "render_metadata": {
                    "output_rel_prefix": str(output_rel_prefix or project_id),
                    "slide_count": len(ordered_results or []),
                    "lipsync_engine": str(avatar_cfg.get("lipsync_engine") or ""),
                    "model_version": str(avatar_cfg.get("model_version") or ""),
                },
                "status": "created",
            },
        )
        _mark_project_avatar_state(
            project_id,
            status="queued",
            message="Avatar is still processing and will be added when ready.",
            job_id=job.id,
            clear_output=True,
        )
        async_result = render_lesson_avatar_overlay.apply_async(
            kwargs={
                "project_id": int(project_id),
                "teacher_id": teacher_id,
                "output_rel_prefix": str(output_rel_prefix or project_id),
                "avatar_job_id": int(job.id),
                "handoff_manifest_path": handoff_manifest_path,
                "base_job_id": resolved_base_job_id,
            },
            queue=_avatar_queue_name(),
        )
        Job.objects.filter(pk=job.id).update(celery_task_id=str(async_result.id or ""), updated_at=timezone.now())
        Project.objects.filter(pk=int(project_id)).update(updated_at=timezone.now())
        return {
            "status": "queued",
            "queued": True,
            "job_id": job.id,
            "base_job_id": resolved_base_job_id,
            "handoff_manifest_path": handoff_manifest_path,
            "celery_task_id": str(async_result.id or ""),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to queue lesson avatar overlay project=%s", project_id, exc_info=True)
        _mark_project_avatar_state(
            project_id,
            status="failed",
            message="Avatar failed. Base video is still published.",
            job_id="",
            clear_output=True,
        )
        _notify_avatar_failed(project_id)
        return {"status": "failed", "queued": False, "reason": _concise_error_text(exc, fallback="avatar_queue_failed")}


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


# ---------------------------------------------------------------------------
# Smoke-test tasks
# ---------------------------------------------------------------------------

@app.task(name="worker.tasks.ping")
def ping(message: str = "ping") -> str:
    """Smoke-test task. Send 'ping', get 'pong'."""
    return "pong" if message == "ping" else f"echo: {message}"


def _replace_intelligence_attempt(metadata: dict[str, Any], provider: str, status_value: str, error: Exception | str | None = None) -> dict[str, Any]:
    from core.intelligence_progressive import provider_attempt

    normalized_provider = str(provider or "").strip().lower()
    attempts = metadata.get("provider_chain_attempts")
    next_attempts = [item for item in attempts if isinstance(item, dict)] if isinstance(attempts, list) else []
    replacement = provider_attempt(normalized_provider, status_value, error)
    replaced = False
    for index, item in enumerate(next_attempts):
        if str(item.get("provider") or "").strip().lower() == normalized_provider:
            next_attempts[index] = replacement
            replaced = True
            break
    if not replaced:
        next_attempts.insert(0, replacement)
    metadata["provider_chain_attempts"] = next_attempts
    return metadata


def _mark_lesson_intelligence_enhancement(
    report_id: int,
    status_value: str,
    *,
    task_id: str = "",
    timeout_seconds: float | int | None = None,
    error: Exception | str | None = None,
    phase: str | None = None,
    chunk_count: int | None = None,
    completed_chunks: int | None = None,
    failed_chunks: int | None = None,
    extra: dict[str, Any] | None = None,
):
    from core.intelligence_progressive import (
        PROGRESSIVE_ENHANCEMENT_KEY,
        TERMINAL_ENHANCEMENT_STATUSES,
        enhancement_lock_key,
        lesson_section_statuses,
        merge_enhancement_metadata,
    )
    from core.models import LessonIntelligenceReport

    report = LessonIntelligenceReport.objects.filter(pk=int(report_id)).first()
    if report is None:
        return None
    progress_extra = dict(extra or {})
    if phase:
        progress_extra["phase"] = str(phase)
    if chunk_count is not None:
        progress_extra["chunk_count"] = int(chunk_count)
    if completed_chunks is not None:
        progress_extra["completed_chunks"] = int(completed_chunks)
    if failed_chunks is not None:
        progress_extra["failed_chunks"] = int(failed_chunks)
    if status_value == "running" and "sections" not in progress_extra:
        progress_extra["sections"] = lesson_section_statuses(status="running", provider="ollama")
    if status_value == "failed" and "sections" not in progress_extra:
        progress_extra["sections"] = lesson_section_statuses(status="failed", provider="heuristic", error=error)
    metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status=status_value,
        task_id=task_id,
        timeout_seconds=timeout_seconds,
        error=error,
        extra=progress_extra,
    )
    if status_value in {"running", "done", "partial", "failed"}:
        attempt_status = "success" if status_value == "done" else status_value
        _replace_intelligence_attempt(metadata, "ollama", attempt_status, error)
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])
    if status_value in TERMINAL_ENHANCEMENT_STATUSES:
        try:
            from django.core.cache import cache

            enhancement = metadata.get(PROGRESSIVE_ENHANCEMENT_KEY) if isinstance(metadata.get(PROGRESSIVE_ENHANCEMENT_KEY), dict) else {}
            lock_key = enhancement_lock_key(str(enhancement.get("run_key") or metadata.get("run_key") or ""))
            if lock_key:
                cache.delete(lock_key)
        except Exception:
            pass
    return report


def _mark_analytics_intelligence_enhancement(
    report_id: int,
    status_value: str,
    *,
    task_id: str = "",
    timeout_seconds: float | int | None = None,
    error: Exception | str | None = None,
    phase: str | None = None,
    chunk_count: int | None = None,
    completed_chunks: int | None = None,
    failed_chunks: int | None = None,
    extra: dict[str, Any] | None = None,
):
    from core.intelligence_progressive import PROGRESSIVE_ENHANCEMENT_KEY, TERMINAL_ENHANCEMENT_STATUSES, enhancement_lock_key, merge_enhancement_metadata
    from core.models import AnalyticsIntelligenceReport

    report = AnalyticsIntelligenceReport.objects.filter(pk=int(report_id)).first()
    if report is None:
        return None
    progress_extra = dict(extra or {})
    if phase:
        progress_extra["phase"] = str(phase)
    if chunk_count is not None:
        progress_extra["chunk_count"] = int(chunk_count)
    if completed_chunks is not None:
        progress_extra["completed_chunks"] = int(completed_chunks)
    if failed_chunks is not None:
        progress_extra["failed_chunks"] = int(failed_chunks)
    metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status=status_value,
        task_id=task_id,
        timeout_seconds=timeout_seconds,
        error=error,
        extra=progress_extra,
    )
    if status_value in {"running", "done", "partial", "failed"}:
        _replace_intelligence_attempt(metadata, "ollama", "success" if status_value == "done" else status_value, error)
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])
    if status_value in TERMINAL_ENHANCEMENT_STATUSES:
        try:
            from django.core.cache import cache

            enhancement = metadata.get(PROGRESSIVE_ENHANCEMENT_KEY) if isinstance(metadata.get(PROGRESSIVE_ENHANCEMENT_KEY), dict) else {}
            lock_key = enhancement_lock_key(str(enhancement.get("run_key") or metadata.get("run_key") or ""))
            if lock_key:
                cache.delete(lock_key)
        except Exception:
            pass
    return report


@app.task(bind=True, name="worker.tasks.schedule_lesson_intelligence", max_retries=0)
def schedule_lesson_intelligence(
    self,
    project_id: int,
    reason: str = "auto",
    requested_by_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create or refresh the quick lesson report and queue slow enhancement."""
    if not _intelligence_feature_enabled():
        return {"status": "disabled", "project_id": int(project_id), "reason": "Intelligence disabled by environment."}
    try:
        from core.views import schedule_lesson_intelligence as schedule

        return schedule(
            int(project_id),
            reason=str(reason or "auto"),
            requested_by_id=requested_by_id,
            force=bool(force),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("schedule_lesson_intelligence task failed project=%s error=%s", project_id, exc.__class__.__name__)
        return {"status": "failed", "project_id": int(project_id), "error": exc.__class__.__name__}


@app.task(bind=True, name="worker.tasks.schedule_creator_analytics_intelligence", max_retries=0)
def schedule_creator_analytics_intelligence(
    self,
    user_id: int,
    reason: str = "auto",
    force: bool = False,
) -> dict[str, Any]:
    """Create or refresh the quick analytics report and queue slow enhancement."""
    if not _intelligence_feature_enabled():
        return {"status": "disabled", "user_id": int(user_id), "reason": "Intelligence disabled by environment."}
    try:
        from core.views import schedule_creator_analytics_intelligence as schedule

        return schedule(int(user_id), reason=str(reason or "auto"), force=bool(force))
    except Exception as exc:  # noqa: BLE001
        logger.warning("schedule_creator_analytics_intelligence task failed user=%s error=%s", user_id, exc.__class__.__name__)
        return {"status": "failed", "user_id": int(user_id), "error": exc.__class__.__name__}


@app.task(bind=True, name="worker.tasks.enhance_lesson_intelligence_report", max_retries=0)
def enhance_lesson_intelligence_report(self, report_id: int, source_hash: str) -> dict[str, Any]:
    """Run slow Ollama lesson analysis outside the API request path."""
    if not _intelligence_feature_enabled():
        return {"report_id": int(report_id), "status": "disabled", "reason": "Intelligence disabled by environment."}
    from core.intelligence_progressive import merge_enhancement_metadata
    from core.lesson_intelligence import (
        LessonIntelligenceInputError,
        adaptive_lesson_intelligence_timeout,
        analyze_lesson_ollama_background,
        apply_lesson_section_fallbacks,
        apply_analysis_to_report,
        build_lesson_intelligence_input,
        lesson_ollama_chunk_count,
        lesson_ollama_run_identity,
        lesson_sections_for_analysis,
    )
    from core.models import LessonIntelligenceReport

    report_id = int(report_id)
    expected_source_hash = str(source_hash or "").strip()
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    timeout_seconds = None
    report = _mark_lesson_intelligence_enhancement(report_id, "running", task_id=task_id)
    if report is None:
        return {"report_id": report_id, "status": "missing"}

    try:
        if report.provider == "ollama":
            metadata = report.metadata if isinstance(report.metadata, dict) else {}
            if str(metadata.get("progressive_enhancement", {}).get("status") or "") == "done":
                return {"report_id": report_id, "status": "already_done", "provider": report.provider}

        report = LessonIntelligenceReport.objects.select_related("project").get(pk=report_id)
        report_metadata = report.metadata if isinstance(report.metadata, dict) else {}
        output_language = str(report_metadata.get("output_language") or "auto")
        lesson_input = build_lesson_intelligence_input(report.project, output_language=output_language)
        if expected_source_hash and lesson_input.source_hash != expected_source_hash:
            _mark_lesson_intelligence_enhancement(report_id, "failed", task_id=task_id, error="source_hash_changed")
            return {"report_id": report_id, "status": "failed", "provider": report.provider, "error": "source_hash_changed"}
        run_identity = lesson_ollama_run_identity(lesson_input)
        stored_run_key = str(
            report_metadata.get("run_key")
            or (report_metadata.get("progressive_enhancement") if isinstance(report_metadata.get("progressive_enhancement"), dict) else {}).get("run_key")
            or ""
        )
        if stored_run_key and stored_run_key != run_identity.get("run_key"):
            _mark_lesson_intelligence_enhancement(
                report_id,
                "failed",
                task_id=task_id,
                error="run_key_changed",
                extra=run_identity,
            )
            return {"report_id": report_id, "status": "failed", "provider": report.provider, "error": "run_key_changed"}

        timeout_seconds = adaptive_lesson_intelligence_timeout(lesson_input.to_provider_payload())
        chunk_count = lesson_ollama_chunk_count(lesson_input)
        _mark_lesson_intelligence_enhancement(
            report_id,
            "running",
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            phase="analyzing_chunks",
            chunk_count=chunk_count,
            completed_chunks=0,
            failed_chunks=0,
            extra=run_identity,
        )

        def progress_callback(
            phase: str,
            chunk_count: int,
            completed_chunks: int,
            failed_chunks: int,
            current_chunk: dict[str, Any] | None = None,
        ) -> None:
            progress_extra = dict(run_identity)
            if isinstance(current_chunk, dict):
                progress_extra["current_chunk"] = current_chunk
                if current_chunk.get("index") is not None:
                    progress_extra["current_chunk_index"] = current_chunk.get("index")
                if current_chunk.get("section"):
                    progress_extra["current_chunk_name"] = current_chunk.get("section")
                if isinstance(current_chunk.get("chunk_diagnostics"), list):
                    progress_extra["chunk_diagnostics"] = current_chunk.get("chunk_diagnostics")
                if current_chunk.get("last_failure_reason"):
                    progress_extra["last_failure_reason"] = current_chunk.get("last_failure_reason")
            _mark_lesson_intelligence_enhancement(
                report_id,
                "running",
                task_id=task_id,
                timeout_seconds=timeout_seconds,
                phase=phase,
                chunk_count=chunk_count,
                completed_chunks=completed_chunks,
                failed_chunks=failed_chunks,
                extra=progress_extra,
            )

        analysis = analyze_lesson_ollama_background(
            lesson_input,
            chain=report.provider_chain,
            progress_callback=progress_callback,
        )
        timeout_seconds = (analysis.get("metadata") if isinstance(analysis.get("metadata"), dict) else {}).get("timeout_seconds") or timeout_seconds
        analysis_meta = analysis.get("metadata") if isinstance(analysis.get("metadata"), dict) else {}
        section_statuses = lesson_sections_for_analysis(
            analysis,
            existing_report=report,
            provider="ollama",
        )
        analysis = apply_lesson_section_fallbacks(report, analysis, section_statuses)
        has_failed_sections = any(
            str(item.get("status") or "").strip().lower() == "failed"
            for item in section_statuses.values()
            if isinstance(item, dict)
        )
        enhancement_extra = {
            **run_identity,
            "phase": "done",
            "chunked": bool(analysis_meta.get("chunked")),
            "chunk_count": int(analysis_meta.get("chunk_count") or 0),
            "completed_chunks": int(analysis_meta.get("completed_chunks") or 0),
            "failed_chunks": int(analysis_meta.get("failed_chunks") or 0),
            "partial_enhancement": bool(analysis_meta.get("partial_enhancement") or has_failed_sections),
            "sections": section_statuses,
        }
        if isinstance(analysis_meta.get("chunk_limitations"), list):
            enhancement_extra["chunk_limitations"] = analysis_meta.get("chunk_limitations")
        if isinstance(analysis_meta.get("chunk_diagnostics"), list):
            enhancement_extra["chunk_diagnostics"] = analysis_meta.get("chunk_diagnostics")
            if analysis_meta.get("chunk_diagnostics"):
                last_diagnostic = analysis_meta.get("chunk_diagnostics")[-1]
                if isinstance(last_diagnostic, dict):
                    enhancement_extra["last_failure_reason"] = last_diagnostic.get("safe_reason") or last_diagnostic.get("reason")
        analysis_metadata = {
            **dict(analysis.get("metadata") or {}),
            "progressive_enhancement": (report_metadata.get("progressive_enhancement") if isinstance(report_metadata, dict) else {}),
            "sections": section_statuses,
        }
        analysis["metadata"] = merge_enhancement_metadata(
            analysis_metadata,
            provider="ollama",
            status="partial" if enhancement_extra["partial_enhancement"] else "done",
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            extra=enhancement_extra,
        )
        report = apply_analysis_to_report(report, analysis, source_hash=lesson_input.source_hash)
        return {"report_id": report.id, "status": "partial" if enhancement_extra["partial_enhancement"] else "done", "provider": report.provider}
    except (LessonIntelligenceInputError, Exception) as exc:  # noqa: BLE001
        logger.warning("Lesson intelligence enhancement failed report=%s error=%s", report_id, exc.__class__.__name__)
        failure_extra: dict[str, Any] = {}
        if isinstance(getattr(exc, "chunk_diagnostics", None), list):
            failure_extra["chunk_diagnostics"] = getattr(exc, "chunk_diagnostics")[-20:]
        elif isinstance(getattr(exc, "diagnostic", None), dict):
            failure_extra["chunk_diagnostics"] = [getattr(exc, "diagnostic")]
        if getattr(exc, "last_failure_reason", None):
            failure_extra["last_failure_reason"] = str(getattr(exc, "last_failure_reason") or "")
        elif isinstance(failure_extra.get("chunk_diagnostics"), list) and failure_extra["chunk_diagnostics"]:
            last_diagnostic = failure_extra["chunk_diagnostics"][-1]
            if isinstance(last_diagnostic, dict):
                failure_extra["last_failure_reason"] = last_diagnostic.get("safe_reason") or last_diagnostic.get("reason")
        _mark_lesson_intelligence_enhancement(
            report_id,
            "failed",
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            error=f"{exc.__class__.__name__}: {exc}",
            phase="failed",
            extra=failure_extra,
        )
        return {"report_id": report_id, "status": "failed", "error": exc.__class__.__name__}


class _AnalyticsTaskRequest:
    def __init__(self, *, user, query_params: dict[str, Any]):
        self.user = user
        self.query_params = query_params


@app.task(bind=True, name="worker.tasks.enhance_analytics_intelligence_report", max_retries=0)
def enhance_analytics_intelligence_report(self, report_id: int, source_hash: str) -> dict[str, Any]:
    """Run slow Ollama creator analytics analysis outside the API request path."""
    if not _intelligence_feature_enabled():
        return {"report_id": int(report_id), "status": "disabled", "reason": "Intelligence disabled by environment."}
    from django.contrib.auth.models import User

    from core.analytics_intelligence import (
        AnalyticsIntelligenceInputError,
        adaptive_analytics_intelligence_timeout,
        analyze_analytics_ollama_background,
        analytics_ollama_chunk_count,
        analytics_ollama_run_identity,
        apply_analytics_analysis_to_report,
        build_analytics_intelligence_input,
    )
    from core.models import AnalyticsIntelligenceReport
    from core.views import CreatorAnalyticsView
    from core.intelligence_progressive import merge_enhancement_metadata

    report_id = int(report_id)
    expected_source_hash = str(source_hash or "").strip()
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    timeout_seconds = None
    report = _mark_analytics_intelligence_enhancement(report_id, "running", task_id=task_id)
    if report is None:
        return {"report_id": report_id, "status": "missing"}

    try:
        if report.provider == "ollama":
            metadata = report.metadata if isinstance(report.metadata, dict) else {}
            if str(metadata.get("progressive_enhancement", {}).get("status") or "") == "done":
                return {"report_id": report_id, "status": "already_done", "provider": report.provider}

        report = AnalyticsIntelligenceReport.objects.get(pk=report_id)
        user = User.objects.filter(pk=report.requested_by_id).first()
        if user is None:
            _mark_analytics_intelligence_enhancement(report_id, "failed", task_id=task_id, error="requesting_user_missing")
            return {"report_id": report_id, "status": "failed", "error": "requesting_user_missing"}

        report_metadata = report.metadata if isinstance(report.metadata, dict) else {}
        filters = report_metadata.get("analytics_filters") if isinstance(report_metadata.get("analytics_filters"), dict) else {}
        query_params = {
            "range": filters.get("range") or (report.date_range or {}).get("range") or 30,
            "from": filters.get("from") or (report.date_range or {}).get("from") or "",
            "to": filters.get("to") or (report.date_range or {}).get("to") or "",
            "category": filters.get("category") or report.category_filter or "",
            "sort": filters.get("sort") or "views",
        }
        analytics_payload = CreatorAnalyticsView().build_payload(
            _AnalyticsTaskRequest(user=user, query_params=query_params)
        )
        output_language = str(report_metadata.get("output_language") or "auto")
        analytics_input = build_analytics_intelligence_input(
            user,
            analytics_payload,
            scope=report.scope or "creator",
            output_language=output_language,
        )
        if expected_source_hash and analytics_input.source_hash != expected_source_hash:
            _mark_analytics_intelligence_enhancement(report_id, "failed", task_id=task_id, error="source_hash_changed")
            return {"report_id": report_id, "status": "failed", "provider": report.provider, "error": "source_hash_changed"}
        run_identity = analytics_ollama_run_identity(analytics_input)
        stored_run_key = str(
            report_metadata.get("run_key")
            or (report_metadata.get("progressive_enhancement") if isinstance(report_metadata.get("progressive_enhancement"), dict) else {}).get("run_key")
            or ""
        )
        if stored_run_key and stored_run_key != run_identity.get("run_key"):
            _mark_analytics_intelligence_enhancement(
                report_id,
                "failed",
                task_id=task_id,
                error="run_key_changed",
                extra=run_identity,
            )
            return {"report_id": report_id, "status": "failed", "provider": report.provider, "error": "run_key_changed"}

        timeout_seconds = adaptive_analytics_intelligence_timeout(analytics_input.to_provider_payload())
        chunk_count = analytics_ollama_chunk_count(analytics_input)
        _mark_analytics_intelligence_enhancement(
            report_id,
            "running",
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            phase="analyzing_chunks",
            chunk_count=chunk_count,
            completed_chunks=0,
            failed_chunks=0,
            extra=run_identity,
        )

        def progress_callback(
            phase: str,
            chunk_count: int,
            completed_chunks: int,
            failed_chunks: int,
            current_chunk: dict[str, Any] | None = None,
        ) -> None:
            progress_extra = dict(run_identity)
            if isinstance(current_chunk, dict):
                progress_extra["current_chunk"] = current_chunk
                if current_chunk.get("index") is not None:
                    progress_extra["current_chunk_index"] = current_chunk.get("index")
                if current_chunk.get("section"):
                    progress_extra["current_chunk_name"] = current_chunk.get("section")
                if isinstance(current_chunk.get("chunk_diagnostics"), list):
                    progress_extra["chunk_diagnostics"] = current_chunk.get("chunk_diagnostics")
                if current_chunk.get("last_failure_reason"):
                    progress_extra["last_failure_reason"] = current_chunk.get("last_failure_reason")
            _mark_analytics_intelligence_enhancement(
                report_id,
                "running",
                task_id=task_id,
                timeout_seconds=timeout_seconds,
                phase=phase,
                chunk_count=chunk_count,
                completed_chunks=completed_chunks,
                failed_chunks=failed_chunks,
                extra=progress_extra,
            )

        analysis = analyze_analytics_ollama_background(
            analytics_input,
            chain=report.provider_chain,
            progress_callback=progress_callback,
        )
        timeout_seconds = (analysis.get("metadata") if isinstance(analysis.get("metadata"), dict) else {}).get("timeout_seconds") or timeout_seconds
        analysis_meta = analysis.get("metadata") if isinstance(analysis.get("metadata"), dict) else {}
        enhancement_extra = {
            **run_identity,
            "phase": "done",
            "chunked": bool(analysis_meta.get("chunked")),
            "chunk_count": int(analysis_meta.get("chunk_count") or 0),
            "completed_chunks": int(analysis_meta.get("completed_chunks") or 0),
            "failed_chunks": int(analysis_meta.get("failed_chunks") or 0),
            "partial_enhancement": bool(analysis_meta.get("partial_enhancement")),
        }
        if isinstance(analysis_meta.get("chunk_limitations"), list):
            enhancement_extra["chunk_limitations"] = analysis_meta.get("chunk_limitations")
        if isinstance(analysis_meta.get("chunk_diagnostics"), list):
            enhancement_extra["chunk_diagnostics"] = analysis_meta.get("chunk_diagnostics")
            if analysis_meta.get("chunk_diagnostics"):
                last_diagnostic = analysis_meta.get("chunk_diagnostics")[-1]
                if isinstance(last_diagnostic, dict):
                    enhancement_extra["last_failure_reason"] = last_diagnostic.get("safe_reason") or last_diagnostic.get("reason")
        analysis_metadata = {
            **dict(analysis.get("metadata") or {}),
            "progressive_enhancement": (report_metadata.get("progressive_enhancement") if isinstance(report_metadata, dict) else {}),
        }
        analysis["metadata"] = merge_enhancement_metadata(
            analysis_metadata,
            provider="ollama",
            status="partial" if enhancement_extra["partial_enhancement"] else "done",
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            extra=enhancement_extra,
        )
        report = apply_analytics_analysis_to_report(report, analysis, source_hash=analytics_input.source_hash)
        return {"report_id": report.id, "status": "partial" if enhancement_extra["partial_enhancement"] else "done", "provider": report.provider}
    except (AnalyticsIntelligenceInputError, Exception) as exc:  # noqa: BLE001
        logger.warning("Analytics intelligence enhancement failed report=%s error=%s", report_id, exc.__class__.__name__)
        failure_extra: dict[str, Any] = {}
        if isinstance(getattr(exc, "chunk_diagnostics", None), list):
            failure_extra["chunk_diagnostics"] = getattr(exc, "chunk_diagnostics")[-20:]
        elif isinstance(getattr(exc, "diagnostic", None), dict):
            failure_extra["chunk_diagnostics"] = [getattr(exc, "diagnostic")]
        if getattr(exc, "last_failure_reason", None):
            failure_extra["last_failure_reason"] = str(getattr(exc, "last_failure_reason") or "")
        elif isinstance(failure_extra.get("chunk_diagnostics"), list) and failure_extra["chunk_diagnostics"]:
            last_diagnostic = failure_extra["chunk_diagnostics"][-1]
            if isinstance(last_diagnostic, dict):
                failure_extra["last_failure_reason"] = last_diagnostic.get("safe_reason") or last_diagnostic.get("reason")
        _mark_analytics_intelligence_enhancement(
            report_id,
            "failed",
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            error=f"{exc.__class__.__name__}: {exc}",
            phase="failed",
            extra=failure_extra,
        )
        return {"report_id": report_id, "status": "failed", "error": exc.__class__.__name__}


def _subtitle_task_active_key(project_id: int) -> str:
    return f"subtitle-generate-active:{int(project_id)}"


def _release_subtitle_task_active_slot(project_id: int) -> None:
    try:
        from django.core.cache import cache

        cache.decr(_subtitle_task_active_key(project_id))
    except Exception:
        try:
            from django.core.cache import cache

            cache.delete(_subtitle_task_active_key(project_id))
        except Exception:
            return


def _safe_subtitle_task_error(error: Exception | str, *, limit: int = 500) -> str:
    text = str(error or "").strip() or "subtitle translation failed"
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


@app.task(bind=True, name="worker.tasks.generate_translated_subtitle_track_task", max_retries=0)
def generate_translated_subtitle_track_task(
    self,
    project_id: int,
    language_code: str,
    language_label: str = "",
    provider: str = "auto",
    storage_root: str | None = None,
    allow_mock_fallback: bool | None = None,
    lock_key: str = "",
    release_public_active_slot: bool = False,
) -> dict[str, Any]:
    """
    Generate a translated subtitle sidecar outside the API request path.

    The API creates a pending/processing TranslatedSubtitleTrack and returns 202;
    this task performs provider I/O, writes SRT/VTT, and releases request locks.
    """
    from django.core.cache import cache
    from django.utils import timezone

    from core.models import TranslatedSubtitleTrack
    from core.subtitle_translation import SubtitleTranslationError, generate_translated_subtitle_track

    project_id = int(project_id)
    language_code = str(language_code or "").strip().lower().replace("_", "-")
    provider = str(provider or "auto").strip().lower() or "auto"
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")

    try:
        track = generate_translated_subtitle_track(
            project_id,
            language_code,
            provider=provider,
            language_label=language_label,
            storage_root=storage_root,
            allow_mock_fallback=allow_mock_fallback,
        )
        metadata = dict(track.metadata or {})
        if task_id:
            metadata["celery_task_id"] = task_id
        metadata["completed_at"] = timezone.now().isoformat()
        track.metadata = metadata
        track.save(update_fields=["metadata", "updated_at"])
        return {
            "project_id": project_id,
            "language_code": track.language_code,
            "status": track.status,
            "track_id": track.id,
            "provider": track.provider,
        }
    except SubtitleTranslationError as exc:
        track = exc.track or TranslatedSubtitleTrack.objects.filter(
            project_id=project_id,
            language_code=language_code,
        ).first()
        if track is not None:
            metadata = dict(track.metadata or {})
            if task_id:
                metadata["celery_task_id"] = task_id
            metadata["completed_at"] = timezone.now().isoformat()
            track.metadata = metadata
            track.save(update_fields=["metadata", "updated_at"])
            return {
                "project_id": project_id,
                "language_code": track.language_code,
                "status": track.status,
                "track_id": track.id,
                "error": _safe_subtitle_task_error(exc),
            }
        return {
            "project_id": project_id,
            "language_code": language_code,
            "status": "failed",
            "error": _safe_subtitle_task_error(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Translated subtitle task failed: project_id=%s language=%s", project_id, language_code)
        safe_error = _safe_subtitle_task_error(exc)
        track = TranslatedSubtitleTrack.objects.filter(project_id=project_id, language_code=language_code).first()
        if track is not None:
            metadata = dict(track.metadata or {})
            if task_id:
                metadata["celery_task_id"] = task_id
            metadata["completed_at"] = timezone.now().isoformat()
            track.status = "failed"
            track.error_message = safe_error
            track.srt_path = ""
            track.vtt_path = ""
            track.metadata = metadata
            track.save(update_fields=["status", "error_message", "srt_path", "vtt_path", "metadata", "updated_at"])
            track_id = track.id
        else:
            track_id = None
        return {
            "project_id": project_id,
            "language_code": language_code,
            "status": "failed",
            "track_id": track_id,
            "error": safe_error,
        }
    finally:
        if lock_key:
            cache.delete(lock_key)
        if release_public_active_slot:
            _release_subtitle_task_active_slot(project_id)


@app.task(name="worker.tasks.run_project_moderation", max_retries=0)
def run_project_moderation(
    project_id: int,
    triggered_by_user_id: int | None = None,
    phase: str = "source_scan",
) -> dict[str, Any]:
    """Run the text-only local moderation orchestrator for a project."""
    from .ai_agents.orchestrator import ModerationOrchestrator

    return ModerationOrchestrator().run(
        project_id=int(project_id),
        triggered_by_user_id=triggered_by_user_id,
        phase=phase,
    )


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
        from scripts.pptx_extract import (
            export_pptx_source_backgrounds,
            export_slide_images_with_metadata,
            extract_speaker_notes,
        )
        from scripts.text_segmentation import build_slide_page_structure
    except ImportError as exc:
        raise RuntimeError(
            f"pptx_extract not importable — check PYTHONPATH. Original error: {exc}"
        ) from exc

    logger.info("export_project START project=%s pptx=%s", project_id, pptx_path)
    self.update_state(state="PROGRESS", meta={"step": "export_start", "progress": 2})

    ws = _workspace(project_id)
    source_type = _source_type_from_value(Path(pptx_path).suffix) or "pptx"
    text_only_source = source_type == "txt"

    # Export slide images (PNG)
    export_metadata = export_slide_images_with_metadata(pptx_path, str(ws["images"]))
    image_paths = list(export_metadata.get("image_paths") or [])
    source_render_method = str(export_metadata.get("source_render_method") or "")
    source_render_warnings = list(export_metadata.get("source_render_warnings") or [])
    source_render_details = _details_list_from_value(export_metadata.get("source_render_details"))
    source_render_dependency_report = dict(export_metadata.get("source_render_dependency_report") or {})
    n_slides = len(image_paths)
    if n_slides == 0:
        raise ValueError(f"No slide images exported from {pptx_path!r}")
    logger.info("export_project: %d slide images exported", n_slides)
    self.update_state(state="PROGRESS", meta={"step": "images_done", "progress": 6})

    source_background_paths: list[str] = []
    source_background_warnings: list[str] = []
    source_background_slide_warnings: list[list[str]] = []
    source_background_details: list[dict[str, Any]] = []
    if source_type == "pptx":
        source_background_metadata = export_pptx_source_backgrounds(
            pptx_path,
            str(ws["source_backgrounds"]),
        )
        source_background_paths = list(source_background_metadata.get("source_background_paths") or [])
        source_background_warnings = _warning_list_from_value(
            source_background_metadata.get("source_background_warnings")
        )
        source_background_slide_warnings = [
            _warning_list_from_value(item)
            for item in list(source_background_metadata.get("source_background_slide_warnings") or [])
        ]
        source_background_details = _details_list_from_value(
            source_background_metadata.get("source_background_details")
        )

    # Extract speaker notes (one .txt per slide)
    note_paths = extract_speaker_notes(pptx_path, str(ws["notes"]))
    logger.info("export_project: %d note files extracted", len(note_paths))
    self.update_state(state="PROGRESS", meta={"step": "notes_done", "progress": 10})

    # Build render descriptors. Source-based visuals preserve the original
    # slide/page count; whiteboard/text-only sources retain long-text splitting.
    slides: list[dict[str, Any]] = []
    display_index = 0
    split_visual_pages = bool(whiteboard_mode_all or not _source_type_uses_visual_mapping(source_type))
    for idx in range(n_slides):
        slide_num = idx + 1

        notes_text = ""
        if idx < len(note_paths):
            txt_file = Path(note_paths[idx])
            if txt_file.exists():
                notes_text = txt_file.read_text(encoding="utf-8").strip()

        if notes_text and split_visual_pages:
            page_items = build_slide_page_structure(idx, notes_text)
        elif notes_text:
            page_items = [_single_source_page_structure(idx, notes_text)]
        else:
            page_items = [
                {
                    "source_slide_index": idx,
                    "split_index": 0,
                    "page_key": f"s{slide_num}-p1",
                    "original_text": "",
                    "narration_text": "",
                    "subtitle_chunks": [],
                }
            ]
        for page in page_items:
            display_index += 1
            page_text = str(page.get("original_text") or "")
            narration_text = str(page.get("narration_text") or notes_text or "")
            clean_background_path = source_background_paths[idx] if idx < len(source_background_paths) else ""
            clean_background_warnings = list(source_background_warnings)
            clean_source_render_details = list(source_render_details)
            clean_background_details = list(source_background_details)
            if idx < len(source_background_slide_warnings):
                clean_background_warnings = list(
                    dict.fromkeys([*clean_background_warnings, *source_background_slide_warnings[idx]])
                )
            slides.append({
                "index": display_index - 1,
                "slide_num": display_index,
                "source_slide_index": idx,
                "source_slide_num": slide_num,
                "split_index": int(page.get("split_index") or 0),
                "page_key": page.get("page_key") or f"s{slide_num}-p1",
                "image_path": image_paths[idx],
                "original_background_path": "" if text_only_source else image_paths[idx],
                "source_background_path": clean_background_path,
                "source_background_warnings": clean_background_warnings,
                "source_type": source_type,
                "source_render_method": source_render_method,
                "source_render_warnings": source_render_warnings,
                "source_render_details": clean_source_render_details,
                "source_render_dependency_report": source_render_dependency_report,
                "source_background_details": clean_background_details,
                "notes_text": narration_text,
                "original_text": page_text,
                "display_text": page_text,
                "narration_text": narration_text,
                "subtitle_chunks": page.get("subtitle_chunks") or ([narration_text] if narration_text else []),
                "whiteboard_mode": bool(whiteboard_mode_all or text_only_source),
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
    raw_original_text = slide_meta.get("original_text") or notes_text
    display_text = _split_consistent_display_text(slide_meta.get("display_text") or raw_original_text, narration_text)
    original_text = display_text
    subtitle_chunks = _subtitle_chunks_for_render(slide_meta.get("subtitle_chunks"), narration_text)
    whiteboard_mode = bool(slide_meta.get("whiteboard_mode"))
    editor_document = slide_meta.get("editor_document") if isinstance(slide_meta.get("editor_document"), dict) else {}
    editor_scene = editor_document.get("scene") if isinstance(editor_document.get("scene"), dict) else {}
    scene_background_mode = _scene_mode_from_value(
        slide_meta.get("scene_background_mode") or editor_scene.get("background_mode"),
        fallback="whiteboard" if whiteboard_mode else "original",
    )
    source_type = _source_type_from_value(slide_meta.get("source_type") or editor_scene.get("source_type"))
    source_background_warnings = _warning_list_from_value(
        slide_meta.get("source_background_warnings") or editor_scene.get("source_background_warnings")
    )
    source_background_details = _details_list_from_value(slide_meta.get("source_background_details"))
    has_custom_background = bool(str(
        slide_meta.get("custom_background_path") or editor_scene.get("custom_background_path") or ""
    ).strip())
    if scene_background_mode == "custom" and not has_custom_background:
        image_path = ""
    scene_background_mode, source_background_warnings, effective_whiteboard_mode = _normalize_scene_mode_for_render(
        scene_background_mode,
        source_type=source_type,
        render_image_path=image_path,
        warnings=source_background_warnings,
    )
    scene_background_fit = _scene_fit_from_value(
        slide_meta.get("scene_background_fit") or editor_scene.get("background_fit")
    )
    raw_scene_text_scale = slide_meta.get("scene_text_scale")
    if raw_scene_text_scale is None or raw_scene_text_scale == "":
        raw_scene_text_scale = editor_scene.get("text_scale")
    scene_text_scale = _scene_text_scale_from_value(
        raw_scene_text_scale,
        fallback=1.0,
    )
    rich_text_html = str(slide_meta.get("rich_text_html") or "")
    if isinstance(editor_document, dict) and not narration_text:
        para_lines = [str(p.get("text") or "") for p in editor_document.get("paragraphs", []) if isinstance(p, dict)]
        if para_lines:
            narration_text = "\n".join(para_lines)
    if isinstance(editor_document, dict) and not rich_text_html:
        rich_text_html = str(editor_document.get("html") or "")
    rich_text_html = _split_consistent_rich_text_html(display_text, rich_text_html)
    notes_text_prepared = str(narration_text or "").strip() or f"Slide {slide_num}."
    audio_out  = slide_meta["audio_out"]
    part_out   = slide_meta["part_out"]

    logger.info(
        "synthesize_and_render_slide START slide=%d project=%s", slide_num, project_id
    )
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
        avatar_required = bool(avatar_state.get("enabled"))

        if effective_whiteboard_mode:
            render_image_path = _make_whiteboard_image(
                display_text or original_text,
                str(Path(part_out).with_suffix(".whiteboard.png")),
                text_scale=scene_text_scale,
            )
        elif scene_background_mode in {"custom", "source_background"}:
            if scene_background_mode == "source_background":
                source_background_warnings = list(
                    dict.fromkeys(
                        [
                            *source_background_warnings,
                            *_source_background_overflow_warnings(
                                image_path,
                                display_text,
                                rich_text_html,
                                text_scale=scene_text_scale,
                                background_fit=scene_background_fit,
                            ),
                        ]
                    )
                )
            render_image_path = _render_transcript_overlay_image(
                image_path,
                display_text,
                rich_text_html,
                str(Path(part_out).with_suffix(".overlay.png")),
                text_scale=scene_text_scale,
                background_fit=scene_background_fit,
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
                            "restoration_enabled": bool(avatar_state.get("restoration_enabled")),
                            "liveportrait_enabled": bool(avatar_state.get("liveportrait_enabled", True)),
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

        combined_source_render_warnings = list(
            dict.fromkeys(
                [
                    *_warning_list_from_value(slide_meta.get("source_render_warnings")),
                    *source_background_warnings,
                ]
            )
        )
        combined_source_render_details = _details_list_from_value(
            [
                *_details_list_from_value(slide_meta.get("source_render_details")),
                *source_background_details,
            ]
        )

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
            "display_text": display_text,
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
            "whiteboard_mode": effective_whiteboard_mode,
            "scene_background_mode": scene_background_mode,
            "source_render_method": str(slide_meta.get("source_render_method") or ""),
            "source_render_warnings": combined_source_render_warnings,
            "source_render_details": combined_source_render_details,
            "source_render_dependency_report": dict(slide_meta.get("source_render_dependency_report") or {}),
            "source_background_warnings": source_background_warnings,
            "source_background_details": source_background_details,
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
    use_draft: bool = False,
    avatar_options: dict[str, Any] | None = None,
    job_id: int | str | None = None,
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

    celery_task_id = str(getattr(getattr(concat_and_finalize, "request", None), "id", "") or "")
    if not _is_current_render_job(project_id, job_id):
        logger.warning(
            "concat_and_finalize stale render skipped project_id=%s job_id=%s celery_task_id=%s",
            project_id,
            job_id,
            celery_task_id,
        )
        return {
            "status": "stale",
            "skipped": True,
            "project_id": int(project_id),
            "job_id": int(job_id) if job_id else None,
            "reason": "stale_render_job",
        }

    logger.info(
        "concat_and_finalize START project_id=%s job_id=%s celery_task_id=%s slides=%d",
        project_id,
        job_id,
        celery_task_id,
        len(results),
    )
    _update_render_job(project_id, job_id, progress=90)

    try:
        # Sort by index — Celery preserves group order since v4, but defensive
        ordered         = sorted(results, key=lambda r: r["index"])
        part_paths      = [r["part_path"] for r in ordered]
        slide_durations = [r["duration"]  for r in ordered]
        _sync_lesson_segments(project_id, ordered)

        ws = _workspace(project_id)
        output_dir = ws["final"]
        output_rel_prefix = str(project_id)
        if use_draft:
            draft_output_token = f"draft-{time.time_ns()}"
            output_dir = ws["final"] / "draft_renders" / draft_output_token
            output_dir.mkdir(parents=True, exist_ok=True)
            output_rel_prefix = f"{project_id}/draft_renders/{draft_output_token}"

        # Concatenate part MP4s → final video
        final_video = str(output_dir / f"{project_id}.mp4")
        logger.info("Concatenating %d clips → %s", len(part_paths), final_video)
        concat_videos(part_paths, final_video)
        _update_render_job(project_id, job_id, progress=95)

        # Persist the render timeline, then build canonical cue text from active
        # transcript rows so captions never consume provider-normalized TTS text.
        # Draft renders promote into active rows only after output files are prepared.
        srt_path = str(output_dir / f"{project_id}.srt")
        vtt_path = str(output_dir / f"{project_id}.vtt")
        result_url_rel = f"{output_rel_prefix}/{project_id}.mp4"
        srt_url_rel = f"{output_rel_prefix}/{project_id}.srt"
        vtt_url_rel = f"{output_rel_prefix}/{project_id}.vtt"
        page_timeline = _build_page_timeline_from_render_results(ordered)
        if use_draft:
            subtitle_cues = _fallback_cues_from_render_results(ordered, slide_durations)
        else:
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
                        "avatar_engine_selected": str(result.get("avatar_engine_selected") or result.get("avatar_engine_used") or "none"),
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

        generate_vtt_from_cues(subtitle_cues, vtt_path)
        logger.info("WebVTT written → %s", vtt_path)
        source_render_metadata = [
            {
                "index": int(item.get("index") or 0),
                "slide_num": int(item.get("slide_num") or 0),
                "page_key": str(item.get("page_key") or ""),
                "method": str(item.get("source_render_method") or ""),
                "warnings": list(item.get("source_render_warnings") or []),
                "details": _details_list_from_value(item.get("source_render_details")),
                "dependency_report": dict(item.get("source_render_dependency_report") or {}),
            }
            for item in ordered
            if item.get("source_render_method") or item.get("source_render_warnings") or item.get("source_render_details")
        ]
        source_render_warnings = list(
            dict.fromkeys(
                warning
                for item in source_render_metadata
                for warning in list(item.get("warnings") or [])
                if str(warning or "").strip()
            )
        )
        source_render_details = _details_list_from_value(
            [
                detail
                for item in source_render_metadata
                for detail in list(item.get("details") or [])
            ]
        )
        protection_mode = _worker_protection_mode()
        playback_assets: dict[str, Any] = {
            "asset_id": f"{DRM_ASSET_ID_PREFIX}{project_id}",
            "content_id": f"{DRM_CONTENT_ID_PREFIX}{project_id}",
            "mp4_rel_path": result_url_rel,
            "srt_rel_path": srt_url_rel,
            "vtt_rel_path": vtt_url_rel,
            "protection_mode": protection_mode,
            "timeline": page_timeline,
            "hls": _hls_sidecar_payload(enabled=False, packaging_status="not_required"),
            "avatar": None,
            "avatar_engine_selected": str((avatar_options or {}).get("avatar_engine_selected") or (avatar_options or {}).get("lipsync_engine") or ""),
            "source_render_metadata": source_render_metadata,
            "source_render_warnings": source_render_warnings,
            "source_render_details": source_render_details,
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
                    "avatar_engine_selected": str(item.get("avatar_engine_selected") or item.get("avatar_engine_used") or "none"),
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
                "avatar_engine_selected": str(item.get("avatar_engine_selected") or item.get("avatar_engine_used") or "none"),
                "source_render_method": str(item.get("source_render_method") or ""),
                "source_render_warnings": list(item.get("source_render_warnings") or []),
                "source_render_details": _details_list_from_value(item.get("source_render_details")),
                "source_render_dependency_report": dict(item.get("source_render_dependency_report") or {}),
                "pause_seconds": playback_assets["pause_durations"][idx],
                "part_rel_path": _safe_rel_path(STORAGE_ROOT, str(item.get("part_path"))),
                "duration": float(item.get("duration") or 0.0),
            }
            for idx, item in enumerate(ordered)
        ]

        if avatar_segments:
            try:
                avatar_track_dir = output_dir / "avatar"
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
                        "track_rel_path": f"{output_rel_prefix}/avatar/avatar_track.mp4",
                        "default_position": "top-right",
                        "default_size": "medium",
                        "segments": avatar_segments,
                    }
            except Exception:
                logger.warning("Avatar track concat failed for project=%s", project_id, exc_info=True)

        playback_assets["hls"] = _package_hls_assets_for_playback(
            project_id=project_id,
            final_video=final_video,
            output_dir=output_dir,
            output_rel_prefix=output_rel_prefix,
            protection_mode=protection_mode,
            package_hls_stream_func=package_hls_stream,
        )

        if use_draft:
            try:
                from core.models import Project
                from core.drafts import promote_project_draft

                project = Project.objects.get(pk=int(project_id))
                promote_project_draft(project, render_outputs={"page_timeline": page_timeline})
            except Exception:
                logger.exception("Draft promotion failed after render project=%s", project_id)
                _update_render_job(project_id, job_id, status="failed", progress=100, error_message="Draft promotion failed after render.")
                raise

        _write_playback_sidecar(project_id, playback_assets)
        background_avatar = {"status": "none", "queued": False}
        if avatar_options is not None:
            background_avatar = _queue_lesson_avatar_overlay_after_base_render(
                project_id=project_id,
                ordered_results=ordered,
                avatar_options=avatar_options,
                output_rel_prefix=output_rel_prefix,
                base_job_id=int(job_id) if job_id else None,
            )
        render_warning_message = ""
        if source_render_warnings:
            render_warning_message = "source_render_warnings:" + ";".join(source_render_warnings)
            logger.warning(
                "Lesson finalized with source render warnings project=%s warnings=%s",
                project_id,
                json.dumps(source_render_warnings, ensure_ascii=True),
            )
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
        hls_warning_message = ""
        hls_warnings = list((playback_assets.get("hls") or {}).get("warnings") or [])
        if hls_warnings:
            hls_warning_message = "hls:" + ";".join(hls_warnings)
            logger.warning(
                "Lesson finalized with HLS warnings project=%s warnings=%s",
                project_id,
                json.dumps(hls_warnings, ensure_ascii=True),
            )
        completion_warning_message = "; ".join(
            warning for warning in [render_warning_message, avatar_warning_message, hls_warning_message] if warning
        )

        # Mark the Job as done and record relative file paths
        _update_render_job(
            project_id,
            job_id,
            status="done",
            progress=100,
            result_url=result_url_rel,
            srt_url=srt_url_rel,
            error_message=completion_warning_message,
        )
        _mark_project_ready_after_successful_render(project_id)
        _notify_render_completed(project_id)
        _schedule_lesson_intelligence_after_worker_event(project_id, reason="render_completed")
        _schedule_creator_analytics_after_worker_event(project_id, reason="render_completed")
        try:
            video_frame_audit = _run_auto_video_frame_audit_after_render(
                project_id,
                _latest_video_export_job_id(project_id),
                final_video,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Auto video frame audit raised after successful render project=%s", project_id, exc_info=True)
            video_frame_audit = {"enabled": True, "status": "failed", "block_render": False}
        if video_frame_audit.get("enabled"):
            logger.info(
                "Auto video frame audit project=%s status=%s final_decision=%s run_id=%s findings=%s frames=%s",
                project_id,
                video_frame_audit.get("status"),
                video_frame_audit.get("final_decision"),
                video_frame_audit.get("run_id"),
                video_frame_audit.get("finding_count"),
                video_frame_audit.get("sampled_frame_count"),
            )

        result = {
            "job_id":      int(job_id) if job_id else None,
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
            "background_avatar": background_avatar,
            "source_render_warnings": source_render_warnings,
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
        logger.exception("concat_and_finalize FAILED project_id=%s job_id=%s celery_task_id=%s", project_id, job_id, celery_task_id)
        _update_render_job(project_id, job_id, status="failed", error_message=error_trace)
        _notify_render_failed(project_id)
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
    avatar_options: dict[str, Any] | None = None,
    job_id: int | str | None = None,
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
                "display_text": str(slide.get("display_text") or slide.get("original_text") or slide.get("notes_text") or ""),
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
    return concat_and_finalize.apply(args=[full_results, project_id, False, avatar_options, job_id]).result


# ---------------------------------------------------------------------------
# Entry-point — kept for backward compatibility
# ---------------------------------------------------------------------------

@app.task(name="worker.tasks.mark_project_render_failed", max_retries=0)
def mark_project_render_failed(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Errback for dispatched render chords so failed headers do not leave jobs running."""
    project_id = kwargs.get("project_id")
    job_id = kwargs.get("job_id")
    error_args = args
    if project_id is None and len(args) >= 2 and str(args[-1]).isdigit() and str(args[-2]).isdigit():
        project_id = args[-2]
        job_id = args[-1]
        error_args = args[:-2]
    elif project_id is None and args:
        project_id = args[-1]
        error_args = args[:-1]
    error_parts = [
        _concise_error_text(arg, fallback="", limit=240)
        for arg in error_args
        if _concise_error_text(arg, fallback="", limit=240)
    ]
    error_message = "render_pipeline_failed"
    if error_parts:
        error_message = f"{error_message}: {'; '.join(error_parts[:3])}"
    if project_id is not None:
        logger.error("Render pipeline failed project_id=%s job_id=%s errback_args=%s", project_id, job_id, error_args)
        _update_render_job(project_id, job_id, status="failed", progress=100, error_message=error_message)
        _notify_render_failed(project_id)
    return {"status": "failed", "project_id": project_id, "job_id": job_id, "error_message": error_message}


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
    use_draft: bool = False,
    job_id: int | str | None = None,
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
    celery_task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    render_job_id = int(job_id) if job_id else _resolve_render_job_id(project_id, celery_task_id)
    logger.info(
        "=== process_pptx_to_video START project_id=%s job_id=%s celery_task_id=%s ===",
        project_id,
        render_job_id,
        celery_task_id,
    )
    _update_render_job(project_id, render_job_id, status="running", progress=0)
    self.update_state(state="PROGRESS", meta={"step": "start", "progress": 0})

    try:
        # ------------------------------------------------------------------
        # Step 1: Export slides inline
        # ------------------------------------------------------------------
        logger.info("Step 1: exporting slides for project=%s …", project_id)
        export_result = export_project.apply(args=[project_id, pptx_path, whiteboard_mode_all])
        if export_result.failed():
            raise RuntimeError(f"export_project raised: {export_result.result}")

        slides: list[dict[str, Any]] = export_result.result
        if use_draft:
            slides = _build_render_slides_from_draft(project_id, slides)
            tts_settings = _draft_render_tts_settings(project_id, tts_settings)
        else:
            slides = _sync_transcript_pages_from_export(project_id, slides)
            if _intelligence_queue_name() != _render_queue_name():
                _schedule_lesson_intelligence_after_worker_event(project_id, reason="transcript_extracted")
        n_slides = len(slides)
        logger.info("Step 1 done: %d slides ready for parallel rendering", n_slides)

        source_moderation = (
            _run_auto_source_moderation_for_draft(project_id)
            if use_draft
            else _run_auto_source_moderation_after_transcript_sync(project_id)
        )
        if source_moderation.get("enabled"):
            logger.info(
                "Auto source moderation project=%s status=%s moderation_status=%s block_render=%s run_id=%s",
                project_id,
                source_moderation.get("status"),
                source_moderation.get("moderation_status"),
                source_moderation.get("block_render"),
                source_moderation.get("run_id"),
            )
        if source_moderation.get("block_render"):
            if use_draft:
                _mark_draft_render_blocked(project_id, source_moderation)
            else:
                _mark_project_source_moderation_blocked(project_id, source_moderation)
            self.update_state(
                state="PROGRESS",
                meta={
                    "step": "source_moderation_blocked",
                    "progress": 100,
                    "project_id": project_id,
                    "moderation_status": source_moderation.get("moderation_status"),
                },
            )
            return {
                "status": "moderation_blocked",
                "project_id": project_id,
                "n_slides": n_slides,
                "moderation": source_moderation,
            }

        visual_moderation = _run_auto_visual_asset_moderation_after_export(project_id, slides, use_draft=use_draft)
        if visual_moderation.get("enabled"):
            logger.info(
                "Auto visual moderation project=%s status=%s final_decision=%s block_render=%s run_id=%s findings=%s",
                project_id,
                visual_moderation.get("status"),
                visual_moderation.get("final_decision"),
                visual_moderation.get("block_render"),
                visual_moderation.get("run_id"),
                visual_moderation.get("finding_count"),
            )
        if visual_moderation.get("block_render"):
            if use_draft:
                _mark_draft_render_blocked(project_id, visual_moderation)
            else:
                _mark_project_visual_moderation_blocked(project_id, visual_moderation)
            self.update_state(
                state="PROGRESS",
                meta={
                    "step": "visual_moderation_blocked",
                    "progress": 100,
                    "project_id": project_id,
                    "visual_decision": visual_moderation.get("final_decision"),
                },
            )
            return {
                "status": "visual_moderation_blocked",
                "project_id": project_id,
                "n_slides": n_slides,
                "moderation": visual_moderation,
            }

        ocr_moderation = _run_auto_ocr_slide_moderation_after_export(project_id, slides)
        if ocr_moderation.get("enabled"):
            logger.info(
                "Auto OCR moderation project=%s status=%s final_decision=%s block_render=%s run_id=%s findings=%s",
                project_id,
                ocr_moderation.get("status"),
                ocr_moderation.get("final_decision"),
                ocr_moderation.get("block_render"),
                ocr_moderation.get("run_id"),
                ocr_moderation.get("finding_count"),
            )
        if ocr_moderation.get("block_render"):
            if use_draft:
                _mark_draft_render_blocked(project_id, ocr_moderation)
            else:
                _mark_project_ocr_moderation_blocked(project_id, ocr_moderation)
            self.update_state(
                state="PROGRESS",
                meta={
                    "step": "ocr_moderation_blocked",
                    "progress": 100,
                    "project_id": project_id,
                    "ocr_decision": ocr_moderation.get("final_decision"),
                },
            )
            return {
                "status": "ocr_moderation_blocked",
                "project_id": project_id,
                "n_slides": n_slides,
                "moderation": ocr_moderation,
            }

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

        _update_render_job(project_id, render_job_id, progress=10)
        tts_settings_summary = _summarize_tts_settings(tts_settings)

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
                "requested": bool(teacher_avatar_cfg.get("enabled")),
                "teacher_id": teacher_id,
                "source_image_rel_path": teacher_avatar_cfg.get("processed_rel_path", ""),
                "source_image_original_rel_path": teacher_avatar_cfg.get("source_rel_path", ""),
                "source_video_rel_path": teacher_avatar_cfg.get("video_rel_path", ""),
                "avatar_reference_type": teacher_avatar_cfg.get("reference_type", "image"),
                "motion_preset": teacher_avatar_cfg.get("motion_preset", "natural"),
                "quality_preset": teacher_avatar_cfg.get("quality_preset", "high"),
                "lipsync_engine": teacher_avatar_cfg.get("lipsync_engine", "musetalk"),
                "restoration_enabled": bool(teacher_avatar_cfg.get("restoration_enabled")),
                "liveportrait_enabled": bool(teacher_avatar_cfg.get("liveportrait_enabled", True)),
                "avatar_runtime_settings": dict(teacher_avatar_cfg.get("avatar_runtime_settings") or {}),
                "model_version": teacher_avatar_cfg.get("model_version", "musetalk:v1"),
                "avatar_source_valid": bool(teacher_avatar_cfg.get("avatar_source_valid")),
                "avatar_source_validation_error": str(teacher_avatar_cfg.get("avatar_source_validation_error") or ""),
                "avatar_source_hash": str(teacher_avatar_cfg.get("avatar_source_hash") or ""),
                "avatar_preview_stale": bool(teacher_avatar_cfg.get("avatar_preview_stale")),
                "avatar_preview_source_hash": str(teacher_avatar_cfg.get("avatar_preview_source_hash") or ""),
                "avatar_moderation_status": str(teacher_avatar_cfg.get("avatar_moderation_status") or "not_scanned"),
                "avatar_moderation_blocked": bool(teacher_avatar_cfg.get("avatar_moderation_blocked")),
                "avatar_moderation_error_code": str(teacher_avatar_cfg.get("avatar_moderation_error_code") or ""),
                "composite_fallback_allowed": False,
            }
        else:
            teacher_id = int(avatar_cfg.get("teacher_id")) if avatar_cfg.get("teacher_id") else None
            avatar_cfg["requested"] = bool(avatar_cfg.get("requested", avatar_cfg.get("enabled", False)))
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
        rerender_set = set() if use_draft else {str(key) for key in (rerender_page_keys or []) if str(key)}
        target_slides = [slide for slide in slides if not rerender_set or str(slide.get("page_key") or "") in rerender_set]
        if not target_slides:
            target_slides = slides

        pipeline_queue = _render_queue_name()
        base_avatar_cfg = dict(avatar_cfg)
        base_avatar_cfg["enabled"] = False

        def _slide_render_signature(slide: dict[str, Any]):
            errback = mark_project_render_failed.s(project_id, render_job_id).set(queue=pipeline_queue)
            return synthesize_and_render_slide.s(
                slide, project_id, voice_id, pause_sec, resolved_lang, tts_mode, base_avatar_cfg, tts_settings
            ).set(queue=pipeline_queue, link_error=errback)

        slide_tasks = group(
            _slide_render_signature(slide)
            for slide in target_slides
        )
        if rerender_set:
            callback = merge_and_finalize_segments.s(project_id, slides, list(rerender_set), avatar_cfg, render_job_id).set(queue=pipeline_queue)
        else:
            callback = concat_and_finalize.s(project_id, bool(use_draft), avatar_cfg, render_job_id).set(queue=pipeline_queue)

        pipeline     = chord(slide_tasks, callback)
        async_result = pipeline.apply_async(queue=pipeline_queue)

        logger.info(
            "Chord dispatched: chord_id=%s n_slides=%d project=%s queue=%s",
            async_result.id, n_slides, project_id, pipeline_queue,
        )

        return {
            "status":     "dispatched",
            "chord_id":   async_result.id,
            "n_slides":   len(target_slides),
            "project_id": project_id,
            "job_id": render_job_id,
            "language_detection": language_detection,
            "rerender_page_keys": list(rerender_set),
            "use_draft": bool(use_draft),
            "tts_settings": tts_settings_summary,
            "avatar": {
                "enabled": bool(avatar_cfg.get("enabled")),
                "requested": bool(avatar_cfg.get("requested", avatar_cfg.get("enabled", False))),
                "processing_status": "queued" if bool(avatar_cfg.get("enabled")) else "none",
                "message": (
                    "Avatar is still processing and will be added when ready."
                    if bool(avatar_cfg.get("enabled"))
                    else str(avatar_cfg.get("disabled_reason") or "")
                ),
                "teacher_id": teacher_id,
                "source_image_rel_path": avatar_cfg.get("source_image_rel_path", ""),
                "source_video_rel_path": avatar_cfg.get("source_video_rel_path", ""),
                "avatar_reference_type": avatar_cfg.get("avatar_reference_type", "image"),
                "avatar_source_valid": bool(avatar_cfg.get("avatar_source_valid")),
                "avatar_source_validation_error": str(avatar_cfg.get("avatar_source_validation_error") or ""),
                "avatar_preview_stale": bool(avatar_cfg.get("avatar_preview_stale")),
                "avatar_moderation_status": str(avatar_cfg.get("avatar_moderation_status") or "not_scanned"),
                "avatar_moderation_blocked": bool(avatar_cfg.get("avatar_moderation_blocked")),
                "composite_fallback_allowed": bool(avatar_cfg.get("composite_fallback_allowed")),
            },
        }

    except Exception as exc:
        error_trace = tb.format_exc()
        logger.exception("process_pptx_to_video FAILED project_id=%s job_id=%s celery_task_id=%s", project_id, render_job_id, celery_task_id)
        _update_render_job(project_id, render_job_id, status="failed", error_message=error_trace)
        _notify_render_failed(project_id)
        self.update_state(
            state="FAILURE",
            meta={
                "exc_type":    type(exc).__name__,
                "exc_message": str(exc),
                "project_id":  project_id,
            },
        )
        raise
