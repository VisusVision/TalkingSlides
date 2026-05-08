"""
API views for AI_ACADEMY.

Auth:
  POST /api/v1/auth/login/   LoginView
  POST /api/v1/auth/logout/  LogoutView
  GET  /api/v1/auth/me/      MeView

Secure media streaming:
  GET  /api/v1/stream/<token>/                  MediaStreamView  (public, token-gated)
  GET  /api/v1/projects/<id>/playback-token/    PlaybackTokenView (published lessons only)
  GET  /api/v1/media/<path>                     MediaServeView  (staff/admin only)

Student catalog (public):
  GET  /api/v1/catalog/                         CatalogListView
  GET  /api/v1/catalog/<id>/                    CatalogDetailView
  GET  /api/v1/categories/                      CategoryListView

Student social (auth required):
  POST /api/v1/catalog/<id>/like/               LessonLikeView
  POST /api/v1/catalog/<id>/progress/           LessonProgressView
  GET  /api/v1/catalog/<id>/comments/           LessonCommentsView (public read)
  POST /api/v1/catalog/<id>/comments/           LessonCommentsView (auth write)

Teacher pipeline:
  POST/GET  /api/v1/projects/                   ProjectUploadView
  GET/DEL   /api/v1/projects/<id>/              ProjectDetailView
  POST      /api/v1/projects/<id>/rerender/     ProjectRerenderView
  GET       /api/v1/projects/<id>/jobs/<jid>/   JobStatusView
  POST      /api/v1/users/<uid>/voice/          VoiceUploadView
"""

import base64
import ast
from datetime import datetime, timedelta
import html
import hashlib
import hmac as _hmac
import json
import logging
import mimetypes
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
from pathlib import Path
from typing import Any

from celery import Celery
from redis import Redis
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.core.cache import cache
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import UnsupportedMediaType, ValidationError
from rest_framework.authtoken.models import Token
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
try:
    from prometheus_client import CollectorRegistry, Gauge, Counter, generate_latest, CONTENT_TYPE_LATEST
    _PROMETHEUS_AVAILABLE = True
except Exception:
    CollectorRegistry = None
    Gauge = None
    Counter = None
    generate_latest = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    _PROMETHEUS_AVAILABLE = False

from core.models import (
    AvatarRenderJob,
    AvatarOverlayPreference,
    Category,
    Job,
    JobActionAudit,
    JobCheckpoint,
    LessonComment,
    LessonLike,
    LessonProgress,
    Project,
    Slide,
    TranscriptPage,
    UserProfile,
    VoiceProfile,
)
from core.serializers import (
    AvatarOverlayPreferenceSerializer,
    AvatarRenderJobSerializer,
    CatalogProjectSerializer,
    CategorySerializer,
    JobSerializer,
    LessonCommentSerializer,
    ProjectCreateSerializer,  # noqa: F401
    ProjectSerializer,
    SlideSerializer,
    TranscriptPageSerializer,
    UserSerializer,
    canonical_project_tts_settings,
    merge_project_tts_settings_patch,
)
from core.tts_llm_suggestions import pronunciation_suggestion_response
from core.avatar_readiness import avatar_preview_readiness, normalize_avatar_engine
from core.avatar_source_validation import (
    refresh_avatar_source_validation,
    stored_avatar_source_state,
)
from core.trace import outbound_trace_headers
from core.internal_http import open_bytes, open_json

try:
    from avatar.pipeline import AvatarValidationError, preprocess_teacher_avatar_image
    from avatar.preprocess import preprocess_avatar_video
except Exception:
    class AvatarValidationError(ValueError):
        pass

    def preprocess_teacher_avatar_image(*args, **kwargs):
        raise RuntimeError(
            "Avatar module is unavailable in this API container. "
            "Mount services/avatar or rebuild compose volume mappings."
        )

    def preprocess_avatar_video(*args, **kwargs):
        raise RuntimeError(
            "Avatar module is unavailable in this API container. "
            "Mount services/avatar or rebuild compose volume mappings."
        )

# ---------------------------------------------------------------------------
# Celery send-only client
# ---------------------------------------------------------------------------
_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
_celery_app = Celery(broker=_BROKER_URL)

_PROCESS_PROJECT_RENDER_TASK = "worker.tasks.process_pptx_to_video"
_AVATAR_PREVIEW_TASK = "worker.tasks.render_avatar_preview"
_CANCELLED_CLEANUP_TASK = "worker.tasks.cleanup_cancelled_project_artifacts"
JOB_CANCELLED_MARKER = "__cancelled_by_user__"
SSE_TICKET_PREFIX = "sse:job_events:ticket"
SSE_TICKET_TTL_SECONDS = 120
JOB_CANCEL_RATE_LIMIT_PER_MINUTE = 12


def _celery_queue_setting(setting_name: str, env_name: str, default: str) -> str:
    value = str(getattr(settings, setting_name, os.environ.get(env_name, default)) or default).strip()
    return value or default


def _render_queue_name() -> str:
    return _celery_queue_setting("CELERY_RENDER_QUEUE", "CELERY_RENDER_QUEUE", "render")


def _render_fast_queue_name() -> str:
    return _celery_queue_setting("CELERY_RENDER_FAST_QUEUE", "CELERY_RENDER_FAST_QUEUE", "render_fast")


def _render_quality_queue_name() -> str:
    return _celery_queue_setting("CELERY_RENDER_QUALITY_QUEUE", "CELERY_RENDER_QUALITY_QUEUE", "render_quality")


def _avatar_queue_name() -> str:
    return _celery_queue_setting("CELERY_AVATAR_QUEUE", "CELERY_AVATAR_QUEUE", "avatar")


def _queue_for_render_profile(render_profile: str | None) -> str:
    profile = str(render_profile or "balanced").strip().lower()
    if profile == "fast":
        return _render_fast_queue_name()
    if profile == "quality":
        return _render_quality_queue_name()
    return _render_queue_name()


def _queue_for_pipeline(avatar_options: dict | None, render_profile: str | None) -> str:
    return _avatar_queue_name() if bool((avatar_options or {}).get("enabled")) else _queue_for_render_profile(render_profile)


def _dispatch_celery_task(task_name: str, *, args: list | None = None, kwargs: dict | None = None, queue: str | None = None):
    """Dispatch through apply_async so callers can choose the target queue."""
    task_args = args or []
    task_kwargs = kwargs or {}
    apply_options = {"queue": queue} if queue else {}
    signature_factory = getattr(_celery_app, "signature", None)
    if callable(signature_factory):
        signature = signature_factory(task_name, args=task_args, kwargs=task_kwargs)
        return signature.apply_async(**apply_options)
    try:
        return _celery_app.send_task(task_name, args=task_args, kwargs=task_kwargs, **apply_options)
    except TypeError:
        # Older tests and thin fakes may not accept Celery's queue kwarg.
        return _celery_app.send_task(task_name, args=task_args, kwargs=task_kwargs)


def _issue_job_events_ticket(*, user_id: int, project_id: int, job_id: int) -> tuple[str, int]:
    ticket = uuid.uuid4().hex
    cache.set(
        f"{SSE_TICKET_PREFIX}:{ticket}",
        {
            "user_id": int(user_id),
            "project_id": int(project_id),
            "job_id": int(job_id),
        },
        timeout=SSE_TICKET_TTL_SECONDS,
    )
    return ticket, SSE_TICKET_TTL_SECONDS


def _resolve_job_events_ticket(ticket: str) -> dict[str, Any] | None:
    value = cache.get(f"{SSE_TICKET_PREFIX}:{ticket}")
    return value if isinstance(value, dict) else None


def _job_cancel_rate_limit_key(user_id: int) -> str:
    bucket = int(time.time() // 60)
    return f"rate:job_cancel:{int(user_id)}:{bucket}"


def _is_job_cancel_rate_limited(user_id: int) -> tuple[bool, int]:
    key = _job_cancel_rate_limit_key(user_id)
    try:
        count = int(cache.get(key) or 0) + 1
        cache.set(key, count, timeout=75)
    except Exception:
        return False, 0
    return count > JOB_CANCEL_RATE_LIMIT_PER_MINUTE, count


def _audit_job_action(
    *,
    job: Job,
    actor,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        JobActionAudit.objects.create(
            job=job,
            project=job.project,
            actor=actor if getattr(actor, "id", None) else None,
            action=str(action),
            metadata=dict(metadata or {}),
        )
    except Exception:
        logger.warning("Failed to write job action audit job_id=%s action=%s", getattr(job, "id", None), action, exc_info=True)


def _task_matches_project(task_payload: dict[str, Any], project_id: int) -> bool:
    """Best-effort matcher for Celery inspect active payloads."""
    target = str(int(project_id))
    args_value = task_payload.get("args", ())
    kwargs_value = task_payload.get("kwargs", {})

    try:
        if isinstance(args_value, str):
            parsed_args = ast.literal_eval(args_value)
        else:
            parsed_args = args_value
    except Exception:
        parsed_args = args_value

    try:
        if isinstance(kwargs_value, str):
            parsed_kwargs = ast.literal_eval(kwargs_value)
        else:
            parsed_kwargs = kwargs_value
    except Exception:
        parsed_kwargs = kwargs_value

    if isinstance(parsed_args, (list, tuple)):
        if any(str(item) == target for item in parsed_args):
            return True
    if isinstance(parsed_kwargs, dict):
        for key in ("project_id", "lesson_id"):
            if key in parsed_kwargs and str(parsed_kwargs.get(key)) == target:
                return True
    return False


def _revoke_project_active_tasks(*, project_id: int, include_task_ids: set[str] | None = None) -> list[str]:
    """Revoke + terminate active tasks belonging to the project across workers."""
    revoked: set[str] = set()
    if include_task_ids:
        revoked.update({str(task_id).strip() for task_id in include_task_ids if str(task_id).strip()})

    try:
        inspector = _celery_app.control.inspect(timeout=1.0)
        active_map = inspector.active() or {}
    except Exception:
        active_map = {}

    for worker_tasks in active_map.values():
        for task_payload in worker_tasks or []:
            if not isinstance(task_payload, dict):
                continue
            task_id = str(task_payload.get("id") or "").strip()
            if not task_id:
                continue
            if _task_matches_project(task_payload, int(project_id)):
                revoked.add(task_id)

    for task_id in sorted(revoked):
        try:
            _celery_app.control.revoke(task_id, terminate=True, signal="SIGKILL")
        except Exception:
            logger.warning("Failed to revoke active task task_id=%s project_id=%s", task_id, project_id, exc_info=True)

    return sorted(revoked)


def _redis_client():
    redis_url = str(getattr(settings, "CELERY_BROKER_URL", _BROKER_URL) or _BROKER_URL)
    return Redis.from_url(redis_url)


def _queue_depth(queue_name: str) -> int:
    try:
        return int(_redis_client().llen(queue_name))
    except Exception:
        logger.warning("Queue depth lookup failed queue=%s", queue_name, exc_info=True)
        return 0


def _queue_profile_eta_seconds(render_profile: str, *, avatar_enabled: bool) -> int:
    if avatar_enabled:
        return int(getattr(settings, "RENDER_ETA_SECONDS_AVATAR", 360))
    profile = str(render_profile or "balanced").strip().lower()
    if profile == "fast":
        return int(getattr(settings, "RENDER_ETA_SECONDS_FAST", 45))
    if profile == "quality":
        return int(getattr(settings, "RENDER_ETA_SECONDS_QUALITY", 240))
    return int(getattr(settings, "RENDER_ETA_SECONDS_BALANCED", 120))


def _estimate_queue_wait_seconds(queue_name: str, render_profile: str, *, avatar_enabled: bool) -> int:
    depth = max(0, _queue_depth(queue_name))
    service_seconds = max(1, _queue_profile_eta_seconds(render_profile, avatar_enabled=avatar_enabled))
    return int(depth * service_seconds)


def _admission_guard_for_render_profile(render_profile: str, queue_name: str) -> tuple[bool, dict[str, Any] | None]:
    profile = str(render_profile or "balanced").strip().lower()
    limit_map = {
        "fast": int(getattr(settings, "RENDER_ADMISSION_FAST_QUEUE_LIMIT", 250)),
        "balanced": int(getattr(settings, "RENDER_ADMISSION_BALANCED_QUEUE_LIMIT", 120)),
        "quality": int(getattr(settings, "RENDER_ADMISSION_QUALITY_QUEUE_LIMIT", 25)),
        "avatar": int(getattr(settings, "RENDER_ADMISSION_AVATAR_QUEUE_LIMIT", 20)),
    }
    limit = int(limit_map.get(profile, limit_map["balanced"]))
    depth = _queue_depth(queue_name)
    if depth <= limit:
        return True, None
    retry_after_seconds = max(30, _queue_profile_eta_seconds(profile, avatar_enabled=(profile == "avatar")))
    if profile == "quality":
        message = "System busy for quality renders right now. Choose fast/balanced or retry shortly."
        suggested_profiles = ["fast", "balanced"]
    elif profile == "avatar":
        message = "Avatar render queue is busy right now. Retry shortly or render without avatar."
        suggested_profiles = ["fast", "balanced"]
    elif profile == "fast":
        message = "Fast render queue is temporarily saturated. Retry shortly."
        suggested_profiles = ["balanced"]
    else:
        message = "Render queue is busy right now. Retry shortly or choose fast profile."
        suggested_profiles = ["fast"]
    return False, {
        "error": message,
        "profile": profile,
        "queue": queue_name,
        "queue_depth": depth,
        "queue_limit": limit,
        "retry_after_seconds": retry_after_seconds,
        "suggested_profiles": suggested_profiles,
    }


def _job_status_counts() -> dict[str, int]:
    rows = Job.objects.values("status").annotate(count=Count("id"))
    result: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "failed": 0}
    for row in rows:
        status_name = str(row.get("status") or "")
        result[status_name] = int(row.get("count") or 0)
    return result


def _oldest_active_job_age_seconds() -> float:
    oldest = Job.objects.filter(status__in=["pending", "running"]).order_by("created_at").first()
    if oldest is None:
        return 0.0
    age = timezone.now() - oldest.created_at
    return max(0.0, age.total_seconds())


def _recent_done_job_latencies_seconds(limit: int = 200) -> list[float]:
    rows = Job.objects.filter(status="done").order_by("-updated_at")[:limit]
    values: list[float] = []
    for row in rows:
        delta = row.updated_at - row.created_at
        values.append(max(0.0, delta.total_seconds()))
    return values


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(round((len(sorted_vals) - 1) * q))
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return float(sorted_vals[idx])


def _render_capacity_snapshot() -> dict[str, Any]:
    queue_map = {
        "fast": _render_fast_queue_name(),
        "balanced": _render_queue_name(),
        "quality": _render_quality_queue_name(),
        "avatar": _avatar_queue_name(),
    }
    snapshot: dict[str, Any] = {"queues": {}}
    for profile_name, queue_name in queue_map.items():
        avatar_enabled = profile_name == "avatar"
        eta_per_job = _queue_profile_eta_seconds(profile_name, avatar_enabled=avatar_enabled)
        depth = _queue_depth(queue_name)
        snapshot["queues"][profile_name] = {
            "queue": queue_name,
            "depth": depth,
            "eta_per_job_seconds": eta_per_job,
            "estimated_wait_seconds": int(depth * eta_per_job),
        }
    quality_depth = int(snapshot["queues"]["quality"]["depth"])
    quality_limit = int(getattr(settings, "RENDER_ADMISSION_QUALITY_QUEUE_LIMIT", 25))
    fast_depth = int(snapshot["queues"]["fast"]["depth"])
    balanced_depth = int(snapshot["queues"]["balanced"]["depth"])
    avatar_depth = int(snapshot["queues"]["avatar"]["depth"])
    fast_limit = int(getattr(settings, "RENDER_ADMISSION_FAST_QUEUE_LIMIT", 80))
    balanced_limit = int(getattr(settings, "RENDER_ADMISSION_BALANCED_QUEUE_LIMIT", 120))
    avatar_limit = int(getattr(settings, "RENDER_ADMISSION_AVATAR_QUEUE_LIMIT", 20))
    snapshot["admission"] = {
        "quality_limit": quality_limit,
        "quality_allowed": quality_depth <= quality_limit,
        "fast_limit": fast_limit,
        "fast_allowed": fast_depth <= fast_limit,
        "balanced_limit": balanced_limit,
        "balanced_allowed": balanced_depth <= balanced_limit,
        "avatar_limit": avatar_limit,
        "avatar_allowed": avatar_depth <= avatar_limit,
    }
    snapshot["generated_at"] = timezone.now().isoformat()
    return snapshot


def _render_metrics_snapshot() -> dict[str, Any]:
    capacity = _render_capacity_snapshot()
    statuses = _job_status_counts()
    latencies = _recent_done_job_latencies_seconds(limit=200)
    return {
        "capacity": capacity,
        "jobs": {
            "status_counts": statuses,
            "oldest_active_age_seconds": _oldest_active_job_age_seconds(),
            "recent_done_count": len(latencies),
            "latency_seconds_p50": _percentile(latencies, 0.50),
            "latency_seconds_p95": _percentile(latencies, 0.95),
            "latency_seconds_p99": _percentile(latencies, 0.99),
        },
        "generated_at": timezone.now().isoformat(),
    }


def _autoscale_thresholds_for_profile(profile: str) -> dict[str, int]:
    profile_key = str(profile or "").strip().lower()
    if profile_key == "fast":
        return {
            "queue_depth_up": int(getattr(settings, "AUTOSCALE_FAST_QUEUE_DEPTH_UP", 12)),
            "queue_depth_down": int(getattr(settings, "AUTOSCALE_FAST_QUEUE_DEPTH_DOWN", 2)),
            "p95_up_seconds": int(getattr(settings, "AUTOSCALE_FAST_P95_UP_SECONDS", 90)),
            "p95_down_seconds": int(getattr(settings, "AUTOSCALE_FAST_P95_DOWN_SECONDS", 35)),
            "min_replicas": int(getattr(settings, "AUTOSCALE_FAST_MIN_REPLICAS", 2)),
            "max_replicas": int(getattr(settings, "AUTOSCALE_FAST_MAX_REPLICAS", 24)),
        }
    if profile_key == "quality":
        return {
            "queue_depth_up": int(getattr(settings, "AUTOSCALE_QUALITY_QUEUE_DEPTH_UP", 6)),
            "queue_depth_down": int(getattr(settings, "AUTOSCALE_QUALITY_QUEUE_DEPTH_DOWN", 1)),
            "p95_up_seconds": int(getattr(settings, "AUTOSCALE_QUALITY_P95_UP_SECONDS", 320)),
            "p95_down_seconds": int(getattr(settings, "AUTOSCALE_QUALITY_P95_DOWN_SECONDS", 200)),
            "min_replicas": int(getattr(settings, "AUTOSCALE_QUALITY_MIN_REPLICAS", 1)),
            "max_replicas": int(getattr(settings, "AUTOSCALE_QUALITY_MAX_REPLICAS", 10)),
        }
    if profile_key == "avatar":
        return {
            "queue_depth_up": int(getattr(settings, "AUTOSCALE_AVATAR_QUEUE_DEPTH_UP", 4)),
            "queue_depth_down": int(getattr(settings, "AUTOSCALE_AVATAR_QUEUE_DEPTH_DOWN", 0)),
            "p95_up_seconds": int(getattr(settings, "AUTOSCALE_AVATAR_P95_UP_SECONDS", 600)),
            "p95_down_seconds": int(getattr(settings, "AUTOSCALE_AVATAR_P95_DOWN_SECONDS", 360)),
            "min_replicas": int(getattr(settings, "AUTOSCALE_AVATAR_MIN_REPLICAS", 1)),
            "max_replicas": int(getattr(settings, "AUTOSCALE_AVATAR_MAX_REPLICAS", 8)),
        }
    return {
        "queue_depth_up": int(getattr(settings, "AUTOSCALE_BALANCED_QUEUE_DEPTH_UP", 10)),
        "queue_depth_down": int(getattr(settings, "AUTOSCALE_BALANCED_QUEUE_DEPTH_DOWN", 2)),
        "p95_up_seconds": int(getattr(settings, "AUTOSCALE_BALANCED_P95_UP_SECONDS", 180)),
        "p95_down_seconds": int(getattr(settings, "AUTOSCALE_BALANCED_P95_DOWN_SECONDS", 90)),
        "min_replicas": int(getattr(settings, "AUTOSCALE_BALANCED_MIN_REPLICAS", 1)),
        "max_replicas": int(getattr(settings, "AUTOSCALE_BALANCED_MAX_REPLICAS", 16)),
    }


def _autoscale_action_for_profile(profile: str, depth: int, p95_seconds: float, thresholds: dict[str, int]) -> str:
    if depth >= thresholds["queue_depth_up"] or p95_seconds >= thresholds["p95_up_seconds"]:
        return "scale_up"
    if depth <= thresholds["queue_depth_down"] and p95_seconds <= thresholds["p95_down_seconds"]:
        return "scale_down"
    return "hold"


def _autoscale_policy_snapshot() -> dict[str, Any]:
    metrics = _render_metrics_snapshot()
    queues = metrics.get("capacity", {}).get("queues", {})
    global_p95 = float(metrics.get("jobs", {}).get("latency_seconds_p95") or 0.0)
    profiles: dict[str, Any] = {}
    for profile in ("fast", "balanced", "quality", "avatar"):
        queue_info = queues.get(profile, {})
        depth = int(queue_info.get("depth") or 0)
        thresholds = _autoscale_thresholds_for_profile(profile)
        action = _autoscale_action_for_profile(profile, depth, global_p95, thresholds)
        profiles[profile] = {
            "queue": queue_info.get("queue"),
            "queue_depth": depth,
            "observed_p95_seconds": global_p95,
            "thresholds": thresholds,
            "action": action,
            "suggested_replica_delta": 1 if action == "scale_up" else (-1 if action == "scale_down" else 0),
            "reason": (
                f"queue_depth={depth} p95={global_p95:.1f}s "
                f"(up if depth>={thresholds['queue_depth_up']} or p95>={thresholds['p95_up_seconds']}s)"
            ),
        }
    return {
        "generated_at": timezone.now().isoformat(),
        "profiles": profiles,
        "global": {
            "latency_p95_seconds": global_p95,
            "oldest_active_age_seconds": float(metrics.get("jobs", {}).get("oldest_active_age_seconds") or 0.0),
            "status_counts": metrics.get("jobs", {}).get("status_counts", {}),
        },
    }

_MAX_LESSON_BYTES = 100 * 1024 * 1024  # 100 MB
_ALLOWED_EXTENSIONS = {".pptx", ".pdf", ".docx", ".txt"}
_MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_RENDER_PROFILE_CHOICES = {"fast", "balanced", "quality"}
logger = logging.getLogger(__name__)


def _request_id_from_request(request) -> str:
    attr_value = str(getattr(request, "request_id", "") or "").strip()
    if attr_value:
        return attr_value[:120]
    for header_name in ("X-Request-Id", "X-Request-ID", "Idempotency-Key"):
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            return value[:120]
    return ""


def _trace_id_from_request(request) -> str:
    attr_value = str(getattr(request, "trace_id", "") or "").strip()
    if attr_value:
        return attr_value[:64]
    traceparent = str(request.headers.get("traceparent") or "").strip()
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 4 and parts[1]:
            return parts[1][:64]
    header_trace = str(request.headers.get("X-Trace-Id") or request.headers.get("X-Trace-ID") or "").strip()
    return header_trace[:64] if header_trace else ""


def _log_with_standard_fields(
    level: str,
    message: str,
    *,
    request=None,
    user_id: int | None = None,
    project_id: int | None = None,
    job_id: int | None = None,
    queue: str | None = None,
    stage: str | None = None,
    started_at: float | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    **extra_fields: Any,
) -> None:
    record = {
        "request_id": str(request_id or (_request_id_from_request(request) if request is not None else "") or ""),
        "trace_id": str(trace_id or (_trace_id_from_request(request) if request is not None else "") or ""),
        "user_id": int(user_id) if user_id is not None else None,
        "project_id": int(project_id) if project_id is not None else None,
        "job_id": int(job_id) if job_id is not None else None,
        "queue": str(queue or ""),
        "stage": str(stage or ""),
        "duration_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000)) if started_at is not None else None,
    }
    for key, value in extra_fields.items():
        record[key] = value
    method = getattr(logger, str(level).lower(), logger.info)
    method("%s | %s", message, json.dumps(record, ensure_ascii=True, sort_keys=True))


def _trace_forward_headers_for_request(request) -> dict[str, str]:
    """Prepare outbound headers for internal service calls."""
    return outbound_trace_headers(request)


# ---------------------------------------------------------------------------
# Media token helpers  (HMAC-SHA256, short-lived)
# ---------------------------------------------------------------------------

def _token_secret() -> bytes:
    return getattr(settings, "MEDIA_TOKEN_SECRET", "media-token-dev-secret").encode()


def _token_ttl() -> int:
    return int(getattr(settings, "MEDIA_TOKEN_TTL_SECONDS", 14400))


def _protection_mode_default() -> str:
    fallback = "public" if bool(getattr(settings, "DEBUG", False)) else "secure_stream"
    mode = str(getattr(settings, "LESSON_PROTECTION_DEFAULT_MODE", fallback) or fallback).strip().lower()
    if mode not in {"public", "secure_stream", "drm_protected"}:
        return fallback
    return mode


def _token_ttl_for_mode(mode: str) -> int:
    if mode == "drm_protected":
        return int(getattr(settings, "LESSON_PROTECTION_TOKEN_TTL_DRM_SECONDS", 7200))
    if mode == "secure_stream":
        return int(getattr(settings, "LESSON_PROTECTION_TOKEN_TTL_SECURE_SECONDS", _token_ttl()))
    return int(getattr(settings, "LESSON_PROTECTION_TOKEN_TTL_PUBLIC_SECONDS", _token_ttl()))


def _stream_url(request, token: str) -> str:
    return request.build_absolute_uri(f"/api/v1/stream/{token}/")


def _playback_identity(request) -> str:
    # Media element requests do not reliably include API auth headers.
    # Use session identity so token issuance, stream access and heartbeat
    # resolve the same grant scope.
    if not request.session.session_key:
        request.session.save()
    return f"session:{request.session.session_key}"


def _playback_binding_identity(request) -> str:
    if not request.session.session_key:
        request.session.save()
    if request.user and request.user.is_authenticated:
        return f"user:{request.user.id}|session:{request.session.session_key}"
    return f"session:{request.session.session_key}"


def _bind_key_for_request(request) -> str:
    identity = _playback_binding_identity(request)
    digest = hashlib.sha256(f"{_token_secret().decode()}|{identity}".encode()).hexdigest()
    return digest[:24]


def _playback_inactivity_ttl() -> int:
    return int(getattr(settings, "LESSON_PROTECTION_INACTIVITY_TTL_SECONDS", 2700))


def _playback_hidden_grace_ttl() -> int:
    return int(getattr(settings, "LESSON_PROTECTION_HIDDEN_GRACE_SECONDS", 300))


def _multi_tab_enforcement_enabled() -> bool:
    return bool(getattr(settings, "LESSON_PROTECTION_MULTI_TAB_ENFORCEMENT", True))


def _risk_window_seconds() -> int:
    return int(getattr(settings, "LESSON_PROTECTION_RISK_WINDOW_SECONDS", 10))


def _segment_burst_threshold() -> int:
    return int(getattr(settings, "LESSON_PROTECTION_SEGMENT_BURST_THRESHOLD", 45))


def _risk_medium_threshold() -> int:
    return int(getattr(settings, "LESSON_PROTECTION_RISK_MEDIUM_THRESHOLD", 3))


def _risk_high_threshold() -> int:
    return int(getattr(settings, "LESSON_PROTECTION_RISK_HIGH_THRESHOLD", 5))


def _scope_key_for(lesson_id: int, identity: str, mode: str) -> str:
    return f"playback:scope:{lesson_id}:{identity}:{mode}"


def _grant_key_for(grant_id: str) -> str:
    return f"playback:grant:{grant_id}"


def _logout_epoch_key_for(identity: str) -> str:
    return f"playback:logout_epoch:{identity}"


def _concurrency_policy() -> str:
    policy = str(getattr(settings, "LESSON_PROTECTION_CONCURRENCY_POLICY", "deny_new") or "deny_new").strip().lower()
    if policy not in {"deny_new", "rotate_old"}:
        return "deny_new"
    return policy


def _grant_is_stale(grant_payload: dict | None) -> bool:
    if not grant_payload:
        return True
    if grant_payload.get("revoked"):
        return True
    now = int(time.time())
    last_seen = int(grant_payload.get("last_seen_at") or grant_payload.get("issued_at") or 0)
    if last_seen and now - last_seen > _playback_inactivity_ttl():
        return True
    hidden_since = grant_payload.get("hidden_since")
    if hidden_since and (now - int(hidden_since) > _playback_hidden_grace_ttl()):
        return True
    return False


def _enforce_playback_concurrency(lesson_id: int, request, mode: str) -> tuple[bool, str | None]:
    identity = _playback_identity(request)
    scope_key = _scope_key_for(lesson_id, identity, mode)
    current_grant_id = cache.get(scope_key)
    if not current_grant_id:
        return True, None

    current_payload = cache.get(_grant_key_for(current_grant_id)) or {}
    if _grant_is_stale(current_payload):
        _revoke_grant(current_grant_id)
        return True, None

    current_bind = current_payload.get("bind_key")
    requested_bind = _bind_key_for_request(request) if bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True)) else None
    if current_bind == requested_bind:
        return True, None

    policy = _concurrency_policy()
    if policy == "rotate_old":
        logger.warning("Playback concurrency rotate_old: lesson=%s mode=%s", lesson_id, mode)
        _revoke_grant(current_grant_id, reason="concurrency_rotated", lesson_id=lesson_id, mode=mode)
        return True, None
    logger.warning("Playback concurrency denied: lesson=%s mode=%s", lesson_id, mode)
    return False, "concurrency_active_elsewhere"


def _issue_playback_grant(lesson_id: int, request, mode: str, ttl_seconds: int) -> tuple[str, str]:
    identity = _playback_identity(request)
    session_binding_active = bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True))
    bind_key = _bind_key_for_request(request) if session_binding_active else None
    scope_key = _scope_key_for(lesson_id, identity, mode)
    now = int(time.time())

    current_grant_id = cache.get(scope_key)
    if current_grant_id:
        current_payload = cache.get(_grant_key_for(current_grant_id)) or {}
        if (
            current_payload.get("lesson_id") == lesson_id
            and current_payload.get("identity") == identity
            and current_payload.get("mode") == mode
            and current_payload.get("bind_key") == bind_key
        ):
            current_payload["last_seen_at"] = now
            current_payload["expires_at"] = now + ttl_seconds
            cache.set(scope_key, current_grant_id, timeout=ttl_seconds)
            cache.set(_grant_key_for(current_grant_id), current_payload, timeout=ttl_seconds)
            logger.info("Playback grant renewed: lesson=%s mode=%s", lesson_id, mode)
            return current_grant_id, scope_key

    grant_id = uuid.uuid4().hex
    grant_key = _grant_key_for(grant_id)
    grant_payload = {
        "lesson_id": lesson_id,
        "identity": identity,
        "mode": mode,
        "issued_at": now,
        "last_seen_at": now,
        "expires_at": now + ttl_seconds,
        "bind_key": bind_key,
        "hidden_since": None,
    }

    cache.set(scope_key, grant_id, timeout=ttl_seconds)
    cache.set(
        grant_key,
        grant_payload,
        timeout=ttl_seconds,
    )
    logger.info("Playback grant issued: lesson=%s mode=%s", lesson_id, mode)
    return grant_id, scope_key


def _revoke_grant(grant_id: str, *, reason: str = "policy", lesson_id: int | None = None, mode: str | None = None) -> None:
    logger.warning("Playback grant revoked: lesson=%s mode=%s reason=%s", lesson_id, mode, reason)
    cache.set(_grant_key_for(grant_id), {"revoked": True, "revoked_at": int(time.time())}, timeout=300)


def _touch_grant_activity(*, grant_id: str, grant_payload: dict, ttl_seconds: int, hidden: bool) -> None:
    now = int(time.time())
    grant_payload["last_seen_at"] = now
    grant_payload["renewed_at"] = now
    grant_payload["expires_at"] = now + ttl_seconds
    if hidden:
        grant_payload["hidden_since"] = grant_payload.get("hidden_since") or now
    else:
        grant_payload["hidden_since"] = None
    cache.set(_grant_key_for(grant_id), grant_payload, timeout=ttl_seconds)


def _header_value(request, name: str) -> str:
    direct = ""
    try:
        direct = (request.headers.get(name) or "").strip()
    except Exception:
        direct = ""
    if direct:
        return direct
    meta_key = f"HTTP_{name.upper().replace('-', '_')}"
    return str((request.META.get(meta_key) if hasattr(request, "META") else "") or "").strip()


def _client_risk_signals(request, *, grant_id: str | None, file_type: str, mode: str) -> tuple[int, list[str]]:
    ua = _header_value(request, "User-Agent").lower()
    accept = _header_value(request, "Accept").lower()
    sec_fetch_dest = _header_value(request, "Sec-Fetch-Dest").lower()
    sec_fetch_mode = _header_value(request, "Sec-Fetch-Mode").lower()
    referer = _header_value(request, "Referer")
    origin = _header_value(request, "Origin")

    score = 0
    reasons: list[str] = []

    suspicious_ua_markers = [
        "idm",
        "internet download manager",
        "wget",
        "curl/",
        "python-requests",
        "aria2",
        "yt-dlp",
        "postman",
        "okhttp",
        "libwww",
    ]
    if any(marker in ua for marker in suspicious_ua_markers):
        score += 2
        reasons.append("ua_automation_pattern")

    if mode == "drm_protected" and file_type in {"hls_manifest", "hls_segment", "hls_key"}:
        if not accept:
            score += 1
            reasons.append("missing_accept")
        if not referer and not origin:
            score += 1
            reasons.append("missing_origin_referer")
        if sec_fetch_dest and sec_fetch_dest not in {"video", "empty", "document"}:
            score += 1
            reasons.append("unexpected_sec_fetch_dest")
        if sec_fetch_mode and sec_fetch_mode not in {"cors", "no-cors", "navigate", "same-origin"}:
            score += 1
            reasons.append("unexpected_sec_fetch_mode")

    if grant_id and file_type == "hls_segment":
        burst_key = f"playback:risk:segment_burst:{grant_id}:{int(time.time()) // max(_risk_window_seconds(), 1)}"
        burst_count = int(cache.get(burst_key) or 0) + 1
        cache.set(burst_key, burst_count, timeout=max(_risk_window_seconds(), 1) + 2)
        if burst_count > _segment_burst_threshold():
            score += 2
            reasons.append("segment_burst")

    return score, reasons


def _effective_grant_ttl(base_ttl: int, risk_score: int, mode: str) -> int:
    if risk_score >= _risk_high_threshold():
        return min(base_ttl, 180 if mode == "drm_protected" else 240)
    if risk_score >= _risk_medium_threshold():
        return min(base_ttl, 600)
    return base_ttl


def _risk_policy_decision(*, mode: str, file_type: str, risk_score: int) -> str:
    if risk_score >= _risk_high_threshold():
        if mode == "drm_protected" or file_type == "hls_manifest":
            return "revoke_and_deny"
        return "shorten_ttl"
    if risk_score >= _risk_medium_threshold() and file_type == "hls_manifest":
        return "fresh_grant_required"
    if risk_score >= _risk_medium_threshold():
        return "shorten_ttl"
    return "allow"


def _grant_usage_limit(file_type: str, mode: str) -> int:
    if mode == "drm_protected":
        limits = {"hls_manifest": 8, "hls_key": 24, "hls_segment": 800, "video": 0, "avatar": 600, "srt": 80}
        return limits.get(file_type, 60)
    if mode == "secure_stream":
        limits = {"hls_manifest": 20, "hls_key": 60, "hls_segment": 2000, "video": 200, "avatar": 1200, "srt": 150}
        return limits.get(file_type, 120)
    return 10_000


def _check_grant_access(request, *, lesson_id: int, grant_id: str | None, bind_key: str | None, mode: str, file_type: str) -> bool:
    if not grant_id:
        if mode == "drm_protected":
            return False
        logger.info("Legacy token access without playback grant: lesson=%s mode=%s file_type=%s", lesson_id, mode, file_type)
        return True

    identity = _playback_identity(request)
    scope_key = _scope_key_for(lesson_id, identity, mode)
    current_grant_id = cache.get(scope_key)
    if current_grant_id != grant_id:
        logger.warning("Playback grant invalidated or mismatched for lesson=%s mode=%s", lesson_id, mode)
        return False

    grant_payload = cache.get(_grant_key_for(grant_id))
    if not grant_payload:
        logger.warning("Playback grant missing/expired for lesson=%s mode=%s", lesson_id, mode)
        return False

    if grant_payload.get("revoked"):
        logger.warning("Playback grant revoked for lesson=%s mode=%s", lesson_id, mode)
        return False

    if bind_key and bind_key != _bind_key_for_request(request):
        logger.warning("Playback bind-key mismatch for lesson=%s mode=%s", lesson_id, mode)
        if grant_id:
            mismatch_key = f"playback:risk:bind_mismatch:{grant_id}"
            mismatch_count = int(cache.get(mismatch_key) or 0) + 1
            cache.set(mismatch_key, mismatch_count, timeout=_token_ttl_for_mode(mode))
            if mismatch_count >= 2:
                _revoke_grant(grant_id, reason="bind_mismatch_repeat", lesson_id=lesson_id, mode=mode)
        return False

    if grant_payload.get("bind_key") and grant_payload.get("bind_key") != _bind_key_for_request(request):
        logger.warning("Playback grant session mismatch for lesson=%s mode=%s", lesson_id, mode)
        mismatch_key = f"playback:risk:bind_mismatch:{grant_id}"
        mismatch_count = int(cache.get(mismatch_key) or 0) + 1
        cache.set(mismatch_key, mismatch_count, timeout=_token_ttl_for_mode(mode))
        if mismatch_count >= 2:
            _revoke_grant(grant_id, reason="session_mismatch_repeat", lesson_id=lesson_id, mode=mode)
        return False

    logout_epoch = cache.get(_logout_epoch_key_for(identity)) or 0
    if int(grant_payload.get("issued_at") or 0) <= int(logout_epoch):
        logger.warning("Playback grant denied after logout for lesson=%s mode=%s", lesson_id, mode)
        return False

    now = int(time.time())
    last_seen = int(grant_payload.get("last_seen_at") or grant_payload.get("issued_at") or now)
    if now - last_seen > _playback_inactivity_ttl():
        logger.warning("Playback grant inactive too long for lesson=%s mode=%s", lesson_id, mode)
        _revoke_grant(grant_id, reason="inactive", lesson_id=lesson_id, mode=mode)
        return False

    hidden_since = grant_payload.get("hidden_since")
    if hidden_since and (now - int(hidden_since) > _playback_hidden_grace_ttl()):
        logger.warning("Playback grant hidden too long for lesson=%s mode=%s", lesson_id, mode)
        _revoke_grant(grant_id, reason="hidden_too_long", lesson_id=lesson_id, mode=mode)
        return False

    usage_key = f"playback:usage:{grant_id}:{file_type}"
    usage = cache.get(usage_key) or 0
    usage = int(usage) + 1
    cache.set(usage_key, usage, timeout=_token_ttl_for_mode(mode))

    max_usage = _grant_usage_limit(file_type, mode)
    if usage > max_usage:
        logger.warning(
            "Suspicious playback usage: job=%s mode=%s file_type=%s usage=%s limit=%s",
            lesson_id,
            mode,
            file_type,
            usage,
            max_usage,
        )
        return False

    _touch_grant_activity(
        grant_id=grant_id,
        grant_payload=grant_payload,
        ttl_seconds=_token_ttl_for_mode(mode),
        hidden=False,
    )

    return True


def _encode_rel_path(rel_path: str | None) -> str:
    if not rel_path:
        return ""
    return base64.urlsafe_b64encode(rel_path.encode()).decode().rstrip("=")


def _decode_rel_path(path_token: str | None) -> str:
    if not path_token:
        return ""
    padding = 4 - (len(path_token) % 4)
    padded = path_token + ("=" * (padding if padding != 4 else 0))
    return base64.urlsafe_b64decode(padded.encode()).decode()


def _playback_sidecar_for_job(storage_root: str, project_id: int) -> dict:
    sidecar_path = Path(storage_root) / str(project_id) / "playback_assets.json"
    if not sidecar_path.exists():
        return {}
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_effective_protection_mode(sidecar: dict | None) -> tuple[str, dict]:
    allowed = {"public", "secure_stream", "drm_protected"}
    env_mode = _protection_mode_default()
    sidecar_mode = str((sidecar or {}).get("protection_mode") or "").strip().lower()
    sidecar_valid = sidecar_mode in allowed

    # Dev-safe behavior: explicit public default must not be silently overridden
    # by older lesson sidecars produced before env changes.
    if env_mode == "public":
        source = "env_default"
        if sidecar_valid and sidecar_mode != "public":
            source = "env_default_public_override"
        return "public", {
            "effective_mode": "public",
            "source": source,
            "env_default_mode": env_mode,
            "sidecar_mode": sidecar_mode if sidecar_valid else None,
            "sidecar_override_applied": bool(sidecar_valid and sidecar_mode != "public"),
        }

    if sidecar_valid:
        return sidecar_mode, {
            "effective_mode": sidecar_mode,
            "source": "sidecar",
            "env_default_mode": env_mode,
            "sidecar_mode": sidecar_mode,
            "sidecar_override_applied": True,
        }

    return env_mode, {
        "effective_mode": env_mode,
        "source": "env_default",
        "env_default_mode": env_mode,
        "sidecar_mode": None,
        "sidecar_override_applied": False,
    }


def _resolve_playback_mode_for_project(project: Project, sidecar: dict | None) -> tuple[str, dict, bool]:
    protection_mode, mode_debug = _resolve_effective_protection_mode(sidecar)
    lesson_is_public = _is_public_lesson(project)
    if not lesson_is_public and protection_mode == "public":
        return "secure_stream", {
            **mode_debug,
            "effective_mode": "secure_stream",
            "source": "draft_preview_secure_stream",
            "base_effective_mode": protection_mode,
            "draft_preview_forced_secure_stream": True,
        }, lesson_is_public
    return protection_mode, mode_debug, lesson_is_public


def _language_detection_sidecar_for_job(storage_root: str, project_id: int) -> dict:
    sidecar_path = Path(storage_root) / str(project_id) / "language_detection.json"
    if not sidecar_path.exists():
        return {
            "detected_language": None,
            "resolved_language": "en",
            "source": "pending",
            "confidence": 0.0,
            "fallback_used": True,
            "supported_languages": ["en"],
            "detector": "placeholder_v1",
        }
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {
        "detected_language": None,
        "resolved_language": "en",
        "source": "fallback_invalid_sidecar",
        "confidence": 0.0,
        "fallback_used": True,
        "supported_languages": ["en"],
        "detector": "placeholder_v1",
    }


def _chunk_transcript_text(text: str, max_chars: int = 120) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return []

    lines = [ln for ln in raw.split("\n")]
    chunks: list[str] = []

    for line in lines:
        # Keep intentional blank line as stronger pause marker.
        if not line.strip():
            continue

        part = re.sub(r"\s+", " ", line).strip()
        if len(part) <= max_chars:
            chunks.append(part)
            continue

        sentence_parts = re.split(r"(?<=[.!?…])\s+", part)
        current = ""
        for sentence in sentence_parts:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(sentence) <= max_chars:
                current = sentence
                continue
            for word in sentence.split():
                candidate = f"{current} {word}".strip() if current else word
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    current = word
        if current:
            chunks.append(current)

    return [c for c in chunks if c]


def _build_editor_document(narration_text: str, rich_text_html: str) -> dict:
    raw = str(narration_text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [line for line in raw.split("\n")]
    return {
        "version": 1,
        "paragraphs": [
            {
                "index": idx,
                "text": paragraph,
            }
            for idx, paragraph in enumerate(paragraphs)
        ],
        "html": str(rich_text_html or ""),
    }


def _active_transcript_pages(project: Project):
    transcript_rel = getattr(project, "transcript_pages", None)
    if transcript_rel is None:
        return TranscriptPage.objects.none()
    return transcript_rel.filter(is_active=True).order_by("order", "id")


def _deleted_transcript_pages(project: Project):
    transcript_rel = getattr(project, "transcript_pages", None)
    if transcript_rel is None:
        return TranscriptPage.objects.none()
    return transcript_rel.filter(is_active=False).order_by("deleted_at", "order", "id")


def _project_transcript_timeline(
    project: Project,
    *,
    include_deleted: bool = False,
    request=None,
) -> list[dict]:
    if include_deleted:
        transcript_rel = getattr(project, "transcript_pages", None)
        if transcript_rel is None:
            return []
        pages = transcript_rel.all().order_by("order", "id")
    else:
        pages = _active_transcript_pages(project)
    serialized = TranscriptPageSerializer(pages, many=True).data
    for page in serialized:
        slide_index = page.get("source_slide_index")
        thumbnail_url = ""
        if slide_index is not None:
            rel_path = f"/api/v1/projects/{project.id}/slides/{int(slide_index)}/image/"
            thumbnail_url = request.build_absolute_uri(rel_path) if request is not None else rel_path
        page["thumbnail_url"] = thumbnail_url
    return serialized


def _project_deleted_transcript_timeline(project: Project) -> list[dict]:
    return TranscriptPageSerializer(_deleted_transcript_pages(project), many=True).data


def _rich_text_html_from_narration(text: str) -> str:
    return html.escape(str(text or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br />")


def _set_page_narration_artifacts(page: TranscriptPage, narration_text: str) -> None:
    page.narration_text = str(narration_text or "")
    page.rich_text_html = _rich_text_html_from_narration(page.narration_text)
    page.editor_document = _build_editor_document(page.narration_text, page.rich_text_html)
    page.subtitle_chunks = _chunk_transcript_text(page.narration_text)


def _normalize_active_transcript_order(project: Project, ordered_pages: list[TranscriptPage] | None = None) -> None:
    pages = ordered_pages if ordered_pages is not None else list(_active_transcript_pages(project))
    for idx, page in enumerate(pages):
        if page.order != idx:
            page.order = idx
            page.save(update_fields=["order", "updated_at"])


def _unique_split_page_key(project: Project, base_key: str, existing_keys: set[str], split_index: int) -> str:
    safe_base = str(base_key or "page").strip() or "page"
    suffix = f"-x{split_index}"
    candidate = f"{safe_base[: max(1, 64 - len(suffix))]}{suffix}"
    while candidate in existing_keys:
        random_suffix = f"-x{split_index}-{uuid.uuid4().hex[:4]}"
        candidate = f"{safe_base[: max(1, 64 - len(random_suffix))]}{random_suffix}"
    existing_keys.add(candidate)
    return candidate


def _safe_merge_separator(raw_separator: Any) -> tuple[str, str | None]:
    if raw_separator is None:
        return "\n\n", None
    if not isinstance(raw_separator, str):
        return "", "separator must be a string."
    if len(raw_separator) > 8:
        return "", "separator is too long."
    if re.sub(r"\s", "", raw_separator):
        return "", "separator may contain only whitespace."
    return raw_separator, None


def _combine_text_with_separator(left: str, right: str, separator: str) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if left_text and right_text:
        return f"{left_text}{separator}{right_text}"
    return left_text or right_text


def _split_pages_on_double_newline(project: Project) -> None:
    """Expand transcript pages where text contains empty-line separators."""
    pages = list(_active_transcript_pages(project))
    existing_keys = {p.page_key for p in pages}

    for page in pages:
        text = str(page.narration_text or "").replace("\r\n", "\n").replace("\r", "\n")
        parts = [part for part in re.split(r"\n\s*\n+", text) if part.strip()]
        if len(parts) <= 1:
            continue

        page.narration_text = parts[0]
        page.subtitle_chunks = _chunk_transcript_text(parts[0])
        page.save(update_fields=["narration_text", "subtitle_chunks", "updated_at"])

        for idx, part in enumerate(parts[1:], start=1):
            candidate_key = f"{page.page_key}-x{idx}"
            while candidate_key in existing_keys:
                candidate_key = f"{page.page_key}-x{idx}-{uuid.uuid4().hex[:4]}"
            existing_keys.add(candidate_key)

            TranscriptPage.objects.create(
                project=project,
                order=page.order + idx,
                source_slide_index=page.source_slide_index,
                split_index=page.split_index + idx,
                page_key=candidate_key,
                original_text=part,
                narration_text=part,
                rich_text_html=part.replace("\n", "<br />"),
                editor_document=_build_editor_document(part, part.replace("\n", "<br />")),
                subtitle_chunks=_chunk_transcript_text(part),
                whiteboard_mode=page.whiteboard_mode,
            )

    # Normalize ordering after insertion to keep sequential slide navigation.
    for idx, row in enumerate(_active_transcript_pages(project)):
        if row.order != idx:
            row.order = idx
            row.save(update_fields=["order", "updated_at"])


def _resolve_child_rel_path(storage_root: Path, manifest_dir: Path, child_ref: str) -> str:
    candidate = (manifest_dir / child_ref).resolve()
    storage_resolved = storage_root.resolve()
    rel_path = os.path.relpath(candidate, storage_resolved).replace("\\", "/")
    if rel_path == ".." or rel_path.startswith("../") or rel_path.startswith("/"):
        raise ValueError("invalid child relative path")
    return rel_path


def _rewrite_hls_manifest_with_tokens(
    manifest_text: str,
    *,
    request,
    job_id: int,
    manifest_path: Path,
    storage_root: Path,
    ttl_seconds: int | None = None,
    grant_id: str | None = None,
    bind_key: str | None = None,
) -> str:
    manifest_dir = manifest_path.parent
    rewritten: list[str] = []

    for line in manifest_text.splitlines():
        stripped = line.strip()
        if not stripped:
            rewritten.append(line)
            continue

        if stripped.startswith("#EXT-X-KEY") and "URI=" in stripped:
            match = re.search(r'URI="([^"]+)"', stripped)
            if match:
                key_name = match.group(1)
                key_rel = _resolve_child_rel_path(storage_root, manifest_dir, key_name)
                key_token = generate_media_token(
                    job_id,
                    "hls_key",
                    rel_path=key_rel,
                    ttl_seconds=ttl_seconds,
                    grant_id=grant_id,
                    bind_key=bind_key,
                )
                key_url = _stream_url(request, key_token)
                rewritten.append(stripped.replace(f'URI="{key_name}"', f'URI="{key_url}"'))
                continue

        if stripped.startswith("#"):
            rewritten.append(line)
            continue

        child_rel = _resolve_child_rel_path(storage_root, manifest_dir, stripped)
        child_type = "hls_key" if stripped.endswith(".key") else "hls_segment"
        child_token = generate_media_token(
            job_id,
            child_type,
            rel_path=child_rel,
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        )
        rewritten.append(_stream_url(request, child_token))

    return "\n".join(rewritten) + "\n"


def _session_label(request) -> str:
    if request.user and request.user.is_authenticated:
        return request.user.username
    if not request.session.session_key:
        request.session.save()
    return f"session:{request.session.session_key[:8]}"


def _default_asset_id(project_id: int) -> str:
    prefix = str(getattr(settings, "DRM_ASSET_ID_PREFIX", "lesson-") or "lesson-")
    return f"{prefix}{project_id}"


def _default_content_id(project_id: int) -> str:
    prefix = str(getattr(settings, "DRM_CONTENT_ID_PREFIX", "project-") or "project-")
    return f"{prefix}{project_id}"


def _playback_session_id(job_id: int, grant_id: str | None) -> str:
    prefix = str(getattr(settings, "DRM_PLAYBACK_SESSION_PREFIX", "playback") or "playback")
    suffix = (grant_id or uuid.uuid4().hex)[:12]
    return f"{prefix}-{job_id}-{suffix}"


def _infer_drm_system_name(key_system: str) -> str:
    mapping = {
        "com.widevine.alpha": "widevine",
        "com.microsoft.playready": "playready",
        "com.apple.fps.1_0": "fairplay",
    }
    return mapping.get((key_system or "").strip().lower(), "")


def _resolve_drm_systems(*, asset_id: str, content_id: str, playback_session_id: str) -> tuple[dict[str, dict], str, dict | None, bool]:
    legacy_enabled = bool(getattr(settings, "DRM_ENABLED", False))
    legacy_key_system = str(getattr(settings, "DRM_KEY_SYSTEM", "") or "").strip()
    legacy_license_url = str(getattr(settings, "DRM_LICENSE_URL", "") or "").strip()
    legacy_certificate_url = str(getattr(settings, "DRM_CERTIFICATE_URL", "") or "").strip()
    preferred_system = str(getattr(settings, "DRM_PREFERRED_SYSTEM", "") or "").strip().lower()
    inferred_legacy_system = _infer_drm_system_name(legacy_key_system)
    systems: dict[str, dict] = {}

    for name, requires_certificate in (("widevine", False), ("playready", False), ("fairplay", True)):
        env_prefix = f"DRM_{name.upper()}"
        system_enabled = bool(getattr(settings, f"{env_prefix}_ENABLED", False))
        key_system = str(getattr(settings, f"{env_prefix}_KEY_SYSTEM", "") or "").strip()
        license_url = str(getattr(settings, f"{env_prefix}_LICENSE_URL", "") or "").strip()
        certificate_url = str(getattr(settings, f"{env_prefix}_CERTIFICATE_URL", "") or "").strip()
        content_type = str(getattr(settings, f"{env_prefix}_CONTENT_TYPE", "video/mp4") or "video/mp4").strip()

        if not any((key_system, license_url, certificate_url)) and legacy_enabled:
            if preferred_system == name or inferred_legacy_system == name:
                system_enabled = True
                key_system = legacy_key_system
                license_url = legacy_license_url
                certificate_url = legacy_certificate_url

        if not system_enabled and legacy_enabled and (preferred_system == name or inferred_legacy_system == name):
            system_enabled = bool(key_system or license_url or certificate_url)

        ready = bool(system_enabled and key_system and license_url and (not requires_certificate or certificate_url))
        systems[name] = {
            "name": name,
            "enabled": system_enabled,
            "ready": ready,
            "key_system": key_system,
            "license_url": license_url,
            "certificate_url": certificate_url,
            "requires_certificate": requires_certificate,
            "content_type": content_type,
            "asset_id": asset_id,
            "content_id": content_id,
            "playback_session_id": playback_session_id,
        }

    selected_name = preferred_system if preferred_system in systems else ""
    if selected_name and not systems[selected_name]["enabled"]:
        selected_name = ""
    if not selected_name and inferred_legacy_system in systems and systems[inferred_legacy_system]["enabled"]:
        selected_name = inferred_legacy_system
    if not selected_name:
        selected_name = next((name for name, payload in systems.items() if payload["ready"]), "")
    if not selected_name:
        selected_name = next((name for name, payload in systems.items() if payload["enabled"]), "")

    selected_system = systems.get(selected_name) if selected_name else None
    any_ready = any(payload["ready"] for payload in systems.values())
    return systems, selected_name, selected_system, any_ready


def _playback_payload(
    request,
    project: Project,
    job: Job,
    video_token: str,
    srt_token: str | None,
    vtt_token: str | None = None,
    hls_manifest_token: str | None = None,
    hls_encrypted: bool = False,
    asset_id: str | None = None,
    content_id: str | None = None,
    protection_mode: str = "secure_stream",
    mode_debug: dict | None = None,
    allow_mp4_fallback: bool = True,
    playback_session_id: str | None = None,
    session_binding_active: bool = False,
    avatar_token: str | None = None,
    avatar_overlay_defaults: dict | None = None,
) -> dict:
    watermark_enabled = bool(getattr(settings, "LECTURE_WATERMARK_ENABLED", True))
    visibility_lock_enabled = bool(getattr(settings, "LECTURE_VISIBILITY_LOCK_ENABLED", True))
    watermark_forced = False
    if protection_mode == "drm_protected" and bool(getattr(settings, "LESSON_PROTECTION_FORCE_WATERMARK_FOR_PROTECTED", True)):
        watermark_enabled = True
        visibility_lock_enabled = True
        watermark_forced = True
    hls_enabled = bool(hls_manifest_token)
    resolved_asset_id = asset_id or _default_asset_id(project.id)
    resolved_content_id = content_id or _default_content_id(project.id)
    resolved_playback_session_id = playback_session_id or _playback_session_id(job.id, None)
    drm_systems, preferred_system_name, preferred_system, drm_configured = _resolve_drm_systems(
        asset_id=resolved_asset_id,
        content_id=resolved_content_id,
        playback_session_id=resolved_playback_session_id,
    )
    drm_enabled = bool(getattr(settings, "DRM_ENABLED", False))
    drm_ready = bool(drm_enabled and drm_configured and hls_enabled)
    manifest_url = _stream_url(request, hls_manifest_token) if hls_manifest_token else ""

    video_url = _stream_url(request, video_token) if (video_token and allow_mp4_fallback) else ""
    vtt_url = _stream_url(request, vtt_token) if vtt_token else None
    payload = {
        "video_url": video_url,
        "srt_url": _stream_url(request, srt_token) if srt_token else None,
        "vtt_url": vtt_url,
        "subtitle_vtt_url": vtt_url,
        "expires_in": _token_ttl_for_mode(protection_mode),
        "protection_mode": protection_mode,
        "allow_mp4_fallback": bool(allow_mp4_fallback),
        "session_binding_active": bool(session_binding_active),
        "watermark": {
            "enabled": watermark_enabled,
            "forced": watermark_forced,
            "text": f"{_session_label(request)} • lesson {project.id} • job {job.id}",
        },
        "protection": {
            "visibility_lock": visibility_lock_enabled,
        },
        "drm": {
            "enabled": drm_enabled,
            "configured": drm_configured,
            "ready": drm_ready,
            "provider": str(getattr(settings, "DRM_PROVIDER_NAME", "external") or "external"),
            "preferred_system": preferred_system_name,
            "key_system": preferred_system.get("key_system", "") if preferred_system else "",
            "license_url": preferred_system.get("license_url", "") if preferred_system else "",
            "certificate_url": preferred_system.get("certificate_url", "") if preferred_system else "",
            "content_type": preferred_system.get("content_type", "application/vnd.apple.mpegurl") if preferred_system else "application/vnd.apple.mpegurl",
            "asset_id": resolved_asset_id,
            "content_id": resolved_content_id,
            "playback_session_id": resolved_playback_session_id,
            "session_binding_active": bool(session_binding_active),
            "grant_bound": bool(playback_session_id),
            "fallback_allowed": bool(allow_mp4_fallback),
            "manifest_url": manifest_url,
            "manifest_type": "hls" if hls_manifest_token else "",
            "encrypted_manifest": bool(hls_manifest_token),
            "encrypted_segments": bool(hls_encrypted),
            "systems": drm_systems,
        },
    }

    payload["streaming"] = {
        "protection_mode": protection_mode,
        "preferred": "hls" if hls_enabled else "mp4",
        "fallback": {
            "type": "mp4",
            "url": payload["video_url"],
        } if allow_mp4_fallback and payload["video_url"] else None,
        "hls": {
            "enabled": hls_enabled,
            "manifest_url": manifest_url,
            "encrypted": bool(hls_encrypted),
        },
    }

    payload["playback_debug"] = {
        "secure_playback_active": protection_mode != "public",
        "selected_mode": "hls" if hls_enabled else "mp4_fallback",
        "hls_available": hls_enabled,
        "mp4_fallback_allowed": bool(allow_mp4_fallback),
        "watermark_enabled": watermark_enabled,
        "visibility_lock_enabled": visibility_lock_enabled,
        "drm_configured": drm_configured,
        "drm_ready": drm_ready,
        "drm_preferred_system": preferred_system_name,
        "mode_debug": mode_debug or {},
    }

    payload["playback_status"] = {
        "protection_mode": protection_mode,
        "mode_source": (mode_debug or {}).get("source", "unknown"),
        "grant_active": bool(playback_session_id),
        "multi_tab_enforcement": _multi_tab_enforcement_enabled(),
        "token_renewal_enabled": bool(playback_session_id),
        "secure_hls_active": bool(hls_manifest_token),
    }
    if avatar_token:
        payload["avatar_overlay"] = {
            "enabled": True,
            "stream_url": _stream_url(request, avatar_token),
            "defaults": avatar_overlay_defaults or {},
        }
    else:
        payload["avatar_overlay"] = {
            "enabled": False,
            "stream_url": "",
            "defaults": avatar_overlay_defaults or {},
        }
    payload["mode_debug"] = mode_debug or {}
    return payload


def _subtitle_vtt_rel_path_for_job(job: Job) -> str:
    rel_path = str(getattr(job, "srt_url", "") or "").strip().replace("\\", "/").lstrip("/")
    if not rel_path or ".." in rel_path.split("/"):
        return ""
    if not rel_path.lower().endswith(".srt"):
        return ""
    return f"{rel_path[:-4]}.vtt"


def _storage_rel_path_exists(storage_root: str | os.PathLike[str], rel_path: str) -> bool:
    if not rel_path or ".." in str(rel_path).replace("\\", "/").split("/"):
        return False
    full_path = Path(storage_root) / str(rel_path).lstrip("/")
    return full_path.exists() and full_path.is_file()


def _generate_vtt_media_token_for_job(
    job: Job,
    *,
    storage_root: str | os.PathLike[str],
    ttl_seconds: int,
    grant_id: str | None,
    bind_key: str | None,
) -> str | None:
    rel_path = _subtitle_vtt_rel_path_for_job(job)
    if not _storage_rel_path_exists(storage_root, rel_path):
        return None
    return generate_media_token(
        job.id,
        "vtt",
        rel_path=rel_path,
        ttl_seconds=ttl_seconds,
        grant_id=grant_id,
        bind_key=bind_key,
    )


def _parse_byte_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    if not range_header or not range_header.startswith("bytes="):
        return None

    range_spec = range_header.split("=", 1)[1].split(",", 1)[0].strip()
    if "-" not in range_spec:
        return None

    start_str, end_str = range_spec.split("-", 1)
    try:
        if start_str == "":
            length = int(end_str)
            if length <= 0:
                return None
            start = max(file_size - length, 0)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
        if start < 0 or end < start or start >= file_size:
            return None
        return start, min(end, file_size - 1)
    except ValueError:
        return None


def _content_type_for_resource(file_type: str, full_path: Path | None = None) -> str:
    if file_type == "video":
        return "video/mp4"
    if file_type == "avatar":
        return "video/mp4"
    if file_type == "hls_manifest":
        return "application/vnd.apple.mpegurl; charset=utf-8"
    if file_type == "hls_segment":
        return "video/mp2t"
    if file_type == "hls_key":
        return "application/octet-stream"
    if file_type == "srt":
        return "text/vtt; charset=utf-8"
    if file_type == "vtt":
        return "text/vtt; charset=utf-8"
    guessed = None
    if full_path is not None:
        guessed, _ = mimetypes.guess_type(str(full_path))
    return guessed or "application/octet-stream"


def _stream_error_response(*, file_type: str, status_code: int, reason: str) -> HttpResponse:
    if file_type in ("srt", "vtt"):
        body = "WEBVTT\n\nNOTE subtitles unavailable\n"
        response = HttpResponse(body, status=status_code, content_type=_content_type_for_resource(file_type))
    else:
        response = HttpResponse(b"", status=status_code, content_type=_content_type_for_resource(file_type))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = "cross-origin"
    logger.warning("Playback stream denied: resource=%s status=%s reason=%s", file_type, status_code, reason)
    return response


def _srt_text_to_vtt(srt_text: str) -> str:
    normalized = (srt_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return "WEBVTT\n\n"
    body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", normalized)
    return f"WEBVTT\n\n{body}\n"


def _subtitle_response(full_path: Path) -> HttpResponse:
    try:
        subtitle_text = full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        subtitle_text = full_path.read_text(encoding="utf-8", errors="replace")
    response = HttpResponse(_srt_text_to_vtt(subtitle_text), content_type=_content_type_for_resource("srt"))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = "cross-origin"
    return response


def _log_stream_delivery(*, file_type: str, content_type: str, status_code: int, token_authorized: bool, note: str = "") -> None:
    logger.info(
        "Playback stream response: resource=%s mime=%s status=%s token_authorized=%s note=%s",
        file_type,
        content_type,
        status_code,
        token_authorized,
        note,
    )


def _media_file_response(request, full_path: Path, content_type: str | None):
    file_size = full_path.stat().st_size
    range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")
    byte_range = _parse_byte_range(range_header, file_size) if range_header else None

    if range_header and byte_range is None:
        response = HttpResponse(status=416, content_type=content_type or "application/octet-stream")
        response["Content-Range"] = f"bytes */{file_size}"
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cross-Origin-Resource-Policy"] = "cross-origin"
        return response

    if byte_range is not None:
        start, end = byte_range
        length = end - start + 1
        with open(full_path, "rb") as fh:
            fh.seek(start)
            data = fh.read(length)
        response = HttpResponse(data, status=206, content_type=content_type or "application/octet-stream")
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(length)
    else:
        response = FileResponse(open(full_path, "rb"), content_type=content_type or "application/octet-stream")
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Cross-Origin-Resource-Policy"] = "cross-origin"
    response["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
    return response


def generate_media_token(
    job_id: int,
    file_type: str = "video",
    rel_path: str | None = None,
    *,
    ttl_seconds: int | None = None,
    grant_id: str | None = None,
    bind_key: str | None = None,
) -> str:
    """Return a URL-safe token encoding media access claims with signature."""
    expiry = int(time.time()) + int(ttl_seconds or _token_ttl())
    path_token = _encode_rel_path(rel_path)
    payload = f"v2:{job_id}:{file_type}:{path_token}:{expiry}:{grant_id or ''}:{bind_key or ''}"
    sig = _hmac.new(_token_secret(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def validate_media_token(token: str):
    """Validate token; return (job_id, file_type, rel_path, grant_id, bind_key)."""
    try:
        padding = 4 - (len(token) % 4)
        padded = token + ("=" * (padding if padding != 4 else 0))
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        # Format: job_id:file_type:expiry:sig  (sig = 64 hex chars, no colons)
        last_sep = decoded.rfind(":")
        sig = decoded[last_sep + 1:]
        rest = decoded[:last_sep]
        parts = rest.split(":")
        if parts and parts[0] == "v2":
            if len(parts) != 7:
                raise ValueError("bad format")
            _, job_id_s, file_type, rel_path_token, expiry_s, grant_id, bind_key = parts
            job_id = int(job_id_s)
            expiry = int(expiry_s)
            rel_path = _decode_rel_path(rel_path_token) if rel_path_token else ""
            grant_id = grant_id or None
            bind_key = bind_key or None
        elif len(parts) == 3:
            job_id, file_type, expiry = int(parts[0]), parts[1], int(parts[2])
            rel_path = ""
            grant_id = None
            bind_key = None
        else:
            if len(parts) != 4:
                raise ValueError("bad format")
            job_id, file_type, rel_path_token, expiry = int(parts[0]), parts[1], parts[2], int(parts[3])
            rel_path = _decode_rel_path(rel_path_token)
            grant_id = None
            bind_key = None

        expected = _hmac.new(_token_secret(), rest.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            raise ValueError("invalid signature")
        if time.time() > expiry:
            raise ValueError("token expired")
        return job_id, file_type, rel_path, grant_id, bind_key
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"bad token: {exc}")


# ---------------------------------------------------------------------------
# Auth views
# ---------------------------------------------------------------------------

def _google_auth_enabled() -> bool:
    return bool(getattr(settings, "GOOGLE_AUTH_ENABLED", False) and getattr(settings, "GOOGLE_CLIENT_ID", ""))


def _google_redirect_enabled() -> bool:
    return bool(
        _google_auth_enabled()
        and getattr(settings, "GOOGLE_CLIENT_SECRET", "")
        and getattr(settings, "GOOGLE_REDIRECT_URI", "")
    )


def _google_oauth_state_cache_key(state: str) -> str:
    return f"google-oauth-state:{state}"


def _token_provider_cache_key(token_key: str) -> str:
    return f"auth-token-provider:{token_key}"


def _google_picture_cache_key(user_id: int) -> str:
    return f"auth-google-picture:{user_id}"


def _set_token_provider(token_key: str, provider: str) -> None:
    cache.set(_token_provider_cache_key(token_key), provider, timeout=30 * 24 * 60 * 60)


def _get_token_provider(token_key: str | None) -> str:
    if not token_key:
        return "password"
    return cache.get(_token_provider_cache_key(token_key)) or "password"


def _delete_token_provider(token_key: str | None) -> None:
    if token_key:
        cache.delete(_token_provider_cache_key(token_key))


def _set_google_profile_picture(user: User, picture_url: str | None) -> None:
    if not user or not user.id:
        return
    normalized = str(picture_url or "").strip()
    key = _google_picture_cache_key(user.id)
    if normalized:
        cache.set(key, normalized, timeout=30 * 24 * 60 * 60)
    else:
        cache.delete(key)


def _get_google_profile_picture(user: User) -> str:
    if not user or not user.id:
        return ""
    return str(cache.get(_google_picture_cache_key(user.id)) or "")


def _serialize_user_with_provider(user: User, provider: str) -> dict:
    payload = UserSerializer(user).data
    payload["auth_provider"] = provider
    payload["auth_picture_url"] = _get_google_profile_picture(user) if provider == "google" else ""
    return payload


def _google_auth_public_config() -> dict:
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    enabled = _google_auth_enabled()
    redirect_enabled = _google_redirect_enabled()
    return {
        "enabled": enabled,
        "client_id": client_id if enabled else "",
        "redirect_uri": getattr(settings, "GOOGLE_REDIRECT_URI", "") if enabled else "",
        "redirect_flow_enabled": redirect_enabled,
        "redirect_success_url_configured": bool(getattr(settings, "GOOGLE_REDIRECT_SUCCESS_URL", "")),
    }


def _google_authorize_url(state: str) -> str:
    params = {
        "client_id": getattr(settings, "GOOGLE_CLIENT_ID", ""),
        "redirect_uri": getattr(settings, "GOOGLE_REDIRECT_URI", ""),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def _exchange_google_oauth_code(code: str) -> dict:
    data = urlencode(
        {
            "code": code,
            "client_id": getattr(settings, "GOOGLE_CLIENT_ID", ""),
            "client_secret": getattr(settings, "GOOGLE_CLIENT_SECRET", ""),
            "redirect_uri": getattr(settings, "GOOGLE_REDIRECT_URI", ""),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _verify_google_credential(credential: str) -> dict:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:
        raise ValueError("Google auth dependency is not installed.") from exc

    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise ValueError("Google client ID is not configured.")

    try:
        payload = id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    except Exception as exc:
        raise ValueError("Google credential verification failed.") from exc

    if not payload.get("email"):
        raise ValueError("Google account email is required.")
    if not payload.get("email_verified", False):
        raise ValueError("Google account email must be verified.")
    return payload


def _make_unique_username(base_value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (base_value or "user")).strip("-_.") or "user"
    candidate = normalized[:150]
    suffix = 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix_str = f"-{suffix}"
        candidate = f"{normalized[: max(1, 150 - len(suffix_str))]}{suffix_str}"
        suffix += 1
    return candidate


def _resolve_google_user(payload: dict) -> tuple[User, bool]:
    email = (payload.get("email") or "").strip().lower()
    matches = User.objects.filter(email__iexact=email).order_by("id")
    if matches.count() > 1:
        raise ValueError("Multiple accounts already use this email address.")

    created = False
    user = matches.first()
    if user is None:
        username_hint = payload.get("email", "").split("@")[0] or payload.get("name") or "user"
        user = User.objects.create_user(
            username=_make_unique_username(username_hint),
            email=email,
            first_name=(payload.get("given_name") or "")[:150],
            last_name=(payload.get("family_name") or "")[:150],
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        created = True
    else:
        changed_fields = []
        if email and user.email != email:
            user.email = email
            changed_fields.append("email")
        first_name = (payload.get("given_name") or "")[:150]
        last_name = (payload.get("family_name") or "")[:150]
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed_fields.append("first_name")
        if last_name and user.last_name != last_name:
            user.last_name = last_name
            changed_fields.append("last_name")
        if changed_fields:
            user.save(update_fields=changed_fields)

    UserProfile.objects.get_or_create(user=user, defaults={"role": "student"})
    _set_google_profile_picture(user, payload.get("picture"))
    return user, created


class AuthProvidersView(APIView):
    """GET /api/v1/auth/providers/ — returns enabled auth provider config for the frontend."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        google_config = _google_auth_public_config()
        return Response(
            {
                "google": {
                    **google_config,
                    "available": google_config["enabled"],
                    "redirect_start_url": "/api/v1/auth/google/redirect/start/" if google_config["redirect_flow_enabled"] else "",
                }
            }
        )


class LoginView(APIView):
    """POST /api/v1/auth/login/ — accepts {username, password}, returns token + user."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username or not password:
            return Response(
                {"error": "Username and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response({"error": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

        token, _ = Token.objects.get_or_create(user=user)
        _set_token_provider(token.key, "password")
        return Response({"token": token.key, "user": _serialize_user_with_provider(user, "password")})


class GoogleLoginView(APIView):
    """POST /api/v1/auth/google/ — accepts Google ID token credential and returns token + user."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        if not _google_auth_enabled():
            return Response(
                {"error": "Google sign-in is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        credential = (request.data.get("credential") or "").strip()
        if not credential:
            return Response(
                {"error": "Google credential is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payload = _verify_google_credential(credential)
            user, created = _resolve_google_user(payload)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        token, _ = Token.objects.get_or_create(user=user)
        _set_token_provider(token.key, "google")
        return Response(
            {
                "token": token.key,
                "user": _serialize_user_with_provider(user, "google"),
                "created": created,
                "provider": "google",
            }
        )


class GoogleRedirectStartView(APIView):
    """GET /api/v1/auth/google/redirect/start/ — returns Google OAuth2 authorization URL."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        if not _google_redirect_enabled():
            return Response(
                {"error": "Google redirect sign-in is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        state = uuid.uuid4().hex
        cache.set(_google_oauth_state_cache_key(state), int(time.time()), timeout=10 * 60)
        return Response({"authorization_url": _google_authorize_url(state), "state": state})


class GoogleRedirectCallbackView(APIView):
    """GET /api/v1/auth/google/redirect/callback/ — exchanges code and logs user in."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        if not _google_redirect_enabled():
            return Response(
                {"error": "Google redirect sign-in is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        oauth_error = (request.query_params.get("error") or "").strip()
        if oauth_error:
            return Response({"error": f"Google authorization failed: {oauth_error}"}, status=status.HTTP_400_BAD_REQUEST)

        state = (request.query_params.get("state") or "").strip()
        code = (request.query_params.get("code") or "").strip()
        if not state or not code:
            return Response({"error": "Missing OAuth callback parameters."}, status=status.HTTP_400_BAD_REQUEST)

        state_key = _google_oauth_state_cache_key(state)
        if cache.get(state_key) is None:
            return Response({"error": "Invalid or expired OAuth state."}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(state_key)

        try:
            token_payload = _exchange_google_oauth_code(code)
            id_token_value = (token_payload.get("id_token") or "").strip()
            if not id_token_value:
                raise ValueError("Google token exchange did not return an ID token.")
            payload = _verify_google_credential(id_token_value)
            user, created = _resolve_google_user(payload)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"error": "Google token exchange failed."}, status=status.HTTP_400_BAD_REQUEST)

        token, _ = Token.objects.get_or_create(user=user)
        _set_token_provider(token.key, "google")

        redirect_success_url = getattr(settings, "GOOGLE_REDIRECT_SUCCESS_URL", "").strip()
        if redirect_success_url:
            fragment = urlencode({"auth_token": token.key, "provider": "google"})
            return HttpResponseRedirect(f"{redirect_success_url}#{fragment}")

        return Response(
            {
                "token": token.key,
                "user": _serialize_user_with_provider(user, "google"),
                "created": created,
                "provider": "google",
            }
        )


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — deletes the current auth token."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        identity = _playback_identity(request)
        cache.set(_logout_epoch_key_for(identity), int(time.time()), timeout=_token_ttl_for_mode("secure_stream"))
        token_key = getattr(request.auth, "key", None)
        _delete_token_provider(token_key)
        try:
            request.auth.delete()
        except Exception:
            pass
        return Response({"status": "logged out"})


class MeView(APIView):
    """GET /api/v1/auth/me/ — returns the current authenticated user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token_key = getattr(request.auth, "key", None)
        provider = _get_token_provider(token_key)
        return Response(_serialize_user_with_provider(request.user, provider))


# ---------------------------------------------------------------------------
# Media serving
# ---------------------------------------------------------------------------

class MediaServeView(APIView):
    """
    GET /api/v1/media/<path:filepath>

    Raw filesystem access.

    - Staff/admin: full debug access to storage files.
    - Student/lesson playback must still use /api/v1/stream/<token>/.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, filepath):
        if ".." in filepath or filepath.startswith("/"):
            raise Http404
        if not _is_staff_user(request.user):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        full_path = Path(storage_root) / filepath
        if not full_path.exists() or not full_path.is_file():
            raise Http404
        content_type, _ = mimetypes.guess_type(str(full_path))
        return _media_file_response(request, full_path, content_type)


class ProjectCoverImageView(APIView):
    """
    GET /api/v1/projects/<project_id>/cover/

    Serves a lesson cover image without exposing raw storage paths.
    - Public access is allowed for published lessons that have a completed render job.
    - Draft/private lesson covers are visible to the owner or staff only.
    """

    permission_classes = [permissions.AllowAny]

    def _can_view_private_cover(self, request, project: Project) -> bool:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if _is_staff_user(user):
            return True
        return bool(project.user_id and int(project.user_id) == int(user.id))

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise Http404

        rel_path = _normalize_rel_storage_path(project.cover_image_processed or project.cover_image_original)
        if not rel_path:
            raise Http404

        if not _is_public_lesson(project) and not self._can_view_private_cover(request, project):
            raise Http404

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        full_path = _resolve_storage_file(storage_root, rel_path)
        if full_path is None:
            raise Http404

        content_type, _ = mimetypes.guess_type(str(full_path))
        response = _media_file_response(request, full_path, content_type)
        response["Cache-Control"] = "public, max-age=300"
        return response


class MediaStreamView(APIView):
    """
    GET /api/v1/stream/<token>/

    Validates a short-lived HMAC token and streams the media file.
    Supports HTTP Range so browsers can seek within the video.
    The raw storage path is never sent to the client.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        requested_type = "video"
        try:
            job_id, file_type, rel_path, grant_id, bind_key = validate_media_token(token)
            requested_type = file_type
        except ValueError as exc:
            logger.warning("Playback token rejected: reason=%s", str(exc))
            return _stream_error_response(file_type=requested_type, status_code=status.HTTP_403_FORBIDDEN, reason="invalid_or_expired_token")

        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return _stream_error_response(file_type=requested_type, status_code=status.HTTP_404_NOT_FOUND, reason="job_not_found")
        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, job.project_id)
        stream_project = getattr(job, "project", None)
        if stream_project:
            protection_mode, mode_debug, lesson_is_public = _resolve_playback_mode_for_project(stream_project, sidecar)
            if not lesson_is_public and not grant_id:
                return _stream_error_response(file_type=requested_type, status_code=status.HTTP_403_FORBIDDEN, reason="draft_preview_requires_grant")
        else:
            protection_mode, mode_debug = _resolve_effective_protection_mode(sidecar)

        if not _check_grant_access(
            request,
            lesson_id=(job.project_id or job_id),
            grant_id=grant_id,
            bind_key=bind_key,
            mode=protection_mode,
            file_type=file_type,
        ):
            return _stream_error_response(file_type=file_type, status_code=status.HTTP_403_FORBIDDEN, reason="grant_invalid")

        risk_score, risk_reasons = _client_risk_signals(
            request,
            grant_id=grant_id,
            file_type=file_type,
            mode=protection_mode,
        )
        if risk_reasons:
            logger.warning(
                "Suspicious playback client signals: lesson=%s mode=%s file_type=%s score=%s reasons=%s",
                job.project_id,
                protection_mode,
                file_type,
                risk_score,
                ",".join(risk_reasons),
            )

        policy = _risk_policy_decision(mode=protection_mode, file_type=file_type, risk_score=risk_score)
        if grant_id and policy in {"shorten_ttl", "fresh_grant_required"}:
            grant_payload = cache.get(_grant_key_for(grant_id)) or {}
            if grant_payload and not grant_payload.get("revoked"):
                _touch_grant_activity(
                    grant_id=grant_id,
                    grant_payload=grant_payload,
                    ttl_seconds=_effective_grant_ttl(_token_ttl_for_mode(protection_mode), risk_score, protection_mode),
                    hidden=False,
                )

        if policy == "revoke_and_deny" and grant_id:
            _revoke_grant(grant_id, reason="high_risk_stream", lesson_id=(job.project_id or job_id), mode=protection_mode)
            return _stream_error_response(file_type=file_type, status_code=status.HTTP_403_FORBIDDEN, reason="high_risk_stream")

        if policy == "fresh_grant_required":
            return _stream_error_response(file_type=file_type, status_code=status.HTTP_409_CONFLICT, reason="fresh_grant_required")

        logger.info("Secure stream request: job_id=%s file_type=%s", job_id, file_type)

        if file_type == "video":
            if protection_mode == "drm_protected":
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_403_FORBIDDEN, reason="mp4_disabled_for_protected")
            if not job.result_url:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="video_missing")
            rel_path = job.result_url.lstrip("/")
        elif file_type == "srt":
            if not job.srt_url:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_200_OK, reason="subtitle_missing")
            rel_path = job.srt_url.lstrip("/")
        elif file_type == "vtt":
            if not job.srt_url:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_200_OK, reason="subtitle_missing")
            rel_path = rel_path.lstrip("/") if rel_path else _subtitle_vtt_rel_path_for_job(job)
            if not rel_path:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_200_OK, reason="subtitle_missing")
            if not rel_path.startswith(f"{job.project_id}/"):
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="resource_outside_project")
        elif file_type in {"hls_manifest", "hls_segment", "hls_key"}:
            if not rel_path:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="resource_missing")
            rel_path = rel_path.lstrip("/")
            if not rel_path.startswith(f"{job.project_id}/"):
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="resource_outside_project")
        elif file_type == "avatar":
            if not rel_path:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="avatar_missing")
            rel_path = rel_path.lstrip("/")
        else:
            return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="unsupported_resource")

        # Path traversal guard
        if ".." in rel_path or rel_path.startswith("/"):
            return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="path_rejected")

        full_path = Path(storage_root) / rel_path
        if not full_path.exists() or not full_path.is_file():
            if file_type in ("srt", "vtt"):
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_200_OK, reason="subtitle_missing")
            raise Http404

        if file_type == "hls_manifest":
            content_type = _content_type_for_resource(file_type)
            manifest_text = full_path.read_text(encoding="utf-8")
            try:
                rewritten_manifest = _rewrite_hls_manifest_with_tokens(
                    manifest_text,
                    request=request,
                    job_id=job.id,
                    manifest_path=full_path,
                    storage_root=Path(storage_root),
                    ttl_seconds=_token_ttl_for_mode(protection_mode),
                    grant_id=grant_id,
                    bind_key=bind_key,
                )
            except Exception:
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="manifest_rewrite_failed")

            response = HttpResponse(rewritten_manifest, content_type=content_type)
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            response["Cross-Origin-Resource-Policy"] = "cross-origin"
            _log_stream_delivery(file_type=file_type, content_type=content_type, status_code=response.status_code, token_authorized=True, note="manifest")
            return response

        if file_type == "srt":
            response = _subtitle_response(full_path)
            _log_stream_delivery(file_type=file_type, content_type=response["Content-Type"], status_code=response.status_code, token_authorized=True, note="subtitle_vtt")
            return response

        content_type = _content_type_for_resource(file_type, full_path)
        response = _media_file_response(request, full_path, content_type)
        response["X-Playback-Protection-Mode"] = protection_mode
        response["X-Playback-Mode-Source"] = mode_debug.get("source", "unknown")
        note = "media"
        if file_type == "hls_key":
            note = "hls_key"
        elif file_type == "hls_segment":
            note = "hls_segment"
        if file_type == "video":
            note = "mp4"
        _log_stream_delivery(file_type=file_type, content_type=content_type, status_code=response.status_code, token_authorized=True, note=note)
        return response


class PlaybackTokenView(APIView):
    """
    GET /api/v1/projects/<project_id>/playback-token/

    Issues short-lived signed tokens for the latest ready video + SRT of a project.
    Published lessons are public. Drafts are available only to the owning
    teacher/publisher or staff for Studio preview.
    The token prevents raw storage paths from ever appearing in the browser.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_lesson_playback(request, project):
            return Response({"error": "Lesson not available."}, status=status.HTTP_404_NOT_FOUND)

        job = project.jobs.filter(status="done").order_by("-created_at").first()
        if not job:
            return Response({"error": "No ready video for this project."}, status=status.HTTP_404_NOT_FOUND)

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)
        protection_mode, mode_debug, _lesson_is_public = _resolve_playback_mode_for_project(project, sidecar)

        allow_mp4_fallback = bool(getattr(settings, "LESSON_PROTECTION_ALLOW_MP4_FALLBACK", True))
        if protection_mode == "drm_protected":
            allow_mp4_fallback = False

        hls_manifest_token = None
        avatar_token = None
        hls_encrypted = False
        asset_id = None
        content_id = None

        hls_payload = sidecar.get("hls") if isinstance(sidecar, dict) else None
        if hls_payload and hls_payload.get("manifest_rel_path"):
            hls_encrypted = bool(hls_payload.get("encrypted"))

        if isinstance(sidecar, dict):
            asset_id = sidecar.get("asset_id")
            content_id = sidecar.get("content_id")

        asset_id = asset_id or _default_asset_id(project.id)
        content_id = content_id or _default_content_id(project.id)

        require_hls_encryption = bool(getattr(settings, "LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM", True))
        require_drm_metadata = bool(getattr(settings, "LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM", True))

        if protection_mode == "drm_protected":
            if not hls_payload or not hls_payload.get("manifest_rel_path"):
                return Response({"error": "DRM-protected lesson requires HLS manifest."}, status=status.HTTP_409_CONFLICT)
            if require_hls_encryption and not hls_encrypted:
                return Response({"error": "DRM-protected lesson requires encrypted HLS."}, status=status.HTTP_409_CONFLICT)
            _systems, _preferred_name, _preferred_system, drm_configured = _resolve_drm_systems(
                asset_id=asset_id,
                content_id=content_id,
                playback_session_id=_playback_session_id(job.id, None),
            )
            if require_drm_metadata and not drm_configured:
                return Response({"error": "DRM-protected lesson requires DRM metadata configuration."}, status=status.HTTP_409_CONFLICT)

        ttl_seconds = _token_ttl_for_mode(protection_mode)
        lesson_id = project.id
        grant_id = None
        bind_key = None
        playback_session_id = None
        session_binding_active = bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True))
        if protection_mode != "public":
            # Studio owners/managers should be able to preview their own lesson
            # without being blocked by learner-focused concurrency guards.
            if not _can_manage_project(request.user, project):
                allowed, deny_reason = _enforce_playback_concurrency(lesson_id, request, protection_mode)
                if not allowed:
                    return Response(
                        {
                            "error": "This lesson is already active in another browser session.",
                            "reason": deny_reason,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
            grant_id, _scope_key = _issue_playback_grant(lesson_id, request, protection_mode, ttl_seconds)
            bind_key = _bind_key_for_request(request) if session_binding_active else None
            playback_session_id = _playback_session_id(job.id, grant_id)
        else:
            session_binding_active = False

        if hls_payload and hls_payload.get("manifest_rel_path"):
            hls_manifest_token = generate_media_token(
                job.id,
                "hls_manifest",
                rel_path=hls_payload["manifest_rel_path"],
                ttl_seconds=ttl_seconds,
                grant_id=grant_id,
                bind_key=bind_key,
            )

        avatar_payload = sidecar.get("avatar") if isinstance(sidecar, dict) else None
        if avatar_payload and avatar_payload.get("track_rel_path") and _avatar_active_for_project(project):
            avatar_token = generate_media_token(
                job.id,
                "avatar",
                rel_path=str(avatar_payload.get("track_rel_path")),
                ttl_seconds=ttl_seconds,
                grant_id=grant_id,
                bind_key=bind_key,
            )

        video_token = generate_media_token(
            job.id,
            "video",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if allow_mp4_fallback else ""
        srt_token = generate_media_token(
            job.id,
            "srt",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if job.srt_url else None
        vtt_token = _generate_vtt_media_token_for_job(
            job,
            storage_root=getattr(settings, "STORAGE_ROOT", ""),
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if job.srt_url else None
        logger.info(
            "Playback token issued: project_id=%s job_id=%s mode=%s has_grant=%s has_hls=%s mp4_fallback=%s",
            project.id,
            job.id,
            protection_mode,
            bool(grant_id),
            bool(hls_manifest_token),
            bool(video_token),
        )
        payload = _playback_payload(
            request,
            project,
            job,
            video_token,
            srt_token,
            vtt_token=vtt_token,
            hls_manifest_token=hls_manifest_token,
            hls_encrypted=hls_encrypted,
            asset_id=asset_id,
            content_id=content_id,
            protection_mode=protection_mode,
            mode_debug=mode_debug,
            allow_mp4_fallback=allow_mp4_fallback,
            playback_session_id=playback_session_id,
            session_binding_active=session_binding_active,
            avatar_token=avatar_token,
            avatar_overlay_defaults=_avatar_overlay_defaults_for_project(project),
        )
        payload["transcript_pages"] = _project_transcript_timeline(project)
        return Response(payload)


class PlaybackSessionHeartbeatView(APIView):
    """
    POST /api/v1/projects/<project_id>/playback-session/heartbeat/

    Renews active playback grant while player is still in use.
    Keeps normal viewing smooth while allowing server-side inactivity/abuse controls.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_lesson_playback(request, project):
            return Response({"error": "Lesson not available."}, status=status.HTTP_404_NOT_FOUND)

        job = project.jobs.filter(status="done").order_by("-created_at").first()
        if not job:
            return Response({"error": "No ready video for this project."}, status=status.HTTP_404_NOT_FOUND)

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)
        protection_mode, mode_debug, _lesson_is_public = _resolve_playback_mode_for_project(project, sidecar)

        if protection_mode == "public":
            return Response(
                {
                    "active": True,
                    "revoked": False,
                    "reason": "public_mode_no_grant_required",
                    "renewed": False,
                    "protection_mode": protection_mode,
                    "mode_source": mode_debug.get("source", "unknown"),
                }
            )

        identity = _playback_identity(request)
        scope_key = _scope_key_for(project.id, identity, protection_mode)
        grant_id = cache.get(scope_key)
        if not grant_id:
            return Response({"active": False, "revoked": True, "reason": "missing_grant"}, status=status.HTTP_409_CONFLICT)

        grant_payload = cache.get(_grant_key_for(grant_id)) or {}
        if grant_payload.get("revoked"):
            return Response({"active": False, "revoked": True, "reason": "revoked"}, status=status.HTTP_409_CONFLICT)

        requested_bind = _bind_key_for_request(request) if bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True)) else None
        if grant_payload.get("bind_key") and grant_payload.get("bind_key") != requested_bind:
            return Response({"active": False, "revoked": True, "reason": "superseded"}, status=status.HTTP_409_CONFLICT)

        ttl_seconds = _token_ttl_for_mode(protection_mode)
        hidden = str(request.data.get("visibility", "visible") or "visible").lower() != "visible"

        now = int(time.time())
        hidden_since = grant_payload.get("hidden_since")
        last_seen = int(grant_payload.get("last_seen_at") or grant_payload.get("issued_at") or now)
        if now - last_seen > _playback_inactivity_ttl():
            _revoke_grant(grant_id, reason="inactive", lesson_id=project.id, mode=protection_mode)
            return Response({"active": False, "revoked": True, "reason": "inactive"}, status=status.HTTP_409_CONFLICT)
        if hidden_since and (now - int(hidden_since) > _playback_hidden_grace_ttl()):
            _revoke_grant(grant_id, reason="hidden_too_long", lesson_id=project.id, mode=protection_mode)
            return Response({"active": False, "revoked": True, "reason": "hidden_too_long"}, status=status.HTTP_409_CONFLICT)

        hb_score, hb_reasons = _client_risk_signals(
            request,
            grant_id=grant_id,
            file_type="heartbeat",
            mode=protection_mode,
        )
        if hb_reasons and hb_score >= _risk_high_threshold():
            _revoke_grant(grant_id, reason="high_risk_heartbeat", lesson_id=project.id, mode=protection_mode)
            return Response({"active": False, "revoked": True, "reason": "risk_blocked"}, status=status.HTTP_409_CONFLICT)

        _touch_grant_activity(
            grant_id=grant_id,
            grant_payload=grant_payload,
            ttl_seconds=_effective_grant_ttl(ttl_seconds, hb_score, protection_mode),
            hidden=hidden,
        )
        logger.info("Playback grant renewed: lesson=%s mode=%s", project.id, protection_mode)

        return Response(
            {
                "active": True,
                "revoked": False,
                "grant_id": grant_id,
                "session_expires_in": ttl_seconds,
                "inactivity_ttl_seconds": _playback_inactivity_ttl(),
                "hidden_grace_seconds": _playback_hidden_grace_ttl(),
                "renewed": True,
                "renewed_at": int(time.time()),
            }
        )


# ---------------------------------------------------------------------------
# Router-based ViewSets
# ---------------------------------------------------------------------------

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.select_related("profile", "voice_profile").all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if _is_staff_user(self.request.user):
            return qs
        return qs.filter(pk=self.request.user.pk)


class SlideViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Slide.objects.select_related("project").all()
    serializer_class = SlideSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if not _is_staff_user(self.request.user):
            if not _is_verified_teacher(self.request.user):
                return qs.none()
            qs = qs.filter(project__user=self.request.user)
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs


class JobViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Job.objects.select_related("project").all()
    serializer_class = JobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        if _is_staff_user(self.request.user):
            return qs
        if _is_verified_teacher(self.request.user):
            return qs.filter(project__user=self.request.user)
        return qs.none()


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _resolve_user(request, user_id_param=None):
    """Return authenticated user, or look up by user_id param, or None."""
    if request.user and request.user.is_authenticated:
        return request.user
    if user_id_param:
        try:
            return User.objects.get(pk=user_id_param)
        except User.DoesNotExist:
            pass
    return None


def _get_voice_id(user):
    """Return the stored voice_id for *user*, or empty string."""
    if user is None:
        return ""
    try:
        return user.voice_profile.voice_id or ""
    except Exception:
        return ""


def _is_verified_teacher(user: User | None) -> bool:
    if user is None:
        return False
    if user.is_staff or user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.role in {"teacher", "publisher"})


def _is_authenticated_user(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def _is_staff_user(user) -> bool:
    return bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


def _can_manage_project(user, project: Project) -> bool:
    """Teacher pipeline access: staff/admin or the verified teacher owner."""
    if not _is_authenticated_user(user):
        return False
    if _is_staff_user(user):
        return True
    return bool(project.user_id and int(project.user_id) == int(user.id) and _is_verified_teacher(user))


def _project_tts_settings(project: Project) -> dict[str, Any]:
    return canonical_project_tts_settings(getattr(project, "tts_settings", None))


def _normalize_render_profile(raw_value: Any, *, default: str = "balanced") -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return default
    if value not in _RENDER_PROFILE_CHOICES:
        raise ValueError("render_profile must be one of: fast, balanced, quality.")
    return value


def _apply_render_profile_to_avatar_options(avatar_options: dict[str, Any], render_profile: str) -> dict[str, Any]:
    options = dict(avatar_options or {})
    if not options:
        return options
    if "quality_preset" not in options or not options.get("quality_preset"):
        if render_profile == "fast":
            options["quality_preset"] = "low"
        elif render_profile == "quality":
            options["quality_preset"] = "high"
        else:
            options["quality_preset"] = "medium"
    return options


def _is_public_lesson(project: Project) -> bool:
    if hasattr(project, "is_published") and not bool(getattr(project, "is_published", False)):
        return False
    try:
        return bool(project.jobs.filter(status="done").exists())
    except AttributeError:
        latest_job = project.jobs.filter(status="done").order_by("-created_at").first()
        return bool(latest_job)


def _can_access_lesson_playback(request, project: Project) -> bool:
    if _is_public_lesson(project):
        return True
    return _can_manage_project(getattr(request, "user", None), project)


def _composite_engine_configured() -> bool:
    return bool(
        str(os.environ.get("AVATAR_LIVEPORTRAIT_CMD", "")).strip()
        and str(os.environ.get("AVATAR_MUSETALK_CMD", "")).strip()
    )


def _composite_lesson_enabled() -> bool:
    return str(os.environ.get("AVATAR_ENABLE_COMPOSITE_LESSON", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _composite_fallback_allowed() -> bool:
    explicit_fallback_flag = str(os.environ.get("AVATAR_ENABLE_COMPOSITE_FALLBACK", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not explicit_fallback_flag:
        return False
    return bool(_composite_engine_configured() and _composite_lesson_enabled())


def _normalize_avatar_engine(value: str | None) -> str:
    """Backward-compatible local wrapper for shared engine normalization."""
    return normalize_avatar_engine(value)


def _normalize_preview_stage(value: str | None, *, job_status: str | None = None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"queued", "rendering", "warning", "ready", "failed", "deleted"}:
        return raw
    if raw in {"settings_saved", "pending"}:
        return "queued"
    if raw in {"tts_pending", "tts_ready", "render_pending", "running", "processing"}:
        return "rendering"
    if raw in {"done", "render_ready"}:
        return "ready"
    if raw in {
        "error",
        "render_failed",
        "tts_failed",
        "setup_failed",
        "preflight_failed",
        "liveportrait_failed",
        "musetalk_failed",
        "validation_failed",
    }:
        return "failed"

    status = str(job_status or "").strip().lower()
    if status == "pending":
        return "queued"
    if status == "running":
        return "rendering"
    if status == "done":
        return "ready"
    if status == "failed":
        return "failed"
    return raw or "idle"


def _extract_preview_error_code(error_text: str | None) -> str:
    message = str(error_text or "").strip()
    if not message:
        return ""
    lowered = message.lower()
    phrase_codes = [
        ("liveportrait_warning", "liveportrait_warning"),
        ("liveportrait_frame_drift", "liveportrait_frame_drift"),
        ("setup_not_prepared", "setup_not_prepared"),
        ("teacher avatar is not prepared", "setup_not_prepared"),
        ("readiness_check_failed", "setup_not_prepared"),
        ("voice profile is missing", "missing_voice_profile"),
        ("invalid_engine_config", "invalid_engine_config"),
        ("crop too tight", "crop_too_tight"),
        ("headroom", "crop_too_tight"),
        ("less torso", "crop_too_tight"),
        ("preflight rejected all sources", "preflight_rejected"),
        ("liveportrait stage output rejected", "liveportrait_failed"),
        ("liveportrait_precheck_failed", "liveportrait_precheck_failed"),
        ("musetalk stage failed", "musetalk_failed"),
        ("strict acceptance gate", "validation_failed"),
        ("strict validation", "validation_failed"),
    ]
    for phrase, code in phrase_codes:
        if phrase in lowered:
            return code
    token = message.split()[0]
    token = token.rstrip(".,;:")
    if ":" in token:
        token = token.split(":", 1)[0]
    if token and token.replace("_", "").replace("-", "").isalnum():
        return token.lower()
    return ""


def _avatar_preview_readiness(profile: UserProfile, voice_profile: VoiceProfile | None, *, storage_root: Path) -> dict[str, Any]:
    return avatar_preview_readiness(profile, voice_profile, storage_root=storage_root)


def _preview_error_code_for_status(status_value: str, error_text: str | None) -> str:
    stage = _normalize_preview_stage(status_value)
    if stage == "warning":
        return _extract_preview_error_code(error_text)
    if stage == "failed":
        lowered = str(error_text or "").lower()
        if "setup_not_prepared" in lowered:
            return "setup_not_prepared"
        if any(token in lowered for token in ["crop", "headroom", "torso", "tight"]):
            return "crop_too_tight"
        return _extract_preview_error_code(error_text) or "render_failed"
    return _extract_preview_error_code(error_text)


def _resolve_avatar_options_for_project(project: Project, request) -> dict:
    teacher = project.user
    if teacher is None:
        return {"enabled": False}

    profile = getattr(teacher, "profile", None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=teacher, defaults={"role": "teacher"})

    request_override = request.data.get("avatar_enabled")
    if request_override is None:
        if project.avatar_enabled_override is None:
            avatar_enabled = bool(profile.avatar_enabled)
        else:
            avatar_enabled = bool(project.avatar_enabled_override)
    else:
        avatar_enabled = str(request_override).strip().lower() in {"1", "true", "yes", "on"}

    storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
    source_state = stored_avatar_source_state(profile, storage_root=storage_root)
    if (profile.avatar_image_processed or profile.avatar_image_original or profile.avatar_video_original) and not bool(
        source_state.get("validation_current")
    ):
        try:
            source_state = refresh_avatar_source_validation(profile, storage_root=storage_root, persist=True)
        except Exception:
            logger.warning("Avatar source validation refresh failed for project=%s teacher=%s", project.id, teacher.id, exc_info=True)
            source_state = stored_avatar_source_state(profile, storage_root=storage_root)

    has_image = bool(profile.avatar_image_processed)
    has_video = bool(profile.avatar_video_processed or profile.avatar_video_original)
    preferred_ref = str(profile.avatar_reference_type or "image").lower()
    if preferred_ref not in {"image", "video"}:
        preferred_ref = "image"
    resolved_ref = "video" if (preferred_ref == "video" and has_video) else "image"

    is_ready = bool(profile.avatar_consent_confirmed and (has_image or has_video))
    selected_engine = _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary or os.environ.get("AVATAR_ENGINE"))
    composite_ready = _composite_engine_configured()
    engine_resolution = "requested"
    disable_reason = ""

    if selected_engine == "liveportrait+musetalk" and not composite_ready:
        engine_resolution = "composite_unconfigured"
        disable_reason = "liveportrait+musetalk requested but composite runtime is not configured"

    lesson_engine = selected_engine
    composite_fallback_allowed = _composite_fallback_allowed()

    return {
        "enabled": bool(avatar_enabled and is_ready and not disable_reason),
        "teacher_id": int(teacher.id),
        "source_image_rel_path": profile.avatar_image_processed or profile.avatar_image_original,
        "source_video_rel_path": profile.avatar_video_processed or profile.avatar_video_original,
        "avatar_reference_type": resolved_ref,
        "motion_preset": profile.avatar_motion_preset or "natural",
        "lipsync_engine": lesson_engine,
        "quality_preset": profile.avatar_quality_preset or "high",
        "engine_primary": selected_engine,
        "engine_fallback": "",
        "model_version": profile.avatar_model_version or f"{selected_engine}:v1",
        "avatar_version_hash": profile.avatar_version_hash or "",
        "avatar_source_valid": bool(source_state.get("valid")),
        "avatar_source_validation_error": str(source_state.get("error") or profile.avatar_source_validation_error or ""),
        "avatar_source_hash": str(source_state.get("source_hash") or profile.avatar_source_hash or ""),
        "avatar_preview_stale": bool(source_state.get("preview_stale")),
        "avatar_preview_source_hash": str(source_state.get("preview_source_hash") or profile.avatar_preview_source_hash or ""),
        "composite_configured": composite_ready,
        "composite_lesson_enabled": _composite_lesson_enabled(),
        "composite_fallback_allowed": composite_fallback_allowed,
        "engine_resolution": engine_resolution,
        "disabled_reason": disable_reason,
    }


def _avatar_overlay_defaults_for_project(project: Project) -> dict:
    project_user = getattr(project, "user", None)
    teacher_profile = getattr(project_user, "profile", None) if project_user else None
    return {
        "position": getattr(teacher_profile, "avatar_overlay_default_position", "top-right") or "top-right",
        "size": getattr(teacher_profile, "avatar_overlay_size", "medium") or "medium",
        "visible": bool(getattr(teacher_profile, "avatar_overlay_visible", True)),
    }


def _avatar_active_for_project(project: Project) -> bool:
    project_user = getattr(project, "user", None)
    teacher_profile = getattr(project_user, "profile", None) if project_user else None
    profile_enabled = bool(
        teacher_profile
        and teacher_profile.avatar_enabled
        and teacher_profile.avatar_consent_confirmed
        and bool(getattr(teacher_profile, "avatar_source_valid", False))
        and not bool(getattr(teacher_profile, "avatar_preview_stale", False))
        and (teacher_profile.avatar_image_processed or teacher_profile.avatar_video_original)
    )
    if project.avatar_enabled_override is None:
        return profile_enabled
    return bool(project.avatar_enabled_override and profile_enabled)


def _resolve_category_from_upload(request) -> Category | None:
    """
    Resolve optional category from upload payload.

    Accepted fields:
      - category_id: existing category primary key
      - category / category_name: existing or new category name
    """
    category_id = request.data.get("category_id")
    category_name = (request.data.get("category") or request.data.get("category_name") or "").strip()

    if category_id not in (None, ""):
        try:
            return Category.objects.get(pk=int(category_id))
        except (ValueError, TypeError):
            raise ValueError("category_id must be an integer")
        except Category.DoesNotExist:
            raise ValueError("category_id does not exist")

    if category_name:
        if len(category_name) > 200:
            raise ValueError("category name must be 200 characters or less")
        existing = Category.objects.filter(name__iexact=category_name).first()
        if existing:
            return existing
        return Category.objects.create(name=category_name)

    return None


def _normalize_rel_storage_path(raw_path: str) -> str:
    rel_path = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel_path:
        return ""
    if rel_path == ".." or rel_path.startswith("../") or "/../" in rel_path:
        return ""
    return rel_path


def _write_uploaded_file(upload, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with open(destination_path, "wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)


def _validate_cover_upload(cover_file) -> str:
    ext = Path(str(getattr(cover_file, "name", ""))).suffix.lower()
    if ext not in _ALLOWED_COVER_EXTENSIONS:
        raise ValueError(
            f"Unsupported cover type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_COVER_EXTENSIONS))}"
        )
    if int(getattr(cover_file, "size", 0) or 0) > _MAX_COVER_BYTES:
        raise ValueError("Cover image exceeds the 10 MB size limit.")
    return ext


def _resolve_storage_file(storage_root: Path, rel_path: str) -> Path | None:
    safe_rel_path = _normalize_rel_storage_path(rel_path)
    if not safe_rel_path:
        return None

    full_path = (storage_root / safe_rel_path).resolve()
    try:
        full_path.relative_to(storage_root.resolve())
    except ValueError:
        return None

    if not full_path.exists() or not full_path.is_file():
        return None
    return full_path


def _normalize_request_id(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "", value)[:120]
    return cleaned


def _client_request_id(request) -> str:
    data_value = ""
    try:
        if hasattr(request, "data"):
            data_value = request.data.get("request_id", "")
    except Exception:
        data_value = ""
    header_value = request.headers.get("Idempotency-Key") or request.headers.get("X-Request-Id") or ""
    return _normalize_request_id(data_value or header_value)


def _idempotency_cache_ttl_seconds() -> int:
    try:
        return max(60, int(getattr(settings, "IDEMPOTENCY_CACHE_TTL_SECONDS", 24 * 60 * 60)))
    except Exception:
        return 24 * 60 * 60


def _idempotency_cache_key(*, project_id: int, job_type: str, request_id: str) -> str:
    return f"idem:job:{int(project_id)}:{str(job_type or '').strip()}:{_normalize_request_id(request_id)}"


def _remember_idempotent_job(project: Project, job_type: str, request_id: str, job_id: int) -> None:
    safe_request_id = _normalize_request_id(request_id)
    if not safe_request_id:
        return
    cache.set(
        _idempotency_cache_key(project_id=int(project.id), job_type=job_type, request_id=safe_request_id),
        int(job_id),
        timeout=_idempotency_cache_ttl_seconds(),
    )


def _find_existing_idempotent_job(project: Project, job_type: str, request_id: str) -> Job | None:
    safe_request_id = _normalize_request_id(request_id)
    if not safe_request_id:
        return None
    cached_job_id = cache.get(
        _idempotency_cache_key(project_id=int(project.id), job_type=job_type, request_id=safe_request_id)
    )
    if cached_job_id:
        cached_job = Job.objects.filter(id=int(cached_job_id), project=project, job_type=job_type).first()
        if cached_job is not None:
            return cached_job
    return (
        Job.objects.filter(project=project, job_type=job_type, request_id=safe_request_id)
        .order_by("-created_at")
        .first()
    )


# ---------------------------------------------------------------------------
# Pipeline views
# ---------------------------------------------------------------------------

class ProjectUploadView(APIView):
    """
    POST /api/v1/projects/ — upload a lesson file (PPTX/PDF/DOCX/TXT).
    GET  /api/v1/projects/ — list projects (own projects if authenticated).
    """
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only verified teacher accounts can access projects."}, status=status.HTTP_403_FORBIDDEN)
        if _is_staff_user(request.user):
            projects = Project.objects.all().order_by("-created_at")
        else:
            projects = Project.objects.filter(user=request.user).order_by("-created_at")
        return Response(ProjectSerializer(projects, many=True, context={"request": request}).data)

    def post(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only verified teacher accounts can upload projects."}, status=status.HTTP_403_FORBIDDEN)

        if "tts_settings" in request.data:
            return Response(
                {
                    "error": (
                        "tts_settings must be updated with PATCH /api/v1/projects/<id>/ after upload; "
                        "it is not applied during initial upload in Phase 2."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Accept 'lesson_file' (preferred) or legacy 'pptx_file'
        lesson_file = request.FILES.get("lesson_file") or request.FILES.get("pptx_file")
        if lesson_file is None:
            return Response(
                {"error": "lesson_file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ext = Path(lesson_file.name).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            return Response(
                {"error": f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if lesson_file.size > _MAX_LESSON_BYTES:
            return Response(
                {"error": "File exceeds the 100 MB size limit."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cover_file = request.FILES.get("cover_file")
        cover_ext = ""
        if cover_file is not None:
            try:
                cover_ext = _validate_cover_upload(cover_file)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        title = request.data.get("title") or lesson_file.name
        lang_hint = request.data.get("lang_hint", "auto")
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            return Response({"error": "pause_sec must be a number."}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        request_id = _client_request_id(request)
        whiteboard_mode_all = str(request.data.get("whiteboard_mode_all", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        try:
            category = _resolve_category_from_upload(request)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        voice_id = _get_voice_id(user)

        avatar_enabled_override = None
        if "avatar_enabled" in request.data:
            avatar_enabled_override = str(request.data.get("avatar_enabled", "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        try:
            render_profile = _normalize_render_profile(request.data.get("render_profile"), default="balanced")
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        project = Project.objects.create(
            title=title,
            user=user,
            category=category,
            avatar_enabled_override=avatar_enabled_override,
            render_profile=render_profile,
        )
        avatar_options = _resolve_avatar_options_for_project(project, request)
        avatar_options = _apply_render_profile_to_avatar_options(avatar_options, render_profile)

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        upload_dir = storage_root / "uploads" / str(project.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved_path = upload_dir / f"lesson{ext}"
        _write_uploaded_file(lesson_file, saved_path)

        if cover_file is not None:
            saved_cover_path = upload_dir / f"cover{cover_ext}"
            _write_uploaded_file(cover_file, saved_cover_path)
            cover_rel_path = str(saved_cover_path.relative_to(storage_root)).replace("\\", "/")
            project.cover_image_original = cover_rel_path
            project.cover_image_processed = cover_rel_path
            project.save(update_fields=["cover_image_original", "cover_image_processed"])

        existing_job = _find_existing_idempotent_job(project, "video_export", request_id)
        if existing_job is not None:
            response = Response(JobSerializer(existing_job).data, status=status.HTTP_200_OK)
            response["X-Idempotent-Replay"] = "1"
            return response

        try:
            job = Job.objects.create(
                project=project,
                job_type="video_export",
                status="pending",
                request_id=request_id,
            )
        except IntegrityError:
            existing_job = _find_existing_idempotent_job(project, "video_export", request_id)
            if existing_job is not None:
                response = Response(JobSerializer(existing_job).data, status=status.HTTP_200_OK)
                response["X-Idempotent-Replay"] = "1"
                return response
            raise
        _remember_idempotent_job(project, "video_export", request_id, int(job.id))
        task_args = [
            str(project.id),
            str(saved_path),
            voice_id,
            pause_sec,
            lang_hint,
            "service",
            whiteboard_mode_all,
            avatar_options,
            None,
            _project_tts_settings(project),
            render_profile,
        ]
        target_queue = _queue_for_pipeline(avatar_options, render_profile)
        admission_profile = "avatar" if bool((avatar_options or {}).get("enabled")) else render_profile
        admitted, admission_payload = _admission_guard_for_render_profile(admission_profile, target_queue)
        if not admitted:
            return Response(admission_payload, status=status.HTTP_429_TOO_MANY_REQUESTS)
        async_result = _dispatch_celery_task(
            _PROCESS_PROJECT_RENDER_TASK,
            args=task_args,
            queue=target_queue,
        )
        job.celery_task_id = async_result.id
        job.save(update_fields=["celery_task_id"])
        payload = JobSerializer(job).data
        payload["queue"] = target_queue
        payload["estimated_wait_seconds"] = _estimate_queue_wait_seconds(
            target_queue,
            render_profile,
            avatar_enabled=bool((avatar_options or {}).get("enabled")),
        )
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class ProjectDetailView(APIView):
    """GET / PATCH / DELETE /api/v1/projects/<project_id>/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        return Response(ProjectSerializer(project, context={"request": request}).data)

    def delete(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request, project_id):
        try:
            project = Project.objects.select_related("category").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        has_category_id = "category_id" in request.data
        has_category_name = "category_name" in request.data or "category" in request.data
        has_avatar_enabled = "avatar_enabled" in request.data
        has_is_published = "is_published" in request.data
        has_tts_settings = "tts_settings" in request.data
        has_render_profile = "render_profile" in request.data
        if not has_category_id and not has_category_name and not has_avatar_enabled and not has_is_published and not has_tts_settings and not has_render_profile:
            return Response(
                {"error": "category_id, category_name, avatar_enabled, is_published, tts_settings, or render_profile is required for updates."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updates: dict[str, Any] = {}
        update_fields: list[str] = []
        category_name_to_apply: str | None = None

        if has_tts_settings:
            try:
                updates["tts_settings"] = merge_project_tts_settings_patch(
                    project.tts_settings,
                    request.data.get("tts_settings"),
                )
            except ValidationError as exc:
                return Response(
                    {"error": "Invalid tts_settings.", "details": exc.detail},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            update_fields.append("tts_settings")

        if has_avatar_enabled:
            raw = str(request.data.get("avatar_enabled", "")).strip().lower()
            if raw in {"", "null"}:
                updates["avatar_enabled_override"] = None
            else:
                updates["avatar_enabled_override"] = raw in {"1", "true", "yes", "on"}
            update_fields.append("avatar_enabled_override")

        if has_is_published:
            raw = str(request.data.get("is_published", "")).strip().lower()
            if raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                return Response({"error": "is_published must be a boolean."}, status=status.HTTP_400_BAD_REQUEST)
            updates["is_published"] = raw in {"1", "true", "yes", "on"}
            update_fields.append("is_published")

        if has_render_profile:
            try:
                updates["render_profile"] = _normalize_render_profile(request.data.get("render_profile"))
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            update_fields.append("render_profile")

        if has_category_id or has_category_name:
            category_name = (request.data.get("category_name") or request.data.get("category") or "").strip()
            if category_name:
                if len(category_name) > 200:
                    return Response({"error": "category name must be 200 characters or less"}, status=status.HTTP_400_BAD_REQUEST)
                category_name_to_apply = category_name
                update_fields.append("category")
            else:
                raw_category_id = request.data.get("category_id")
                if raw_category_id in (None, "", "null"):
                    updates["category"] = None
                    update_fields.append("category")
                else:
                    try:
                        category_id = int(raw_category_id)
                    except (TypeError, ValueError):
                        return Response({"error": "category_id must be an integer or null."}, status=status.HTTP_400_BAD_REQUEST)

                    try:
                        updates["category"] = Category.objects.get(pk=category_id)
                    except Category.DoesNotExist:
                        return Response({"error": "Category not found."}, status=status.HTTP_404_NOT_FOUND)
                    update_fields.append("category")

        with transaction.atomic():
            if category_name_to_apply is not None:
                category = Category.objects.filter(name__iexact=category_name_to_apply).first()
                if category is None:
                    category = Category.objects.create(name=category_name_to_apply)
                updates["category"] = category

            for field_name, value in updates.items():
                setattr(project, field_name, value)
            project.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])
        return Response(ProjectSerializer(project, context={"request": request}).data)


def _queue_transcript_rerender(
    *,
    project: Project,
    request,
    changed_page_keys: list[str] | set[str],
    pause_sec: float,
    lang_hint: str,
    full_rerender: bool = False,
) -> dict | None:
    voice_id = _get_voice_id(project.user)
    storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
    upload_dir = Path(storage_root) / "uploads" / str(project.id)
    lesson_files = sorted(upload_dir.glob("lesson.*")) if upload_dir.exists() else []
    if not lesson_files:
        return None

    saved_path = str(lesson_files[0])
    request_id = _client_request_id(request)
    existing_job = _find_existing_idempotent_job(project, "video_export", request_id)
    if existing_job is not None:
        return JobSerializer(existing_job).data

    try:
        job = Job.objects.create(
            project=project,
            job_type="video_export",
            status="pending",
            request_id=request_id,
        )
    except IntegrityError:
        existing_job = _find_existing_idempotent_job(project, "video_export", request_id)
        if existing_job is not None:
            return JobSerializer(existing_job).data
        raise
    _remember_idempotent_job(project, "video_export", request_id, int(job.id))
    avatar_options = _resolve_avatar_options_for_project(project, request)
    avatar_options = _apply_render_profile_to_avatar_options(avatar_options, project.render_profile)
    rerender_keys = [] if full_rerender else sorted({str(key) for key in changed_page_keys if str(key)})
    task_args = [
        str(project.id),
        saved_path,
        voice_id,
        pause_sec,
        lang_hint,
        "service",
        False,
        avatar_options,
        rerender_keys,
        _project_tts_settings(project),
        project.render_profile,
    ]
    target_queue = _queue_for_pipeline(avatar_options, project.render_profile)
    admitted, admission_payload = _admission_guard_for_render_profile(project.render_profile, target_queue)
    if not admitted:
        return admission_payload
    async_result = _dispatch_celery_task(
        _PROCESS_PROJECT_RENDER_TASK,
        args=task_args,
        queue=target_queue,
    )
    job.celery_task_id = async_result.id
    job.save(update_fields=["celery_task_id"])
    payload = JobSerializer(job).data
    payload["queue"] = target_queue
    payload["estimated_wait_seconds"] = _estimate_queue_wait_seconds(
        target_queue,
        project.render_profile,
        avatar_enabled=bool((avatar_options or {}).get("enabled")),
    )
    return payload


class TranscriptActionError(ValueError):
    """Validation error for transcript structural actions."""


def _coerce_page_id(value: Any, field_name: str = "page_id") -> int:
    try:
        page_id = int(value)
    except (TypeError, ValueError):
        raise TranscriptActionError(f"{field_name} is required.") from None
    if page_id <= 0:
        raise TranscriptActionError(f"{field_name} is required.")
    return page_id


def _get_project_page(project: Project, page_id: Any, *, active: bool | None = True, field_name: str = "page_id") -> TranscriptPage:
    coerced_id = _coerce_page_id(page_id, field_name)
    qs = project.transcript_pages.filter(id=coerced_id)
    if active is True:
        qs = qs.filter(is_active=True)
    elif active is False:
        qs = qs.filter(is_active=False)
    page = qs.first()
    if page is None:
        state = "active " if active is True else "deleted " if active is False else ""
        raise TranscriptActionError(f"{field_name} must reference a {state}page in this project.")
    return page


def _split_transcript_page(project: Project, payload: dict) -> list[str]:
    page = _get_project_page(project, payload.get("page_id"), active=True)
    parts_payload = payload.get("parts")
    if not isinstance(parts_payload, list) or len(parts_payload) < 2:
        raise TranscriptActionError("parts must contain at least two transcript parts.")
    if len(parts_payload) > 20:
        raise TranscriptActionError("parts may not contain more than 20 entries.")

    parts: list[str] = []
    for item in parts_payload:
        if not isinstance(item, dict):
            raise TranscriptActionError("each part must be an object.")
        parts.append(str(item.get("narration_text") or ""))
    if not any(part.strip() for part in parts):
        raise TranscriptActionError("at least one split part must contain narration text.")

    active_pages = list(_active_transcript_pages(project))
    try:
        insert_at = next(idx for idx, candidate in enumerate(active_pages) if candidate.id == page.id)
    except StopIteration:
        raise TranscriptActionError("page_id must reference an active page in this project.") from None

    existing_keys = set(project.transcript_pages.values_list("page_key", flat=True))
    _set_page_narration_artifacts(page, parts[0])
    page.save(update_fields=["narration_text", "rich_text_html", "editor_document", "subtitle_chunks", "updated_at"])

    created_pages: list[TranscriptPage] = []
    for offset, part in enumerate(parts[1:], start=1):
        new_page = TranscriptPage(
            project=project,
            order=page.order + offset,
            source_slide_index=page.source_slide_index,
            split_index=page.split_index + offset,
            page_key=_unique_split_page_key(project, page.page_key, existing_keys, offset),
            original_text="",
            whiteboard_mode=page.whiteboard_mode,
            is_active=True,
            deleted_at=None,
        )
        _set_page_narration_artifacts(new_page, part)
        new_page.save()
        created_pages.append(new_page)

    ordered_pages = active_pages[: insert_at + 1] + created_pages + active_pages[insert_at + 1 :]
    _normalize_active_transcript_order(project, ordered_pages)
    return [str(page.page_key), *[str(created.page_key) for created in created_pages]]


def _merge_transcript_pages(project: Project, payload: dict, *, direction: str) -> list[str]:
    page = _get_project_page(project, payload.get("page_id"), active=True)
    separator, separator_error = _safe_merge_separator(payload.get("separator"))
    if separator_error:
        raise TranscriptActionError(separator_error)

    active_pages = list(_active_transcript_pages(project))
    current_index = next((idx for idx, candidate in enumerate(active_pages) if candidate.id == page.id), -1)
    if current_index < 0:
        raise TranscriptActionError("page_id must reference an active page in this project.")

    if direction == "next":
        if current_index >= len(active_pages) - 1:
            raise TranscriptActionError("page has no active next page to merge.")
        survivor = page
        merged_away = active_pages[current_index + 1]
    else:
        if current_index <= 0:
            raise TranscriptActionError("page has no active previous page to merge.")
        survivor = active_pages[current_index - 1]
        merged_away = page

    merged_narration = _combine_text_with_separator(survivor.narration_text, merged_away.narration_text, separator)
    merged_original = _combine_text_with_separator(survivor.original_text, merged_away.original_text, separator)
    _set_page_narration_artifacts(survivor, merged_narration)
    survivor.original_text = merged_original
    survivor.save(
        update_fields=[
            "original_text",
            "narration_text",
            "rich_text_html",
            "editor_document",
            "subtitle_chunks",
            "updated_at",
        ]
    )

    merged_away.is_active = False
    merged_away.deleted_at = timezone.now()
    merged_away.save(update_fields=["is_active", "deleted_at", "updated_at"])

    _normalize_active_transcript_order(project, [candidate for candidate in active_pages if candidate.id != merged_away.id])
    return [str(survivor.page_key)]


def _reorder_transcript_pages(project: Project, payload: dict) -> list[str]:
    page_ids = payload.get("page_ids")
    if not isinstance(page_ids, list) or not page_ids:
        raise TranscriptActionError("page_ids must be a non-empty list.")
    try:
        normalized_ids = [int(item) for item in page_ids]
    except (TypeError, ValueError):
        raise TranscriptActionError("page_ids must contain only page IDs.") from None
    if len(normalized_ids) != len(set(normalized_ids)):
        raise TranscriptActionError("page_ids must not contain duplicates.")

    active_pages = list(_active_transcript_pages(project))
    active_by_id = {page.id: page for page in active_pages}
    if set(normalized_ids) != set(active_by_id):
        raise TranscriptActionError("page_ids must contain every active page exactly once.")

    ordered_pages = [active_by_id[page_id] for page_id in normalized_ids]
    _normalize_active_transcript_order(project, ordered_pages)
    # Reorder is sequence-only. Returning an empty changed set makes trigger_rerender use a full same-project render.
    return []


def _delete_transcript_page(project: Project, payload: dict) -> list[str]:
    page = _get_project_page(project, payload.get("page_id"), active=True)
    active_pages = list(_active_transcript_pages(project))
    if len(active_pages) <= 1:
        raise TranscriptActionError("cannot delete the last active transcript page.")

    page.is_active = False
    page.deleted_at = timezone.now()
    page.save(update_fields=["is_active", "deleted_at", "updated_at"])

    _normalize_active_transcript_order(project, [candidate for candidate in active_pages if candidate.id != page.id])
    return [str(page.page_key)]


def _restore_transcript_page(project: Project, payload: dict) -> list[str]:
    page = _get_project_page(project, payload.get("page_id"), active=False)
    position = str(payload.get("position") or "end").strip().lower()
    if position not in {"start", "end", "after"}:
        raise TranscriptActionError("position must be start, end, or after.")

    active_pages = list(_active_transcript_pages(project))
    if position == "start":
        ordered_pages = [page, *active_pages]
    elif position == "after":
        after_page = _get_project_page(project, payload.get("after_page_id"), active=True, field_name="after_page_id")
        insert_at = next((idx for idx, candidate in enumerate(active_pages) if candidate.id == after_page.id), -1)
        if insert_at < 0:
            raise TranscriptActionError("after_page_id must reference an active page in this project.")
        ordered_pages = active_pages[: insert_at + 1] + [page] + active_pages[insert_at + 1 :]
    else:
        ordered_pages = [*active_pages, page]

    page.is_active = True
    page.deleted_at = None
    page.save(update_fields=["is_active", "deleted_at", "updated_at"])
    _normalize_active_transcript_order(project, ordered_pages)
    return [str(page.page_key)]


class ProjectTranscriptView(APIView):
    """GET/PATCH /api/v1/projects/<project_id>/transcript/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        return Response(
            {
                "project_id": project.id,
                "pages": _project_transcript_timeline(project, request=request),
            }
        )

    def patch(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        updates = request.data.get("pages")
        if not isinstance(updates, list):
            return Response({"error": "pages must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        trigger_rerender = bool(request.data.get("trigger_rerender"))
        lang_hint = request.data.get("lang_hint", "auto")
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            pause_sec = 2.2

        page_map = {
            p.id: p
            for p in _active_transcript_pages(project)
        }
        changed_page_keys: set[str] = set()

        for item in updates:
            if not isinstance(item, dict):
                continue
            page_id = item.get("id")
            if not page_id or page_id not in page_map:
                continue

            page = page_map[page_id]
            dirty_fields: list[str] = []

            if "narration_text" in item:
                narration_text = str(item.get("narration_text") or "")
                page.narration_text = narration_text
                page.subtitle_chunks = _chunk_transcript_text(narration_text)
                dirty_fields.extend(["narration_text", "subtitle_chunks"])

            if "rich_text_html" in item:
                page.rich_text_html = str(item.get("rich_text_html") or "")
                dirty_fields.append("rich_text_html")

            if "editor_document" in item and isinstance(item.get("editor_document"), dict):
                page.editor_document = item.get("editor_document")
                dirty_fields.append("editor_document")
            elif "narration_text" in item or "rich_text_html" in item:
                page.editor_document = _build_editor_document(page.narration_text, page.rich_text_html)
                dirty_fields.append("editor_document")

            if "whiteboard_mode" in item:
                page.whiteboard_mode = bool(item.get("whiteboard_mode"))
                dirty_fields.append("whiteboard_mode")

            if dirty_fields:
                dirty_fields.append("updated_at")
                page.save(update_fields=dirty_fields)
                changed_page_keys.add(str(page.page_key))

        rerender_job = None
        if trigger_rerender:
            rerender_job = _queue_transcript_rerender(
                project=project,
                request=request,
                changed_page_keys=changed_page_keys,
                pause_sec=pause_sec,
                lang_hint=lang_hint,
            )

        payload = {
            "project_id": project.id,
            "pages": _project_transcript_timeline(project, request=request),
        }
        if rerender_job:
            payload["rerender_job"] = rerender_job
        return Response(payload)


class ProjectTranscriptActionView(APIView):
    """POST /api/v1/projects/<project_id>/transcript/actions/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        action = str(request.data.get("action") or "").strip().lower()
        if action not in {
            "split_page",
            "merge_with_next",
            "merge_with_previous",
            "reorder_pages",
            "delete_page",
            "restore_page",
        }:
            return Response({"error": "Unsupported transcript action."}, status=status.HTTP_400_BAD_REQUEST)

        trigger_rerender = bool(request.data.get("trigger_rerender"))
        lang_hint = request.data.get("lang_hint", "auto")
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            pause_sec = 2.2

        try:
            with transaction.atomic():
                project = Project.objects.select_for_update().get(pk=project.id)
                if action == "split_page":
                    changed_page_keys = _split_transcript_page(project, request.data)
                elif action == "merge_with_next":
                    changed_page_keys = _merge_transcript_pages(project, request.data, direction="next")
                elif action == "merge_with_previous":
                    changed_page_keys = _merge_transcript_pages(project, request.data, direction="previous")
                elif action == "reorder_pages":
                    changed_page_keys = _reorder_transcript_pages(project, request.data)
                elif action == "delete_page":
                    changed_page_keys = _delete_transcript_page(project, request.data)
                else:
                    changed_page_keys = _restore_transcript_page(project, request.data)
        except TranscriptActionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Structural actions can change sequence membership/order, so action-triggered rerender is full-project.
        # The response still returns changed_page_keys for UI status and later targeted-render refinement.
        rerender_job = None
        rerender_strategy = "none"
        if trigger_rerender:
            rerender_job = _queue_transcript_rerender(
                project=project,
                request=request,
                changed_page_keys=changed_page_keys,
                pause_sec=pause_sec,
                lang_hint=lang_hint,
                full_rerender=True,
            )
            if rerender_job:
                rerender_strategy = "full"

        payload = {
            "project_id": project.id,
            "action": action,
            "pages": _project_transcript_timeline(project, request=request),
            "deleted_pages": _project_deleted_transcript_timeline(project),
            "changed_page_keys": changed_page_keys,
            "rerender_job": rerender_job,
            "rerender_strategy": rerender_strategy,
        }
        return Response(payload)


class ProjectSlideImageView(APIView):
    """GET /api/v1/projects/<project_id>/slides/<slide_index>/image/"""

    permission_classes = [permissions.AllowAny]

    def get(self, request, project_id, slide_index):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise Http404

        can_manage = _can_manage_project(request.user, project)
        if not can_manage:
            token_key = str(request.GET.get("token") or "").strip()
            if token_key:
                token = Token.objects.filter(key=token_key).select_related("user").first()
                can_manage = _can_manage_project(getattr(token, "user", None), project)
        if not can_manage:
            raise Http404

        try:
            slide_idx = int(slide_index)
        except (TypeError, ValueError):
            raise Http404
        if slide_idx < 0:
            raise Http404

        slide = (
            Slide.objects.filter(project=project, order=slide_idx).first()
            or Slide.objects.filter(project=project, order=slide_idx + 1).first()
        )
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        full_path = None

        if slide and slide.image_file:
            rel_path = _normalize_rel_storage_path(str(slide.image_file.name))
            full_path = _resolve_storage_file(storage_root, rel_path)

        if full_path is None:
            # Fallback for worker-exported slide images.
            fallback_rel = f"{project.id}/images/slide-{slide_idx + 1}.png"
            full_path = _resolve_storage_file(storage_root, fallback_rel)

        if full_path is None:
            raise Http404

        content_type, _ = mimetypes.guess_type(str(full_path))
        response = _media_file_response(request, full_path, content_type)
        response["Cache-Control"] = "private, max-age=300"
        return response


class ProjectRerenderView(APIView):
    """POST /api/v1/projects/<project_id>/rerender/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        voice_id = _get_voice_id(project.user)

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        upload_dir = Path(storage_root) / "uploads" / str(project.id)
        lesson_files = sorted(upload_dir.glob("lesson.*")) if upload_dir.exists() else []
        if not lesson_files:
            return Response({"error": "Original lesson file not found."}, status=status.HTTP_400_BAD_REQUEST)
        saved_path = str(lesson_files[0])

        lang_hint = request.data.get("lang_hint", "auto")
        whiteboard_mode_all = str(request.data.get("whiteboard_mode_all", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            pause_sec = 2.2

        request_id = _client_request_id(request)
        existing_job = _find_existing_idempotent_job(project, "video_export", request_id)
        if existing_job is not None:
            response = Response(JobSerializer(existing_job).data, status=status.HTTP_200_OK)
            response["X-Idempotent-Replay"] = "1"
            return response

        try:
            job = Job.objects.create(
                project=project,
                job_type="video_export",
                status="pending",
                request_id=request_id,
            )
        except IntegrityError:
            existing_job = _find_existing_idempotent_job(project, "video_export", request_id)
            if existing_job is not None:
                response = Response(JobSerializer(existing_job).data, status=status.HTTP_200_OK)
                response["X-Idempotent-Replay"] = "1"
                return response
            raise
        _remember_idempotent_job(project, "video_export", request_id, int(job.id))
        avatar_options = _resolve_avatar_options_for_project(project, request)
        render_profile = project.render_profile
        if "render_profile" in request.data:
            try:
                render_profile = _normalize_render_profile(request.data.get("render_profile"))
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        avatar_options = _apply_render_profile_to_avatar_options(avatar_options, render_profile)
        task_args = [
            str(project.id),
            saved_path,
            voice_id,
            pause_sec,
            lang_hint,
            "service",
            whiteboard_mode_all,
            avatar_options,
            None,
            _project_tts_settings(project),
            render_profile,
        ]
        target_queue = _queue_for_pipeline(avatar_options, render_profile)
        admission_profile = "avatar" if bool((avatar_options or {}).get("enabled")) else render_profile
        admitted, admission_payload = _admission_guard_for_render_profile(admission_profile, target_queue)
        if not admitted:
            return Response(admission_payload, status=status.HTTP_429_TOO_MANY_REQUESTS)
        async_result = _dispatch_celery_task(
            _PROCESS_PROJECT_RENDER_TASK,
            args=task_args,
            queue=target_queue,
        )
        job.celery_task_id = async_result.id
        job.save(update_fields=["celery_task_id"])
        payload = JobSerializer(job).data
        payload["queue"] = target_queue
        payload["estimated_wait_seconds"] = _estimate_queue_wait_seconds(
            target_queue,
            render_profile,
            avatar_enabled=bool((avatar_options or {}).get("enabled")),
        )
        _log_with_standard_fields(
            "info",
            "project_rerender_dispatched",
            request=request,
            user_id=int(request.user.id),
            project_id=int(project.id),
            job_id=int(job.id),
            queue=target_queue,
            stage="dispatch",
        )
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class JobStatusView(APIView):
    """GET /api/v1/projects/<project_id>/jobs/<job_id>/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id, job_id):
        started_at = time.perf_counter()
        response_schema = str(request.query_params.get("response_schema", "light_v1") or "light_v1").strip().lower()
        include_transcript = str(request.query_params.get("include_transcript_pages", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        include_language_detection = str(request.query_params.get("include_language_detection", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            job = Job.objects.get(pk=job_id, project_id=project_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if job.project and not _can_manage_project(request.user, job.project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        data = JobSerializer(job).data
        if include_language_detection:
            storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
            data["language_detection"] = _language_detection_sidecar_for_job(storage_root, int(project_id))
        if include_transcript:
            data["transcript_pages"] = _project_transcript_timeline(job.project) if job.project else []
        status_name = str(getattr(job, "status", "") or "").strip().lower()
        data["cancelled"] = (
            status_name == "cancelled"
            or JOB_CANCELLED_MARKER in str(getattr(job, "error_message", "") or "")
        )
        checkpoint_rows = [
            {
                "stage_name": row.stage_name,
                "stage_status": row.stage_status,
                "payload": row.payload,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in JobCheckpoint.objects.filter(job_id=job.id).order_by("updated_at", "id")
        ]
        data["checkpoints"] = checkpoint_rows
        concat_row = next((row for row in checkpoint_rows if row.get("stage_name") == "concat_finalize"), None)
        concat_payload = concat_row.get("payload", {}) if isinstance(concat_row, dict) else {}
        resume_source = str(concat_payload.get("source") or "")
        recovered_parts_count = int(concat_payload.get("recovered_parts_count") or 0)
        if not resume_source:
            render_dispatch = next((row for row in checkpoint_rows if row.get("stage_name") == "render_dispatch"), None)
            dispatch_payload = render_dispatch.get("payload", {}) if isinstance(render_dispatch, dict) else {}
            resume_source = str(dispatch_payload.get("source") or "")
        data["resume_source"] = resume_source
        data["recovered_parts_count"] = recovered_parts_count
        if response_schema in {"light_v1", "light-v1"}:
            light = {
                "id": data.get("id"),
                "project": data.get("project"),
                "job_type": data.get("job_type"),
                "status": data.get("status"),
                "progress": data.get("progress"),
                "request_id": data.get("request_id"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "result_url": data.get("result_url"),
                "srt_url": data.get("srt_url"),
                "error_message": data.get("error_message"),
                "cancelled": data.get("cancelled"),
                "resume_source": data.get("resume_source"),
                "recovered_parts_count": data.get("recovered_parts_count"),
                "checkpoints": data.get("checkpoints", []),
                "response_schema": "light_v1",
            }
            if include_language_detection and "language_detection" in data:
                light["language_detection"] = data["language_detection"]
            if include_transcript and "transcript_pages" in data:
                light["transcript_pages"] = data["transcript_pages"]
            _log_with_standard_fields(
                "info",
                "job_status_read",
                request=request,
                user_id=int(request.user.id),
                project_id=int(project_id),
                job_id=int(job_id),
                stage="read",
                started_at=started_at,
                response_schema="light_v1",
            )
            return Response(light)
        data["response_schema"] = "full_v1"
        _log_with_standard_fields(
            "info",
            "job_status_read",
            request=request,
            user_id=int(request.user.id),
            project_id=int(project_id),
            job_id=int(job_id),
            stage="read",
            started_at=started_at,
            response_schema="full_v1",
        )
        return Response(data)


class JobCancelView(APIView):
    """POST /api/v1/projects/<project_id>/jobs/<job_id>/cancel/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, job_id):
        started_at = time.perf_counter()
        try:
            job = Job.objects.select_related("project").get(pk=job_id, project_id=project_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if job.project and not _can_manage_project(request.user, job.project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        limited, attempt_count = _is_job_cancel_rate_limited(int(request.user.id))
        if limited:
            _audit_job_action(
                job=job,
                actor=request.user,
                action="cancel_rejected",
                metadata={
                    "reason": "rate_limited",
                    "attempt_count": int(attempt_count),
                    "limit_per_minute": int(JOB_CANCEL_RATE_LIMIT_PER_MINUTE),
                },
            )
            return Response(
                {
                    "error": "Too many cancel requests. Please retry shortly.",
                    "rate_limited": True,
                    "limit_per_minute": int(JOB_CANCEL_RATE_LIMIT_PER_MINUTE),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        status_name = str(job.status or "").strip().lower()
        if status_name in {"done", "failed", "cancelled"}:
            return Response(
                {
                    "error": "Job is already finalized.",
                    "status": job.status,
                    "cancelled": JOB_CANCELLED_MARKER in str(job.error_message or ""),
                },
                status=status.HTTP_409_CONFLICT,
            )

        root_task_id = str(job.celery_task_id or "").strip()
        revoked_task_ids = _revoke_project_active_tasks(
            project_id=int(project_id),
            include_task_ids={root_task_id} if root_task_id else None,
        )

        cancel_reason = str(request.data.get("reason") or "").strip()[:180]
        message = JOB_CANCELLED_MARKER
        if cancel_reason:
            message = f"{message}: {cancel_reason}"
        Job.objects.filter(id=job.id).update(status="cancelled", error_message=message)
        job.refresh_from_db()
        _audit_job_action(
            job=job,
            actor=request.user,
            action="cancel_requested",
            metadata={
                "reason": cancel_reason,
                "attempt_count": int(attempt_count),
            },
        )
        try:
            cleanup_queue = _queue_for_pipeline(
                _resolve_avatar_options_for_project(job.project, request) if job.project else {},
                getattr(job.project, "render_profile", "balanced") if job.project else "balanced",
            )
            _dispatch_celery_task(
                _CANCELLED_CLEANUP_TASK,
                kwargs={"project_id": int(project_id), "job_id": int(job.id)},
                queue=cleanup_queue,
            )
        except Exception:
            logger.warning("Failed to dispatch cancelled artifact cleanup for job=%s", job.id, exc_info=True)
        payload = JobSerializer(job).data
        payload["cancelled"] = True
        payload["revoked_task_ids"] = revoked_task_ids
        _log_with_standard_fields(
            "info",
            "job_cancelled",
            request=request,
            user_id=int(request.user.id),
            project_id=int(project_id),
            job_id=int(job.id),
            stage="cancel",
            started_at=started_at,
        )
        return Response(payload, status=status.HTTP_200_OK)


class JobRetryView(APIView):
    """POST /api/v1/projects/<project_id>/jobs/<job_id>/retry/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, job_id):
        started_at = time.perf_counter()
        try:
            original = Job.objects.select_related("project").get(pk=job_id, project_id=project_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if original.project and not _can_manage_project(request.user, original.project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if original.project is None:
            return Response({"error": "Project context is missing for this job."}, status=status.HTTP_409_CONFLICT)

        original_status = str(original.status or "").strip().lower()
        if original_status in {"pending", "running"}:
            return Response(
                {"error": "Running or queued jobs cannot be retried.", "status": original.status},
                status=status.HTTP_409_CONFLICT,
            )
        if original_status == "done":
            return Response(
                {"error": "Completed jobs cannot be retried.", "status": original.status},
                status=status.HTTP_409_CONFLICT,
            )

        retry_request_id_raw = (
            request.data.get("request_id")
            or request.data.get("retry_request_id")
            or request.headers.get("Idempotency-Key")
            or request.headers.get("X-Request-Id")
            or ""
        )
        retry_request_id = _normalize_request_id(retry_request_id_raw)
        if not retry_request_id:
            return Response({"error": "request_id is required for safe idempotent retry."}, status=status.HTTP_400_BAD_REQUEST)

        existing_job = _find_existing_idempotent_job(original.project, "video_export", retry_request_id)
        if existing_job is not None:
            payload = JobSerializer(existing_job).data
            payload["idempotent_replay"] = True
            payload["retried_from_job_id"] = int(original.id)
            _log_with_standard_fields(
                "info",
                "job_retry_idempotent_replay",
                request=request,
                user_id=int(request.user.id),
                project_id=int(project_id),
                job_id=int(existing_job.id),
                stage="retry",
                started_at=started_at,
            )
            response = Response(payload, status=status.HTTP_200_OK)
            response["X-Idempotent-Replay"] = "1"
            return response

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        upload_dir = Path(storage_root) / "uploads" / str(original.project.id)
        lesson_files = sorted(upload_dir.glob("lesson.*")) if upload_dir.exists() else []
        if not lesson_files:
            return Response({"error": "Original lesson file not found."}, status=status.HTTP_400_BAD_REQUEST)
        saved_path = str(lesson_files[0])

        voice_id = _get_voice_id(original.project.user)
        lang_hint = request.data.get("lang_hint", "auto")
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            pause_sec = 2.2

        render_profile = getattr(original.project, "render_profile", "balanced")
        if "render_profile" in request.data:
            try:
                render_profile = _normalize_render_profile(request.data.get("render_profile"))
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        avatar_options = _apply_render_profile_to_avatar_options(
            _resolve_avatar_options_for_project(original.project, request),
            render_profile,
        )
        target_queue = _queue_for_pipeline(avatar_options, render_profile)
        admission_profile = "avatar" if bool((avatar_options or {}).get("enabled")) else render_profile
        admitted, admission_payload = _admission_guard_for_render_profile(admission_profile, target_queue)
        if not admitted:
            return Response(admission_payload, status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            retry_job = Job.objects.create(
                project=original.project,
                job_type="video_export",
                status="pending",
                request_id=retry_request_id,
            )
        except IntegrityError:
            existing_job = _find_existing_idempotent_job(original.project, "video_export", retry_request_id)
            if existing_job is not None:
                payload = JobSerializer(existing_job).data
                payload["idempotent_replay"] = True
                payload["retried_from_job_id"] = int(original.id)
                response = Response(payload, status=status.HTTP_200_OK)
                response["X-Idempotent-Replay"] = "1"
                return response
            raise

        _remember_idempotent_job(original.project, "video_export", retry_request_id, int(retry_job.id))
        task_args = [
            str(original.project.id),
            saved_path,
            voice_id,
            pause_sec,
            lang_hint,
            "service",
            False,
            avatar_options,
            None,
            _project_tts_settings(original.project),
            render_profile,
        ]
        async_result = _dispatch_celery_task(
            _PROCESS_PROJECT_RENDER_TASK,
            args=task_args,
            queue=target_queue,
        )
        retry_job.celery_task_id = async_result.id
        retry_job.save(update_fields=["celery_task_id"])
        payload = JobSerializer(retry_job).data
        payload["retried_from_job_id"] = int(original.id)
        payload["queue"] = target_queue
        payload["idempotent_replay"] = False
        _log_with_standard_fields(
            "info",
            "job_retry_dispatched",
            request=request,
            user_id=int(request.user.id),
            project_id=int(project_id),
            job_id=int(retry_job.id),
            queue=target_queue,
            stage="retry",
            started_at=started_at,
        )
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class JobEventsStreamView(APIView):
    """GET /api/v1/projects/<project_id>/jobs/<job_id>/events/ (SSE job status stream)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @staticmethod
    def _resolve_user(request, *, project_id: int, job_id: int):
        if getattr(request, "user", None) and getattr(request.user, "is_authenticated", False):
            return request.user
        ticket = str(request.GET.get("stream_ticket") or "").strip()
        if ticket:
            payload = _resolve_job_events_ticket(ticket)
            if payload:
                if int(payload.get("project_id") or -1) != int(project_id):
                    return None
                if int(payload.get("job_id") or -1) != int(job_id):
                    return None
                user_id = int(payload.get("user_id") or 0)
                if user_id:
                    return User.objects.filter(pk=user_id).first()
        # Backward compatibility fallback: legacy token query support.
        token_key = str(request.GET.get("token") or "").strip()
        if token_key:
            token = Token.objects.filter(key=token_key).select_related("user").first()
            return getattr(token, "user", None) if token else None
        return None

    def get(self, request, project_id, job_id):
        user = self._resolve_user(request, project_id=int(project_id), job_id=int(job_id))
        if user is None:
            return Response({"error": "Unauthorized."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            job = Job.objects.select_related("project").get(pk=job_id, project_id=project_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if job.project and not _can_manage_project(user, job.project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        resume_raw = str(request.GET.get("last_event_id") or request.headers.get("Last-Event-ID") or "").strip()
        try:
            event_seq = max(0, int(resume_raw))
        except ValueError:
            event_seq = 0

        def _event_stream():
            nonlocal event_seq
            # Let EventSource know how long to wait before reconnect attempts.
            yield "retry: 3000\n\n"
            last_fingerprint = ""
            for i in range(120):
                current = Job.objects.filter(pk=job.id).first()
                if current is None:
                    event_seq += 1
                    yield f"id: {event_seq}\nevent: job_deleted\ndata: {{}}\n\n"
                    return
                payload = JobSerializer(current).data
                fingerprint = f"{payload.get('status')}|{payload.get('progress')}|{payload.get('updated_at')}"
                if fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    event_seq += 1
                    yield f"id: {event_seq}\nevent: job_status\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
                elif i % 5 == 0:
                    event_seq += 1
                    yield f"id: {event_seq}\nevent: heartbeat\ndata: {{\"job_id\": {int(job.id)}}}\n\n"
                if str(current.status or "").lower() in {"done", "failed", "cancelled"}:
                    return
                time.sleep(2)

        response = StreamingHttpResponse(_event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["Connection"] = "keep-alive"
        response["X-Accel-Buffering"] = "no"
        return response


class JobEventsAuthTicketView(APIView):
    """POST /api/v1/projects/<project_id>/jobs/<job_id>/events/ticket/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, job_id):
        try:
            job = Job.objects.select_related("project").get(pk=job_id, project_id=project_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if job.project and not _can_manage_project(request.user, job.project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        ticket, ttl = _issue_job_events_ticket(
            user_id=int(request.user.id),
            project_id=int(project_id),
            job_id=int(job_id),
        )
        return Response({"stream_ticket": ticket, "expires_in": int(ttl)})


class RenderCapacityView(APIView):
    """GET /api/v1/system/render-capacity/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not (_is_staff_user(request.user) or _is_verified_teacher(request.user)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        return Response(_render_capacity_snapshot())


class RenderMetricsView(APIView):
    """GET /api/v1/system/render-metrics/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not (_is_staff_user(request.user) or _is_verified_teacher(request.user)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        return Response(_render_metrics_snapshot())


class AutoscalePolicyView(APIView):
    """GET /api/v1/system/autoscale-policy/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not (_is_staff_user(request.user) or _is_verified_teacher(request.user)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        return Response(_autoscale_policy_snapshot())


class PrometheusMetricsView(APIView):
    """GET /api/v1/system/metrics/prometheus/"""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @staticmethod
    def _authorized(request) -> bool:
        configured_token = str(getattr(settings, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()
        if configured_token:
            provided_token = str(request.headers.get("X-Metrics-Token") or request.GET.get("token") or "").strip()
            if provided_token and _hmac.compare_digest(provided_token, configured_token):
                return True
        return bool(getattr(request, "user", None) and request.user.is_authenticated and _is_staff_user(request.user))

    def get(self, request):
        started_at = time.perf_counter()
        if not self._authorized(request):
            _log_with_standard_fields(
                "warning",
                "prometheus_metrics_denied",
                request=request,
                user_id=int(request.user.id) if getattr(request, "user", None) and request.user.is_authenticated else None,
                stage="metrics_auth",
                started_at=started_at,
            )
            return Response({"error": "Unauthorized."}, status=status.HTTP_401_UNAUTHORIZED)
        if not _PROMETHEUS_AVAILABLE:
            return Response(
                {"error": "prometheus_client dependency is unavailable in this runtime."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        snapshot = _render_metrics_snapshot()
        registry = CollectorRegistry()

        queue_depth = Gauge(
            "vidlab_render_queue_depth",
            "Queue depth by render profile",
            ["profile", "queue"],
            registry=registry,
        )
        queue_wait = Gauge(
            "vidlab_render_estimated_wait_seconds",
            "Estimated wait time by render profile",
            ["profile", "queue"],
            registry=registry,
        )
        status_count = Gauge(
            "vidlab_jobs_status_total",
            "Job status counts",
            ["status"],
            registry=registry,
        )
        oldest_age = Gauge(
            "vidlab_jobs_oldest_active_age_seconds",
            "Age of the oldest pending/running job in seconds",
            registry=registry,
        )
        latency_p50 = Gauge("vidlab_jobs_latency_p50_seconds", "P50 latency for recent done jobs", registry=registry)
        latency_p95 = Gauge("vidlab_jobs_latency_p95_seconds", "P95 latency for recent done jobs", registry=registry)
        latency_p99 = Gauge("vidlab_jobs_latency_p99_seconds", "P99 latency for recent done jobs", registry=registry)
        recent_done = Counter(
            "vidlab_jobs_recent_done_observations_total",
            "Number of recent done jobs used for latency snapshots",
            registry=registry,
        )
        autoscale_signal = Gauge(
            "vidlab_autoscale_signal",
            "Autoscale recommendation signal (-1 scale_down, 0 hold, 1 scale_up)",
            ["profile", "action"],
            registry=registry,
        )

        queues = snapshot.get("capacity", {}).get("queues", {})
        for profile, info in queues.items():
            qname = str(info.get("queue") or "")
            queue_depth.labels(profile=profile, queue=qname).set(float(info.get("depth") or 0))
            queue_wait.labels(profile=profile, queue=qname).set(float(info.get("estimated_wait_seconds") or 0))

        status_counts = snapshot.get("jobs", {}).get("status_counts", {})
        for status_name, value in status_counts.items():
            status_count.labels(status=status_name).set(float(value or 0))

        oldest_age.set(float(snapshot.get("jobs", {}).get("oldest_active_age_seconds") or 0))
        latency_p50.set(float(snapshot.get("jobs", {}).get("latency_seconds_p50") or 0))
        latency_p95.set(float(snapshot.get("jobs", {}).get("latency_seconds_p95") or 0))
        latency_p99.set(float(snapshot.get("jobs", {}).get("latency_seconds_p99") or 0))
        for _ in range(int(snapshot.get("jobs", {}).get("recent_done_count") or 0)):
            recent_done.inc()

        autoscale = _autoscale_policy_snapshot().get("profiles", {})
        for profile, decision in autoscale.items():
            action = str(decision.get("action") or "hold")
            signal = 1.0 if action == "scale_up" else (-1.0 if action == "scale_down" else 0.0)
            autoscale_signal.labels(profile=profile, action=action).set(signal)

        _log_with_standard_fields(
            "info",
            "prometheus_metrics_served",
            request=request,
            user_id=int(request.user.id) if getattr(request, "user", None) and request.user.is_authenticated else None,
            stage="metrics_serve",
            started_at=started_at,
        )
        return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)


class SystemOrphanCleanupRunView(APIView):
    """POST /api/v1/system/orphan-cleanup/run/"""

    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        try:
            min_age_hours = max(1, int(request.data.get("min_age_hours", 6)))
        except (TypeError, ValueError):
            min_age_hours = 6
        try:
            async_result = _dispatch_celery_task(
                "worker.tasks.cleanup_orphan_render_artifacts",
                kwargs={"min_age_hours": int(min_age_hours)},
                queue=_render_queue_name(),
            )
        except Exception:
            logger.exception("Failed to dispatch orphan cleanup janitor task")
            return Response({"error": "Failed to dispatch janitor task."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(
            {
                "status": "accepted",
                "task_id": str(getattr(async_result, "id", "")),
                "min_age_hours": int(min_age_hours),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class VoiceUploadView(APIView):
    """
    POST /api/v1/users/<user_id>/voice/

    Saves the uploaded audio as the teacher's XTTS v2 reference voice.
    File is stored at STORAGE_ROOT/voices/<voice_id>.wav so the TTS
    service can locate it by voice_id.
    """
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, user_id):
        if not (_is_staff_user(request.user) or request.user.id == int(user_id)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(pk=int(user_id)).first()
        if user is None:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can upload voices."}, status=status.HTTP_403_FORBIDDEN)

        audio_file = request.FILES.get("voice_file")
        if not audio_file:
            return Response({"error": "voice_file is required."}, status=status.HTTP_400_BAD_REQUEST)

        new_voice_id = f"voice_{uuid.uuid4().hex[:12]}"

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        voices_dir = Path(storage_root) / "voices"
        voices_dir.mkdir(parents=True, exist_ok=True)

        # Save as .wav — XTTS service expects {voice_id}.wav
        voice_path = voices_dir / f"{new_voice_id}.wav"
        with open(voice_path, "wb") as fh:
            for chunk in audio_file.chunks():
                fh.write(chunk)

        profile, _ = VoiceProfile.objects.get_or_create(user=user)
        profile.voice_id = new_voice_id
        profile.provider = "xtts_v2"
        profile.save(update_fields=["voice_id", "provider"])

        return Response({"voice_id": new_voice_id, "status": "ready", "provider": "xtts_v2"})


class AvatarProfileView(APIView):
    """
    GET/POST/PATCH /api/v1/users/<user_id>/avatar/

    Handles teacher avatar identity upload, validation, preprocessing, and settings.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    permission_classes = [permissions.IsAuthenticated]

    def _resolve_user(self, request, user_id: int):
        if _is_staff_user(request.user) or request.user.id == int(user_id):
            return User.objects.filter(pk=int(user_id)).first()
        return None

    def get(self, request, user_id):
        user = self._resolve_user(request, user_id)
        if user is None:
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        if profile.avatar_image_processed or profile.avatar_image_original or profile.avatar_video_original:
            try:
                refresh_avatar_source_validation(profile, storage_root=storage_root, persist=True)
            except Exception:
                logger.warning("Avatar source validation refresh failed for user=%s", user.id, exc_info=True)

        readiness = _avatar_preview_readiness(
            profile,
            VoiceProfile.objects.filter(user=user).first(),
            storage_root=storage_root,
        )

        payload = {
            "profile": UserSerializer(user).data.get("profile", {}),
            "readiness": readiness,
            "avatar_summary": {
                "status": profile.avatar_image_status,
                "model_version": profile.avatar_model_version,
                "lipsync_engine": _normalize_avatar_engine(profile.avatar_lipsync_engine),
                "engine_primary": _normalize_avatar_engine(profile.avatar_engine_primary),
                "reference_type": profile.avatar_reference_type,
                "last_rendered_at": profile.avatar_last_rendered_at,
                "last_preview_status": profile.avatar_last_preview_status,
                "last_preview_job_id": profile.avatar_last_preview_job_id,
                "last_preview_error": profile.avatar_preview_error,
                "preview_ready": bool(readiness.get("avatar_ready")),
                "avatar_ready": bool(readiness.get("avatar_ready")),
                "avatar_source_valid": bool(profile.avatar_source_valid),
                "avatar_source_validation_error": profile.avatar_source_validation_error,
                "avatar_source_hash": profile.avatar_source_hash,
                "avatar_source_image_hash": profile.avatar_source_image_hash,
                "avatar_source_video_hash": profile.avatar_source_video_hash,
                "avatar_preview_stale": bool(profile.avatar_preview_stale or readiness.get("avatar_preview_stale")),
                "avatar_preview_source_hash": profile.avatar_preview_source_hash,
                "missing_requirements": readiness.get("missing_requirements") or [],
                "readiness_checks": readiness.get("checks") or {},
                "composite_configured": _composite_engine_configured(),
                "composite_lesson_enabled": _composite_lesson_enabled(),
            },
            "recent_jobs": AvatarRenderJobSerializer(
                AvatarRenderJob.objects.filter(teacher=user).order_by("-created_at")[:15],
                many=True,
            ).data,
        }
        return Response(payload)

    def post(self, request, user_id):
        user = self._resolve_user(request, user_id)
        if user is None:
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
        avatar_file = request.FILES.get("avatar_file")
        avatar_video_file = request.FILES.get("avatar_video_file")
        if avatar_file is None and avatar_video_file is None:
            return Response({"error": "avatar_video_file (preferred) or avatar_file is required."}, status=status.HTTP_400_BAD_REQUEST)

        consent_value = str(request.data.get("avatar_consent_confirmed", "")).strip().lower()
        if consent_value not in {"1", "true", "yes", "on"}:
            return Response(
                {"error": "Explicit avatar consent is required before generation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile.avatar_image_status = "processing"
        profile.avatar_preview_error = ""
        profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        upload_dir = Path(storage_root) / "avatars" / str(user.id) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        rel_original = ""
        rel_video_original = profile.avatar_video_original
        result_warnings = []
        reference_type = "image"

        if avatar_video_file is not None:
            reference_type = "video"
            video_ext = Path(avatar_video_file.name).suffix.lower() or ".mp4"
            saved_video = upload_dir / f"avatar_source_video{video_ext}"
            with open(saved_video, "wb") as handle:
                for chunk in avatar_video_file.chunks():
                    handle.write(chunk)

            rel_video_original = str(saved_video.relative_to(Path(storage_root))).replace("\\", "/")
            try:
                v_result = preprocess_avatar_video(
                    video_bytes=saved_video.read_bytes(),
                    original_filename=saved_video.name,
                    storage_root=storage_root,
                    teacher_id=user.id,
                    model_version="musetalk:v1",
                )
            except AvatarValidationError as exc:
                profile.avatar_image_status = "rejected"
                profile.avatar_preview_error = str(exc)
                profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            profile.avatar_image_processed = str(v_result.get("processed_rel_path") or "")
            profile.avatar_video_processed = str(v_result.get("video_rel_path") or rel_video_original)
            profile.avatar_version_hash = str(v_result.get("source_hash") or "")
            result_warnings = list(v_result.get("warnings") or [])

        if avatar_file is not None:
            ext = Path(avatar_file.name).suffix.lower() or ".png"
            saved_original = upload_dir / f"avatar_original{ext}"
            with open(saved_original, "wb") as handle:
                for chunk in avatar_file.chunks():
                    handle.write(chunk)

            rel_original = str(saved_original.relative_to(Path(storage_root))).replace("\\", "/")

            try:
                result = preprocess_teacher_avatar_image(
                    image_bytes=saved_original.read_bytes(),
                    original_filename=saved_original.name,
                    storage_root=storage_root,
                    teacher_id=user.id,
                    model_version="musetalk:v1",
                )
            except AvatarValidationError as exc:
                profile.avatar_image_status = "rejected"
                profile.avatar_preview_error = str(exc)
                profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            profile.avatar_image_original = rel_original
            if avatar_video_file is None:
                # Image-only mode: use processed image as primary identity source.
                profile.avatar_image_processed = result.processed_rel_path
                profile.avatar_version_hash = result.source_hash
            result_warnings.extend(result.warnings)
            if avatar_video_file is None:
                reference_type = "image"

        if avatar_video_file is not None and not profile.avatar_image_original and rel_original:
            profile.avatar_image_original = rel_original

        if avatar_video_file is not None:
            profile.avatar_video_original = rel_video_original

        requested_engine = _normalize_avatar_engine(
            request.data.get("avatar_lipsync_engine")
            or profile.avatar_lipsync_engine
            or profile.avatar_engine_primary
            or os.environ.get("AVATAR_ENGINE")
        )
        if requested_engine == "liveportrait+musetalk" and not _composite_engine_configured():
            profile.avatar_image_status = "failed"
            profile.avatar_preview_error = (
                "liveportrait+musetalk is selected but AVATAR_LIVEPORTRAIT_CMD and/or AVATAR_MUSETALK_CMD is not configured"
            )
            profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
            return Response({"error": profile.avatar_preview_error}, status=status.HTTP_400_BAD_REQUEST)

        profile.avatar_reference_type = reference_type
        validation = refresh_avatar_source_validation(profile, storage_root=Path(storage_root), persist=True)
        if not bool(validation.get("valid")):
            profile.avatar_image_status = "rejected"
            profile.avatar_preview_error = str(validation.get("error") or "avatar_source_invalid")
            profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
            return Response(
                {
                    "error": profile.avatar_preview_error,
                    "error_code": "avatar_source_invalid",
                    "avatar_source_valid": False,
                    "avatar_source_validation_error": profile.avatar_preview_error,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile.avatar_image_status = "ready"
        profile.avatar_model_version = f"{requested_engine}:v1"
        profile.avatar_enabled = True
        profile.avatar_consent_confirmed = True
        profile.avatar_motion_preset = str(request.data.get("avatar_motion_preset") or profile.avatar_motion_preset or "natural")
        profile.avatar_lipsync_engine = requested_engine
        profile.avatar_quality_preset = str(request.data.get("avatar_quality_preset") or profile.avatar_quality_preset or "high")
        profile.avatar_engine_primary = requested_engine
        profile.avatar_engine_fallback = ""
        profile.save(
            update_fields=[
                "avatar_image_original",
                "avatar_image_processed",
                "avatar_video_original",
                "avatar_video_processed",
                "avatar_image_status",
                "avatar_model_version",
                "avatar_reference_type",
                "avatar_enabled",
                "avatar_consent_confirmed",
                "avatar_motion_preset",
                "avatar_lipsync_engine",
                "avatar_quality_preset",
                "avatar_engine_primary",
                "avatar_engine_fallback",
                "avatar_version_hash",
                "updated_at",
            ]
        )

        return Response(
            {
                "status": "ready",
                "profile": UserSerializer(user).data.get("profile", {}),
                "warnings": result_warnings,
            }
        )

    def patch(self, request, user_id):
        user = self._resolve_user(request, user_id)
        if user is None:
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
        content_type = str(request.content_type or "").split(";", 1)[0].strip().lower()
        raw_body = (request.body or b"").decode("utf-8", errors="ignore").strip()
        if raw_body and content_type in {"", "text/plain", "application/octet-stream"}:
            if not raw_body:
                return Response({"error": "Unsupported Media Type: empty request body."}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            try:
                parsed = json.loads(raw_body)
            except Exception:
                return Response(
                    {"error": "Unsupported Media Type. Use application/json or multipart/form-data."},
                    status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                )
            if not isinstance(parsed, dict):
                return Response(
                    {"error": "Invalid avatar settings payload."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            data = parsed
        else:
            try:
                data = request.data
            except UnsupportedMediaType:
                if not raw_body:
                    return Response({"error": "Unsupported Media Type: empty request body."}, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
                try:
                    parsed = json.loads(raw_body)
                except Exception:
                    return Response(
                        {"error": "Unsupported Media Type. Use application/json or multipart/form-data."},
                        status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    )
                if not isinstance(parsed, dict):
                    return Response(
                        {"error": "Invalid avatar settings payload."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                data = parsed

        if "avatar_enabled" in data:
            profile.avatar_enabled = str(data.get("avatar_enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
        if "avatar_motion_preset" in data:
            profile.avatar_motion_preset = str(data.get("avatar_motion_preset") or "natural")
        if "avatar_lipsync_engine" in data:
            requested_engine = _normalize_avatar_engine(str(data.get("avatar_lipsync_engine") or "musetalk"))
            if requested_engine == "liveportrait+musetalk" and not _composite_engine_configured():
                return Response(
                    {"error": "liveportrait+musetalk is selected but AVATAR_LIVEPORTRAIT_CMD and/or AVATAR_MUSETALK_CMD is not configured"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            profile.avatar_lipsync_engine = requested_engine
            profile.avatar_engine_primary = requested_engine
            profile.avatar_model_version = f"{requested_engine}:v1"
        if "avatar_reference_type" in data:
            ref = str(data.get("avatar_reference_type") or "image").strip().lower()
            if ref in {"image", "video"}:
                profile.avatar_reference_type = ref
                try:
                    validation = refresh_avatar_source_validation(
                        profile,
                        storage_root=Path(getattr(settings, "STORAGE_ROOT", "storage_local")),
                        persist=True,
                    )
                    if not bool(validation.get("valid")):
                        profile.avatar_image_status = "rejected"
                        profile.avatar_preview_error = str(validation.get("error") or "avatar_source_invalid")
                        profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
                        return Response(
                            {
                                "error": profile.avatar_preview_error,
                                "error_code": "avatar_source_invalid",
                                "avatar_source_valid": False,
                                "avatar_source_validation_error": profile.avatar_preview_error,
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                except Exception as exc:
                    return Response({"error": str(exc or "avatar_source_validation_failed")}, status=status.HTTP_400_BAD_REQUEST)
        profile.avatar_engine_primary = _normalize_avatar_engine(profile.avatar_engine_primary or profile.avatar_lipsync_engine)
        profile.avatar_engine_fallback = ""
        if "avatar_quality_preset" in data:
            profile.avatar_quality_preset = str(data.get("avatar_quality_preset") or "high")
        if "avatar_overlay_default_position" in data:
            profile.avatar_overlay_default_position = str(data.get("avatar_overlay_default_position") or "top-right")
        if "avatar_overlay_size" in data:
            profile.avatar_overlay_size = str(data.get("avatar_overlay_size") or "medium")
        if "avatar_overlay_visible" in data:
            profile.avatar_overlay_visible = str(data.get("avatar_overlay_visible", "")).strip().lower() in {"1", "true", "yes", "on"}
        if "avatar_consent_confirmed" in data:
            profile.avatar_consent_confirmed = str(data.get("avatar_consent_confirmed", "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        profile.save(update_fields=[
            "avatar_enabled",
            "avatar_motion_preset",
            "avatar_lipsync_engine",
            "avatar_model_version",
            "avatar_reference_type",
            "avatar_engine_primary",
            "avatar_engine_fallback",
            "avatar_quality_preset",
            "avatar_overlay_default_position",
            "avatar_overlay_size",
            "avatar_overlay_visible",
            "avatar_consent_confirmed",
            "updated_at",
        ])
        return Response({"status": "updated", "profile": UserSerializer(user).data.get("profile", {})})


class AvatarPreviewRegenerateView(APIView):
    """POST /api/v1/users/<user_id>/avatar/preview/ — regenerate a local preview clip."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, user_id):
        if not (_is_staff_user(request.user) or request.user.id == int(user_id)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(pk=int(user_id)).first()
        if user is None:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
        voice_profile = VoiceProfile.objects.filter(user=user).first()
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        if profile.avatar_image_processed or profile.avatar_image_original or profile.avatar_video_original:
            try:
                refresh_avatar_source_validation(profile, storage_root=storage_root, persist=True)
            except Exception:
                logger.warning("Avatar source validation refresh failed before preview teacher=%s", user.id, exc_info=True)
        readiness = _avatar_preview_readiness(
            profile,
            voice_profile,
            storage_root=storage_root,
        )
        if not bool(readiness.get("ready")):
            profile.avatar_last_preview_status = "setup_failed"
            profile.avatar_image_status = "failed"
            profile.avatar_preview_error = str(readiness.get("error") or "Avatar is not prepared for preview.")
            profile.save(update_fields=["avatar_last_preview_status", "avatar_image_status", "avatar_preview_error", "updated_at"])
            return Response(
                {
                    "error": readiness.get("error") or "Avatar is not prepared for preview.",
                    "error_code": readiness.get("error_code") or "setup_not_prepared",
                    "missing_requirements": readiness.get("missing_requirements") or [],
                    "readiness": readiness,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = Job.objects.create(job_type="avatar_render", status="pending")
        async_result = _dispatch_celery_task(
            _AVATAR_PREVIEW_TASK,
            kwargs={
                "teacher_id": int(user.id),
                "job_id": int(job.id),
            },
            queue=_avatar_queue_name(),
        )
        job.celery_task_id = async_result.id
        job.save(update_fields=["celery_task_id"])

        profile.avatar_image_status = "processing"
        profile.avatar_last_preview_status = "queued"
        profile.avatar_last_preview_job_id = str(job.id)
        profile.avatar_last_preview_path = ""
        profile.avatar_preview_video = ""
        profile.avatar_preview_error = ""
        profile.save(update_fields=["avatar_image_status", "avatar_last_preview_status", "avatar_last_preview_job_id", "avatar_last_preview_path", "avatar_preview_video", "avatar_preview_error", "updated_at"])

        return Response(
            {
                "status": "queued",
                "job_id": job.id,
                "task_id": async_result.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class AvatarPrepareView(APIView):
    """POST /api/v1/users/<user_id>/avatar/prepare/ — canonical one-click prepare action."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, user_id):
        if not (_is_staff_user(request.user) or request.user.id == int(user_id)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(pk=int(user_id)).first()
        if user is None:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        voice_profile = VoiceProfile.objects.filter(user=user).first()
        actions: list[str] = []
        warnings: list[str] = []

        if "avatar_enabled" in request.data:
            profile.avatar_enabled = str(request.data.get("avatar_enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
            actions.append("avatar_enabled_updated")
        elif not profile.avatar_enabled:
            profile.avatar_enabled = True
            actions.append("avatar_enabled_auto_enabled")

        if "avatar_consent_confirmed" in request.data:
            profile.avatar_consent_confirmed = str(request.data.get("avatar_consent_confirmed", "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            actions.append("avatar_consent_updated")

        force_reprocess = str(request.data.get("force_reprocess", "0")).strip().lower() in {"1", "true", "yes", "on"}
        processed_rel = str(profile.avatar_image_processed or "").strip()
        processed_abs = (storage_root / processed_rel) if processed_rel else None
        processed_missing = (processed_abs is None) or (not processed_abs.exists()) or (not processed_abs.is_file())
        should_reprocess = bool(force_reprocess or (not processed_rel) or processed_missing)

        original_rel = str(profile.avatar_image_original or "").strip()
        original_abs = (storage_root / original_rel) if original_rel else None
        if should_reprocess:
            if original_abs is None or (not original_abs.exists()) or (not original_abs.is_file()):
                warnings.append("missing_avatar_image_original")
            else:
                try:
                    result = preprocess_teacher_avatar_image(
                        image_bytes=original_abs.read_bytes(),
                        original_filename=original_abs.name,
                        storage_root=str(storage_root),
                        teacher_id=int(user.id),
                        model_version=f"{normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary)}:v1",
                    )
                    profile.avatar_image_processed = str(result.processed_rel_path)
                    profile.avatar_version_hash = str(result.source_hash or profile.avatar_version_hash)
                    actions.append("processed_reference_generated")
                except AvatarValidationError as exc:
                    profile.avatar_image_status = "rejected"
                    profile.avatar_preview_error = str(exc)
                    profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
                    return Response(
                        {
                            "status": "avatar_not_prepared",
                            "error_code": "crop_too_tight",
                            "error": str(exc),
                            "missing_requirements": ["missing_avatar_image_processed", "missing_processed_reference_file"],
                            "actions": actions,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        if profile.avatar_image_processed or profile.avatar_image_original or profile.avatar_video_original:
            validation = refresh_avatar_source_validation(profile, storage_root=storage_root, persist=True)
            if not bool(validation.get("valid")):
                profile.avatar_image_status = "rejected"
                profile.avatar_preview_error = str(validation.get("error") or "avatar_source_invalid")
                profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
                return Response(
                    {
                        "status": "avatar_not_prepared",
                        "error_code": "avatar_source_invalid",
                        "error": profile.avatar_preview_error,
                        "missing_requirements": ["avatar_source_invalid"],
                        "actions": actions,
                        "warnings": warnings,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        readiness = _avatar_preview_readiness(profile, voice_profile, storage_root=storage_root)
        if readiness.get("ready"):
            profile.avatar_image_status = "ready"
            profile.avatar_preview_error = ""
            profile.save(update_fields=["avatar_enabled", "avatar_consent_confirmed", "avatar_image_processed", "avatar_version_hash", "avatar_image_status", "avatar_preview_error", "updated_at"])
            return Response(
                {
                    "status": "avatar_ready",
                    "readiness": readiness,
                    "actions": actions,
                    "warnings": warnings,
                }
            )

        profile.avatar_image_status = "failed"
        profile.avatar_preview_error = str(readiness.get("error") or "Avatar is not prepared for preview.")
        profile.save(update_fields=["avatar_enabled", "avatar_consent_confirmed", "avatar_image_status", "avatar_preview_error", "updated_at"])
        return Response(
            {
                "status": "avatar_not_prepared",
                "error_code": "setup_not_prepared",
                "error": readiness.get("error") or "Avatar is not prepared for preview.",
                "missing_requirements": readiness.get("missing_requirements") or [],
                "readiness": readiness,
                "actions": actions,
                "warnings": warnings,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class AvatarPreviewStatusView(APIView):
    """GET /api/v1/users/<user_id>/avatar/preview/<job_id>/ — poll async preview status."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, user_id, job_id):
        if not (_is_staff_user(request.user) or request.user.id == int(user_id)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(pk=int(user_id)).first()
        if user is None:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
        try:
            job = Job.objects.get(id=int(job_id), job_type="avatar_render")
        except Job.DoesNotExist:
            return Response({"error": "Preview job not found."}, status=status.HTTP_404_NOT_FOUND)

        payload = JobSerializer(job).data
        payload["preview_status"] = _normalize_preview_stage(profile.avatar_last_preview_status, job_status=payload.get("status"))
        preview_rel_path = ""
        ui_returned_playable_file = ""
        if str(profile.avatar_last_preview_job_id or "") == str(job.id) and payload["preview_status"] in {"ready", "warning", "done"}:
            preview_rel_path = str(profile.avatar_last_preview_path or profile.avatar_preview_video or "")
            ui_returned_playable_file = str(payload.get("result_url") or "").strip() or preview_rel_path
        payload["preview_rel_path"] = preview_rel_path
        payload["ui_returned_playable_file"] = ui_returned_playable_file

        playable_path = str(payload.get("ui_returned_playable_file") or "").strip()
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        playable_exists = False
        if playable_path:
            playable_candidate = Path(playable_path)
            if playable_candidate.is_absolute():
                playable_exists = playable_candidate.exists() and playable_candidate.is_file()
            else:
                joined = storage_root / playable_candidate
                playable_exists = joined.exists() and joined.is_file()
        explicit_unusable = "preview_usable=false" in str(payload.get("error_message") or "").lower()
        payload["preview_file_exists"] = bool(playable_exists)
        payload["preview_usable"] = bool(
            playable_exists
            and str(payload.get("preview_status") or "") in {"ready", "warning"}
            and not explicit_unusable
        )

        payload["avatar_image_status"] = profile.avatar_image_status
        raw_job_message = str(payload.get("error_message") or "")
        preview_warning = ""
        if raw_job_message.startswith("preview_warning:"):
            preview_warning = raw_job_message.replace("preview_warning:", "", 1).strip()
            raw_job_message = ""
        if not preview_warning and str(profile.avatar_last_preview_status or "").strip().lower() == "warning":
            preview_warning = str(profile.avatar_preview_error or "").replace("preview_warning:", "", 1).strip()
        if preview_warning and payload.get("preview_rel_path"):
            payload["preview_status"] = "warning"
        payload["preview_warning"] = preview_warning
        payload["preview_error"] = profile.avatar_preview_error or raw_job_message or preview_warning
        payload["preview_error_code"] = _preview_error_code_for_status(
            payload.get("preview_status") or "",
            payload.get("preview_error"),
        )
        readiness = _avatar_preview_readiness(
            profile,
            VoiceProfile.objects.filter(user=user).first(),
            storage_root=Path(getattr(settings, "STORAGE_ROOT", "storage_local")),
        )
        payload["preview_readiness"] = readiness
        payload["avatar_ready"] = bool(readiness.get("avatar_ready"))
        payload["avatar_source_valid"] = bool(profile.avatar_source_valid)
        payload["avatar_source_validation_error"] = str(profile.avatar_source_validation_error or "")
        payload["avatar_source_hash"] = str(profile.avatar_source_hash or "")
        payload["avatar_preview_stale"] = bool(profile.avatar_preview_stale or readiness.get("avatar_preview_stale"))
        payload["avatar_preview_source_hash"] = str(profile.avatar_preview_source_hash or "")
        if payload["avatar_preview_stale"]:
            payload["preview_rel_path"] = ""
            payload["ui_returned_playable_file"] = ""
            payload["preview_file_exists"] = False
            payload["preview_usable"] = False
        
        logger.info(
            "Avatar preview api_player_binding teacher_id=%s job_id=%s player_file=%s preview_rel_path=%s preview_status=%s preview_file_exists=%s preview_usable=%s",
            int(user_id),
            int(job_id),
            playable_path or "",
            preview_rel_path or "",
            payload.get("preview_status") or "unknown",
            bool(payload.get("preview_file_exists")),
            bool(payload.get("preview_usable")),
        )

        return Response(payload)


class AvatarPreviewDeleteView(APIView):
    """DELETE /api/v1/users/<user_id>/avatar/preview/ — remove current preview asset."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, user_id):
        if not (_is_staff_user(request.user) or request.user.id == int(user_id)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(pk=int(user_id)).first()
        if user is None:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_verified_teacher(user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        preview_rel = profile.avatar_last_preview_path or profile.avatar_preview_video
        if preview_rel:
            preview_abs = storage_root / preview_rel
            if preview_abs.exists() and preview_abs.is_file():
                preview_abs.unlink(missing_ok=True)

        profile.avatar_last_preview_path = ""
        profile.avatar_preview_video = ""
        profile.avatar_last_preview_status = "deleted"
        profile.avatar_preview_error = ""
        profile.save(update_fields=["avatar_last_preview_path", "avatar_preview_video", "avatar_last_preview_status", "avatar_preview_error", "updated_at"])
        return Response({"status": "deleted"})


class AvatarOverlayPreferenceView(APIView):
    """GET/PUT /api/v1/projects/<project_id>/avatar-overlay/ — persist per-user per-lesson overlay UI preferences."""

    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _to_bool(value, default):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if _is_verified_teacher(request.user) and not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if not (_is_public_lesson(project) or _can_manage_project(request.user, project)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        pref, _ = AvatarOverlayPreference.objects.get_or_create(user=request.user, lesson=project)
        return Response(AvatarOverlayPreferenceSerializer(pref).data)

    def put(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if _is_verified_teacher(request.user) and not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if not (_is_public_lesson(project) or _can_manage_project(request.user, project)):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        pref, _ = AvatarOverlayPreference.objects.get_or_create(user=request.user, lesson=project)
        pref.anchor = str(request.data.get("anchor") or pref.anchor)
        pref.x_percent = float(request.data.get("x_percent", pref.x_percent))
        pref.y_percent = float(request.data.get("y_percent", pref.y_percent))
        pref.width_percent = float(request.data.get("width_percent", pref.width_percent))
        pref.visible = self._to_bool(request.data.get("visible"), pref.visible)
        pref.pinned = self._to_bool(request.data.get("pinned"), pref.pinned)
        pref.save(update_fields=["anchor", "x_percent", "y_percent", "width_percent", "visible", "pinned", "updated_at"])
        return Response(AvatarOverlayPreferenceSerializer(pref).data)


def _compat_avatar_profile_payload(user: User, profile: UserProfile) -> dict:
    avatar_asset_path = str(profile.avatar_image_processed or profile.avatar_image_original or "")
    return {
        "user_id": int(user.id),
        "avatar_enabled": bool(profile.avatar_enabled),
        "avatar_asset_path": avatar_asset_path,
        "avatar_image_original": str(profile.avatar_image_original or ""),
        "avatar_image_processed": str(profile.avatar_image_processed or ""),
        "avatar_preview_video": str(profile.avatar_preview_video or profile.avatar_last_preview_path or ""),
        "overlay_position": str(profile.avatar_overlay_default_position or "top-right"),
        "overlay_size": str(profile.avatar_overlay_size or "medium"),
        "overlay_visible": bool(profile.avatar_overlay_visible),
        "avatar_lipsync_engine": _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary),
        "avatar_quality_preset": str(profile.avatar_quality_preset or "high"),
        "avatar_motion_preset": str(profile.avatar_motion_preset or "natural"),
        "avatar_status": str(profile.avatar_image_status or ""),
        "avatar_preview_error": str(profile.avatar_preview_error or ""),
    }


class AvatarCompatProfileView(APIView):
    """GET/POST/DELETE /api/v1/avatar/profile (Engincan compatibility)."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "teacher"})
        return Response(_compat_avatar_profile_payload(request.user, profile))

    def post(self, request):
        user_id = int(request.user.id)
        if request.FILES.get("avatar_file") or request.FILES.get("avatar_video_file"):
            upload_resp = AvatarProfileView().post(request, user_id)
            if upload_resp.status_code >= 400:
                return upload_resp
        patch_resp = AvatarProfileView().patch(request, user_id)
        if patch_resp.status_code >= 400:
            return patch_resp
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "teacher"})
        return Response(_compat_avatar_profile_payload(request.user, profile))

    def patch(self, request):
        patch_resp = AvatarProfileView().patch(request, int(request.user.id))
        if patch_resp.status_code >= 400:
            return patch_resp
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "teacher"})
        return Response(_compat_avatar_profile_payload(request.user, profile))

    def delete(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "teacher"})
        profile.avatar_enabled = False
        profile.avatar_image_original = ""
        profile.avatar_image_processed = ""
        profile.avatar_video_original = ""
        profile.avatar_video_processed = ""
        profile.avatar_preview_video = ""
        profile.avatar_last_preview_path = ""
        profile.avatar_preview_error = ""
        profile.avatar_image_status = "idle"
        profile.save(update_fields=[
            "avatar_enabled",
            "avatar_image_original",
            "avatar_image_processed",
            "avatar_video_original",
            "avatar_video_processed",
            "avatar_preview_video",
            "avatar_last_preview_path",
            "avatar_preview_error",
            "avatar_image_status",
            "updated_at",
        ])
        return Response({"status": "deleted"})


class AvatarCompatUploadView(APIView):
    """POST /api/v1/avatar/upload (Engincan compatibility)."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        resp = AvatarProfileView().post(request, int(request.user.id))
        if resp.status_code >= 400:
            return resp
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "teacher"})
        return Response({
            "status": "ready",
            "avatar_asset_path": str(profile.avatar_image_processed or profile.avatar_image_original or ""),
            "profile": _compat_avatar_profile_payload(request.user, profile),
        })


class AvatarCompatPreviewView(APIView):
    """POST /api/v1/avatar/preview (Engincan compatibility)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        return AvatarPreviewRegenerateView().post(request, int(request.user.id))


class AvatarCompatPreviewStatusView(APIView):
    """GET /api/v1/avatar/preview/<job_id> (Engincan compatibility)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, job_id):
        return AvatarPreviewStatusView().get(request, int(request.user.id), int(job_id))


class AvatarCompatReadinessView(APIView):
    """GET /api/v1/avatar/readiness (Engincan compatibility)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only verified teacher accounts can use avatars."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "teacher"})
        voice_profile = VoiceProfile.objects.filter(user=request.user).first()
        readiness = _avatar_preview_readiness(
            profile,
            voice_profile,
            storage_root=Path(getattr(settings, "STORAGE_ROOT", "storage_local")),
        )
        return Response(readiness)


# ---------------------------------------------------------------------------
# Student catalog (public browsing)
# ---------------------------------------------------------------------------

class CategoryListView(APIView):
    """GET /api/v1/categories/ — public list of lesson categories."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        categories = Category.objects.all()
        return Response(CategorySerializer(categories, many=True).data)


class CatalogListView(APIView):
    """
    GET /api/v1/catalog/
    Public list of published lessons that have at least one completed render job.
    Supports ?category=<slug> filter.
    Returns only safe metadata — no raw storage paths.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        projects = (
            Project.objects.filter(is_published=True, jobs__status="done")
            .select_related("user", "category")
            .prefetch_related("jobs", "likes", "comments")
            .distinct()
            .order_by("-created_at")
        )
        category_slug = request.query_params.get("category")
        if category_slug:
            projects = projects.filter(category__slug=category_slug)
        return Response(CatalogProjectSerializer(projects, many=True, context={"request": request}).data)


class CatalogFeedView(APIView):
    """
    GET /api/v1/catalog/feed/
    Public, sectioned feed contract for the student home page.

    Placeholder ranking hooks are exposed for future personalization jobs:
      - interests (category slugs)
      - watched history (requires auth)
      - teacher filtering
      - popularity ordering
      - recency ordering
    """
    permission_classes = [permissions.AllowAny]

    def _parse_csv(self, raw_value: str, max_items: int = 20) -> list[str]:
        if not raw_value:
            return []
        parts = [part.strip() for part in raw_value.split(",")]
        cleaned = [part for part in parts if part]
        return cleaned[:max_items]

    def _normalize_rank_by(self, value: str) -> str:
        normalized = (value or "blended").strip().lower()
        if normalized not in {"blended", "popularity", "recency"}:
            return "blended"
        return normalized

    def _project_to_feed_item(
        self,
        project: Project,
        serializer_data: dict,
        progress_pct: int,
        now_ts: float,
        interest_slugs: set[str],
    ) -> dict:
        likes = project.likes.count()
        comments = project.comments.count()
        age_hours = max(1.0, (now_ts - project.created_at.timestamp()) / 3600.0)
        recency_score = round(1000.0 / (1.0 + age_hours), 2)
        popularity_score = round((likes * 7.0) + (comments * 4.0), 2)
        blended_score = round((popularity_score * 0.65) + (recency_score * 0.35), 2)

        data = dict(serializer_data)
        data.update(
            {
                "teacher_id": project.user_id,
                "teacher_username": project.user.username if project.user else "",
                "duration_minutes": max(2, (project.slides.count() or 1) * 2),
                "view_count": max(1, int((likes * 14) + (comments * 9) + 32)),
                "user_progress": progress_pct,
                "is_saved": progress_pct >= 90,
                "is_recommended": bool(
                    (project.category and project.category.slug in interest_slugs)
                    or blended_score >= 60
                ),
                "scores": {
                    "blended": blended_score,
                    "popularity": popularity_score,
                    "recency": recency_score,
                },
            }
        )
        return data

    def _take(self, items: list[dict], limit: int) -> list[dict]:
        return items[:limit]

    def _sort_items(self, items: list[dict], key_name: str) -> list[dict]:
        return sorted(items, key=lambda item: item["scores"][key_name], reverse=True)

    def get(self, request):
        query = (request.query_params.get("q") or "").strip().lower()
        category_slug = (request.query_params.get("category") or "").strip()
        teacher_id_raw = (request.query_params.get("teacher") or "").strip()
        interest_slugs = set(self._parse_csv(request.query_params.get("interests") or ""))
        rank_by = self._normalize_rank_by(request.query_params.get("rank_by") or "blended")
        watched_only = (request.query_params.get("watched") or "0").strip().lower() in {"1", "true", "yes"}

        try:
            limit = int(request.query_params.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        limit = max(4, min(24, limit))

        projects = (
            Project.objects.filter(is_published=True, jobs__status="done")
            .select_related("user", "category")
            .prefetch_related("jobs", "likes", "comments", "slides")
            .distinct()
            .order_by("-created_at")
        )

        if category_slug:
            projects = projects.filter(category__slug=category_slug)

        if teacher_id_raw.isdigit():
            projects = projects.filter(user_id=int(teacher_id_raw))

        if query:
            projects = projects.filter(title__icontains=query)

        project_list = list(projects)
        project_ids = [project.id for project in project_list]
        progress_map = {}
        if request.user and request.user.is_authenticated and project_ids:
            progress_rows = LessonProgress.objects.filter(
                user=request.user,
                project_id__in=project_ids,
            ).values_list("project_id", "progress_pct")
            progress_map = {project_id: int(progress_pct) for project_id, progress_pct in progress_rows}

        now_ts = time.time()
        serialized_map = {
            item["id"]: item
            for item in CatalogProjectSerializer(project_list, many=True, context={"request": request}).data
        }

        items = [
            self._project_to_feed_item(
                project=project,
                serializer_data=serialized_map.get(project.id, {}),
                progress_pct=progress_map.get(project.id, 0),
                now_ts=now_ts,
                interest_slugs=interest_slugs,
            )
            for project in project_list
        ]

        if watched_only:
            items = [item for item in items if item["user_progress"] > 0]

        base_items = self._sort_items(items, rank_by if rank_by in {"popularity", "recency"} else "blended")
        trending_items = self._sort_items(items, "popularity")
        recent_items = self._sort_items(items, "recency")
        continue_items = [
            item for item in base_items if item["user_progress"] > 0 and item["user_progress"] < 100
        ]

        history_items = [
            item for item in trending_items
            if "history" in (item.get("category_name") or "").lower()
        ]
        math_items = [
            item for item in trending_items
            if "math" in (item.get("category_name") or "").lower()
        ]

        publisher_stats = {}
        for item in items:
            teacher_id = item.get("teacher_id")
            if not teacher_id:
                continue
            entry = publisher_stats.setdefault(
                teacher_id,
                {
                    "teacher_id": teacher_id,
                    "teacher_name": item.get("teacher_name") or "Unknown teacher",
                    "teacher_username": item.get("teacher_username") or "",
                    "lesson_count": 0,
                    "total_popularity": 0.0,
                },
            )
            entry["lesson_count"] += 1
            entry["total_popularity"] += float(item["scores"]["popularity"])

        featured_publishers = sorted(
            publisher_stats.values(),
            key=lambda row: (row["total_popularity"], row["lesson_count"]),
            reverse=True,
        )[:10]

        sections = [
            {
                "key": "recommended",
                "title": "Recommended for you",
                "strategy": "blended_score",
                "items": self._take(base_items, limit),
            },
            {
                "key": "trending",
                "title": "Trending this week",
                "strategy": "popularity",
                "items": self._take(trending_items, limit),
            },
            {
                "key": "continue_watching",
                "title": "Continue watching",
                "strategy": "progress",
                "items": self._take(continue_items, limit),
            },
            {
                "key": "popular_history",
                "title": "Popular in History",
                "strategy": "category_popularity",
                "items": self._take(history_items, limit),
            },
            {
                "key": "popular_math",
                "title": "Popular in Math",
                "strategy": "category_popularity",
                "items": self._take(math_items, limit),
            },
            {
                "key": "recent",
                "title": "New this week",
                "strategy": "recency",
                "items": self._take(recent_items, limit),
            },
        ]

        return Response(
            {
                "sections": sections,
                "featured_publishers": featured_publishers,
                "filters": {
                    "query": query,
                    "category": category_slug,
                    "teacher": int(teacher_id_raw) if teacher_id_raw.isdigit() else None,
                    "interests": sorted(interest_slugs),
                    "watched": watched_only,
                    "rank_by": rank_by,
                    "supported_rank_by": ["blended", "popularity", "recency"],
                },
                "meta": {
                    "contract": "catalog_feed_v1",
                    "placeholder_logic": True,
                    "notes": "Ranking is heuristic and intentionally lightweight. Replace with recommendation jobs later.",
                },
            }
        )


class AdminStatsDashboardView(APIView):
    """
    GET /api/v1/admin/stats/
    Admin analytics dashboard contract.

    Uses current lesson progress, likes, comments, and project metadata to
    produce production-safe statistics. Some metrics are estimated placeholders
    and are explicitly marked for later analytics pipelines.
    """

    permission_classes = [permissions.IsAdminUser]

    def _parse_date(self, raw_value: str | None, default_date):
        if not raw_value:
            return default_date
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return default_date

    def _pct_delta(self, current_value: float, previous_value: float) -> float:
        if previous_value <= 0:
            return 100.0 if current_value > 0 else 0.0
        return round(((current_value - previous_value) / previous_value) * 100.0, 2)

    def _bounded_date_range(self, request):
        today = timezone.now().date()
        default_from = today - timedelta(days=29)
        date_from = self._parse_date(request.query_params.get("from"), default_from)
        date_to = self._parse_date(request.query_params.get("to"), today)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        max_days = 180
        if (date_to - date_from).days > max_days:
            date_from = date_to - timedelta(days=max_days)
        return date_from, date_to

    def get(self, request):
        date_from, date_to = self._bounded_date_range(request)
        from_dt = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
        to_dt = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

        category_slug = (request.query_params.get("category") or "").strip()
        teacher_raw = (request.query_params.get("teacher") or "").strip()
        sort_by = (request.query_params.get("sort") or "views").strip().lower()

        projects_qs = Project.objects.filter(jobs__status="done").select_related("user", "category").distinct()
        if category_slug:
            projects_qs = projects_qs.filter(category__slug=category_slug)
        if teacher_raw.isdigit():
            projects_qs = projects_qs.filter(user_id=int(teacher_raw))

        project_list = list(projects_qs)
        project_ids = [project.id for project in project_list]

        progress_qs = LessonProgress.objects.filter(
            project_id__in=project_ids,
            updated_at__gte=from_dt,
            updated_at__lte=to_dt,
        )
        like_qs = LessonLike.objects.filter(
            project_id__in=project_ids,
            created_at__gte=from_dt,
            created_at__lte=to_dt,
        )
        comment_qs = LessonComment.objects.filter(
            project_id__in=project_ids,
            created_at__gte=from_dt,
            created_at__lte=to_dt,
        )

        duration_map = {
            project.id: max(2, project.slides.count() * 2)
            for project in project_list
        }

        progress_rows = list(progress_qs.values("project_id", "user_id", "progress_pct"))
        progress_count = len(progress_rows)
        unique_viewers = len({row["user_id"] for row in progress_rows})
        completed_rows = [row for row in progress_rows if int(row["progress_pct"] or 0) >= 90]
        completion_rate = round((len(completed_rows) / progress_count) * 100.0, 2) if progress_count else 0.0

        estimated_views = progress_count + (like_qs.count() * 2) + comment_qs.count()
        estimated_watch_minutes = 0.0
        for row in progress_rows:
            duration = float(duration_map.get(row["project_id"], 8))
            estimated_watch_minutes += duration * (float(row["progress_pct"] or 0) / 100.0)
        estimated_watch_minutes = round(estimated_watch_minutes, 2)

        total_engagement = like_qs.count() + comment_qs.count() + progress_count

        previous_to = from_dt - timedelta(microseconds=1)
        window_days = max(1, (date_to - date_from).days + 1)
        previous_from = previous_to - timedelta(days=window_days)

        previous_progress_qs = LessonProgress.objects.filter(
            project_id__in=project_ids,
            updated_at__gte=previous_from,
            updated_at__lte=previous_to,
        )
        prev_views = previous_progress_qs.count()
        prev_unique = previous_progress_qs.values("user_id").distinct().count()
        prev_completion_count = previous_progress_qs.filter(progress_pct__gte=90).count()
        prev_completion_rate = round((prev_completion_count / prev_views) * 100.0, 2) if prev_views else 0.0

        lesson_rollup = {}
        for project in project_list:
            lesson_rollup[project.id] = {
                "lesson_id": project.id,
                "title": project.title,
                "teacher_id": project.user_id,
                "teacher_name": project.user.get_full_name() if project.user and project.user.get_full_name() else (project.user.username if project.user else "Unknown"),
                "category_slug": project.category.slug if project.category else "",
                "category_name": project.category.name if project.category else "Uncategorized",
                "views": 0,
                "unique_viewers": 0,
                "avg_completion_rate": 0.0,
                "completion_count": 0,
                "likes": 0,
                "comments": 0,
                "estimated_watch_minutes": 0.0,
            }

        by_lesson_users = {}
        by_lesson_progress_sum = {}
        for row in progress_rows:
            lesson = lesson_rollup.get(row["project_id"])
            if not lesson:
                continue
            lesson["views"] += 1
            lesson["estimated_watch_minutes"] += float(duration_map.get(row["project_id"], 8)) * (float(row["progress_pct"] or 0) / 100.0)
            if int(row["progress_pct"] or 0) >= 90:
                lesson["completion_count"] += 1
            by_lesson_users.setdefault(row["project_id"], set()).add(row["user_id"])
            by_lesson_progress_sum[row["project_id"]] = by_lesson_progress_sum.get(row["project_id"], 0.0) + float(row["progress_pct"] or 0)

        for row in like_qs.values("project_id").annotate(total=Count("id")):
            lesson = lesson_rollup.get(row["project_id"])
            if lesson:
                lesson["likes"] = int(row["total"])

        for row in comment_qs.values("project_id").annotate(total=Count("id")):
            lesson = lesson_rollup.get(row["project_id"])
            if lesson:
                lesson["comments"] = int(row["total"])

        lessons_table = []
        for lesson_id, payload in lesson_rollup.items():
            users = by_lesson_users.get(lesson_id, set())
            payload["unique_viewers"] = len(users)
            views = payload["views"]
            payload["avg_completion_rate"] = round((by_lesson_progress_sum.get(lesson_id, 0.0) / views), 2) if views else 0.0
            payload["estimated_watch_minutes"] = round(payload["estimated_watch_minutes"], 2)
            lessons_table.append(payload)

        if sort_by == "completion":
            lessons_table.sort(key=lambda item: item["avg_completion_rate"], reverse=True)
        elif sort_by == "watch_time":
            lessons_table.sort(key=lambda item: item["estimated_watch_minutes"], reverse=True)
        else:
            lessons_table.sort(key=lambda item: item["views"], reverse=True)

        publishers = {}
        for lesson in lessons_table:
            teacher_id = lesson["teacher_id"] or 0
            row = publishers.setdefault(
                teacher_id,
                {
                    "teacher_id": lesson["teacher_id"],
                    "teacher_name": lesson["teacher_name"],
                    "lesson_count": 0,
                    "views": 0,
                    "unique_viewers": 0,
                    "avg_completion_rate": 0.0,
                    "estimated_watch_minutes": 0.0,
                },
            )
            row["lesson_count"] += 1
            row["views"] += lesson["views"]
            row["unique_viewers"] += lesson["unique_viewers"]
            row["estimated_watch_minutes"] += lesson["estimated_watch_minutes"]
            row["avg_completion_rate"] += lesson["avg_completion_rate"]

        publisher_table = []
        for item in publishers.values():
            item["avg_completion_rate"] = round(item["avg_completion_rate"] / max(1, item["lesson_count"]), 2)
            item["estimated_watch_minutes"] = round(item["estimated_watch_minutes"], 2)
            publisher_table.append(item)
        publisher_table.sort(key=lambda item: item["views"], reverse=True)

        category_rows = {}
        for lesson in lessons_table:
            key = lesson["category_slug"] or "uncategorized"
            row = category_rows.setdefault(
                key,
                {
                    "category_slug": key,
                    "category_name": lesson["category_name"],
                    "lesson_count": 0,
                    "views": 0,
                    "unique_viewers": 0,
                    "avg_completion_rate": 0.0,
                    "estimated_watch_minutes": 0.0,
                },
            )
            row["lesson_count"] += 1
            row["views"] += lesson["views"]
            row["unique_viewers"] += lesson["unique_viewers"]
            row["avg_completion_rate"] += lesson["avg_completion_rate"]
            row["estimated_watch_minutes"] += lesson["estimated_watch_minutes"]

        category_table = []
        for item in category_rows.values():
            item["avg_completion_rate"] = round(item["avg_completion_rate"] / max(1, item["lesson_count"]), 2)
            item["estimated_watch_minutes"] = round(item["estimated_watch_minutes"], 2)
            category_table.append(item)
        category_table.sort(key=lambda item: item["views"], reverse=True)

        trend_points = []
        cursor = date_from
        while cursor <= date_to:
            day_start = timezone.make_aware(datetime.combine(cursor, datetime.min.time()))
            day_end = timezone.make_aware(datetime.combine(cursor, datetime.max.time()))
            day_progress = progress_qs.filter(updated_at__gte=day_start, updated_at__lte=day_end)
            views = day_progress.count()
            completions = day_progress.filter(progress_pct__gte=90).count()
            unique = day_progress.values("user_id").distinct().count()
            likes = like_qs.filter(created_at__gte=day_start, created_at__lte=day_end).count()
            comments = comment_qs.filter(created_at__gte=day_start, created_at__lte=day_end).count()
            trend_points.append(
                {
                    "date": cursor.isoformat(),
                    "views": views,
                    "unique_viewers": unique,
                    "completions": completions,
                    "engagement": likes + comments,
                }
            )
            cursor += timedelta(days=1)

        recent_activity = []
        for progress in progress_qs.select_related("user", "project").order_by("-updated_at")[:25]:
            recent_activity.append(
                {
                    "type": "progress",
                    "timestamp": progress.updated_at.isoformat(),
                    "username": progress.user.username,
                    "lesson_id": progress.project_id,
                    "lesson_title": progress.project.title,
                    "value": int(progress.progress_pct),
                    "description": f"{progress.user.username} reached {int(progress.progress_pct)}%",
                }
            )
        for like in like_qs.select_related("user", "project").order_by("-created_at")[:20]:
            recent_activity.append(
                {
                    "type": "like",
                    "timestamp": like.created_at.isoformat(),
                    "username": like.user.username,
                    "lesson_id": like.project_id,
                    "lesson_title": like.project.title,
                    "value": 1,
                    "description": f"{like.user.username} liked {like.project.title}",
                }
            )
        for comment in comment_qs.select_related("user", "project").order_by("-created_at")[:20]:
            recent_activity.append(
                {
                    "type": "comment",
                    "timestamp": comment.created_at.isoformat(),
                    "username": comment.user.username,
                    "lesson_id": comment.project_id,
                    "lesson_title": comment.project.title,
                    "value": 1,
                    "description": f"{comment.user.username} commented on {comment.project.title}",
                }
            )
        recent_activity.sort(key=lambda item: item["timestamp"], reverse=True)
        recent_activity = recent_activity[:30]

        user_category_interest = (
            progress_qs.values("user_id", "project__category__slug", "project__category__name")
            .annotate(total=Count("id"))
            .order_by("-total")[:80]
        )
        user_publisher_interest = (
            progress_qs.values("user_id", "project__user_id", "project__user__username")
            .annotate(total=Count("id"))
            .order_by("-total")[:80]
        )
        repeat_watch_users = (
            progress_qs.values("user_id")
            .annotate(lesson_count=Count("project_id", distinct=True))
            .filter(lesson_count__gte=3)
            .count()
        )

        return Response(
            {
                "summary": {
                    "lessons_published": len(project_list),
                    "video_plays": estimated_views,
                    "unique_viewers": unique_viewers,
                    "estimated_watch_time_minutes": estimated_watch_minutes,
                    "completion_rate": completion_rate,
                    "engagement_events": total_engagement,
                    "trends": {
                        "video_plays_pct": self._pct_delta(float(progress_count), float(prev_views)),
                        "unique_viewers_pct": self._pct_delta(float(unique_viewers), float(prev_unique)),
                        "completion_rate_pct": self._pct_delta(float(completion_rate), float(prev_completion_rate)),
                    },
                },
                "charts": {
                    "engagement_trend": trend_points,
                    "category_popularity": category_table[:8],
                },
                "tables": {
                    "top_lessons": lessons_table[:20],
                    "top_publishers": publisher_table[:15],
                    "top_categories": category_table[:15],
                },
                "recent_activity": recent_activity,
                "user_interest_aggregates": {
                    "top_user_categories": [
                        {
                            "user_id": row["user_id"],
                            "category_slug": row.get("project__category__slug") or "uncategorized",
                            "category_name": row.get("project__category__name") or "Uncategorized",
                            "watch_events": row["total"],
                        }
                        for row in user_category_interest
                    ],
                    "top_user_publishers": [
                        {
                            "user_id": row["user_id"],
                            "publisher_id": row.get("project__user_id"),
                            "publisher_name": row.get("project__user__username") or "Unknown",
                            "watch_events": row["total"],
                        }
                        for row in user_publisher_interest
                    ],
                    "repeat_watch": {
                        "repeat_viewers": repeat_watch_users,
                        "definition": "Users with progress across at least 3 distinct lessons in the selected range.",
                    },
                },
                "filters": {
                    "from": date_from.isoformat(),
                    "to": date_to.isoformat(),
                    "category": category_slug,
                    "teacher": int(teacher_raw) if teacher_raw.isdigit() else None,
                    "sort": sort_by,
                },
                "options": {
                    "categories": CategorySerializer(Category.objects.all(), many=True).data,
                    "publishers": [
                        {
                            "teacher_id": row["teacher_id"],
                            "teacher_name": row["teacher_name"],
                        }
                        for row in publisher_table[:50]
                        if row.get("teacher_id")
                    ],
                    "supported_sort": ["views", "completion", "watch_time"],
                },
                "meta": {
                    "contract": "admin_stats_v1",
                    "estimated_metrics": [
                        "video_plays",
                        "estimated_watch_time_minutes",
                    ],
                    "placeholder_fields": [
                        "repeat_watch",
                        "top_user_categories",
                        "top_user_publishers",
                    ],
                },
            }
        )


class CatalogDetailView(APIView):
    """
    GET /api/v1/catalog/<project_id>/
    Public lesson detail with short-lived playback tokens.
    Draft lesson detail is available to the owning teacher/publisher or staff.
    The frontend only receives tokens + metadata — never raw file paths.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, project_id):
        try:
            project = Project.objects.select_related("user", "category").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_lesson_playback(request, project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)

        job = project.jobs.filter(status="done").order_by("-created_at").first()
        if not job:
            return Response({"error": "Lesson video not ready."}, status=status.HTTP_404_NOT_FOUND)

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)
        protection_mode, mode_debug, _lesson_is_public = _resolve_playback_mode_for_project(project, sidecar)

        allow_mp4_fallback = bool(getattr(settings, "LESSON_PROTECTION_ALLOW_MP4_FALLBACK", True))
        if protection_mode == "drm_protected":
            allow_mp4_fallback = False

        hls_manifest_token = None
        avatar_token = None
        hls_encrypted = False
        asset_id = None
        content_id = None
        hls_payload = sidecar.get("hls") if isinstance(sidecar, dict) else None
        if hls_payload and hls_payload.get("manifest_rel_path"):
            hls_encrypted = bool(hls_payload.get("encrypted"))

        if isinstance(sidecar, dict):
            asset_id = sidecar.get("asset_id")
            content_id = sidecar.get("content_id")

        asset_id = asset_id or _default_asset_id(project.id)
        content_id = content_id or _default_content_id(project.id)

        ttl_seconds = _token_ttl_for_mode(protection_mode)
        grant_id = None
        bind_key = None
        playback_session_id = None
        session_binding_active = bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True))
        if protection_mode != "public":
            if not (_is_authenticated_user(request.user) and _can_manage_project(request.user, project)):
                allowed, deny_reason = _enforce_playback_concurrency(project.id, request, protection_mode)
                if not allowed:
                    return Response(
                        {
                            "error": "This lesson is already active in another browser session.",
                            "reason": deny_reason,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
            grant_id, _scope_key = _issue_playback_grant(project.id, request, protection_mode, ttl_seconds)
            bind_key = _bind_key_for_request(request) if session_binding_active else None
            playback_session_id = _playback_session_id(job.id, grant_id)
        else:
            session_binding_active = False

        if hls_payload and hls_payload.get("manifest_rel_path"):
            hls_manifest_token = generate_media_token(
                job.id,
                "hls_manifest",
                rel_path=hls_payload["manifest_rel_path"],
                ttl_seconds=ttl_seconds,
                grant_id=grant_id,
                bind_key=bind_key,
            )

        avatar_payload = sidecar.get("avatar") if isinstance(sidecar, dict) else None
        if avatar_payload and avatar_payload.get("track_rel_path") and _avatar_active_for_project(project):
            avatar_token = generate_media_token(
                job.id,
                "avatar",
                rel_path=str(avatar_payload.get("track_rel_path")),
                ttl_seconds=ttl_seconds,
                grant_id=grant_id,
                bind_key=bind_key,
            )

        video_token = generate_media_token(
            job.id,
            "video",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if allow_mp4_fallback else ""
        srt_token = generate_media_token(
            job.id,
            "srt",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if job.srt_url else None
        vtt_token = _generate_vtt_media_token_for_job(
            job,
            storage_root=getattr(settings, "STORAGE_ROOT", ""),
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if job.srt_url else None

        data = CatalogProjectSerializer(project, context={"request": request}).data
        playback = _playback_payload(
            request,
            project,
            job,
            video_token,
            srt_token,
            vtt_token=vtt_token,
            hls_manifest_token=hls_manifest_token,
            hls_encrypted=hls_encrypted,
            asset_id=asset_id,
            content_id=content_id,
            protection_mode=protection_mode,
            mode_debug=mode_debug,
            allow_mp4_fallback=allow_mp4_fallback,
            playback_session_id=playback_session_id,
            session_binding_active=session_binding_active,
            avatar_token=avatar_token,
            avatar_overlay_defaults=_avatar_overlay_defaults_for_project(project),
        )
        data["stream_url"] = playback["video_url"]
        data["srt_url"] = playback["srt_url"]
        data["vtt_url"] = playback.get("vtt_url")
        data["subtitle_vtt_url"] = playback.get("subtitle_vtt_url")
        data["has_srt"] = bool(job.srt_url)
        data["has_vtt"] = bool(playback.get("vtt_url"))
        data["expires_in"] = playback["expires_in"]
        data["watermark"] = playback["watermark"]
        data["protection"] = playback["protection"]
        data["drm"] = playback["drm"]
        data["avatar_overlay"] = playback.get("avatar_overlay", {"enabled": False, "stream_url": "", "defaults": {}})
        data["avatar_active_for_lesson"] = _avatar_active_for_project(project)
        data["playback_status"] = playback.get("playback_status")
        data["mode_debug"] = playback.get("mode_debug")
        data["transcript_pages"] = _project_transcript_timeline(project)
        data["like_count"] = project.likes.count()
        data["comment_count"] = project.comments.count()

        if request.user and request.user.is_authenticated:
            data["user_liked"] = project.likes.filter(user=request.user).exists()
            progress = project.progress_records.filter(user=request.user).first()
            data["user_progress"] = progress.progress_pct if progress else 0
        else:
            data["user_liked"] = False
        data["user_progress"] = 0

        logger.info(
            "Catalog playback payload issued: project_id=%s job_id=%s mode=%s has_grant=%s has_hls=%s mp4_fallback=%s",
            project.id,
            job.id,
            protection_mode,
            bool(grant_id),
            bool(hls_manifest_token),
            bool(video_token),
        )

        return Response(data)


# ---------------------------------------------------------------------------
# Student social features (authentication required)
# ---------------------------------------------------------------------------

class LessonLikeView(APIView):
    """POST /api/v1/catalog/<project_id>/like/ — toggle like (auth required)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_public_lesson(project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        like, created = LessonLike.objects.get_or_create(user=request.user, project=project)
        if not created:
            like.delete()
            return Response({"liked": False, "like_count": project.likes.count()})
        return Response({"liked": True, "like_count": project.likes.count()})


class LessonProgressView(APIView):
    """POST /api/v1/catalog/<project_id>/progress/ — save watch progress (auth required)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_public_lesson(project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            pct = max(0, min(100, int(request.data.get("progress_pct", 0))))
        except (TypeError, ValueError):
            return Response(
                {"error": "progress_pct must be 0-100."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        LessonProgress.objects.update_or_create(
            user=request.user,
            project=project,
            defaults={"progress_pct": pct},
        )
        return Response({"progress_pct": pct})


class LessonCommentsView(APIView):
    """
    GET  /api/v1/catalog/<project_id>/comments/ — list comments (public)
    POST /api/v1/catalog/<project_id>/comments/ — add comment (auth required)
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_public_lesson(project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        comments = project.comments.select_related("user").order_by("-created_at")[:50]
        return Response(LessonCommentSerializer(comments, many=True).data)

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_public_lesson(project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"error": "Comment text is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 2000:
            return Response(
                {"error": "Comment too long (max 2000 chars)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        comment = LessonComment.objects.create(user=request.user, project=project, text=text)
        return Response(LessonCommentSerializer(comment).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Phase 1 - TTS preview  POST /api/v1/tts/preview/
# ---------------------------------------------------------------------------

class TTSPreviewView(APIView):
    """
    POST /api/v1/tts/preview/

    Accepts a preview payload and proxies it to the TTS service
    /normalization/preview endpoint via direct HTTP proxy to the TTS service.

    Does NOT enqueue Celery tasks.
    Does NOT synthesize audio.
    Does NOT require an existing Project.
    Fails open: always returns JSON even if the TTS service is unavailable.

    Auth: IsAuthenticated.
    """

    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _to_bool(value, default=True):
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _resolve_preview_language(text: str, language: str | None) -> str:
        cleaned = str(language or "").strip().split("-")[0].split("_")[0].lower()
        if cleaned in {"tr", "en"}:
            return cleaned

        sample = str(text or "").lower()[:6000]
        if not sample.strip():
            return "tr"

        turkish_chars = set("çğıöşüÇĞİÖŞÜ")
        turkish_words = {"ve", "bir", "için", "olan", "de", "da", "ile", "bu", "çok", "değil"}
        english_words = {"the", "and", "with", "for", "of", "is", "this", "that"}
        tr_char_hits = sum(1 for ch in sample if ch in turkish_chars)
        tokens = set(re.findall(r"[a-zçğıöşü]+", sample, flags=re.IGNORECASE))
        tr_word_hits = sum(1 for token in turkish_words if token in tokens)
        en_word_hits = sum(1 for token in english_words if token in tokens)

        if tr_char_hits >= 1 or tr_word_hits > en_word_hits or (tr_word_hits >= 1 and en_word_hits == 0):
            return "tr"
        return "en"

    @staticmethod
    def _load_tts_preprocess_helpers():
        try:
            from tts_preprocess import get_preprocess_config, prepare_text_for_tts
            from tts_preprocess.glossary import apply_glossary_with_rules
            from tts_preprocess.segmenter import split_text_to_chunks
        except ModuleNotFoundError:
            tts_root = Path(__file__).resolve().parents[2] / "tts_service"
            if tts_root.exists() and str(tts_root) not in sys.path:
                sys.path.insert(0, str(tts_root))
            from tts_preprocess import get_preprocess_config, prepare_text_for_tts
            from tts_preprocess.glossary import apply_glossary_with_rules
            from tts_preprocess.segmenter import split_text_to_chunks
        return get_preprocess_config, prepare_text_for_tts, apply_glossary_with_rules, split_text_to_chunks

    @staticmethod
    def _preview_override_glossary(
        technical_overrides: dict[str, str] | None,
        abbreviation_overrides: dict[str, str] | None,
        mixed_word_overrides: dict[str, str] | None,
    ) -> dict[str, str]:
        merged: dict[str, str] = {}
        for source in (
            technical_overrides or {},
            abbreviation_overrides or {},
            mixed_word_overrides or {},
        ):
            for term, spoken in source.items():
                cleaned_term = str(term or "").strip()
                cleaned_spoken = str(spoken or "").strip()
                if cleaned_term and cleaned_spoken:
                    merged[cleaned_term] = cleaned_spoken
        return merged

    @staticmethod
    def _restore_preview_overrides(text: str, replacement_map: dict[str, str]) -> str:
        restored = str(text or "")
        for placeholder, replacement in replacement_map.items():
            restored = restored.replace(placeholder, replacement)
        return restored

    @staticmethod
    def _raw_fail_open_payload(
        text: str,
        language: str,
        normalization_enabled: bool,
        normalization_mode: str,
        unknown_word_strategy: str,
        reason: str,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        warning_values = warnings or [f"tts_preview_service_unavailable:{reason}"]
        return {
            "original_text": text,
            "normalized_text": text,
            "spoken_text": text,
            "used_text": text,
            "chunks": [text] if text else [],
            "chunk_pause_ms": [0] if text else [],
            "tts_normalization_language": language,
            "tts_normalization_rules_applied": [],
            "unknown_terms": [],
            "ambiguous_terms": [],
            "normalization_enabled": normalization_enabled,
            "normalization_mode": normalization_mode,
            "unknown_word_strategy": unknown_word_strategy,
            "resolved_language": language,
            "applied_overrides": {},
            "warnings": warning_values,
            "error": reason,
            "fallback_used": True,
        }

    @classmethod
    def _fail_open_payload(
        cls,
        text: str,
        language: str,
        normalization_enabled: bool,
        normalization_mode: str,
        unknown_word_strategy: str,
        reason: str,
        technical_overrides: dict[str, str] | None = None,
        abbreviation_overrides: dict[str, str] | None = None,
        mixed_word_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        warnings = [f"tts_preview_service_unavailable:{reason}"]
        applied_overrides = {
            "technical_overrides": technical_overrides or {},
            "abbreviation_overrides": abbreviation_overrides or {},
            "mixed_word_overrides": mixed_word_overrides or {},
        }
        applied_overrides["merged_override_count"] = len(
            cls._preview_override_glossary(
                technical_overrides,
                abbreviation_overrides,
                mixed_word_overrides,
            )
        )

        if not normalization_enabled:
            return {
                **cls._raw_fail_open_payload(
                    text=text,
                    language=language,
                    normalization_enabled=normalization_enabled,
                    normalization_mode=normalization_mode,
                    unknown_word_strategy=unknown_word_strategy,
                    reason=reason,
                    warnings=[*warnings, "normalization_disabled"],
                ),
                "applied_overrides": applied_overrides,
            }

        try:
            (
                get_preprocess_config,
                prepare_text_for_tts,
                apply_glossary_with_rules,
                split_text_to_chunks,
            ) = cls._load_tts_preprocess_helpers()
            override_glossary = cls._preview_override_glossary(
                technical_overrides,
                abbreviation_overrides,
                mixed_word_overrides,
            )
            source_text = text
            replacement_map: dict[str, str] = {}
            pre_rules: list[dict[str, Any]] = []
            if override_glossary:
                placeholder_glossary: dict[str, str] = {}
                for index, (term, replacement) in enumerate(override_glossary.items()):
                    placeholder = f"__API_PREVIEW_OVERRIDE_{index}__"
                    placeholder_glossary[term] = placeholder
                    replacement_map[placeholder] = replacement
                source_text, pre_rules = apply_glossary_with_rules(source_text, placeholder_glossary, language=language)
                for rule in pre_rules:
                    rule["source"] = "preview_pre_override"
                    replacement = rule.get("replacement")
                    if isinstance(replacement, str) and replacement in replacement_map:
                        rule["actual_replacement"] = replacement_map[replacement]

            prepared = prepare_text_for_tts(source_text, language=language)
            spoken_text = cls._restore_preview_overrides(prepared.spoken_text, replacement_map)
            normalized_text = cls._restore_preview_overrides(prepared.normalized_text, replacement_map)
            cfg = get_preprocess_config()
            chunks, chunk_pause_ms, chunk_warnings = split_text_to_chunks(
                spoken_text,
                max_chars=cfg.max_chars_per_chunk,
                target_chars=cfg.target_chars_per_chunk,
                sentence_pause_ms=cfg.sentence_pause_ms,
                paragraph_pause_ms=cfg.paragraph_pause_ms,
            )
            warnings.extend(w for w in prepared.warnings if w not in warnings)
            warnings.extend(w for w in chunk_warnings if w not in warnings)
            return {
                "original_text": text,
                "normalized_text": normalized_text,
                "spoken_text": spoken_text,
                "used_text": spoken_text,
                "chunks": chunks,
                "chunk_pause_ms": chunk_pause_ms,
                "tts_normalization_language": prepared.tts_normalization_language,
                "tts_normalization_rules_applied": pre_rules + list(prepared.tts_normalization_rules_applied or []),
                "unknown_terms": list(prepared.unknown_terms or []),
                "ambiguous_terms": list(prepared.ambiguous_terms or []),
                "normalization_enabled": normalization_enabled,
                "normalization_mode": normalization_mode,
                "unknown_word_strategy": unknown_word_strategy,
                "resolved_language": prepared.tts_normalization_language,
                "applied_overrides": applied_overrides,
                "warnings": warnings,
                "error": reason,
                "fallback_used": True,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTSPreviewView: local fail-open normalization failed: %s", exc)
            return {
                **cls._raw_fail_open_payload(
                    text=text,
                    language=language,
                    normalization_enabled=normalization_enabled,
                    normalization_mode=normalization_mode,
                    unknown_word_strategy=unknown_word_strategy,
                    reason=reason,
                    warnings=[*warnings, f"tts_preview_local_fallback_failed:{exc.__class__.__name__}"],
                ),
                "applied_overrides": applied_overrides,
            }

    def post(self, request):
        data = request.data or {}
        text = str(data.get("text") or "").strip()
        if not text:
            return Response({"error": "text is required"}, status=status.HTTP_400_BAD_REQUEST)

        requested_language = str(data.get("language") or "auto")
        language = self._resolve_preview_language(text, requested_language)
        normalization_enabled = self._to_bool(data.get("normalization_enabled"), True)
        normalization_mode = str(data.get("normalization_mode") or "loose")
        unknown_word_strategy = str(data.get("unknown_word_strategy") or "keep")
        technical_overrides = data.get("technical_overrides") or {}
        abbreviation_overrides = data.get("abbreviation_overrides") or {}
        mixed_word_overrides = data.get("mixed_word_overrides") or {}

        if not isinstance(technical_overrides, dict):
            technical_overrides = {}
        if not isinstance(abbreviation_overrides, dict):
            abbreviation_overrides = {}
        if not isinstance(mixed_word_overrides, dict):
            mixed_word_overrides = {}

        service_url = (
            str(getattr(settings, "TTS_SERVICE_URL", "") or os.environ.get("TTS_SERVICE_URL") or "http://tts_service:8001")
            .rstrip("/")
        )
        endpoint = f"{service_url}/normalization/preview"
        payload: dict[str, Any] = {
            "text": text,
            "language": language,
            "normalization_enabled": normalization_enabled,
            "normalization_mode": normalization_mode,
            "unknown_word_strategy": unknown_word_strategy,
        }
        if technical_overrides:
            payload["technical_overrides"] = technical_overrides
        if abbreviation_overrides:
            payload["abbreviation_overrides"] = abbreviation_overrides
        if mixed_word_overrides:
            payload["mixed_word_overrides"] = mixed_word_overrides

        try:
            result = open_json(
                endpoint,
                method="POST",
                body=payload,
                timeout=5.0,
                request=request,
            )
            if not isinstance(result, dict):
                raise ValueError("invalid_json_payload")
            result.setdefault("fallback_used", False)
            result.setdefault("error", None)
            result["requested_language"] = requested_language
            result["resolved_language"] = str(result.get("tts_normalization_language") or language)
            return Response(result, status=status.HTTP_200_OK)
        except HTTPError as exc:
            reason = f"http_{exc.code}"
        except URLError as exc:
            reason = f"url_error:{exc.reason}"
        except TimeoutError:
            reason = "timeout"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            reason = "invalid_json"
        except Exception as exc:  # noqa: BLE001
            reason = f"unexpected_error:{exc.__class__.__name__}"

        logger.warning("TTSPreviewView: fail-open response from proxy path: %s", reason)
        return Response(
            self._fail_open_payload(
                text=text,
                language=language,
                normalization_enabled=normalization_enabled,
                normalization_mode=normalization_mode,
                unknown_word_strategy=unknown_word_strategy,
                reason=reason,
                technical_overrides=technical_overrides,
                abbreviation_overrides=abbreviation_overrides,
                mixed_word_overrides=mixed_word_overrides,
            ),
            status=status.HTTP_200_OK,
        )


class TTSPreviewAudioView(APIView):
    """
    POST /api/v1/tts/preview-audio/

    Synthesizes a short, authenticated, display-only preview sample through the
    existing TTS service fallback chain. It does not mutate project settings,
    transcript text, or captions.
    """

    permission_classes = [permissions.IsAuthenticated]
    MAX_PREVIEW_AUDIO_CHARS = 400
    MAX_AUDIO_BYTES = 5 * 1024 * 1024

    def post(self, request):
        data = request.data or {}
        raw_text = str(data.get("text") or "").strip()
        if not raw_text:
            return Response({"error": "text is required"}, status=status.HTTP_400_BAD_REQUEST)

        warnings: list[str] = []
        text = raw_text
        if len(text) > self.MAX_PREVIEW_AUDIO_CHARS:
            text = text[: self.MAX_PREVIEW_AUDIO_CHARS].rstrip()
            warnings.append(f"preview_audio_truncated_to_{self.MAX_PREVIEW_AUDIO_CHARS}_chars")

        requested_language = str(data.get("language") or "auto")
        language = TTSPreviewView._resolve_preview_language(text, requested_language)
        payload: dict[str, Any] = {
            "text": text,
            "voice_id": _get_voice_id(request.user),
            "language": language,
            "normalization_enabled": TTSPreviewView._to_bool(data.get("normalization_enabled"), True),
            "normalization_mode": str(data.get("normalization_mode") or "loose"),
            "unknown_word_strategy": str(data.get("unknown_word_strategy") or "keep"),
            "provider_preference": str(data.get("provider_preference") or "auto"),
        }

        for source_key in ("technical_overrides", "abbreviation_overrides", "mixed_word_overrides"):
            value = data.get(source_key)
            if isinstance(value, dict) and value:
                payload[source_key] = value

        service_url = (
            str(getattr(settings, "TTS_SERVICE_URL", "") or os.environ.get("TTS_SERVICE_URL") or "http://tts_service:8001")
            .rstrip("/")
        )

        try:
            synth_result = open_json(
                f"{service_url}/synthesize",
                method="POST",
                body=payload,
                timeout=20.0,
                request=request,
            )
            if not isinstance(synth_result, dict):
                raise ValueError("invalid_synthesize_payload")
            audio_url = str(synth_result.get("audio_url") or "").strip()
            if not audio_url:
                raise ValueError("missing_audio_url")

            audio_bytes, audio_headers = open_bytes(
                audio_url,
                method="GET",
                headers={"Accept": "audio/mpeg"},
                timeout=20.0,
                max_bytes=self.MAX_AUDIO_BYTES,
                request=request,
            )
            content_type = str(audio_headers.get("Content-Type") or "audio/mpeg").split(";", 1)[0]
            if len(audio_bytes) > self.MAX_AUDIO_BYTES:
                raise ValueError("audio_preview_too_large")
            if not audio_bytes:
                raise ValueError("empty_audio_preview")

            encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
            return Response(
                {
                    "audio_data_url": f"data:{content_type};base64,{encoded_audio}",
                    "content_type": content_type,
                    "duration": synth_result.get("duration"),
                    "provider": synth_result.get("provider"),
                    "fallback_used": bool(synth_result.get("fallback_used")),
                    "fallback_reason": str(synth_result.get("fallback_reason") or synth_result.get("message") or ""),
                    "requested_language": requested_language,
                    "resolved_language": str(synth_result.get("tts_normalization_language") or language),
                    "warnings": [*warnings, *list(synth_result.get("preprocessing_warnings") or [])],
                },
                status=status.HTTP_200_OK,
            )
        except HTTPError as exc:
            reason = f"http_{exc.code}"
        except URLError as exc:
            reason = f"url_error:{exc.reason}"
        except TimeoutError:
            reason = "timeout"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            reason = str(exc) or "invalid_json"
        except Exception as exc:  # noqa: BLE001
            reason = f"unexpected_error:{exc.__class__.__name__}"

        logger.warning("TTSPreviewAudioView failed: %s", reason)
        return Response({"error": "Failed to synthesize preview audio.", "details": reason}, status=status.HTTP_502_BAD_GATEWAY)


class TTSPronunciationSuggestionsView(APIView):
    """
    POST /api/v1/tts/pronunciation-suggestions/

    Optional Studio-assisted LLM suggestions for D1 resolver terms. This view
    does not synthesize audio, enqueue render work, mutate transcripts, or save
    project TTS settings.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data or {}
        terms = data.get("terms")
        if not isinstance(terms, list):
            return Response({"error": "terms must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        project_id = data.get("project_id")
        if project_id not in (None, "", "null"):
            try:
                project = Project.objects.get(pk=int(project_id))
            except (TypeError, ValueError, Project.DoesNotExist):
                return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
            if not _can_manage_project(request.user, project):
                return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        result = pronunciation_suggestion_response(
            language=data.get("language") or "tr",
            raw_terms=terms,
            raw_context=data.get("context") or "",
        )
        return Response(result, status=status.HTTP_200_OK)


class _NotImplementedCompatView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return Response({"error": "Endpoint temporarily unavailable."}, status=status.HTTP_501_NOT_IMPLEMENTED)

    def post(self, request, *args, **kwargs):
        return Response({"error": "Endpoint temporarily unavailable."}, status=status.HTTP_501_NOT_IMPLEMENTED)


class ProjectSubtitleTrackListView(_NotImplementedCompatView):
    pass


class ProjectBackgroundApplyAllView(_NotImplementedCompatView):
    pass


class TranscriptPageBackgroundImageView(_NotImplementedCompatView):
    pass


class TranscriptPageBackgroundUploadView(_NotImplementedCompatView):
    pass


class TranscriptPageSceneView(_NotImplementedCompatView):
    pass


class UserFollowingView(_NotImplementedCompatView):
    pass


class UserHistoryView(_NotImplementedCompatView):
    pass


class UserLikedLessonsView(_NotImplementedCompatView):
    pass
