"""
API views for AI_ACADEMY.

Auth:
  POST /api/v1/auth/login/   LoginView
  POST /api/v1/auth/logout/  LogoutView
  GET  /api/v1/auth/me/      MeView
  GET/PATCH /api/v1/me/profile/ CurrentUserProfileView
  GET  /api/v1/help/         HelpContentView

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
from copy import deepcopy
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
from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Exists, Max, OuterRef, Prefetch
from django.core.cache import cache
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import UnsupportedMediaType, ValidationError
from rest_framework.authtoken.models import Token
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from PIL import Image, ImageOps, UnidentifiedImageError

from core.models import (
    AnalyticsIntelligenceReport,
    AvatarRenderJob,
    AvatarOverlayPreference,
    Category,
    Job,
    LessonComment,
    LessonIntelligenceReport,
    LessonLike,
    LessonProgress,
    Notification,
    Playlist,
    PlaylistItem,
    Project,
    PublisherFollow,
    SavedPlaylist,
    SiteHelpContent,
    Slide,
    TranscriptPage,
    TranslatedSubtitleTrack,
    UserProfile,
    VoiceProfile,
)
from core.serializers import (
    AvatarOverlayPreferenceSerializer,
    AvatarRenderJobSerializer,
    CatalogProjectSerializer,
    CategorySerializer,
    CurrentUserProfileSerializer,
    JobSerializer,
    LessonCommentSerializer,
    NotificationSerializer,
    PlaylistPublicSerializer,
    PlaylistSerializer,
    ProjectCreateSerializer,  # noqa: F401
    ProjectSerializer,
    SlideSerializer,
    SiteHelpContentSerializer,
    TranscriptPageSerializer,
    UserSerializer,
    canonical_project_tts_settings,
    merge_project_tts_settings_patch,
)
from core.analytics_intelligence import (
    AnalyticsIntelligenceInputError,
    AnalyticsIntelligenceInputTooLarge,
    analyze_analytics_heuristic_immediate,
    analytics_intelligence_enabled,
    analytics_provider_chain_from_settings,
    analytics_report_response_payload,
    analyze_analytics_with_provider_chain,
    progressive_analytics_ollama_enabled,
    apply_analytics_analysis_to_report,
    build_analytics_intelligence_input,
    analytics_ollama_run_identity,
)
from core.drafts import (
    ensure_project_draft_data,
    get_draft_project_fields,
    get_project_draft_data,
    get_studio_transcript_pages,
    has_dirty_draft,
    has_project_draft,
    save_project_draft_data,
)
from core.lesson_intelligence import (
    LessonIntelligenceInputError,
    LessonIntelligenceInputTooLarge,
    analyze_lesson_heuristic_immediate,
    analyze_with_provider_chain,
    apply_analysis_to_report,
    build_lesson_intelligence_input,
    lesson_ollama_run_identity,
    lesson_intelligence_enabled,
    progressive_ollama_enabled,
    provider_chain_from_settings,
    report_response_payload,
)
from core.intelligence_progressive import (
    PENDING_ENHANCEMENT_STATUSES,
    PROGRESSIVE_ENHANCEMENT_KEY,
    enhancement_lock_key,
    enhancement_from_metadata,
    enhancement_metadata,
    intelligence_retry_cooldown_seconds,
    lesson_section_statuses,
    merge_enhancement_metadata,
    provider_attempt as progressive_provider_attempt,
    safe_enhancement_error,
)
from core.tts_llm_suggestions import pronunciation_suggestion_response
from core.avatar_readiness import avatar_preview_readiness, normalize_avatar_engine
from core.avatar_placement import (
    apply_avatar_placement_to_preference,
    normalize_avatar_placement,
    project_avatar_placement,
)
from core.avatar_runtime_settings import (
    project_avatar_runtime_settings,
    save_project_avatar_runtime_settings,
)
from core.avatar_image_moderation import (
    avatar_image_moderation_auto_enabled,
    avatar_image_moderation_gate,
    run_avatar_image_moderation,
)
from core.avatar_source_validation import (
    refresh_avatar_source_validation,
    stored_avatar_source_state,
)
from ai_agents.policies import (
    APPROVED_MODERATION_STATUSES,
    moderation_is_approved_for_catalog,
    publication_block_payload,
    project_can_publish,
)

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
_RUN_PROJECT_MODERATION_TASK = "worker.tasks.run_project_moderation"
_AVATAR_PREVIEW_TASK = "worker.tasks.render_avatar_preview"
_AVATAR_OVERLAY_TASK = "worker.tasks.render_lesson_avatar_overlay"
_SUBTITLE_TRANSLATION_TASK = "worker.tasks.generate_translated_subtitle_track_task"
_LESSON_INTELLIGENCE_ENHANCEMENT_TASK = "worker.tasks.enhance_lesson_intelligence_report"
_ANALYTICS_INTELLIGENCE_ENHANCEMENT_TASK = "worker.tasks.enhance_analytics_intelligence_report"
_LESSON_INTELLIGENCE_SCHEDULE_TASK = "worker.tasks.schedule_lesson_intelligence"
_ANALYTICS_INTELLIGENCE_SCHEDULE_TASK = "worker.tasks.schedule_creator_analytics_intelligence"


def _celery_queue_setting(setting_name: str, env_name: str, default: str) -> str:
    value = str(getattr(settings, setting_name, os.environ.get(env_name, default)) or default).strip()
    return value or default


def _render_queue_name() -> str:
    return _celery_queue_setting("CELERY_RENDER_QUEUE", "CELERY_RENDER_QUEUE", "render")


def _avatar_queue_name() -> str:
    return _celery_queue_setting("CELERY_AVATAR_QUEUE", "CELERY_AVATAR_QUEUE", "avatar")


def _intelligence_queue_name() -> str:
    value = str(
        getattr(
            settings,
            "INTELLIGENCE_CELERY_QUEUE",
            os.environ.get("INTELLIGENCE_CELERY_QUEUE")
            or os.environ.get("CELERY_INTELLIGENCE_QUEUE")
            or _render_queue_name(),
        )
        or _render_queue_name()
    ).strip()
    return value or _render_queue_name()


def _lesson_intelligence_queue_name() -> str:
    shared = _intelligence_queue_name()
    configured = str(getattr(settings, "INTELLIGENCE_LESSON_CELERY_QUEUE", "") or "").strip()
    if os.environ.get("INTELLIGENCE_LESSON_CELERY_QUEUE") is not None:
        return configured or shared
    default_queue = str(getattr(settings, "INTELLIGENCE_CELERY_QUEUE_DEFAULT", "") or "").strip()
    if configured and configured not in {default_queue, _render_queue_name()}:
        return configured
    return shared


def _analytics_intelligence_queue_name() -> str:
    shared = _intelligence_queue_name()
    configured = str(getattr(settings, "INTELLIGENCE_ANALYTICS_CELERY_QUEUE", "") or "").strip()
    if os.environ.get("INTELLIGENCE_ANALYTICS_CELERY_QUEUE") is not None:
        return configured or shared
    default_queue = str(getattr(settings, "INTELLIGENCE_CELERY_QUEUE_DEFAULT", "") or "").strip()
    if configured and configured not in {default_queue, _render_queue_name()}:
        return configured
    return shared


def _queue_for_avatar_options(avatar_options: dict | None) -> str:
    return _avatar_queue_name() if bool((avatar_options or {}).get("enabled")) else _render_queue_name()


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

_MAX_LESSON_BYTES = 100 * 1024 * 1024  # 100 MB
_ALLOWED_EXTENSIONS = {".pptx", ".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_MAX_PROFILE_ASSET_BYTES = 8 * 1024 * 1024  # 8 MB
_ALLOWED_PROFILE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_PROFILE_ASSET_KINDS = {"banner", "logo"}
logger = logging.getLogger(__name__)
_CATALOG_CACHE_TTL_SECONDS = int(os.environ.get("CATALOG_CACHE_TTL_SECONDS", "30"))


def _catalog_cache_key(prefix: str, request) -> str:
    query_items = sorted((request.query_params or {}).items())
    raw_query = urlencode(query_items, doseq=True)
    query_hash = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()[:16]
    return f"catalog:{prefix}:v1:{query_hash}"


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
    if mode in {"secure_stream", "studio_preview"}:
        return int(getattr(settings, "LESSON_PROTECTION_TOKEN_TTL_SECURE_SECONDS", _token_ttl()))
    return int(getattr(settings, "LESSON_PROTECTION_TOKEN_TTL_PUBLIC_SECONDS", _token_ttl()))


def _stream_url(request, token: str) -> str:
    return request.build_absolute_uri(f"/api/v1/stream/{token}/")


def _playback_identity(request) -> str:
    if request.user and request.user.is_authenticated:
        return f"user:{request.user.id}"
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


def _issue_playback_grant(
    lesson_id: int,
    request,
    mode: str,
    ttl_seconds: int,
    *,
    bind_to_session: bool | None = None,
) -> tuple[str, str]:
    identity = _playback_identity(request)
    session_binding_active = (
        bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True))
        if bind_to_session is None
        else bool(bind_to_session)
    )
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
        limits = {"hls_manifest": 8, "hls_key": 24, "hls_segment": 800, "video": 0, "avatar": 600, "srt": 80, "vtt": 80}
        return limits.get(file_type, 60)
    if mode in {"secure_stream", "studio_preview"}:
        limits = {"hls_manifest": 20, "hls_key": 60, "hls_segment": 2000, "video": 200, "avatar": 1200, "srt": 150, "vtt": 150}
        return limits.get(file_type, 120)
    return 10_000


def _check_studio_preview_grant_access(
    *,
    grant_id: str,
    grant_payload: dict,
    lesson_id: int,
    bind_key: str | None,
    file_type: str,
) -> bool:
    mode = "studio_preview"
    if bind_key or grant_payload.get("bind_key"):
        logger.warning("Studio preview grant unexpectedly bound to session: lesson=%s", lesson_id)
        return False
    if int(grant_payload.get("lesson_id") or 0) != int(lesson_id):
        logger.warning("Studio preview grant lesson mismatch: lesson=%s", lesson_id)
        return False

    now = int(time.time())
    expires_at = int(grant_payload.get("expires_at") or 0)
    if expires_at and now > expires_at:
        logger.warning("Studio preview grant expired for lesson=%s", lesson_id)
        _revoke_grant(grant_id, reason="expired", lesson_id=lesson_id, mode=mode)
        return False

    issuer_identity = str(grant_payload.get("identity") or "")
    logout_epoch = cache.get(_logout_epoch_key_for(issuer_identity)) if issuer_identity else 0
    if issuer_identity and int(grant_payload.get("issued_at") or 0) <= int(logout_epoch or 0):
        logger.warning("Studio preview grant denied after issuer logout for lesson=%s", lesson_id)
        return False

    last_seen = int(grant_payload.get("last_seen_at") or grant_payload.get("issued_at") or now)
    if now - last_seen > _playback_inactivity_ttl():
        logger.warning("Studio preview grant inactive too long for lesson=%s", lesson_id)
        _revoke_grant(grant_id, reason="inactive", lesson_id=lesson_id, mode=mode)
        return False

    hidden_since = grant_payload.get("hidden_since")
    if hidden_since and (now - int(hidden_since) > _playback_hidden_grace_ttl()):
        logger.warning("Studio preview grant hidden too long for lesson=%s", lesson_id)
        _revoke_grant(grant_id, reason="hidden_too_long", lesson_id=lesson_id, mode=mode)
        return False

    usage_key = f"playback:usage:{grant_id}:{file_type}"
    usage = int(cache.get(usage_key) or 0) + 1
    cache.set(usage_key, usage, timeout=_token_ttl_for_mode(mode))
    max_usage = _grant_usage_limit(file_type, mode)
    if usage > max_usage:
        logger.warning(
            "Suspicious studio preview usage: lesson=%s file_type=%s usage=%s limit=%s",
            lesson_id,
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


def _check_grant_access(request, *, lesson_id: int, grant_id: str | None, bind_key: str | None, mode: str, file_type: str) -> bool:
    if not grant_id:
        if mode == "drm_protected":
            return False
        logger.info("Legacy token access without playback grant: lesson=%s mode=%s file_type=%s", lesson_id, mode, file_type)
        return True

    grant_payload = cache.get(_grant_key_for(grant_id))
    if not grant_payload:
        logger.warning("Playback grant missing/expired for lesson=%s mode=%s", lesson_id, mode)
        return False

    if grant_payload.get("revoked"):
        logger.warning("Playback grant revoked for lesson=%s mode=%s", lesson_id, mode)
        return False

    grant_mode = str(grant_payload.get("mode") or "").strip().lower()
    if grant_mode == "studio_preview":
        return _check_studio_preview_grant_access(
            grant_id=grant_id,
            grant_payload=grant_payload,
            lesson_id=lesson_id,
            bind_key=bind_key,
            file_type=file_type,
        )

    identity = _playback_identity(request)
    scope_key = _scope_key_for(lesson_id, identity, mode)
    current_grant_id = cache.get(scope_key)
    if current_grant_id != grant_id:
        logger.warning("Playback grant invalidated or mismatched for lesson=%s mode=%s", lesson_id, mode)
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

    if env_mode == "public" and sidecar_mode == "secure_stream":
        return "public", {
            "effective_mode": "public",
            "source": "env_default_public_override",
            "env_default_mode": env_mode,
            "sidecar_mode": sidecar_mode,
            "sidecar_override_applied": True,
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
    lesson_is_public = _is_public_lesson(project) or _is_published_playable_lesson(project)
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


def _split_text_on_blank_lines(value: Any) -> list[str]:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]


def _normalized_text_for_compare(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _text_flags_from_editor_document(editor_document: Any, *, original_text: str = "", narration_text: str = "") -> dict:
    text_flags = {}
    if isinstance(editor_document, dict) and isinstance(editor_document.get("text"), dict):
        text_flags = dict(editor_document.get("text") or {})
    narration_customized = bool(text_flags.get("narration_customized"))
    display_text_customized = bool(text_flags.get("display_text_customized"))
    if not text_flags:
        original_normalized = re.sub(r"\s+", " ", str(original_text or "")).strip()
        narration_normalized = re.sub(r"\s+", " ", str(narration_text or "")).strip()
        narration_customized = bool(narration_normalized and original_normalized != narration_normalized)
    return {
        "narration_customized": narration_customized,
        "display_text_customized": display_text_customized,
    }


def _build_editor_document(display_text: str, rich_text_html: str, *, text_flags: dict | None = None) -> dict:
    raw = str(display_text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [line for line in raw.split("\n")]
    return {
        "version": 1,
        "text": {
            "narration_customized": bool((text_flags or {}).get("narration_customized")),
            "display_text_customized": bool((text_flags or {}).get("display_text_customized")),
        },
        "paragraphs": [
            {
                "index": idx,
                "text": paragraph,
            }
            for idx, paragraph in enumerate(paragraphs)
        ],
        "html": str(rich_text_html or ""),
    }


SCENE_BACKGROUND_MODES = {"original", "whiteboard", "custom", "source_background"}
SCENE_BACKGROUND_FITS = {"contain", "cover", "stretch"}
SCENE_TEXT_SCALE_MIN = 0.75
SCENE_TEXT_SCALE_MAX = 2.0
SOURCE_BACKGROUND_SUPPORTED_TYPES = {"pptx"}
SCENE_HIGHLIGHT_STYLES = {"none", "box", "bold"}
SCENE_HIGHLIGHT_DETECTORS = {"auto"}
SCENE_HIGHLIGHT_TARGETS = {"block"}
SCENE_HIGHLIGHT_SPEC_VERSION = "v1"


def _raw_scene_from_document(editor_document: Any) -> dict:
    if not isinstance(editor_document, dict):
        return {}
    scene = editor_document.get("scene")
    return dict(scene) if isinstance(scene, dict) else {}


def _merge_editor_document_preserving_scene(next_document: dict, current_document: Any) -> dict:
    document = deepcopy(next_document or {})
    current_scene = deepcopy(_raw_scene_from_document(current_document))
    if current_scene:
        incoming_scene = deepcopy(_raw_scene_from_document(document))
        merged_scene = {**current_scene, **incoming_scene}
        for unsafe_key in (
            "original_background_path",
            "custom_background_path",
            "source_background_path",
        ):
            if not incoming_scene.get(unsafe_key) and current_scene.get(unsafe_key):
                merged_scene[unsafe_key] = deepcopy(current_scene[unsafe_key])
        document["scene"] = merged_scene
    return document


def _editor_document_with_scene(page: TranscriptPage, display_text: str, rich_text_html: str, *, text_flags: dict | None = None) -> dict:
    if text_flags is None:
        text_flags = _text_flags_from_editor_document(
            getattr(page, "editor_document", None),
            original_text=getattr(page, "original_text", ""),
            narration_text=getattr(page, "narration_text", ""),
        )
    return _merge_editor_document_preserving_scene(
        _build_editor_document(display_text, rich_text_html, text_flags=text_flags),
        getattr(page, "editor_document", None),
    )


def _clean_scene_mode(value: Any, *, fallback: str = "original") -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in SCENE_BACKGROUND_MODES else fallback


def _clean_scene_fit(value: Any, *, fallback: str = "contain") -> str:
    fit = str(value or "").strip().lower()
    return fit if fit in SCENE_BACKGROUND_FITS else fallback


def _clean_scene_text_scale(value: Any, *, fallback: float = 1.0) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        scale = fallback
    return max(SCENE_TEXT_SCALE_MIN, min(scale, SCENE_TEXT_SCALE_MAX))


def _clean_scene_highlight_style(value: Any, *, fallback: str = "none") -> str:
    style = str(value or "").strip().lower()
    return style if style in SCENE_HIGHLIGHT_STYLES else fallback


def _clean_scene_highlight_detector(value: Any, *, fallback: str = "auto") -> str:
    detector = str(value or "").strip().lower()
    return detector if detector in SCENE_HIGHLIGHT_DETECTORS else fallback


def _normalize_scene_highlight_spec(scene: dict | None) -> dict[str, Any]:
    raw_scene = scene if isinstance(scene, dict) else {}
    raw_spec = raw_scene.get("highlight")
    spec = raw_spec if isinstance(raw_spec, dict) else {}
    enabled = bool(spec.get("enabled", raw_scene.get("highlight_enabled", False)))
    style = _clean_scene_highlight_style(spec.get("style", raw_scene.get("highlight_style")), fallback="none")
    detector = _clean_scene_highlight_detector(spec.get("detector", raw_scene.get("highlight_detector")), fallback="auto")
    target = str(spec.get("target") or "block").strip().lower()
    if target not in SCENE_HIGHLIGHT_TARGETS:
        target = "block"
    version = str(spec.get("version") or SCENE_HIGHLIGHT_SPEC_VERSION).strip() or SCENE_HIGHLIGHT_SPEC_VERSION
    return {
        "enabled": enabled,
        "style": style,
        "detector": detector,
        "target": target,
        "version": version,
    }


def _apply_scene_highlight_spec(scene: dict, highlight_spec: dict[str, Any]) -> None:
    scene["highlight"] = deepcopy(highlight_spec)
    scene["highlight_enabled"] = bool(highlight_spec.get("enabled", False))
    scene["highlight_style"] = _clean_scene_highlight_style(highlight_spec.get("style"), fallback="none")
    scene["highlight_detector"] = _clean_scene_highlight_detector(highlight_spec.get("detector"), fallback="auto")


def _page_scene_for_storage(page: TranscriptPage) -> dict:
    scene = _raw_scene_from_document(getattr(page, "editor_document", None))
    mode = _clean_scene_mode(
        scene.get("background_mode"),
        fallback="whiteboard" if bool(getattr(page, "whiteboard_mode", False)) else "original",
    )
    highlight_spec = _normalize_scene_highlight_spec(scene)
    return {
        **scene,
        "background_mode": mode,
        "background_fit": _clean_scene_fit(scene.get("background_fit"), fallback="contain"),
        "text_scale": _clean_scene_text_scale(scene.get("text_scale"), fallback=1.0),
        "highlight": highlight_spec,
        "highlight_enabled": bool(highlight_spec.get("enabled", False)),
        "highlight_style": _clean_scene_highlight_style(highlight_spec.get("style"), fallback="none"),
        "highlight_detector": _clean_scene_highlight_detector(highlight_spec.get("detector"), fallback="auto"),
        "highlight_updated_at": str(scene.get("highlight_updated_at") or ""),
        "highlight_preview_path": _normalize_rel_storage_path(str(scene.get("highlight_preview_path") or "")),
    }


def _set_page_scene(page: TranscriptPage, scene: dict) -> None:
    editor_document = deepcopy(page.editor_document or {})
    editor_document["scene"] = deepcopy(scene or {})
    page.editor_document = editor_document


def _page_scene_path(page: TranscriptPage, kind: str) -> str:
    if kind not in {"original", "custom", "source", "source_background"}:
        return ""
    scene = _page_scene_for_storage(page)
    key = "source_background_path" if kind in {"source", "source_background"} else f"{kind}_background_path"
    return _normalize_rel_storage_path(str(scene.get(key) or ""))


def _project_lesson_source_type(project: Project) -> str:
    try:
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        upload_dir = storage_root / "uploads" / str(project.id)
        if upload_dir.exists():
            lesson_files = sorted(upload_dir.glob("lesson.*"))
            if lesson_files:
                return lesson_files[0].suffix.lower().lstrip(".")
    except Exception:
        pass
    return ""


def _page_source_type_for_scene(project: Project, scene: dict) -> str:
    source_type = str(scene.get("source_type") or "").strip().lower().lstrip(".")
    if source_type:
        return source_type
    source_type = _project_lesson_source_type(project)
    if source_type:
        return source_type
    if _normalize_rel_storage_path(str(scene.get("source_background_path") or "")):
        return "pptx"
    return ""


def _scene_path_exists(rel_path: str) -> bool:
    safe_path = _normalize_rel_storage_path(str(rel_path or ""))
    if not safe_path:
        return False
    storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
    return _resolve_storage_file(storage_root, safe_path) is not None


def _scene_mode_validation_error(project: Project, scene: dict, mode: str) -> str:
    if mode == "source_background":
        source_type = _page_source_type_for_scene(project, scene)
        if source_type not in SOURCE_BACKGROUND_SUPPORTED_TYPES:
            return "Source Background is currently available for PPTX lessons only."
        if not _scene_path_exists(str(scene.get("source_background_path") or "")):
            return "Source Background is not available for this page."
    if mode == "custom" and not _scene_path_exists(str(scene.get("custom_background_path") or "")):
        return "Upload/select a custom background first."
    if mode == "original":
        source_type = _page_source_type_for_scene(project, scene)
        if source_type == "txt" and not _scene_path_exists(str(scene.get("original_background_path") or "")):
            return "Original mode is not available for this source."
    return ""


def _page_scene_response(page: TranscriptPage, request) -> dict:
    return TranscriptPageSerializer(page, context={"request": request}).data


def _draft_page_for_active_page(draft_data: dict, page: TranscriptPage) -> dict | None:
    pages = draft_data.get("transcript_pages")
    if not isinstance(pages, list):
        return None
    page_id = int(getattr(page, "id", 0) or 0)
    page_key = str(getattr(page, "page_key", "") or "")
    for draft_page in pages:
        if not isinstance(draft_page, dict):
            continue
        try:
            if page_id and int(draft_page.get("id") or 0) == page_id:
                return draft_page
        except (TypeError, ValueError):
            pass
        if page_key and str(draft_page.get("page_key") or "") == page_key:
            return draft_page
    return None


def _draft_page_for_ref(draft_data: dict, page_ref: Any) -> dict | None:
    pages = draft_data.get("transcript_pages")
    if not isinstance(pages, list):
        return None
    ref = str(page_ref or "").strip()
    if not ref:
        return None
    for draft_page in pages:
        if not isinstance(draft_page, dict):
            continue
        if str(draft_page.get("page_key") or "") == ref:
            return draft_page
        if str(draft_page.get("id") or "") == ref:
            return draft_page
    return None


def _active_page_for_draft_page(project: Project, draft_page: dict) -> TranscriptPage | None:
    try:
        draft_id = int(draft_page.get("id") or 0)
    except (TypeError, ValueError):
        draft_id = 0
    if draft_id > 0:
        active_page = project.transcript_pages.filter(pk=draft_id).first()
        if active_page is not None:
            return active_page
    page_key = str(draft_page.get("page_key") or "").strip()
    if page_key:
        return project.transcript_pages.filter(page_key=page_key).first()
    return None


def _draft_page_scene_for_storage(draft_page: dict, fallback_page: TranscriptPage | None) -> dict:
    scene = _raw_scene_from_document(draft_page.get("editor_document"))
    mode = _clean_scene_mode(
        scene.get("background_mode"),
        fallback="whiteboard"
        if bool(draft_page.get("whiteboard_mode", getattr(fallback_page, "whiteboard_mode", False)))
        else "original",
    )
    highlight_spec = _normalize_scene_highlight_spec(scene)
    return {
        **scene,
        "background_mode": mode,
        "background_fit": _clean_scene_fit(scene.get("background_fit"), fallback="contain"),
        "text_scale": _clean_scene_text_scale(scene.get("text_scale"), fallback=1.0),
        "highlight": highlight_spec,
        "highlight_enabled": bool(highlight_spec.get("enabled", False)),
        "highlight_style": _clean_scene_highlight_style(highlight_spec.get("style"), fallback="none"),
        "highlight_detector": _clean_scene_highlight_detector(highlight_spec.get("detector"), fallback="auto"),
        "highlight_updated_at": str(scene.get("highlight_updated_at") or ""),
        "highlight_preview_path": _normalize_rel_storage_path(str(scene.get("highlight_preview_path") or "")),
    }


def _set_draft_page_scene(draft_page: dict, scene: dict) -> None:
    editor_document = deepcopy(draft_page.get("editor_document")) if isinstance(draft_page.get("editor_document"), dict) else {}
    editor_document["scene"] = deepcopy(scene or {})
    draft_page["editor_document"] = editor_document
    draft_page["whiteboard_mode"] = scene.get("background_mode") == "whiteboard"


def _draft_page_scene_path(project: Project, page: TranscriptPage, kind: str) -> str:
    if kind not in {"original", "custom", "source", "source_background"}:
        return ""
    draft_data = get_project_draft_data(project)
    if not has_project_draft(project):
        return ""
    draft_page = _draft_page_for_active_page(draft_data, page)
    if draft_page is None:
        return ""
    scene = _draft_page_scene_for_storage(draft_page, page)
    key = "source_background_path" if kind in {"source", "source_background"} else f"{kind}_background_path"
    return _normalize_rel_storage_path(str(scene.get(key) or ""))


def _draft_page_scene_path_by_ref(project: Project, page_ref: Any, kind: str) -> str:
    if kind not in {"original", "custom", "source", "source_background"}:
        return ""
    draft_data = get_project_draft_data(project)
    if not has_project_draft(project):
        return ""
    draft_page = _draft_page_for_ref(draft_data, page_ref)
    if draft_page is None:
        return ""
    scene = _draft_page_scene_for_storage(draft_page, _active_page_for_draft_page(project, draft_page))
    key = "source_background_path" if kind in {"source", "source_background"} else f"{kind}_background_path"
    return _normalize_rel_storage_path(str(scene.get(key) or ""))


def _draft_url(url: str) -> str:
    if not url:
        return ""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}draft=1"


def _draft_scene_differs(active_page: TranscriptPage | None, draft_page: dict) -> bool:
    if active_page is None:
        return True
    active_scene = _page_scene_for_storage(active_page)
    draft_scene = _draft_page_scene_for_storage(draft_page, active_page)
    keys = {
        "background_mode",
        "background_fit",
        "text_scale",
        "original_background_path",
        "custom_background_path",
        "source_background_path",
        "highlight_enabled",
        "highlight_style",
        "highlight_detector",
        "highlight_preview_path",
    }
    for key in keys:
        active_value = active_scene.get(key)
        draft_value = draft_scene.get(key)
        if key.endswith("_path"):
            active_value = _normalize_rel_storage_path(str(active_value or ""))
            draft_value = _normalize_rel_storage_path(str(draft_value or ""))
        if active_value != draft_value:
            return True
    return bool(draft_page.get("whiteboard_mode")) != bool(getattr(active_page, "whiteboard_mode", False))


def _draft_page_response(project: Project, draft_page: dict, request) -> dict:
    page = TranscriptPage(project=project)
    for field in (
        "id",
        "order",
        "source_slide_index",
        "split_index",
        "page_key",
        "original_text",
        "narration_text",
        "rich_text_html",
        "editor_document",
        "subtitle_chunks",
        "whiteboard_mode",
        "is_active",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
    ):
        if field in draft_page:
            setattr(page, field, deepcopy(draft_page.get(field)))
    page.is_active = True
    active_page = _active_page_for_draft_page(project, draft_page)

    data = TranscriptPageSerializer(page, context={"request": request}).data
    scene = data.get("editor_document", {}).get("scene")
    if isinstance(scene, dict):
        for url_key in ("original_background_url", "custom_background_url", "source_background_url", "highlight_preview_url"):
            if scene.get(url_key):
                scene[url_key] = _draft_url(scene[url_key])
    data["draft_scene_dirty"] = _draft_scene_differs(active_page, draft_page)
    data["draft_background_dirty"] = bool(data["draft_scene_dirty"])
    return data


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


def _project_transcript_timeline(project: Project, *, include_deleted: bool = False, context: dict | None = None) -> list[dict]:
    if include_deleted:
        transcript_rel = getattr(project, "transcript_pages", None)
        if transcript_rel is None:
            return []
        pages = transcript_rel.all().order_by("order", "id")
    else:
        pages = _active_transcript_pages(project)
    return TranscriptPageSerializer(pages, many=True, context=context or {}).data


def _project_deleted_transcript_timeline(project: Project, *, context: dict | None = None) -> list[dict]:
    return TranscriptPageSerializer(_deleted_transcript_pages(project), many=True, context=context or {}).data


def _rich_text_html_from_narration(text: str) -> str:
    return html.escape(str(text or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br />")


def _split_segment_records(
    parts_payload: list[dict],
    *,
    source_display_text: str,
    text_flags: dict,
) -> list[dict]:
    narration_parts: list[str] = []
    explicit_display_parts: list[str] = []
    has_explicit_display = False

    for item in parts_payload:
        if not isinstance(item, dict):
            raise TranscriptActionError("each part must be an object.")
        narration_parts.append(str(item.get("narration_text") or item.get("text") or ""))
        if "original_text" in item or "display_text" in item:
            has_explicit_display = True
            explicit_display_parts.append(str(item.get("original_text") if "original_text" in item else item.get("display_text") or ""))
        else:
            explicit_display_parts.append("")

    if not any(part.strip() for part in narration_parts):
        raise TranscriptActionError("at least one split part must contain narration text.")

    source_display_parts = _split_text_on_blank_lines(source_display_text)
    use_source_display_parts = len(source_display_parts) == len(narration_parts)
    display_from_narration = not has_explicit_display and not use_source_display_parts

    records: list[dict] = []
    for index, narration_text in enumerate(narration_parts):
        if has_explicit_display:
            display_text = explicit_display_parts[index]
        elif use_source_display_parts:
            display_text = source_display_parts[index]
        else:
            display_text = narration_text
        rich_text_html = _rich_text_html_from_narration(display_text)
        segment_flags = {
            "display_text_customized": bool(text_flags.get("display_text_customized") and not display_from_narration),
            "narration_customized": bool(
                _normalized_text_for_compare(narration_text)
                and _normalized_text_for_compare(display_text) != _normalized_text_for_compare(narration_text)
            ),
        }
        records.append(
            {
                "display_text": display_text,
                "narration_text": narration_text,
                "rich_text_html": rich_text_html,
                "subtitle_chunks": _chunk_transcript_text(narration_text),
                "text_flags": segment_flags,
            }
        )
    return records


def _set_page_text_artifacts(page: TranscriptPage, record: dict) -> None:
    page.original_text = str(record.get("display_text") or "")
    page.narration_text = str(record.get("narration_text") or "")
    page.rich_text_html = str(record.get("rich_text_html") or _rich_text_html_from_narration(page.original_text))
    page.editor_document = _editor_document_with_scene(
        page,
        page.original_text,
        page.rich_text_html,
        text_flags=dict(record.get("text_flags") or {}),
    )
    page.subtitle_chunks = list(record.get("subtitle_chunks") or _chunk_transcript_text(page.narration_text))


def _set_page_narration_artifacts(page: TranscriptPage, narration_text: str) -> None:
    text_flags = _text_flags_from_editor_document(
        getattr(page, "editor_document", None),
        original_text=getattr(page, "original_text", ""),
        narration_text=str(narration_text or ""),
    )
    text_flags["narration_customized"] = bool(
        re.sub(r"\s+", " ", str(narration_text or "")).strip()
        and re.sub(r"\s+", " ", str(page.original_text or "")).strip()
        != re.sub(r"\s+", " ", str(narration_text or "")).strip()
    )
    _set_page_text_artifacts(
        page,
        {
            "display_text": page.original_text,
            "narration_text": str(narration_text or ""),
            "rich_text_html": _rich_text_html_from_narration(page.original_text),
            "subtitle_chunks": _chunk_transcript_text(str(narration_text or "")),
            "text_flags": text_flags,
        },
    )


def _set_draft_text_artifacts(page: dict, record: dict) -> None:
    page["original_text"] = str(record.get("display_text") or "")
    page["narration_text"] = str(record.get("narration_text") or "")
    page["rich_text_html"] = str(record.get("rich_text_html") or _rich_text_html_from_narration(page["original_text"]))
    page["subtitle_chunks"] = list(record.get("subtitle_chunks") or _chunk_transcript_text(page["narration_text"]))
    page["editor_document"] = _merge_editor_document_preserving_scene(
        _build_editor_document(page["original_text"], page["rich_text_html"], text_flags=dict(record.get("text_flags") or {})),
        page.get("editor_document") or {},
    )


def _normalize_active_transcript_order(project: Project, ordered_pages: list[TranscriptPage] | None = None) -> None:
    pages = ordered_pages if ordered_pages is not None else list(_active_transcript_pages(project))
    for idx, page in enumerate(pages):
        if page.order != idx:
            page.order = idx
            page.save(update_fields=["order", "updated_at"])


def _unique_split_page_key(project: Project, base_key: str, existing_keys: set[str], split_index: int) -> str:
    safe_base = str(base_key or "page").strip() or "page"
    match = re.match(r"^(s\d+-p)(\d+)$", safe_base)
    if match:
        candidate = f"{match.group(1)}{int(match.group(2)) + int(split_index)}"
    else:
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
        parts = _split_text_on_blank_lines(text)
        if len(parts) <= 1:
            continue

        text_flags = _text_flags_from_editor_document(
            page.editor_document,
            original_text=page.original_text,
            narration_text=page.narration_text,
        )
        records = _split_segment_records(
            [{"narration_text": part} for part in parts],
            source_display_text=page.original_text,
            text_flags=text_flags,
        )

        _set_page_text_artifacts(page, records[0])
        page.save(update_fields=["original_text", "narration_text", "rich_text_html", "editor_document", "subtitle_chunks", "updated_at"])

        for idx, record in enumerate(records[1:], start=1):
            candidate_key = _unique_split_page_key(project, page.page_key, existing_keys, idx)
            TranscriptPage.objects.create(
                project=project,
                order=page.order + idx,
                source_slide_index=page.source_slide_index,
                split_index=page.split_index + idx,
                page_key=candidate_key,
                original_text=record["display_text"],
                narration_text=record["narration_text"],
                rich_text_html=record["rich_text_html"],
                editor_document=_merge_editor_document_preserving_scene(
                    _build_editor_document(
                        record["display_text"],
                        record["rich_text_html"],
                        text_flags=dict(record.get("text_flags") or {}),
                    ),
                    page.editor_document,
                ),
                subtitle_chunks=record["subtitle_chunks"],
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
    avatar_artifact_state: dict | None = None,
) -> dict:
    watermark_enabled = bool(getattr(settings, "LECTURE_WATERMARK_ENABLED", True))
    visibility_lock_enabled = bool(getattr(settings, "LECTURE_VISIBILITY_LOCK_ENABLED", True))
    watermark_forced = False
    if protection_mode == "drm_protected":
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
    avatar_defaults = dict(avatar_overlay_defaults or {})
    avatar_placement = normalize_avatar_placement(
        avatar_defaults.get("avatar_placement") if isinstance(avatar_defaults.get("avatar_placement"), dict) else avatar_defaults
    )
    avatar_defaults.update(avatar_placement)
    avatar_defaults["avatar_placement"] = avatar_placement

    avatar_state = dict(avatar_artifact_state or {})
    avatar_overlay_meta = {
        "quality": str(avatar_state.get("quality") or ""),
        "enhanced_available": bool(avatar_state.get("enhanced_available")),
        "enhanced_pending": bool(avatar_state.get("enhanced_pending")),
        "version": str(avatar_state.get("version") or ""),
        "updated_at": str(avatar_state.get("updated_at") or ""),
    }

    if avatar_token:
        payload["avatar_overlay"] = {
            "enabled": True,
            "stream_url": _stream_url(request, avatar_token),
            "placement": avatar_placement,
            "defaults": avatar_defaults,
            **avatar_overlay_meta,
        }
    else:
        payload["avatar_overlay"] = {
            "enabled": False,
            "stream_url": "",
            "placement": avatar_placement,
            "defaults": avatar_defaults,
            **avatar_overlay_meta,
        }
    payload.update(
        _avatar_playback_state_payload(
            project,
            avatar_available=bool(avatar_token),
            avatar_artifact_state=avatar_state,
        )
    )
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
    throttle_scope = "login"

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


DEFAULT_HELP_CONTENT = {
    "title": "Help and Support",
    "slug": "default",
    "body": (
        "Use Studio with a publisher account to create lessons, then use Watch for "
        "transcript-first study and local notes. Contact support if you need account, "
        "publishing, or playback assistance."
    ),
    "contact_email": "",
    "contact_phone": "",
    "company_name": "",
    "company_address": "",
    "support_url": "",
    "updated_at": None,
    "is_default": True,
}


class HelpContentView(APIView):
    """GET /api/v1/help/ - public published Help page content."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        content = SiteHelpContent.objects.filter(is_published=True).order_by("-updated_at", "-id").first()
        if content is None:
            return Response(DEFAULT_HELP_CONTENT)
        data = dict(SiteHelpContentSerializer(content).data)
        data["is_default"] = False
        return Response(data)


class CurrentUserProfileView(APIView):
    """GET/PATCH /api/v1/me/profile/ - current user's public name and bio only."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(CurrentUserProfileSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        serializer = CurrentUserProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(CurrentUserProfileSerializer(request.user, context={"request": request}).data)


def _profile_asset_url_for_request(request, user_id: int, kind: str, version: str | None = None) -> str:
    if kind not in _PROFILE_ASSET_KINDS:
        return ""
    url_path = f"/api/v1/users/{int(user_id)}/profile-assets/{kind}/"
    if version:
        url_path = f"{url_path}?v={version}"
    try:
        return request.build_absolute_uri(url_path)
    except Exception:
        return url_path


def _profile_asset_version(profile: UserProfile) -> str:
    updated_at = getattr(profile, "updated_at", None)
    return str(int(updated_at.timestamp())) if updated_at else ""


def _validate_profile_asset_upload(image_file) -> str:
    ext = Path(str(getattr(image_file, "name", ""))).suffix.lower()
    if ext not in _ALLOWED_PROFILE_IMAGE_EXTENSIONS:
        raise ValueError(
            f"Unsupported image type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_PROFILE_IMAGE_EXTENSIONS))}"
        )
    if int(getattr(image_file, "size", 0) or 0) > _MAX_PROFILE_ASSET_BYTES:
        raise ValueError("Profile image exceeds the 8 MB size limit.")
    return ".jpg" if ext == ".jpeg" else ext


def _profile_asset_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        alpha = image.convert("RGBA").getchannel("A")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image.convert("RGBA"), mask=alpha)
        return background
    return image.convert("RGB")


def _process_profile_asset_image(source_path: Path, destination_path: Path, kind: str) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source_path) as image:
            image = ImageOps.exif_transpose(image)
            target_size = (1600, 500) if kind == "banner" else (512, 512)
            processed = ImageOps.fit(image, target_size, method=Image.Resampling.LANCZOS)
            processed = _profile_asset_to_rgb(processed)
            processed.save(destination_path, format="JPEG", quality=88, optimize=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Uploaded file is not a valid image.") from exc


def _save_profile_asset(profile: UserProfile, image_file, kind: str) -> None:
    if kind not in _PROFILE_ASSET_KINDS:
        raise ValueError("Unsupported profile asset kind.")
    image_ext = _validate_profile_asset_upload(image_file)
    storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local")).resolve()
    asset_dir = storage_root / "profiles" / str(profile.user_id)
    asset_dir.mkdir(parents=True, exist_ok=True)
    original_path = asset_dir / f"{kind}_original{image_ext}"
    processed_path = asset_dir / f"{kind}_processed.jpg"
    _write_uploaded_file(image_file, original_path)
    try:
        _process_profile_asset_image(original_path, processed_path, kind)
    except ValueError:
        try:
            original_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    setattr(profile, f"{kind}_image_original", str(original_path.relative_to(storage_root)).replace("\\", "/"))
    setattr(profile, f"{kind}_image_processed", str(processed_path.relative_to(storage_root)).replace("\\", "/"))


class CurrentUserProfileAssetsView(APIView):
    """POST /api/v1/me/profile-assets/ - upload current user's banner/logo images."""

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        banner_file = request.FILES.get("banner_file")
        logo_file = request.FILES.get("logo_file")
        if banner_file is None and logo_file is None:
            return Response(
                {"error": "banner_file or logo_file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": "student"})
        update_fields: list[str] = []
        try:
            if banner_file is not None:
                _save_profile_asset(profile, banner_file, "banner")
                update_fields.extend(["banner_image_original", "banner_image_processed"])
            if logo_file is not None:
                _save_profile_asset(profile, logo_file, "logo")
                update_fields.extend(["logo_image_original", "logo_image_processed"])
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if update_fields:
            profile.save(update_fields=[*update_fields, "updated_at"])
        request.user.refresh_from_db()
        return Response(CurrentUserProfileSerializer(request.user, context={"request": request}).data)


class UserProfileAssetView(APIView):
    """GET /api/v1/users/<id>/profile-assets/<kind>/ - safe processed profile image."""

    permission_classes = [permissions.AllowAny]

    def _can_view_private_profile_asset(self, request, user: User) -> bool:
        viewer = getattr(request, "user", None)
        if not viewer or not viewer.is_authenticated:
            return False
        return _is_staff_user(viewer) or int(viewer.id) == int(user.id)

    def get(self, request, user_id, kind):
        if kind not in _PROFILE_ASSET_KINDS:
            raise Http404
        try:
            user = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            raise Http404
        profile = _safe_user_profile(user)
        if profile is None:
            raise Http404
        if not bool(profile.is_public_profile) and not self._can_view_private_profile_asset(request, user):
            raise Http404

        rel_path = _normalize_rel_storage_path(getattr(profile, f"{kind}_image_processed", "") or "")
        if not rel_path:
            raise Http404
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local")).resolve()
        full_path = _resolve_storage_file(storage_root, rel_path)
        if full_path is None:
            raise Http404

        response = _media_file_response(request, full_path, "image/jpeg")
        response["Cache-Control"] = "public, max-age=86400" if profile.is_public_profile else "private, no-store"
        return response


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

    parser_classes = [MultiPartParser, FormParser]
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

        use_draft = _truthy_request_value(request.query_params.get("draft"))
        if use_draft:
            if not self._can_view_private_cover(request, project):
                raise Http404
            draft_fields = get_draft_project_fields(project) if has_project_draft(project) else {}
            rel_path = _normalize_rel_storage_path(
                draft_fields.get("cover_image_processed") or draft_fields.get("cover_image_original")
            )
        else:
            rel_path = _normalize_rel_storage_path(project.cover_image_processed or project.cover_image_original)
        if not rel_path:
            raise Http404

        if not use_draft and not _is_public_lesson(project) and not self._can_view_private_cover(request, project):
            raise Http404

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        full_path = _resolve_storage_file(storage_root, rel_path)
        if full_path is None:
            raise Http404

        content_type, _ = mimetypes.guess_type(str(full_path))
        response = _media_file_response(request, full_path, content_type)
        response["Cache-Control"] = "public, max-age=300"
        return response

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        cover_file = request.FILES.get("cover_file") or request.FILES.get("image") or request.FILES.get("file")
        if cover_file is None:
            return Response({"error": "cover_file is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cover_ext = _validate_cover_upload(cover_file)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        upload_dir = storage_root / "uploads" / str(project.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved_cover_path = upload_dir / f"cover_{uuid.uuid4().hex[:10]}{cover_ext}"
        _write_uploaded_file(cover_file, saved_cover_path)
        cover_rel_path = str(saved_cover_path.relative_to(storage_root)).replace("\\", "/")

        if _truthy_request_value(request.data.get("draft_only") or request.query_params.get("draft_only")):
            draft_data = ensure_project_draft_data(project)
            project_fields = draft_data.setdefault("project", {})
            project_fields["cover_image_original"] = cover_rel_path
            project_fields["cover_image_processed"] = cover_rel_path
            metadata = draft_data.setdefault("metadata", {})
            metadata["cover_dirty"] = True
            metadata["visual_assets_dirty"] = True
            save_project_draft_data(project, draft_data, dirty=True)
            project.refresh_from_db()
            return Response(ProjectSerializer(project, context={"request": request}).data, status=status.HTTP_200_OK)

        project.cover_image_original = cover_rel_path
        project.cover_image_processed = cover_rel_path
        project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
        _mark_project_visual_moderation_stale(
            project,
            reason="studio_cover_changed",
            asset_type="cover",
            asset_path=cover_rel_path,
        )
        _run_auto_visual_moderation_for_changed_asset(
            project,
            asset_type="cover",
            asset_path=cover_rel_path,
        )
        project.refresh_from_db(fields=["moderation_status", "moderation_summary"])
        return Response(ProjectSerializer(project, context={"request": request}).data, status=status.HTTP_200_OK)


class MediaStreamView(APIView):
    """
    GET /api/v1/stream/<token>/

    Validates a short-lived HMAC token and streams the media file.
    Supports HTTP Range so browsers can seek within the video.
    The raw storage path is never sent to the client.
    """
    permission_classes = [permissions.AllowAny]
    # Stream traffic (especially HLS segments) is high-frequency by design.
    # Do not apply DRF request throttles here; playback protection is enforced
    # by signed tokens, grant/session checks, and risk policy controls.
    throttle_classes = []

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

        if grant_id:
            grant_payload = cache.get(_grant_key_for(grant_id)) or {}
            grant_mode = str(grant_payload.get("mode") or "").strip().lower()
            if grant_mode in {"public", "secure_stream", "drm_protected", "studio_preview"} and grant_mode != protection_mode:
                protection_mode = grant_mode
                mode_debug = {**(mode_debug or {}), "source": "grant_mode", "grant_mode_override_applied": True}

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
            rel_path = rel_path.lstrip("/") if rel_path else job.srt_url.lstrip("/")
            if rel_path != job.srt_url.lstrip("/") and not rel_path.startswith(f"{job.project_id}/"):
                return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="resource_outside_project")
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
            if stream_project:
                avatar_rel_paths = _avatar_artifact_rel_paths(stream_project, sidecar)
                if rel_path not in avatar_rel_paths or not _avatar_active_for_project(stream_project):
                    return _stream_error_response(file_type=file_type, status_code=status.HTTP_404_NOT_FOUND, reason="avatar_not_available")
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

        job = _latest_completed_video_export_job(project)
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

        avatar_artifact_state = _avatar_artifact_state(project, sidecar)
        avatar_available = bool(avatar_artifact_state.get("available"))
        avatar_rel_path = str(avatar_artifact_state.get("rel_path") or "")
        if avatar_available and avatar_rel_path and _avatar_active_for_project(project):
            avatar_token = generate_media_token(
                job.id,
                "avatar",
                rel_path=avatar_rel_path,
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
            "Playback token issued: project_id=%s job_id=%s mode=%s has_hls=%s mp4_fallback=%s",
            project.id,
            job.id,
            protection_mode,
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
            avatar_artifact_state=avatar_artifact_state,
        )
        payload["transcript_pages"] = _project_transcript_timeline(project, context={"request": request})
        return Response(payload)



class StudioPreviewTokenView(APIView):
    """
    GET /api/v1/projects/<project_id>/studio-preview-token/

    Issues playback tokens for Studio preview.
    - Requires owner/staff/admin permissions.
    - Works for unpublished/draft lessons if ready.
    - Skips public playback concurrency locks to allow preview while editing.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        job = _latest_completed_video_export_job(project)
        if not job:
            return Response({"error": "No ready video for this project."}, status=status.HTTP_404_NOT_FOUND)

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)

        # Force a non-DRM, secure stream mode for Studio preview
        protection_mode = "secure_stream"
        mode_debug = {
            "effective_mode": "secure_stream",
            "source": "studio_preview",
            "draft_preview_forced_secure_stream": True,
        }

        ttl_seconds = _token_ttl_for_mode(protection_mode)
        # Studio preview bypasses public session locks and session binding at
        # grant issuance; the signed stream token still expires and stays scoped
        # to this lesson's media resources.
        grant_id, _scope_key = _issue_playback_grant(
            project.id,
            request,
            mode="studio_preview",
            ttl_seconds=ttl_seconds,
            bind_to_session=False,
        )
        bind_key = None
        playback_session_id = _playback_session_id(job.id, grant_id)

        hls_payload = sidecar.get("hls") if isinstance(sidecar, dict) else None
        hls_manifest_token = None
        if hls_payload and hls_payload.get("manifest_rel_path"):
            hls_manifest_token = generate_media_token(
                job.id,
                "hls_manifest",
                rel_path=hls_payload["manifest_rel_path"],
                ttl_seconds=ttl_seconds,
                grant_id=grant_id,
                bind_key=bind_key,
            )

        avatar_artifact_state = _avatar_artifact_state(project, sidecar)
        avatar_token = None
        if bool(avatar_artifact_state.get("available")) and avatar_artifact_state.get("rel_path") and _avatar_active_for_project(project):
            avatar_token = generate_media_token(
                job.id,
                "avatar",
                rel_path=str(avatar_artifact_state.get("rel_path")),
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
        )

        srt_token = generate_media_token(
            job.id,
            "srt",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if job.srt_url else None

        vtt_token = _generate_vtt_media_token_for_job(
            job,
            storage_root=storage_root,
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        ) if job.srt_url else None

        payload = _playback_payload(
            request,
            project,
            job,
            video_token,
            srt_token,
            vtt_token=vtt_token,
            hls_manifest_token=hls_manifest_token,
            hls_encrypted=bool(hls_payload.get("encrypted")) if hls_payload else False,
            asset_id=_default_asset_id(project.id),
            content_id=_default_content_id(project.id),
            protection_mode=protection_mode,
            mode_debug=mode_debug,
            allow_mp4_fallback=True,
            playback_session_id=playback_session_id,
            session_binding_active=False,
            avatar_token=avatar_token,
            avatar_overlay_defaults=_avatar_overlay_defaults_for_project(project),
            avatar_artifact_state=avatar_artifact_state,
        )
        payload["transcript_pages"] = _project_transcript_timeline(project, context={"request": request})
        payload["is_studio_preview"] = True

        return Response(payload)


class ProjectSubtitleTrackListView(APIView):

    """
    GET/POST /api/v1/projects/<project_id>/subtitle-tracks/

    GET returns original subtitle track metadata plus translated track metadata.
    POST synchronously generates a translated subtitle sidecar through the
    configured provider chain when subtitle translation is enabled.

    Ready track URLs are short-lived stream tokens and never raw storage paths.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request, project_id):
        try:
            project = Project.objects.select_related("user").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_subtitle_tracks(request, project):
            return Response({"error": "Lesson not available."}, status=status.HTTP_404_NOT_FOUND)

        latest_job = _latest_completed_video_export_job(project)
        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)
        language_payload = _language_detection_sidecar_for_job(storage_root, project.id)
        source_language = str(
            language_payload.get("resolved_language")
            or language_payload.get("detected_language")
            or ""
        ).strip().lower()

        protection_mode = "public"
        ttl_seconds = _token_ttl_for_mode(protection_mode)
        grant_id = None
        bind_key = None
        if latest_job is not None:
            protection_mode, ttl_seconds, grant_id, bind_key = _subtitle_playback_token_context(request, project, latest_job, sidecar)

        original_vtt_url = None
        if latest_job is not None and latest_job.srt_url:
            vtt_token = _generate_vtt_media_token_for_job(
                latest_job,
                storage_root=storage_root,
                ttl_seconds=ttl_seconds,
                grant_id=grant_id,
                bind_key=bind_key,
            )
            original_vtt_url = _stream_url(request, vtt_token) if vtt_token else None

        tracks = [
            {
                "id": "original",
                "type": "original",
                "language_code": "original",
                "language_label": "Original",
                "source_language_code": source_language,
                "provider": "original",
                "status": "ready" if original_vtt_url else ("srt_only" if latest_job and latest_job.srt_url else "missing"),
                "cue_count": None,
                "vtt_url": original_vtt_url,
                "has_vtt": bool(original_vtt_url),
                "is_original": True,
                "requires_rerender": bool(latest_job and latest_job.srt_url and not original_vtt_url),
                "metadata": {"source": "original"},
            }
        ]

        can_manage = _can_manage_project(getattr(request, "user", None), project)
        translated_tracks = TranslatedSubtitleTrack.objects.filter(project=project).select_related("job").order_by("language_code", "id")
        if not can_manage:
            translated_tracks = translated_tracks.filter(status__in=["pending", "processing", "ready", "failed"])

        for track in translated_tracks:
            stream_job = track.job if track.job_id and track.job and track.job.srt_url else latest_job
            tracks.append(
                _translated_track_payload(
                    request,
                    track=track,
                    stream_job=stream_job,
                    ttl_seconds=ttl_seconds,
                    grant_id=grant_id,
                    bind_key=bind_key,
                )
            )

        return Response(
            {
                "project_id": project.id,
                "translation_enabled": bool(getattr(settings, "SUBTITLE_TRANSLATION_ENABLED", False)),
                "translation_provider": str(getattr(settings, "SUBTITLE_TRANSLATION_PROVIDER", "auto") or "auto"),
                "target_languages": _translation_target_languages(),
                "requestable_languages": _public_subtitle_request_languages(),
                "tracks": tracks,
            }
        )

    def post(self, request, project_id):
        try:
            project = Project.objects.select_related("user").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        can_manage = _can_manage_project(getattr(request, "user", None), project)
        is_public_request = not can_manage
        if is_public_request and not _is_published_playable_lesson(project):
            return Response({"error": "Lesson not available."}, status=status.HTTP_404_NOT_FOUND)
        if is_public_request and not bool(getattr(settings, "SUBTITLE_PUBLIC_REQUESTS_ENABLED", True)):
            return Response(
                {
                    "error": "public_requests_disabled",
                    "details": "Public subtitle generation requests are disabled.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if not bool(getattr(settings, "SUBTITLE_TRANSLATION_ENABLED", False)):
            return Response(
                {
                    "error": "Subtitle translation generation is disabled.",
                    "translation_enabled": False,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        language_code = str(request.data.get("language_code") or "").strip()
        if not language_code:
            return Response({"error": "language_code is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            normalized_language_code = _normalize_subtitle_request_language_code(language_code)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if is_public_request and normalized_language_code not in _public_subtitle_request_language_allowlist():
            return Response(
                {
                    "error": "unsupported_language",
                    "details": "This subtitle language is not available for public requests.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        language_label = str(request.data.get("language_label") or "").strip()
        if is_public_request:
            language_label = _public_subtitle_request_language_label(normalized_language_code)
        provider = str(request.data.get("provider") or getattr(settings, "SUBTITLE_TRANSLATION_PROVIDER", "auto") or "auto").strip().lower()
        if is_public_request:
            provider = str(getattr(settings, "SUBTITLE_TRANSLATION_PROVIDER", "auto") or "auto").strip().lower()

        latest_job = _latest_completed_video_export_job(project)
        existing_track = (
            TranslatedSubtitleTrack.objects.filter(
                project=project,
                language_code=normalized_language_code,
                status="ready",
            )
            .exclude(vtt_path="")
            .select_related("job")
            .order_by("-updated_at", "-id")
            .first()
        )
        if existing_track is not None:
            sidecar = _playback_sidecar_for_job(getattr(settings, "STORAGE_ROOT", "storage_local"), project.id)
            stream_job = existing_track.job if existing_track.job_id and existing_track.job and existing_track.job.srt_url else latest_job
            _protection_mode, ttl_seconds, grant_id, bind_key = (
                _subtitle_playback_token_context(request, project, stream_job, sidecar)
                if stream_job
                else ("public", _token_ttl_for_mode("public"), None, None)
            )
            return Response(
                {
                    "already_available": True,
                    "track": _translated_track_payload(
                        request,
                        track=existing_track,
                        stream_job=stream_job,
                        ttl_seconds=ttl_seconds,
                        grant_id=grant_id,
                        bind_key=bind_key,
                    ),
                },
                status=status.HTTP_200_OK,
            )

        processing_track = (
            TranslatedSubtitleTrack.objects.filter(
                project=project,
                language_code=normalized_language_code,
                status__in=["pending", "processing"],
            )
            .select_related("job")
            .order_by("-updated_at", "-id")
            .first()
        )
        if processing_track is not None:
            sidecar = _playback_sidecar_for_job(getattr(settings, "STORAGE_ROOT", "storage_local"), project.id)
            stream_job = processing_track.job if processing_track.job_id and processing_track.job and processing_track.job.srt_url else latest_job
            _protection_mode, ttl_seconds, grant_id, bind_key = (
                _subtitle_playback_token_context(request, project, stream_job, sidecar)
                if stream_job
                else ("public", _token_ttl_for_mode("public"), None, None)
            )
            return Response(
                {
                    "status": "processing",
                    "details": "Subtitle generation for this language is already in progress.",
                    "track": _translated_track_payload(
                        request,
                        track=processing_track,
                        stream_job=stream_job,
                        ttl_seconds=ttl_seconds,
                        grant_id=grant_id,
                        bind_key=bind_key,
                    ),
                },
                status=status.HTTP_202_ACCEPTED,
            )

        if is_public_request:
            rate_limit_response = _public_subtitle_rate_limit_response(request, project.id)
            if rate_limit_response is not None:
                return rate_limit_response
            if provider == "mock" and not _public_subtitle_mock_fallback_allowed():
                return Response(
                    {
                        "error": "provider_unavailable",
                        "details": "Mock subtitle translation fallback is disabled for public requests.",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        lock_key = _subtitle_generation_lock_key(project.id, normalized_language_code)
        lock_seconds = int(getattr(settings, "SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS", 300) or 300)
        if not cache.add(lock_key, "1", timeout=max(lock_seconds, 1)):
            return Response(
                {
                    "error": "generation_in_progress",
                    "details": "Subtitle generation for this language is already in progress.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        active_reserved = False
        try:
            if is_public_request:
                active_reserved = _reserve_public_subtitle_active_slot(project.id)
                if not active_reserved:
                    cache.delete(lock_key)
                    return Response(
                        {
                            "error": "generation_in_progress",
                            "details": "Too many subtitle generations are already running for this lesson.",
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

            existing_for_update = TranslatedSubtitleTrack.objects.filter(
                project=project,
                language_code=normalized_language_code,
            ).first()
            metadata = dict(existing_for_update.metadata or {}) if existing_for_update else {}
            metadata.update(
                {
                    "provider_requested": provider,
                    "generation_mode": "async",
                    "request_source": "public" if is_public_request else "owner",
                    "queued_at": timezone.now().isoformat(),
                }
            )
            track, _created = TranslatedSubtitleTrack.objects.update_or_create(
                project=project,
                language_code=normalized_language_code,
                defaults={
                    "job": latest_job,
                    "language_label": language_label or _public_subtitle_request_language_label(normalized_language_code),
                    "provider": provider,
                    "status": "processing",
                    "srt_path": "",
                    "vtt_path": "",
                    "cue_count": 0,
                    "error_message": "",
                    "metadata": metadata,
                },
            )
            async_result = _dispatch_celery_task(
                _SUBTITLE_TRANSLATION_TASK,
                kwargs={
                    "project_id": project.id,
                    "language_code": normalized_language_code,
                    "language_label": track.language_label,
                    "provider": provider,
                    "storage_root": getattr(settings, "STORAGE_ROOT", "storage_local"),
                    "allow_mock_fallback": _public_subtitle_mock_fallback_allowed() if is_public_request else None,
                    "lock_key": lock_key,
                    "release_public_active_slot": bool(active_reserved),
                },
                queue=_render_queue_name(),
            )
            task_id = str(getattr(async_result, "id", "") or "")
            if task_id:
                task_metadata = dict(track.metadata or {})
                task_metadata["celery_task_id"] = task_id
                track.metadata = task_metadata
                track.save(update_fields=["metadata", "updated_at"])
        except Exception as exc:  # noqa: BLE001
            if active_reserved:
                _release_public_subtitle_active_slot(project.id)
            cache.delete(lock_key)
            failed_track = TranslatedSubtitleTrack.objects.filter(
                project=project,
                language_code=normalized_language_code,
            ).first()
            if failed_track is not None:
                failed_track.status = "failed"
                failed_track.error_message = "Subtitle generation could not be queued."
                failed_track.save(update_fields=["status", "error_message", "updated_at"])
            logger.exception("Subtitle generation enqueue failed for project_id=%s language=%s", project.id, normalized_language_code)
            return Response(
                {
                    "error": "enqueue_failed",
                    "details": "Subtitle generation could not be queued. Try again later.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)
        stream_job = track.job if track.job_id and track.job and track.job.srt_url else latest_job
        _protection_mode, ttl_seconds, grant_id, bind_key = (
            _subtitle_playback_token_context(request, project, stream_job, sidecar)
            if stream_job
            else ("public", _token_ttl_for_mode("public"), None, None)
        )
        return Response(
            {
                "status": "processing",
                "details": "Subtitle generation has started.",
                "task_id": task_id,
                "track": _translated_track_payload(
                    request,
                    track=track,
                    stream_job=stream_job,
                    ttl_seconds=ttl_seconds,
                    grant_id=grant_id,
                    bind_key=bind_key,
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


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

        job = _latest_completed_video_export_job(project)
        if not job:
            return Response({"error": "No ready video for this project."}, status=status.HTTP_404_NOT_FOUND)

        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        sidecar = _playback_sidecar_for_job(storage_root, project.id)
        protection_mode, mode_debug, _lesson_is_public = _resolve_playback_mode_for_project(project, sidecar)

        identity = _playback_identity(request)
        if protection_mode == "public":
            for candidate_mode in ("drm_protected", "secure_stream", "studio_preview"):
                candidate_scope = _scope_key_for(project.id, identity, candidate_mode)
                if cache.get(candidate_scope):
                    protection_mode = candidate_mode
                    mode_debug = {**(mode_debug or {}), "source": "grant_mode_fallback", "grant_mode_override_applied": True}
                    break

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
    """Teacher pipeline access: staff/admin or the authenticated project owner."""
    if not _is_authenticated_user(user):
        return False
    if _is_staff_user(user):
        return True
    return bool(project.user_id and int(project.user_id) == int(user.id))


def _can_run_lesson_intelligence(user, project: Project) -> bool:
    if not _is_authenticated_user(user):
        return False
    if _is_staff_user(user):
        return True
    if not project.user_id or int(project.user_id) != int(user.id):
        return False
    profile = getattr(user, "profile", None)
    return bool(profile and profile.role in {"teacher", "publisher"})


def _latest_completed_video_export_job(project: Project) -> Job | None:
    return (
        project.jobs.filter(job_type="video_export", status="done")
        .order_by("-created_at", "-id")
        .first()
    )


def _project_has_completed_video_export(project: Project) -> bool:
    return bool(project.jobs.filter(job_type="video_export", status="done").exists())


def _project_tts_settings(project: Project) -> dict[str, Any]:
    return canonical_project_tts_settings(getattr(project, "tts_settings", None))


def _project_render_tts_settings(project: Project, *, use_draft: bool = False) -> dict[str, Any]:
    if use_draft:
        draft_fields = get_draft_project_fields(project)
        if isinstance(draft_fields.get("tts_settings"), dict):
            return canonical_project_tts_settings(draft_fields.get("tts_settings"))
    return _project_tts_settings(project)


def _is_public_lesson(project: Project) -> bool:
    """True only for catalog-visible lessons: published + render done + moderation approved.

    This governs public/anonymous access (catalog, social endpoints, playback token).
    Owner/staff access is always gated by _can_manage_project(), not this function.
    """
    if hasattr(project, "is_published") and not bool(getattr(project, "is_published", False)):
        return False
    if hasattr(project, "moderation_status") and not moderation_is_approved_for_catalog(project):
        return False
    # Render must also be complete for a fully public lesson.
    if hasattr(project, "status") and str(getattr(project, "status", "") or "") != "ready":
        return False
    try:
        return _project_has_completed_video_export(project)
    except AttributeError:
        latest_job = _latest_completed_video_export_job(project)
        return bool(latest_job)


def _project_has_completed_render(project: Project) -> bool:
    try:
        return _project_has_completed_video_export(project)
    except AttributeError:
        latest_job = _latest_completed_video_export_job(project)
        return bool(latest_job)


def _is_published_playable_lesson(project: Project) -> bool:
    """Published, moderation-allowed, and backed by a completed render.

    Some existing lessons have a stale Project.status="draft" even though their
    latest render job is done. Subtitle track metadata should follow playable
    render state without changing catalog/dashboard status semantics.
    """
    if hasattr(project, "is_published") and not bool(getattr(project, "is_published", False)):
        return False
    if hasattr(project, "moderation_status") and not moderation_is_approved_for_catalog(project):
        return False
    return _project_has_completed_render(project)


def _can_access_subtitle_tracks(request, project: Project) -> bool:
    if _can_access_lesson_playback(request, project):
        return True
    if _is_published_playable_lesson(project):
        return True
    return False


def _can_access_lesson_playback(request, project: Project) -> bool:
    if _is_public_lesson(project):
        return True
    return _can_manage_project(getattr(request, "user", None), project)


def _translation_target_languages() -> list[str]:
    raw = str(getattr(settings, "SUBTITLE_TRANSLATION_TARGET_LANGUAGES", "") or "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


_DEFAULT_PUBLIC_SUBTITLE_LANGUAGE_ALLOWLIST = "en,ar,tr,fr,de,es,it,pt,ru,zh,ja,ko,hi,ur,id,fa"

_DEFAULT_PUBLIC_SUBTITLE_LANGUAGE_LABELS = {
    "en": "English",
    "ar": "Arabic",
    "tr": "Turkish",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "hi": "Hindi",
    "ur": "Urdu",
    "id": "Indonesian",
    "fa": "Persian",
}


def _normalize_subtitle_request_language_code(value: str) -> str:
    code = str(value or "").strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", code):
        raise ValueError("language_code must be a supported BCP-47 style language code.")
    return code


def _public_subtitle_request_language_allowlist() -> set[str]:
    return set(_public_subtitle_request_language_codes())


def _public_subtitle_request_language_codes() -> list[str]:
    raw = str(
        getattr(
            settings,
            "SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST",
            _DEFAULT_PUBLIC_SUBTITLE_LANGUAGE_ALLOWLIST,
        )
        or ""
    )
    codes = []
    seen = set()
    for item in raw.split(","):
        if not str(item or "").strip():
            continue
        code = _normalize_subtitle_request_language_code(item)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def _public_subtitle_request_languages() -> list[dict]:
    return [
        {
            "language_code": code,
            "language_label": _public_subtitle_request_language_label(code),
        }
        for code in _public_subtitle_request_language_codes()
    ]


def _public_subtitle_request_language_label(language_code: str) -> str:
    code = _normalize_subtitle_request_language_code(language_code)
    return _DEFAULT_PUBLIC_SUBTITLE_LANGUAGE_LABELS.get(code, code.upper())


def _subtitle_request_actor_key(request) -> tuple[str, str]:
    user = getattr(request, "user", None)
    if _is_authenticated_user(user):
        return "user", str(user.id)
    forwarded_for = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",", 1)[0].strip()
    remote_addr = str(request.META.get("REMOTE_ADDR") or "").strip()
    ip_address = forwarded_for or remote_addr or "unknown"
    return "ip", ip_address


def _public_subtitle_rate_limit_response(request, project_id: int) -> Response | None:
    actor_type, actor_value = _subtitle_request_actor_key(request)
    limit_setting = (
        "SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR"
        if actor_type == "user"
        else "SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR"
    )
    limit = int(getattr(settings, limit_setting, 10 if actor_type == "user" else 5) or 0)
    if limit <= 0:
        return Response(
            {
                "error": "rate_limited",
                "details": "Too many subtitle generation requests. Try again later.",
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    actor_hash = hashlib.sha256(actor_value.encode("utf-8")).hexdigest()[:24]
    key = f"subtitle-request-rate:{actor_type}:{actor_hash}:project:{int(project_id)}"
    cache.add(key, 0, timeout=3600)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=3600)
        count = 1
    if count > limit:
        return Response(
            {
                "error": "rate_limited",
                "details": "Too many subtitle generation requests. Try again later.",
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    return None


def _subtitle_generation_lock_key(project_id: int, language_code: str) -> str:
    return f"subtitle-generate:{int(project_id)}:{_normalize_subtitle_request_language_code(language_code)}"


def _public_subtitle_active_key(project_id: int) -> str:
    return f"subtitle-generate-active:{int(project_id)}"


def _reserve_public_subtitle_active_slot(project_id: int) -> bool:
    max_active = int(getattr(settings, "SUBTITLE_PUBLIC_REQUEST_MAX_ACTIVE_PER_PROJECT", 3) or 0)
    if max_active <= 0:
        return False
    timeout = max(int(getattr(settings, "SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS", 300) or 300), 1)
    key = _public_subtitle_active_key(project_id)
    cache.add(key, 0, timeout=timeout)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)
        count = 1
    if count > max_active:
        try:
            cache.decr(key)
        except ValueError:
            cache.set(key, 0, timeout=timeout)
        return False
    return True


def _release_public_subtitle_active_slot(project_id: int) -> None:
    key = _public_subtitle_active_key(project_id)
    try:
        cache.decr(key)
    except ValueError:
        cache.delete(key)


def _public_subtitle_mock_fallback_allowed() -> bool:
    return bool(
        getattr(settings, "SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK", True)
        and getattr(settings, "SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK", bool(getattr(settings, "DEBUG", False)))
    )


def _safe_project_subtitle_rel_path(project_id: int, rel_path: str) -> str:
    normalized = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in normalized.split("/"):
        return ""
    if not normalized.startswith(f"{int(project_id)}/"):
        return ""
    return normalized


def _subtitle_stream_url_for_rel_path(
    request,
    *,
    job: Job | None,
    rel_path: str,
    file_type: str,
    ttl_seconds: int,
    grant_id: str | None,
    bind_key: str | None,
) -> str | None:
    if job is None:
        return None
    safe_rel = _safe_project_subtitle_rel_path(int(job.project_id or 0), rel_path)
    if not safe_rel:
        return None
    storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
    if not _storage_rel_path_exists(storage_root, safe_rel):
        return None
    token = generate_media_token(
        job.id,
        file_type,
        rel_path=safe_rel,
        ttl_seconds=ttl_seconds,
        grant_id=grant_id,
        bind_key=bind_key,
    )
    return _stream_url(request, token)


def _subtitle_playback_token_context(request, project: Project, job: Job, sidecar: dict | None) -> tuple[str, int, str | None, str | None]:
    protection_mode, _mode_debug, lesson_is_public = _resolve_playback_mode_for_project(project, sidecar)
    ttl_seconds = _token_ttl_for_mode(protection_mode)
    grant_id = None
    bind_key = None
    if not lesson_is_public or protection_mode == "drm_protected":
        grant_id, _scope_key = _issue_playback_grant(project.id, request, protection_mode, ttl_seconds)
        bind_key = _bind_key_for_request(request) if bool(getattr(settings, "LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", True)) else None
    return protection_mode, ttl_seconds, grant_id, bind_key


def _translated_track_payload(
    request,
    *,
    track: TranslatedSubtitleTrack,
    stream_job: Job | None,
    ttl_seconds: int,
    grant_id: str | None,
    bind_key: str | None,
) -> dict:
    srt_url = None
    vtt_url = None
    if track.status == "ready" and track.srt_path:
        srt_url = _subtitle_stream_url_for_rel_path(
            request,
            job=stream_job,
            rel_path=track.srt_path,
            file_type="srt",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        )
    if track.status == "ready" and track.vtt_path:
        vtt_url = _subtitle_stream_url_for_rel_path(
            request,
            job=stream_job,
            rel_path=track.vtt_path,
            file_type="vtt",
            ttl_seconds=ttl_seconds,
            grant_id=grant_id,
            bind_key=bind_key,
        )
    return {
        "id": track.id,
        "type": "translated",
        "language_code": track.language_code,
        "language_label": track.language_label or track.language_code,
        "source_language_code": track.source_language_code or "",
        "provider": track.provider,
        "status": track.status,
        "cue_count": int(track.cue_count or 0),
        "srt_url": srt_url,
        "vtt_url": vtt_url,
        "has_vtt": bool(vtt_url),
        "is_original": False,
        "created_at": track.created_at,
        "updated_at": track.updated_at,
        "metadata": track.metadata or {},
        "error_message": track.error_message if track.status == "failed" else "",
    }


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


def _avatar_moderation_block_response(profile: UserProfile, gate: dict, *, status_label: str = "avatar_not_prepared"):
    profile.avatar_image_status = "rejected"
    profile.avatar_preview_error = str(gate.get("message") or "Avatar source image needs moderation review.")
    profile.save(update_fields=["avatar_image_status", "avatar_preview_error", "updated_at"])
    return Response(
        {
            "status": status_label,
            "error_code": gate.get("error_code") or "avatar_image_moderation_blocked",
            "error": profile.avatar_preview_error,
            "avatar_moderation_status": gate.get("status") or profile.avatar_moderation_status,
            "avatar_moderation_summary": gate.get("summary") or profile.avatar_moderation_summary,
            "missing_requirements": [gate.get("error_code") or "avatar_image_moderation_blocked"],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


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
    moderation_gate = avatar_image_moderation_gate(profile)
    if moderation_gate.get("blocked"):
        disable_reason = str(moderation_gate.get("message") or "Avatar source image needs moderation review.")

    lesson_engine = selected_engine
    composite_fallback_allowed = _composite_fallback_allowed()
    runtime_settings = project_avatar_runtime_settings(project)

    return {
        "requested": bool(avatar_enabled),
        "enabled": bool(avatar_enabled and is_ready and not disable_reason),
        "teacher_id": int(teacher.id),
        "source_image_rel_path": profile.avatar_image_processed or profile.avatar_image_original,
        "source_image_original_rel_path": profile.avatar_image_original or profile.avatar_image_processed,
        "source_video_rel_path": profile.avatar_video_processed or profile.avatar_video_original,
        "avatar_reference_type": resolved_ref,
        "motion_preset": runtime_settings["motion_preset"],
        "avatar_runtime_settings": runtime_settings,
        "restoration_enabled": bool(runtime_settings["restoration_enabled"]),
        "liveportrait_enabled": bool(runtime_settings["liveportrait_enabled"]),
        "lipsync_engine": lesson_engine,
        "avatar_engine_selected": lesson_engine,
        "normalized_engine": lesson_engine,
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
        "avatar_moderation_status": str(profile.avatar_moderation_status or "not_scanned"),
        "avatar_moderation_blocked": bool(moderation_gate.get("blocked")),
        "avatar_moderation_error_code": str(moderation_gate.get("error_code") or ""),
        "avatar_moderation_summary": dict(profile.avatar_moderation_summary or {}),
        "composite_configured": composite_ready,
        "composite_lesson_enabled": _composite_lesson_enabled(),
        "composite_fallback_allowed": composite_fallback_allowed,
        "engine_resolution": engine_resolution,
        "disabled_reason": disable_reason,
    }


def _avatar_overlay_defaults_for_project(project: Project) -> dict:
    project_user = getattr(project, "user", None)
    teacher_profile = getattr(project_user, "profile", None) if project_user else None
    placement = project_avatar_placement(project)
    return {
        **placement,
        "avatar_placement": placement,
        "visible": bool(getattr(teacher_profile, "avatar_overlay_visible", True)),
    }


def _avatar_active_for_project(project: Project) -> bool:
    project_user = getattr(project, "user", None)
    teacher_profile = getattr(project_user, "profile", None) if project_user else None
    moderation_gate = avatar_image_moderation_gate(teacher_profile) if teacher_profile is not None else {"blocked": False}
    profile_enabled = bool(
        teacher_profile
        and teacher_profile.avatar_enabled
        and teacher_profile.avatar_consent_confirmed
        and not bool(moderation_gate.get("blocked"))
        and bool(getattr(teacher_profile, "avatar_source_valid", False))
        and not bool(getattr(teacher_profile, "avatar_preview_stale", False))
        and (teacher_profile.avatar_image_processed or teacher_profile.avatar_video_original)
    )
    if project.avatar_enabled_override is None:
        return profile_enabled
    return bool(project.avatar_enabled_override and profile_enabled)


def _avatar_file_version(storage_root: str | os.PathLike[str], rel_path: str) -> str:
    try:
        full_path = Path(storage_root) / str(rel_path).lstrip("/")
        if not full_path.exists() or not full_path.is_file():
            return ""
        stat = full_path.stat()
        digest = hashlib.sha256(f"{rel_path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()
        return digest[:16]
    except Exception:
        return ""


def _avatar_artifact_state(project: Project, sidecar: dict | None = None) -> dict[str, Any]:
    storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
    avatar_payload = sidecar.get("avatar") if isinstance(sidecar, dict) else None
    avatar_payload = avatar_payload if isinstance(avatar_payload, dict) else {}

    project_rel = _normalize_rel_storage_path(str(getattr(project, "avatar_output_path", "") or ""))
    sidecar_rel = _normalize_rel_storage_path(str(avatar_payload.get("track_rel_path") or ""))
    fast_rel = _normalize_rel_storage_path(str(avatar_payload.get("track_fast_rel_path") or ""))
    restored_rel = _normalize_rel_storage_path(str(avatar_payload.get("track_restored_rel_path") or ""))

    fast_exists = bool(fast_rel and _storage_rel_path_exists(storage_root, fast_rel))
    restored_exists = bool(restored_rel and _storage_rel_path_exists(storage_root, restored_rel))
    sidecar_exists = bool(sidecar_rel and _storage_rel_path_exists(storage_root, sidecar_rel))
    project_exists = bool(project_rel and _storage_rel_path_exists(storage_root, project_rel))

    preferred_rel = ""
    quality = str(avatar_payload.get("quality") or "").strip().lower()
    if restored_exists:
        preferred_rel = restored_rel
        quality = "restored"
    elif sidecar_exists:
        preferred_rel = sidecar_rel
        if not quality:
            quality = "fast" if fast_rel and sidecar_rel == fast_rel else "ready"
    elif fast_exists:
        preferred_rel = fast_rel
        quality = "fast"
    elif project_exists:
        preferred_rel = project_rel
        quality = quality or "ready"

    status_ready = str(getattr(project, "avatar_processing_status", "") or "") == "ready"
    visible = bool(getattr(project, "avatar_visible", True))
    available = bool(visible and status_ready and preferred_rel)
    enhanced_pending = bool(avatar_payload.get("enhanced_pending")) and not restored_exists
    updated_at = str(avatar_payload.get("updated_at") or "")
    version = str(avatar_payload.get("version") or "") or _avatar_file_version(storage_root, preferred_rel)
    rel_paths = [
        rel
        for rel in (preferred_rel, sidecar_rel, fast_rel, restored_rel, project_rel)
        if rel and _storage_rel_path_exists(storage_root, rel)
    ]

    return {
        "available": available,
        "rel_path": preferred_rel if available else "",
        "quality": quality or "",
        "enhanced_available": bool(available and restored_exists),
        "enhanced_pending": bool(available and enhanced_pending),
        "version": version,
        "updated_at": updated_at,
        "track_fast_rel_path": fast_rel if fast_exists else "",
        "track_restored_rel_path": restored_rel if restored_exists else "",
        "rel_paths": sorted(set(rel_paths)),
    }


def _avatar_artifact_available(project: Project, sidecar: dict | None = None) -> tuple[bool, str]:
    state = _avatar_artifact_state(project, sidecar)
    return bool(state.get("available")), str(state.get("rel_path") or "")


def _avatar_artifact_rel_paths(project: Project, sidecar: dict | None = None) -> set[str]:
    if not bool(getattr(project, "avatar_visible", True)):
        return set()
    if str(getattr(project, "avatar_processing_status", "") or "") != "ready":
        return set()
    state = _avatar_artifact_state(project, sidecar)
    return {str(path) for path in state.get("rel_paths") or [] if path}


def _legacy_avatar_artifact_available(project: Project, sidecar: dict | None = None) -> tuple[bool, str]:
    if not bool(getattr(project, "avatar_visible", True)):
        return False, ""
    if str(getattr(project, "avatar_processing_status", "") or "") != "ready":
        return False, ""
    avatar_payload = sidecar.get("avatar") if isinstance(sidecar, dict) else None
    sidecar_rel = _normalize_rel_storage_path(str((avatar_payload or {}).get("track_rel_path") or ""))
    project_rel = _normalize_rel_storage_path(str(getattr(project, "avatar_output_path", "") or ""))
    rel_path = project_rel or sidecar_rel
    if not rel_path:
        return False, ""
    if sidecar_rel and project_rel and sidecar_rel != project_rel:
        return False, ""
    if not _storage_rel_path_exists(getattr(settings, "STORAGE_ROOT", "storage_local"), rel_path):
        return False, ""
    return True, rel_path


def _avatar_engine_chain_for_project(project: Project) -> list[str]:
    latest = project.avatar_render_jobs.exclude(render_status="pending").order_by("-created_at").first()
    if latest is None:
        return []
    metadata = latest.metadata if isinstance(latest.metadata, dict) else {}
    chain = metadata.get("final_avatar_engine_chain") or metadata.get("fallback_chain_used") or latest.fallback_chain_used
    return list(chain or [])


def _avatar_engine_selected_for_project(project: Project) -> str:
    latest = project.avatar_render_jobs.exclude(render_status="pending").order_by("-created_at").first()
    if latest is not None:
        metadata = latest.metadata if isinstance(latest.metadata, dict) else {}
        selected = metadata.get("avatar_engine_selected") or metadata.get("normalized_engine") or latest.engine_used
        if selected and str(selected) != "none":
            return str(selected)
    profile = getattr(getattr(project, "user", None), "profile", None)
    if profile is None:
        return ""
    return _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary or os.environ.get("AVATAR_ENGINE"))


def _avatar_runtime_status_for_project(project: Project) -> dict[str, Any]:
    latest = project.avatar_render_jobs.exclude(render_status="pending").order_by("-created_at").first()
    metadata = latest.metadata if latest is not None and isinstance(latest.metadata, dict) else {}
    source_kind = str(metadata.get("musetalk_source_kind") or "")
    selected = str(metadata.get("avatar_engine_selected") or metadata.get("normalized_engine") or getattr(latest, "engine_used", "") or "")
    static_fallback = bool(metadata.get("liveportrait_fallback_used")) or source_kind in {"static_fallback", "static_source"}
    warning = ""
    if source_kind == "static_fallback" or bool(metadata.get("liveportrait_fallback_used")):
        warning = "Avatar used static fallback because motion stage failed."
    elif source_kind == "static_source" or bool(metadata.get("liveportrait_bypassed")):
        warning = "Avatar lip-sync completed; motion fallback was used."
    elif bool(metadata.get("restoration_failed")):
        warning = "Avatar restoration failed; lip-sync output was used."
    return {
        "liveportrait_used": bool(metadata.get("liveportrait_succeeded")) and source_kind == "liveportrait",
        "static_fallback_used": static_fallback,
        "musetalk_only_used": selected == "musetalk_only_fast" or source_kind == "static_source",
        "musetalk_source_kind": source_kind,
        "restoration_failed": bool(metadata.get("restoration_failed")),
        "warning": warning,
    }


def _avatar_playback_state_payload(
    project: Project,
    *,
    avatar_available: bool | None = None,
    avatar_artifact_state: dict | None = None,
) -> dict[str, Any]:
    state = dict(avatar_artifact_state or {})
    available = bool(avatar_available) if avatar_available is not None else _avatar_artifact_available(project)[0]
    updated_at = getattr(project, "avatar_updated_at", None)
    avatar_engine_selected = _avatar_engine_selected_for_project(project)
    return {
        "avatar_processing_status": str(getattr(project, "avatar_processing_status", "") or "none"),
        "avatar_processing_message": str(getattr(project, "avatar_processing_message", "") or ""),
        "avatar_visible": bool(getattr(project, "avatar_visible", True)),
        "avatar_available": available,
        "avatar_updated_at": updated_at.isoformat() if updated_at else None,
        "avatar_engine_selected": avatar_engine_selected,
        "normalized_engine": avatar_engine_selected,
        "final_avatar_engine_chain": _avatar_engine_chain_for_project(project),
        "avatar_runtime_settings": project_avatar_runtime_settings(project),
        "avatar_runtime_status": _avatar_runtime_status_for_project(project),
        "avatar_enhancement": {
            "quality": str(state.get("quality") or ""),
            "enhanced_available": bool(state.get("enhanced_available")),
            "enhanced_pending": bool(state.get("enhanced_pending")),
            "version": str(state.get("version") or ""),
            "updated_at": str(state.get("updated_at") or ""),
        },
    }


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


def _avatar_rerender_output_prefix(project_id: int, base_job: Job, sidecar: dict) -> str:
    rel_path = _normalize_rel_storage_path(
        str((sidecar or {}).get("mp4_rel_path") or getattr(base_job, "result_url", "") or "")
    )
    if not rel_path:
        return str(project_id)
    parent = str(Path(rel_path).parent).replace("\\", "/").strip("/")
    return parent if parent and parent != "." else str(project_id)


def _sidecar_text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("spoken_text", "text", "narration_text", "original_text"):
            if str(value.get(key) or "").strip():
                return str(value.get(key) or "")
        return ""
    return str(value or "")


def _avatar_rerender_ordered_results_from_sidecar(
    *,
    sidecar: dict,
    storage_root: Path,
) -> tuple[list[dict[str, Any]], str]:
    final_segments = sidecar.get("final_segments") if isinstance(sidecar, dict) else None
    if not isinstance(final_segments, list) or not final_segments:
        return [], "playback_assets_missing_final_segments"

    ordered_results: list[dict[str, Any]] = []
    missing_audio: list[int] = []
    for position, segment in enumerate(final_segments, start=1):
        if not isinstance(segment, dict):
            continue
        index = int(segment.get("index") or position - 1)
        tts_rel = _normalize_rel_storage_path(
            str(segment.get("tts_audio") or segment.get("tts_audio_path") or "")
        )
        audio_path = _resolve_storage_file(storage_root, tts_rel) if tts_rel else None
        if audio_path is None:
            missing_audio.append(index)
            continue

        slide_rel = _normalize_rel_storage_path(
            str(segment.get("slide") or segment.get("slide_path") or "")
        )
        slide_path = _resolve_storage_file(storage_root, slide_rel) if slide_rel else None
        try:
            duration = max(0.0, float(segment.get("duration") or 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        try:
            pause_seconds = max(0.0, float(segment.get("pause_seconds") or 0.0))
        except (TypeError, ValueError):
            pause_seconds = 0.0

        ordered_results.append(
            {
                "index": index,
                "slide_num": int(segment.get("slide_num") or position),
                "page_key": str(segment.get("page_key") or ""),
                "text": _sidecar_text_value(segment.get("transcript")),
                "tts_audio_path": str(audio_path),
                "slide_path": str(slide_path or ""),
                "duration": duration,
                "pause_seconds": pause_seconds,
            }
        )

    if missing_audio:
        return [], "avatar_rerender_audio_missing"
    if not ordered_results:
        return [], "playback_assets_missing_render_segments"
    return sorted(ordered_results, key=lambda item: int(item.get("index") or 0)), ""


def _update_project_avatar_api_state(
    project: Project,
    *,
    avatar_status: str,
    message: str,
    job_id: str | int | None = None,
) -> None:
    updates: dict[str, Any] = {
        "avatar_processing_status": str(avatar_status or "none"),
        "avatar_processing_message": str(message or ""),
        "avatar_updated_at": timezone.now(),
        "updated_at": timezone.now(),
    }
    if job_id is not None:
        updates["avatar_last_job_id"] = str(job_id or "")
    Project.objects.filter(pk=project.pk).update(**updates)


def _write_avatar_rerender_handoff_manifest(
    *,
    storage_root: Path,
    project_id: int,
    base_job_id: int,
    payload: dict[str, Any],
) -> str:
    target = storage_root / "projects" / str(project_id) / "renders" / str(base_job_id or "unknown") / "avatar_handoff.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return str(target)


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
        whiteboard_mode_all = str(request.data.get("whiteboard_mode_all", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        try:
            category = _resolve_category_from_upload(request)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        voice_id = _get_voice_id(user)

        avatar_enabled_override = False
        if "avatar_enabled" in request.data:
            avatar_enabled_override = str(request.data.get("avatar_enabled", "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        project = Project.objects.create(
            title=title,
            user=user,
            category=category,
            avatar_enabled_override=avatar_enabled_override,
        )
        avatar_options = _resolve_avatar_options_for_project(project, request)

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

        job = Job.objects.create(project=project, job_type="video_export", status="pending")
        avatar_options = {**avatar_options, "base_job_id": job.id}
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
        ]
        async_result = _dispatch_celery_task(
            _PROCESS_PROJECT_RENDER_TASK,
            args=task_args,
            queue=_render_queue_name(),
        )
        job.celery_task_id = async_result.id
        job.save(update_fields=["celery_task_id"])

        data = JobSerializer(job).data
        data["avatar_processing_status"] = "queued" if avatar_options.get("enabled") else "none"
        data["avatar_processing_message"] = (
            "Avatar is still processing and will be added when ready."
            if avatar_options.get("enabled")
            else str(avatar_options.get("disabled_reason") or "")
        )
        return Response(data, status=status.HTTP_202_ACCEPTED)


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

        was_published = bool(getattr(project, "is_published", False))
        has_category_id = "category_id" in request.data
        has_category_name = "category_name" in request.data or "category" in request.data
        has_avatar_enabled = "avatar_enabled" in request.data
        has_avatar_visible = "avatar_visible" in request.data or "show_avatar" in request.data
        has_avatar_placement = "avatar_placement" in request.data
        has_avatar_runtime_settings = "avatar_runtime_settings" in request.data
        has_is_published = "is_published" in request.data
        has_tts_settings = "tts_settings" in request.data
        draft_only = _truthy_request_value(request.data.get("draft_only"))
        if (
            not has_category_id
            and not has_category_name
            and not has_avatar_enabled
            and not has_avatar_visible
            and not has_avatar_placement
            and not has_avatar_runtime_settings
            and not has_is_published
            and not has_tts_settings
        ):
            return Response(
                {"error": "category_id, category_name, avatar_enabled, avatar_visible, avatar_placement, avatar_runtime_settings, is_published, or tts_settings is required for updates."},
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
            if draft_only and not (has_category_id or has_category_name or has_avatar_enabled or has_avatar_placement or has_avatar_runtime_settings or has_is_published):
                draft_data = ensure_project_draft_data(project)
                draft_data.setdefault("project", {})["tts_settings"] = updates["tts_settings"]
                save_project_draft_data(project, draft_data, dirty=True)
                project.refresh_from_db()
                return Response(ProjectSerializer(project, context={"request": request}).data)
            update_fields.append("tts_settings")

        if has_avatar_enabled:
            raw = str(request.data.get("avatar_enabled", "")).strip().lower()
            if raw in {"", "null"}:
                updates["avatar_enabled_override"] = None
            else:
                updates["avatar_enabled_override"] = raw in {"1", "true", "yes", "on"}
            update_fields.append("avatar_enabled_override")

        if has_avatar_visible:
            raw_visible = request.data.get("avatar_visible", request.data.get("show_avatar"))
            raw = str(raw_visible).strip().lower()
            if raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                return Response({"error": "avatar_visible must be a boolean."}, status=status.HTTP_400_BAD_REQUEST)
            updates["avatar_visible"] = raw in {"1", "true", "yes", "on"}
            update_fields.append("avatar_visible")

        if has_is_published:
            raw = str(request.data.get("is_published", "")).strip().lower()
            if raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                return Response({"error": "is_published must be a boolean."}, status=status.HTTP_400_BAD_REQUEST)
            publish_requested = raw in {"1", "true", "yes", "on"}
            if publish_requested and not project_can_publish(project):
                return Response(publication_block_payload(project), status=status.HTTP_400_BAD_REQUEST)
            updates["is_published"] = publish_requested
            update_fields.append("is_published")

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
            if update_fields:
                project.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])
            if has_avatar_placement:
                pref, _ = AvatarOverlayPreference.objects.get_or_create(user=project.user, lesson=project)
                apply_avatar_placement_to_preference(pref, request.data.get("avatar_placement"))
                pref.save(update_fields=["anchor", "x_percent", "y_percent", "width_percent", "updated_at"])
            if has_avatar_runtime_settings:
                save_project_avatar_runtime_settings(project, request.data.get("avatar_runtime_settings"))
                project.refresh_from_db()
        if has_is_published and not was_published and bool(getattr(project, "is_published", False)):
            try:
                from core.notifications import notify_publisher_posted_lesson

                notify_publisher_posted_lesson(project)
            except Exception:
                logger.warning("Publish notification hook failed for project=%s", project.id, exc_info=True)
            _queue_lesson_intelligence_schedule(
                project.id,
                reason="lesson_published",
                requested_by_id=request.user.id,
                force=False,
            )
            _queue_creator_analytics_intelligence_schedule(
                project.user_id,
                reason="lesson_published",
                force=False,
            )
        return Response(ProjectSerializer(project, context={"request": request}).data)


def _queue_transcript_rerender(
    *,
    project: Project,
    request,
    changed_page_keys: list[str] | set[str],
    pause_sec: float,
    lang_hint: str,
    full_rerender: bool = False,
    use_draft: bool = False,
) -> dict | None:
    voice_id = _get_voice_id(project.user)
    storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
    upload_dir = Path(storage_root) / "uploads" / str(project.id)
    lesson_files = sorted(upload_dir.glob("lesson.*")) if upload_dir.exists() else []
    if not lesson_files:
        return None

    saved_path = str(lesson_files[0])
    job = Job.objects.create(project=project, job_type="video_export", status="pending")
    avatar_options = _resolve_avatar_options_for_project(project, request)
    avatar_options = {**avatar_options, "base_job_id": job.id}
    rerender_keys = [] if full_rerender or use_draft else sorted({str(key) for key in changed_page_keys if str(key)})
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
        _project_render_tts_settings(project, use_draft=use_draft),
    ]
    task_kwargs = {"use_draft": True} if use_draft else {}
    async_result = _dispatch_celery_task(
        _PROCESS_PROJECT_RENDER_TASK,
        args=task_args,
        kwargs=task_kwargs,
        queue=_queue_for_avatar_options(avatar_options),
    )
    job.celery_task_id = async_result.id
    job.save(update_fields=["celery_task_id"])
    data = JobSerializer(job).data
    data["avatar_processing_status"] = "queued" if avatar_options.get("enabled") else "none"
    data["avatar_processing_message"] = (
        "Avatar is still processing and will be added when ready."
        if avatar_options.get("enabled")
        else str(avatar_options.get("disabled_reason") or "")
    )
    return data


def _source_moderation_auto_enabled() -> bool:
    return bool(getattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", False))


def _dispatch_project_moderation_rescan(project: Project, request, *, phase: str):
    return _dispatch_celery_task(
        _RUN_PROJECT_MODERATION_TASK,
        args=[int(project.id)],
        kwargs={
            "triggered_by_user_id": request.user.id if request.user and request.user.is_authenticated else None,
            "phase": phase,
        },
        queue=_render_queue_name(),
    )


def _mark_project_text_moderation_stale(project: Project, request, *, changed_fields: set[str]) -> None:
    if not changed_fields:
        return
    phase = "studio_text_edit"
    changed_at = timezone.now().isoformat()
    summary = dict(project.moderation_summary or {})
    auto_enabled = _source_moderation_auto_enabled()
    next_status = "pending" if auto_enabled else "not_scanned"
    summary.update(
        {
            "moderation_status": next_status,
            "message": (
                "Moderation scan is running for updated Studio text."
                if auto_enabled
                else "Studio text changed. Moderation needs to run again."
            ),
            "phase": phase,
            "editor_text_changed": {
                "status": "pending" if auto_enabled else "needs_rescan",
                "stale": True,
                "needs_rescan": not auto_enabled,
                "reason": "studio_text_changed",
                "changed_fields": sorted(changed_fields),
                "changed_at": changed_at,
            },
        }
    )
    project.moderation_status = next_status
    project.moderation_summary = summary
    project.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])

    if not auto_enabled:
        return

    try:
        task_result = _dispatch_project_moderation_rescan(project, request, phase=phase)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Studio text moderation rescan dispatch failed project=%s", project.id, exc_info=True)
        summary = dict(project.moderation_summary or {})
        editor_text_changed = dict(summary.get("editor_text_changed") or {})
        editor_text_changed.update(
            {
                "status": "needs_rescan",
                "needs_rescan": True,
                "dispatch_error": str(exc)[:240],
            }
        )
        summary["editor_text_changed"] = editor_text_changed
        summary["message"] = "Studio text changed. Moderation needs to run again."
        project.moderation_status = "not_scanned"
        project.moderation_summary = summary
        project.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])
        return

    summary = dict(project.moderation_summary or {})
    editor_text_changed = dict(summary.get("editor_text_changed") or {})
    editor_text_changed["task_id"] = str(getattr(task_result, "id", "") or "")
    summary["editor_text_changed"] = editor_text_changed
    project.moderation_summary = summary
    project.save(update_fields=["moderation_summary", "updated_at"])


def _mark_project_visual_moderation_stale(
    project: Project,
    *,
    reason: str,
    asset_type: str,
    page: TranscriptPage | None = None,
    asset_path: str = "",
) -> None:
    summary = dict(project.moderation_summary or {})
    previous_scan = summary.get("visual_asset_scan")
    if not isinstance(previous_scan, dict):
        previous_scan = {}
    stale_payload = {
        **previous_scan,
        "status": "needs_rescan",
        "stale": True,
        "needs_rescan": True,
        "reason": reason,
        "asset_type": asset_type,
        "message": "Visual asset changed in Studio. Visual moderation needs to run again.",
        "changed_at": timezone.now().isoformat(),
    }
    if asset_path:
        stale_payload["asset_path"] = asset_path
    if page is not None:
        stale_payload.update(
            {
                "transcript_page_id": int(page.id),
                "page_key": str(page.page_key or ""),
                "slide_order": int(page.order or 0),
            }
        )
    summary["visual_asset_scan"] = stale_payload
    project.moderation_summary = summary
    project.save(update_fields=["moderation_summary", "updated_at"])


def _run_auto_visual_moderation_for_changed_asset(
    project: Project,
    *,
    asset_type: str,
    asset_path: str,
    page: TranscriptPage | None = None,
) -> dict | None:
    if not bool(getattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", False)):
        return None
    try:
        services_root = Path(__file__).resolve().parents[2]
        if str(services_root) not in sys.path:
            sys.path.insert(0, str(services_root))
        from worker import tasks as worker_tasks

        export_result: list[dict[str, Any]] = []
        if asset_type != "cover":
            storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
            resolved_path = _resolve_storage_file(storage_root, asset_path)
            export_result.append(
                {
                    "index": int(page.order or 0) if page is not None else 0,
                    "source_slide_index": int(page.source_slide_index or page.order or 0) if page is not None else 0,
                    "page_key": str(page.page_key or "") if page is not None else "",
                    "image_path": str(resolved_path or asset_path),
                }
            )
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, export_result)
        return result if isinstance(result, dict) else None
    except Exception:
        logger.warning(
            "Auto visual moderation scan after asset change failed project=%s asset_type=%s",
            project.id,
            asset_type,
            exc_info=True,
        )
        return None


def _project_moderation_state_payload(project: Project) -> dict:
    summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
    return {
        "moderation_status": project.moderation_status,
        "moderation_summary": summary,
    }


def _truthy_request_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _studio_draft_metadata(project: Project) -> dict:
    draft_data = get_project_draft_data(project)
    metadata = draft_data.get("metadata")
    if isinstance(metadata, dict) and metadata.get("dirty"):
        return dict(metadata)
    return {}


def _studio_transcript_pages(project: Project, request) -> list[dict]:
    if has_project_draft(project):
        return [_draft_page_response(project, page, request) for page in get_studio_transcript_pages(project)]
    return _project_transcript_timeline(project, context={"request": request})


def _studio_deleted_transcript_pages(project: Project, request) -> list[dict]:
    draft_data = get_project_draft_data(project)
    deleted_pages = draft_data.get("deleted_transcript_pages")
    if has_project_draft(project) and isinstance(deleted_pages, list):
        return deleted_pages
    return _project_deleted_transcript_timeline(project, context={"request": request})


def _studio_transcript_response_payload(project: Project, request) -> dict:
    return {
        "project_id": project.id,
        "pages": _studio_transcript_pages(project, request),
        "has_draft": has_project_draft(project),
        "draft_metadata": _studio_draft_metadata(project),
    }


def _draft_moderation_run_id(project: Project) -> int | None:
    draft_data = get_project_draft_data(project)
    metadata = draft_data.get("metadata") if isinstance(draft_data.get("metadata"), dict) else {}
    draft_summary = metadata.get("moderation") if isinstance(metadata.get("moderation"), dict) else {}
    summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
    summary_draft = summary.get("draft_moderation") if isinstance(summary.get("draft_moderation"), dict) else {}
    for value in (draft_summary.get("run_id"), summary_draft.get("run_id")):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _latest_non_draft_moderation_run_id(project: Project) -> int | None:
    try:
        from ai_agents.models import AgentRun
    except Exception:
        return None
    run = (
        AgentRun.objects.filter(project=project, purpose="moderation")
        .exclude(phase__iendswith="_draft")
        .order_by("-created_at", "-id")
        .first()
    )
    return int(run.id) if run else None


def _last_moderation_run_is_draft(project: Project, draft_run_id: int | None) -> bool:
    current_run_id = getattr(project, "last_moderation_run_id", None)
    if not current_run_id:
        return False
    if draft_run_id and int(current_run_id) == int(draft_run_id):
        return True
    try:
        from ai_agents.models import AgentRun
    except Exception:
        return False
    run = AgentRun.objects.filter(pk=int(current_run_id), project=project, purpose="moderation").first()
    return bool(run and str(run.phase or "").lower().endswith("_draft"))


def _discard_project_draft(project: Project) -> Project:
    with transaction.atomic():
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        draft_data = get_project_draft_data(locked_project)
        draft_run_id = _draft_moderation_run_id(locked_project)
        summary = dict(locked_project.moderation_summary or {})
        update_fields: list[str] = []

        if draft_data:
            locked_project.draft_data = {}
            update_fields.append("draft_data")

        if "draft_moderation" in summary:
            summary.pop("draft_moderation", None)
            locked_project.moderation_summary = summary
            update_fields.append("moderation_summary")

        if _last_moderation_run_is_draft(locked_project, draft_run_id):
            locked_project.last_moderation_run_id = _latest_non_draft_moderation_run_id(locked_project)
            update_fields.append("last_moderation_run_id")

        if update_fields:
            locked_project.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])

    locked_project.refresh_from_db()
    return locked_project


def _draft_page_lookup(pages: list[dict], payload: dict) -> dict | None:
    page_id = payload.get("id") or payload.get("page_id")
    page_key_value = payload.get("page_key")
    for page in pages:
        if page_id and page.get("id") and int(page["id"]) == int(page_id):
            return page
        if page_key_value and str(page.get("page_key") or "") == str(page_key_value):
            return page
    return None


def _normalize_draft_page_order(pages: list[dict]) -> None:
    for index, page in enumerate(pages):
        page["order"] = index


def _draft_text_flags(page: dict) -> dict:
    return _text_flags_from_editor_document(
        page.get("editor_document"),
        original_text=page.get("original_text", ""),
        narration_text=page.get("narration_text", ""),
    )


def _set_draft_narration(page: dict, narration_text: str) -> None:
    page["narration_text"] = str(narration_text or "")
    page["subtitle_chunks"] = _chunk_transcript_text(page["narration_text"])


def _draft_page_key(existing_keys: set[str], base_key: str, split_index: int) -> str:
    safe_base = str(base_key or "page").strip() or "page"
    match = re.match(r"^(s\d+-p)(\d+)$", safe_base)
    if match:
        candidate = f"{match.group(1)}{int(match.group(2)) + int(split_index)}"
    else:
        suffix = f"-x{split_index}"
        candidate = f"{safe_base[: max(1, 64 - len(suffix))]}{suffix}"
    while candidate in existing_keys:
        random_suffix = f"-x{split_index}-{uuid.uuid4().hex[:4]}"
        candidate = f"{safe_base[: max(1, 64 - len(random_suffix))]}{random_suffix}"
    existing_keys.add(candidate)
    return candidate


def _next_draft_temp_page_id(draft_data: dict) -> int:
    ids = []
    for collection_key in ("transcript_pages", "deleted_transcript_pages"):
        for page in draft_data.get(collection_key) or []:
            try:
                ids.append(int(page.get("id")))
            except (TypeError, ValueError):
                continue
    negative_ids = [value for value in ids if value < 0]
    return (min(negative_ids) - 1) if negative_ids else -1


def _apply_transcript_draft_updates(project: Project, updates: list[dict]) -> tuple[dict, set[str]]:
    draft_data = ensure_project_draft_data(project)
    pages = draft_data.setdefault("transcript_pages", [])
    changed_page_keys: set[str] = set()

    for item in updates:
        if not isinstance(item, dict):
            continue
        page = _draft_page_lookup(pages, item)
        if page is None:
            continue

        changed = False
        text_flags = _draft_text_flags(page)
        narration_was_customized = bool(text_flags.get("narration_customized"))
        has_display_text = "original_text" in item or "display_text" in item

        if has_display_text:
            raw_display_text = item.get("original_text") if "original_text" in item else item.get("display_text")
            display_text = str(raw_display_text or "")
            if page.get("original_text", "") != display_text:
                page["original_text"] = display_text
                changed = True
            page["rich_text_html"] = str(item.get("rich_text_html") or _rich_text_html_from_narration(display_text))
            text_flags["display_text_customized"] = True
            if not narration_was_customized and "narration_text" not in item:
                if page.get("narration_text", "") != display_text:
                    _set_draft_narration(page, display_text)
                    changed = True
                text_flags["narration_customized"] = False

        if "narration_text" in item:
            narration_text = str(item.get("narration_text") or "")
            if page.get("narration_text", "") != narration_text:
                _set_draft_narration(page, narration_text)
                changed = True
            original_normalized = re.sub(r"\s+", " ", str(page.get("original_text") or "")).strip()
            narration_normalized = re.sub(r"\s+", " ", narration_text).strip()
            text_flags["narration_customized"] = bool(narration_normalized and narration_normalized != original_normalized)

        if "editor_document" in item and isinstance(item.get("editor_document"), dict):
            incoming_document = dict(item.get("editor_document") or {})
            incoming_document.setdefault("text", {})
            if isinstance(incoming_document["text"], dict):
                incoming_document["text"].update({
                    "narration_customized": bool(text_flags.get("narration_customized")),
                    "display_text_customized": bool(text_flags.get("display_text_customized")),
                })
            page["editor_document"] = _merge_editor_document_preserving_scene(
                incoming_document,
                page.get("editor_document") or {},
            )
            changed = True
        elif has_display_text or "narration_text" in item:
            document = dict(page.get("editor_document") or {})
            document["html"] = page.get("rich_text_html", "")
            document["text"] = {
                "narration_customized": bool(text_flags.get("narration_customized")),
                "display_text_customized": bool(text_flags.get("display_text_customized")),
            }
            page["editor_document"] = document
            changed = True

        if "whiteboard_mode" in item:
            next_whiteboard = bool(item.get("whiteboard_mode"))
            if bool(page.get("whiteboard_mode")) != next_whiteboard:
                page["whiteboard_mode"] = next_whiteboard
                changed = True

        if changed:
            changed_page_keys.add(str(page.get("page_key") or page.get("id") or ""))

    _normalize_draft_page_order(pages)
    return draft_data, {key for key in changed_page_keys if key}


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

    text_flags = _text_flags_from_editor_document(
        page.editor_document,
        original_text=page.original_text,
        narration_text=page.narration_text,
    )
    records = _split_segment_records(
        parts_payload,
        source_display_text=page.original_text,
        text_flags=text_flags,
    )

    active_pages = list(_active_transcript_pages(project))
    try:
        insert_at = next(idx for idx, candidate in enumerate(active_pages) if candidate.id == page.id)
    except StopIteration:
        raise TranscriptActionError("page_id must reference an active page in this project.") from None

    existing_keys = set(project.transcript_pages.values_list("page_key", flat=True))
    first_record = dict(records[0])
    first_record["display_text"] = page.original_text
    _set_page_text_artifacts(page, first_record)
    page.save(update_fields=["original_text", "narration_text", "rich_text_html", "editor_document", "subtitle_chunks", "updated_at"])

    created_pages: list[TranscriptPage] = []
    for offset, record in enumerate(records[1:], start=1):
        record_payload = dict(record)
        part_payload = parts_payload[offset] if offset < len(parts_payload) else {}
        has_explicit_display = isinstance(part_payload, dict) and (
            "original_text" in part_payload or "display_text" in part_payload
        )
        if not has_explicit_display:
            record_payload["display_text"] = ""
        split_suffix = f"-x{offset}"
        split_key = f"{page.page_key[: max(1, 64 - len(split_suffix))]}{split_suffix}"
        while split_key in existing_keys:
            random_suffix = f"-x{offset}-{uuid.uuid4().hex[:4]}"
            split_key = f"{page.page_key[: max(1, 64 - len(random_suffix))]}{random_suffix}"
        existing_keys.add(split_key)
        new_page = TranscriptPage(
            project=project,
            order=page.order + offset,
            source_slide_index=page.source_slide_index,
            split_index=page.split_index + offset,
            page_key=split_key,
            whiteboard_mode=page.whiteboard_mode,
            is_active=True,
            deleted_at=None,
        )
        _set_page_text_artifacts(new_page, record_payload)
        new_page.editor_document = _merge_editor_document_preserving_scene(new_page.editor_document, page.editor_document)
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
    survivor.original_text = merged_original
    _set_page_narration_artifacts(survivor, merged_narration)
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


def _draft_split_transcript_page(draft_data: dict, payload: dict) -> list[str]:
    pages = draft_data.setdefault("transcript_pages", [])
    page = _draft_page_lookup(pages, payload)
    if page is None:
        raise TranscriptActionError("page_id must reference an active page in this project.")
    parts_payload = payload.get("parts")
    if not isinstance(parts_payload, list) or len(parts_payload) < 2:
        raise TranscriptActionError("parts must contain at least two transcript parts.")
    if len(parts_payload) > 20:
        raise TranscriptActionError("parts may not contain more than 20 entries.")

    text_flags = _draft_text_flags(page)
    records = _split_segment_records(
        parts_payload,
        source_display_text=str(page.get("original_text") or ""),
        text_flags=text_flags,
    )

    insert_at = pages.index(page)
    existing_keys = {str(candidate.get("page_key") or "") for candidate in pages if candidate.get("page_key")}
    _set_draft_text_artifacts(page, records[0])
    changed_keys = [str(page.get("page_key") or page.get("id") or "")]

    created_pages = []
    next_temp_id = _next_draft_temp_page_id(draft_data)
    for offset, record in enumerate(records[1:], start=1):
        new_page = {
            "id": next_temp_id,
            "order": int(page.get("order") or 0) + offset,
            "source_slide_index": page.get("source_slide_index"),
            "split_index": int(page.get("split_index") or 0) + offset,
            "page_key": _draft_page_key(existing_keys, str(page.get("page_key") or "page"), offset),
            "whiteboard_mode": bool(page.get("whiteboard_mode")),
        }
        _set_draft_text_artifacts(new_page, record)
        new_page["editor_document"] = _merge_editor_document_preserving_scene(
            new_page.get("editor_document") or {},
            page.get("editor_document") or {},
        )
        created_pages.append(new_page)
        changed_keys.append(new_page["page_key"])
        next_temp_id -= 1

    pages[insert_at + 1:insert_at + 1] = created_pages
    _normalize_draft_page_order(pages)
    return [key for key in changed_keys if key]


def _draft_merge_transcript_pages(draft_data: dict, payload: dict, *, direction: str) -> list[str]:
    pages = draft_data.setdefault("transcript_pages", [])
    page = _draft_page_lookup(pages, payload)
    if page is None:
        raise TranscriptActionError("page_id must reference an active page in this project.")
    separator, separator_error = _safe_merge_separator(payload.get("separator"))
    if separator_error:
        raise TranscriptActionError(separator_error)

    current_index = pages.index(page)
    if direction == "next":
        if current_index >= len(pages) - 1:
            raise TranscriptActionError("page has no active next page to merge.")
        survivor = page
        merged_away = pages[current_index + 1]
    else:
        if current_index <= 0:
            raise TranscriptActionError("page has no active previous page to merge.")
        survivor = pages[current_index - 1]
        merged_away = page

    survivor["original_text"] = _combine_text_with_separator(
        survivor.get("original_text", ""),
        merged_away.get("original_text", ""),
        separator,
    )
    _set_draft_narration(
        survivor,
        _combine_text_with_separator(survivor.get("narration_text", ""), merged_away.get("narration_text", ""), separator),
    )
    survivor["rich_text_html"] = _rich_text_html_from_narration(survivor.get("original_text", ""))
    draft_data.setdefault("deleted_transcript_pages", []).append({**merged_away, "deleted_at": timezone.now().isoformat()})
    pages.remove(merged_away)
    _normalize_draft_page_order(pages)
    return [str(survivor.get("page_key") or survivor.get("id") or "")]


def _draft_reorder_transcript_pages(draft_data: dict, payload: dict) -> list[str]:
    page_ids = payload.get("page_ids")
    if not isinstance(page_ids, list) or not page_ids:
        raise TranscriptActionError("page_ids must be a non-empty list.")
    try:
        normalized_ids = [int(item) for item in page_ids]
    except (TypeError, ValueError):
        raise TranscriptActionError("page_ids must contain only page IDs.") from None
    if len(normalized_ids) != len(set(normalized_ids)):
        raise TranscriptActionError("page_ids must not contain duplicates.")

    pages = draft_data.setdefault("transcript_pages", [])
    active_by_id = {int(page["id"]): page for page in pages if page.get("id")}
    if set(normalized_ids) != set(active_by_id):
        raise TranscriptActionError("page_ids must contain every active page exactly once.")

    draft_data["transcript_pages"] = [active_by_id[page_id] for page_id in normalized_ids]
    _normalize_draft_page_order(draft_data["transcript_pages"])
    return []


def _draft_delete_transcript_page(draft_data: dict, payload: dict) -> list[str]:
    pages = draft_data.setdefault("transcript_pages", [])
    page = _draft_page_lookup(pages, payload)
    if page is None:
        raise TranscriptActionError("page_id must reference an active page in this project.")
    if len(pages) <= 1:
        raise TranscriptActionError("cannot delete the last active transcript page.")

    draft_data.setdefault("deleted_transcript_pages", []).append({**page, "deleted_at": timezone.now().isoformat()})
    pages.remove(page)
    _normalize_draft_page_order(pages)
    return [str(page.get("page_key") or page.get("id") or "")]


def _draft_restore_transcript_page(draft_data: dict, payload: dict) -> list[str]:
    deleted_pages = draft_data.setdefault("deleted_transcript_pages", [])
    page = _draft_page_lookup(deleted_pages, payload)
    if page is None:
        raise TranscriptActionError("page_id must reference a deleted page in this project.")

    restored = {key: value for key, value in page.items() if key != "deleted_at"}
    draft_data.setdefault("transcript_pages", []).append(restored)
    deleted_pages.remove(page)
    _normalize_draft_page_order(draft_data["transcript_pages"])
    return [str(restored.get("page_key") or restored.get("id") or "")]


def _apply_transcript_draft_action(project: Project, action: str, payload: dict) -> tuple[dict, list[str]]:
    draft_data = ensure_project_draft_data(project)
    if action == "split_page":
        changed_page_keys = _draft_split_transcript_page(draft_data, payload)
    elif action == "merge_with_next":
        changed_page_keys = _draft_merge_transcript_pages(draft_data, payload, direction="next")
    elif action == "merge_with_previous":
        changed_page_keys = _draft_merge_transcript_pages(draft_data, payload, direction="previous")
    elif action == "reorder_pages":
        changed_page_keys = _draft_reorder_transcript_pages(draft_data, payload)
    elif action == "delete_page":
        changed_page_keys = _draft_delete_transcript_page(draft_data, payload)
    else:
        changed_page_keys = _draft_restore_transcript_page(draft_data, payload)
    return draft_data, changed_page_keys


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

        return Response(_studio_transcript_response_payload(project, request))

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
        requested_draft = _truthy_request_value(request.data.get("draft_only"))
        draft_rerender = trigger_rerender and (requested_draft or has_dirty_draft(project))
        draft_only = requested_draft and not trigger_rerender
        lang_hint = request.data.get("lang_hint", "auto")
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            pause_sec = 2.2

        if draft_only or draft_rerender:
            draft_data, changed_page_keys = _apply_transcript_draft_updates(project, updates)
            save_project_draft_data(project, draft_data, dirty=True)
            project.refresh_from_db()
            rerender_job = None
            if draft_rerender:
                rerender_job = _queue_transcript_rerender(
                    project=project,
                    request=request,
                    changed_page_keys=changed_page_keys,
                    pause_sec=pause_sec,
                    lang_hint=lang_hint,
                    full_rerender=True,
                    use_draft=True,
                )
            payload = {
                **_studio_transcript_response_payload(project, request),
                **_project_moderation_state_payload(project),
                "changed_page_keys": sorted(changed_page_keys),
            }
            if rerender_job:
                payload["rerender_job"] = rerender_job
                payload["rerender_strategy"] = "draft_full"
            if changed_page_keys:
                payload["intelligence_auto_scheduled"] = _queue_lesson_intelligence_schedule(
                    project.id,
                    reason="draft_rerender_requested" if draft_rerender else "draft_transcript_saved",
                    requested_by_id=request.user.id,
                    force=False,
                )
            return Response(payload)

        page_map = {
            p.id: p
            for p in _active_transcript_pages(project)
        }
        changed_page_keys: set[str] = set()
        moderation_changed_fields: set[str] = set()

        for item in updates:
            if not isinstance(item, dict):
                continue
            page_id = item.get("id")
            if not page_id or page_id not in page_map:
                continue

            page = page_map[page_id]
            dirty_fields: list[str] = []
            text_flags = _text_flags_from_editor_document(
                page.editor_document,
                original_text=page.original_text,
                narration_text=page.narration_text,
            )
            narration_was_customized = bool(text_flags.get("narration_customized"))
            display_text_changed = False
            narration_text_changed = False

            has_display_text = "original_text" in item or "display_text" in item
            if has_display_text:
                raw_display_text = item.get("original_text") if "original_text" in item else item.get("display_text")
                display_text = str(raw_display_text or "")
                if page.original_text != display_text:
                    page.original_text = display_text
                    dirty_fields.append("original_text")
                    changed_page_keys.add(str(page.page_key))
                    moderation_changed_fields.add("original_text")
                    display_text_changed = True
                display_html = _rich_text_html_from_narration(display_text)
                if page.rich_text_html != display_html:
                    page.rich_text_html = display_html
                    dirty_fields.append("rich_text_html")
                text_flags["display_text_customized"] = True
                if display_text_changed and not narration_was_customized and "narration_text" not in item:
                    if page.narration_text != display_text:
                        page.narration_text = display_text
                        page.subtitle_chunks = _chunk_transcript_text(display_text)
                        dirty_fields.extend(["narration_text", "subtitle_chunks"])
                        moderation_changed_fields.add("narration_text")
                    text_flags["narration_customized"] = False

            if "narration_text" in item:
                narration_text = str(item.get("narration_text") or "")
                if page.narration_text != narration_text:
                    page.narration_text = narration_text
                    page.subtitle_chunks = _chunk_transcript_text(narration_text)
                    dirty_fields.extend(["narration_text", "subtitle_chunks"])
                    changed_page_keys.add(str(page.page_key))
                    moderation_changed_fields.add("narration_text")
                    narration_text_changed = True
                original_normalized = re.sub(r"\s+", " ", str(page.original_text or "")).strip()
                narration_normalized = re.sub(r"\s+", " ", narration_text).strip()
                text_flags["narration_customized"] = bool(narration_normalized and narration_normalized != original_normalized)

            if "editor_document" in item and isinstance(item.get("editor_document"), dict):
                incoming_document = dict(item.get("editor_document") or {})
                if not has_display_text:
                    current_document = _editor_document_with_scene(
                        page,
                        page.original_text,
                        page.rich_text_html or _rich_text_html_from_narration(page.original_text),
                        text_flags=text_flags,
                    )
                    incoming_scene = _raw_scene_from_document(incoming_document)
                    if isinstance(incoming_document.get("text"), dict):
                        incoming_text_flags = _text_flags_from_editor_document(
                            incoming_document,
                            original_text=page.original_text,
                            narration_text=page.narration_text,
                        )
                        text_flags.update(incoming_text_flags)
                    incoming_document = {
                        **current_document,
                        "text": {
                            "narration_customized": bool(text_flags.get("narration_customized")),
                            "display_text_customized": bool(text_flags.get("display_text_customized")),
                        },
                    }
                    if incoming_scene:
                        incoming_document["scene"] = incoming_scene
                page.editor_document = _merge_editor_document_preserving_scene(incoming_document, page.editor_document)
                dirty_fields.append("editor_document")
            elif "narration_text" in item or has_display_text:
                display_text = page.original_text
                display_html = page.rich_text_html or _rich_text_html_from_narration(display_text)
                page.editor_document = _editor_document_with_scene(page, display_text, display_html, text_flags=text_flags)
                dirty_fields.append("editor_document")

            if "whiteboard_mode" in item:
                page.whiteboard_mode = bool(item.get("whiteboard_mode"))
                dirty_fields.append("whiteboard_mode")

            if dirty_fields:
                dirty_fields.append("updated_at")
                page.save(update_fields=dirty_fields)
                changed_page_keys.add(str(page.page_key))

            if display_text_changed or narration_text_changed:
                page_map[page_id] = page

        rerender_job = None
        if trigger_rerender:
            rerender_job = _queue_transcript_rerender(
                project=project,
                request=request,
                changed_page_keys=changed_page_keys,
                pause_sec=pause_sec,
                lang_hint=lang_hint,
            )

        if moderation_changed_fields:
            project.refresh_from_db(fields=["moderation_status", "moderation_summary"])
            _mark_project_text_moderation_stale(project, request, changed_fields=moderation_changed_fields)
            project.refresh_from_db(fields=["moderation_status", "moderation_summary"])

        payload = {
            "project_id": project.id,
            "pages": _project_transcript_timeline(project, context={"request": request}),
            **_project_moderation_state_payload(project),
        }
        if rerender_job:
            payload["rerender_job"] = rerender_job
        if changed_page_keys:
            payload["intelligence_auto_scheduled"] = _queue_lesson_intelligence_schedule(
                project.id,
                reason="transcript_rerender_requested" if trigger_rerender else "transcript_saved",
                requested_by_id=request.user.id,
                force=False,
            )
        return Response(payload)


def _enhancement_status_for_report(report) -> str:
    if report is None:
        return ""
    return str(enhancement_from_metadata(report.metadata if isinstance(report.metadata, dict) else {}).get("status") or "").lower()


def _intelligence_enhancement_stale_seconds() -> int:
    try:
        return max(1, int(getattr(settings, "INTELLIGENCE_ENHANCEMENT_STALE_SECONDS", 900)))
    except (TypeError, ValueError):
        return 900


def _parse_enhancement_timestamp(value: Any):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _enhancement_is_stale(report) -> bool:
    if report is None:
        return False
    enhancement = enhancement_from_metadata(report.metadata if isinstance(report.metadata, dict) else {})
    status_value = str(enhancement.get("status") or "").strip().lower()
    if status_value not in PENDING_ENHANCEMENT_STATUSES:
        return False
    reference = _parse_enhancement_timestamp(
        enhancement.get("started_at") if status_value == "running" else enhancement.get("queued_at")
    ) or _parse_enhancement_timestamp(enhancement.get("queued_at")) or report.updated_at or report.created_at
    if reference is None:
        return False
    return timezone.now() - reference > timedelta(seconds=_intelligence_enhancement_stale_seconds())


def _with_replaced_intelligence_attempt(
    metadata: dict[str, Any],
    provider: str,
    status_value: str,
    error: Exception | str | None = None,
) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    attempts = metadata.get("provider_chain_attempts")
    next_attempts = [item for item in attempts if isinstance(item, dict)] if isinstance(attempts, list) else []
    replacement = progressive_provider_attempt(normalized_provider, status_value, error)
    for index, item in enumerate(next_attempts):
        if str(item.get("provider") or "").strip().lower() == normalized_provider:
            next_attempts[index] = replacement
            break
    else:
        next_attempts.insert(0, replacement)
    metadata["provider_chain_attempts"] = next_attempts
    return metadata


def _mark_lesson_enhancement_failed(
    report: LessonIntelligenceReport,
    error: Exception | str,
    *,
    stale: bool = False,
) -> LessonIntelligenceReport:
    metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status="failed",
        error=error,
    )
    enhancement = enhancement_from_metadata(metadata)
    if stale:
        enhancement["stale"] = True
    metadata[PROGRESSIVE_ENHANCEMENT_KEY] = enhancement
    _with_replaced_intelligence_attempt(metadata, "ollama", "failed", enhancement.get("error"))
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])
    lock_key = enhancement_lock_key(str(enhancement.get("run_key") or metadata.get("run_key") or ""))
    if lock_key:
        cache.delete(lock_key)
    return report


def _mark_lesson_enhancement_stale(report: LessonIntelligenceReport) -> LessonIntelligenceReport:
    return _mark_lesson_enhancement_failed(
        report,
        "Enhancement task did not complete before stale timeout.",
        stale=True,
    )


def _mark_analytics_enhancement_failed(
    report: AnalyticsIntelligenceReport,
    error: Exception | str,
    *,
    stale: bool = False,
) -> AnalyticsIntelligenceReport:
    metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status="failed",
        error=error,
    )
    enhancement = enhancement_from_metadata(metadata)
    if stale:
        enhancement["stale"] = True
    metadata[PROGRESSIVE_ENHANCEMENT_KEY] = enhancement
    _with_replaced_intelligence_attempt(metadata, "ollama", "failed", enhancement.get("error"))
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])
    lock_key = enhancement_lock_key(str(enhancement.get("run_key") or metadata.get("run_key") or ""))
    if lock_key:
        cache.delete(lock_key)
    return report


def _mark_analytics_enhancement_stale(report: AnalyticsIntelligenceReport) -> AnalyticsIntelligenceReport:
    return _mark_analytics_enhancement_failed(
        report,
        "Enhancement task did not complete before stale timeout.",
        stale=True,
    )


def _recover_stale_lesson_enhancement(report: LessonIntelligenceReport | None) -> LessonIntelligenceReport | None:
    if report is not None and _enhancement_is_stale(report):
        return _mark_lesson_enhancement_stale(report)
    return report


def _recover_stale_analytics_enhancement(report: AnalyticsIntelligenceReport | None) -> AnalyticsIntelligenceReport | None:
    if report is not None and _enhancement_is_stale(report):
        return _mark_analytics_enhancement_stale(report)
    return report


def _report_run_key(report) -> str:
    if report is None:
        return ""
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    enhancement = enhancement_from_metadata(metadata)
    return str(enhancement.get("run_key") or metadata.get("run_key") or "").strip()


def _identity_metadata(run_identity: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(run_identity, dict):
        return {}
    return {
        key: str(run_identity.get(key) or "")
        for key in (
            "run_key",
            "source_hash",
            "provider",
            "model",
            "output_language",
            "prompt_version",
            "hardware_profile",
            "input_fingerprint",
        )
        if run_identity.get(key) is not None
    }


def _force_metadata(force: bool) -> dict[str, Any]:
    if not force:
        return {}
    return {
        "force": True,
        "forced_at": timezone.now().isoformat(),
    }


def _ollama_fallback_retry_candidate(report) -> bool:
    if report is None:
        return False
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    enhancement = enhancement_from_metadata(metadata)
    return bool(
        report.provider == "heuristic"
        and report.fallback_used
        and str(enhancement.get("provider") or "").strip().lower() == "ollama"
        and str(enhancement.get("status") or "").strip().lower() == "failed"
    )


def _retry_available_at_for_report(report):
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    enhancement = enhancement_from_metadata(metadata)
    available_at = _parse_enhancement_timestamp(enhancement.get("retry_available_at"))
    if available_at is not None:
        return available_at
    failure_at = (
        _parse_enhancement_timestamp(enhancement.get("last_ollama_failure_at"))
        or _parse_enhancement_timestamp(enhancement.get("failed_at"))
        or _parse_enhancement_timestamp(enhancement.get("finished_at"))
        or report.updated_at
        or report.created_at
        or timezone.now()
    )
    return failure_at + timedelta(seconds=intelligence_retry_cooldown_seconds())


def _sync_retry_metadata(report):
    if report is None or not _ollama_fallback_retry_candidate(report):
        return report
    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    enhancement = enhancement_from_metadata(metadata)
    available_at = _retry_available_at_for_report(report)
    failure_at = (
        str(enhancement.get("last_ollama_failure_at") or "")
        or str(enhancement.get("failed_at") or "")
        or timezone.now().isoformat()
    )
    retry_count = 0
    try:
        retry_count = max(0, int(enhancement.get("retry_count") or metadata.get("retry_count") or 0))
    except (TypeError, ValueError):
        retry_count = 0
    enhancement.update(
        {
            "last_ollama_failure_at": failure_at,
            "retry_available_at": available_at.isoformat(),
            "retry_cooldown_seconds": intelligence_retry_cooldown_seconds(),
            "retry_count": retry_count,
        }
    )
    diagnostic_reason = ""
    diagnostics = enhancement.get("chunk_diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        last_diagnostic = diagnostics[-1]
        if isinstance(last_diagnostic, dict):
            diagnostic_reason = str(last_diagnostic.get("safe_reason") or last_diagnostic.get("reason") or "")
    current_reason = str(enhancement.get("last_failure_reason") or "")
    if diagnostic_reason and (not current_reason or "chunk analysis failed for all chunks" in current_reason):
        enhancement["last_failure_reason"] = diagnostic_reason
    elif not current_reason:
        enhancement["last_failure_reason"] = enhancement.get("error") or "Ollama enhancement failed."
    metadata[PROGRESSIVE_ENHANCEMENT_KEY] = enhancement
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])
    return report


def _retry_cooldown_active(report) -> bool:
    if report is None or not _ollama_fallback_retry_candidate(report):
        return False
    available_at = _retry_available_at_for_report(report)
    return timezone.now() < available_at


def _retry_attempt_metadata(previous_report, *, force: bool, manual_retry: bool) -> dict[str, Any]:
    if previous_report is None or not _ollama_fallback_retry_candidate(previous_report):
        return {}
    metadata = previous_report.metadata if isinstance(previous_report.metadata, dict) else {}
    enhancement = enhancement_from_metadata(metadata)
    try:
        retry_count = max(0, int(enhancement.get("retry_count") or metadata.get("retry_count") or 0)) + 1
    except (TypeError, ValueError):
        retry_count = 1
    return {
        "manual_retry": bool(manual_retry),
        "retry_count": retry_count,
        "retry_requested_at": timezone.now().isoformat(),
        "retry_bypassed_cooldown": bool(force),
    }


def _latest_lesson_report_for_source(
    project: Project,
    source_hash: str,
    *,
    force: bool = False,
    run_key: str = "",
    manual_retry: bool = False,
):
    normalized_run_key = str(run_key or "").strip()
    if normalized_run_key:
        for report in project.lesson_intelligence_reports.filter(source_hash=source_hash).order_by("-created_at", "-id")[:25]:
            if _report_run_key(report) != normalized_run_key:
                continue
            if force and _enhancement_status_for_report(report) in PENDING_ENHANCEMENT_STATUSES:
                _mark_lesson_enhancement_failed(report, "Enhancement was superseded by a new analysis request.")
                return None
            report = _recover_stale_lesson_enhancement(report)
            status_value = _enhancement_status_for_report(report)
            if status_value in PENDING_ENHANCEMENT_STATUSES:
                return report
            if bool(enhancement_from_metadata(report.metadata if isinstance(report.metadata, dict) else {}).get("stale")):
                return None
            if not force and _ollama_fallback_retry_candidate(report):
                report = _sync_retry_metadata(report)
                if manual_retry and not _retry_cooldown_active(report):
                    return None
                return report
            if not force and status_value == "failed":
                return report
            if not force and (report.provider == "ollama" and (report.status == "done" or status_value in {"done", "partial"})):
                return report
            if not force and report.provider == "heuristic":
                return report
            return None
        return None

    done_ollama = (
        project.lesson_intelligence_reports.filter(source_hash=source_hash, provider="ollama")
        .order_by("-created_at", "-id")
        .first()
    )
    if not force and done_ollama and (done_ollama.status == "done" or _enhancement_status_for_report(done_ollama) in {"done", "partial"}):
        return done_ollama
    latest = (
        project.lesson_intelligence_reports.filter(source_hash=source_hash)
        .order_by("-created_at", "-id")
        .first()
    )
    if latest is None:
        return None
    status_value = _enhancement_status_for_report(latest)
    if status_value in PENDING_ENHANCEMENT_STATUSES:
        if force:
            _mark_lesson_enhancement_failed(latest, "Enhancement was superseded by a new analysis request.")
            return None
        latest = _recover_stale_lesson_enhancement(latest)
        if _enhancement_status_for_report(latest) not in PENDING_ENHANCEMENT_STATUSES:
            return None
        return latest
    if not force and _ollama_fallback_retry_candidate(latest):
        latest = _sync_retry_metadata(latest)
        if manual_retry and not _retry_cooldown_active(latest):
            return None
        return latest
    if not force and latest.provider == "heuristic":
        return latest
    return None


def _latest_analytics_report_for_source(user: User, analytics_input, *, force: bool = False, run_key: str = "", manual_retry: bool = False):
    normalized_run_key = str(run_key or "").strip()
    if normalized_run_key:
        reports = AnalyticsIntelligenceReport.objects.filter(
            requested_by=user,
            scope=analytics_input.scope,
            date_range=analytics_input.date_range,
            category_filter=analytics_input.category_filter,
            source_hash=analytics_input.source_hash,
        ).order_by("-created_at", "-id")[:25]
        for report in reports:
            if _report_run_key(report) != normalized_run_key:
                continue
            if force and _enhancement_status_for_report(report) in PENDING_ENHANCEMENT_STATUSES:
                _mark_analytics_enhancement_failed(report, "Enhancement was superseded by a new analysis request.")
                return None
            report = _recover_stale_analytics_enhancement(report)
            status_value = _enhancement_status_for_report(report)
            if status_value in PENDING_ENHANCEMENT_STATUSES:
                return report
            if bool(enhancement_from_metadata(report.metadata if isinstance(report.metadata, dict) else {}).get("stale")):
                return None
            if not force and _ollama_fallback_retry_candidate(report):
                report = _sync_retry_metadata(report)
                if manual_retry and not _retry_cooldown_active(report):
                    return None
                return report
            if not force and status_value == "failed":
                return report
            if not force and (report.provider == "ollama" and (report.status == "done" or status_value in {"done", "partial"})):
                return report
            if not force and report.provider == "heuristic":
                return report
            return None
        return None

    done_ollama = (
        AnalyticsIntelligenceReport.objects.filter(
            requested_by=user,
            scope=analytics_input.scope,
            date_range=analytics_input.date_range,
            category_filter=analytics_input.category_filter,
            source_hash=analytics_input.source_hash,
            provider="ollama",
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if not force and done_ollama and (done_ollama.status == "done" or _enhancement_status_for_report(done_ollama) in {"done", "partial"}):
        return done_ollama
    latest = (
        AnalyticsIntelligenceReport.objects.filter(
            requested_by=user,
            scope=analytics_input.scope,
            date_range=analytics_input.date_range,
            category_filter=analytics_input.category_filter,
            source_hash=analytics_input.source_hash,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if latest is None:
        return None
    status_value = _enhancement_status_for_report(latest)
    if status_value in PENDING_ENHANCEMENT_STATUSES:
        if force:
            _mark_analytics_enhancement_failed(latest, "Enhancement was superseded by a new analysis request.")
            return None
        latest = _recover_stale_analytics_enhancement(latest)
        if _enhancement_status_for_report(latest) not in PENDING_ENHANCEMENT_STATUSES:
            return None
        return latest
    if not force and _ollama_fallback_retry_candidate(latest):
        latest = _sync_retry_metadata(latest)
        if manual_retry and not _retry_cooldown_active(latest):
            return None
        return latest
    if not force and latest.provider == "heuristic":
        return latest
    return None


def _record_lesson_enhancement_dispatch_failure(report: LessonIntelligenceReport, error: Exception | str):
    metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status="failed",
        error=f"background_queue_unavailable: {safe_enhancement_error(error)}",
    )
    metadata["provider_chain_attempts"] = [
        progressive_provider_attempt("ollama", "failed", "background_queue_unavailable"),
        *[
            item
            for item in (metadata.get("provider_chain_attempts") if isinstance(metadata.get("provider_chain_attempts"), list) else [])
            if isinstance(item, dict) and str(item.get("provider") or "").lower() != "ollama"
        ],
    ]
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])


def _record_analytics_enhancement_dispatch_failure(report: AnalyticsIntelligenceReport, error: Exception | str):
    metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status="failed",
        error=f"background_queue_unavailable: {safe_enhancement_error(error)}",
    )
    metadata["provider_chain_attempts"] = [
        progressive_provider_attempt("ollama", "failed", "background_queue_unavailable"),
        *[
            item
            for item in (metadata.get("provider_chain_attempts") if isinstance(metadata.get("provider_chain_attempts"), list) else [])
            if isinstance(item, dict) and str(item.get("provider") or "").lower() != "ollama"
        ],
    ]
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])


def _reserve_enhancement_run_lock(report) -> str:
    run_key = _report_run_key(report)
    lock_key = enhancement_lock_key(run_key)
    if not lock_key:
        return ""
    timeout = max(_intelligence_enhancement_stale_seconds(), int(getattr(settings, "INTELLIGENCE_ENHANCEMENT_STALE_SECONDS", 900)))
    if cache.add(lock_key, int(report.id), timeout=timeout):
        return lock_key
    return "__duplicate__"


def _queue_lesson_intelligence_enhancement(report: LessonIntelligenceReport) -> None:
    queue_name = _lesson_intelligence_queue_name()
    lock_key = _reserve_enhancement_run_lock(report)
    if lock_key == "__duplicate__":
        _mark_lesson_enhancement_failed(report, "duplicate_enhancement_run_already_queued")
        return
    try:
        async_result = _dispatch_celery_task(
            _LESSON_INTELLIGENCE_ENHANCEMENT_TASK,
            args=[report.id, report.source_hash],
            queue=queue_name,
        )
    except Exception as exc:  # noqa: BLE001
        if lock_key:
            cache.delete(lock_key)
        logger.warning("Lesson intelligence enhancement dispatch failed report=%s error=%s", report.id, exc.__class__.__name__)
        _record_lesson_enhancement_dispatch_failure(report, exc)
        return

    task_id = str(getattr(async_result, "id", "") or "")
    report.metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status="pending",
        task_id=task_id,
        queue=queue_name,
        extra={"sections": lesson_section_statuses(status="pending", provider="ollama")},
    )
    report.save(update_fields=["metadata", "updated_at"])


def _queue_analytics_intelligence_enhancement(report: AnalyticsIntelligenceReport) -> None:
    queue_name = _analytics_intelligence_queue_name()
    lock_key = _reserve_enhancement_run_lock(report)
    if lock_key == "__duplicate__":
        _mark_analytics_enhancement_failed(report, "duplicate_enhancement_run_already_queued")
        return
    try:
        async_result = _dispatch_celery_task(
            _ANALYTICS_INTELLIGENCE_ENHANCEMENT_TASK,
            args=[report.id, report.source_hash],
            queue=queue_name,
        )
    except Exception as exc:  # noqa: BLE001
        if lock_key:
            cache.delete(lock_key)
        logger.warning("Analytics intelligence enhancement dispatch failed report=%s error=%s", report.id, exc.__class__.__name__)
        _record_analytics_enhancement_dispatch_failure(report, exc)
        return

    task_id = str(getattr(async_result, "id", "") or "")
    report.metadata = merge_enhancement_metadata(
        report.metadata if isinstance(report.metadata, dict) else {},
        provider="ollama",
        status="pending",
        task_id=task_id,
        queue=queue_name,
    )
    report.save(update_fields=["metadata", "updated_at"])


def _queue_lesson_intelligence_schedule(
    project_id: int,
    *,
    reason: str,
    requested_by_id: int | None = None,
    force: bool = False,
) -> bool:
    queue_name = _lesson_intelligence_queue_name()
    try:
        _dispatch_celery_task(
            _LESSON_INTELLIGENCE_SCHEDULE_TASK,
            args=[int(project_id)],
            kwargs={
                "reason": str(reason or "auto"),
                "requested_by_id": int(requested_by_id) if requested_by_id else None,
                "force": bool(force),
            },
            queue=queue_name,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Lesson intelligence schedule dispatch failed project=%s", project_id, exc_info=True)
        return False


def _analytics_auto_enabled() -> bool:
    return bool(getattr(settings, "ANALYTICS_INTELLIGENCE_AUTO_ENABLED", True))


def _analytics_auto_interval_seconds() -> int:
    try:
        return max(1, int(getattr(settings, "ANALYTICS_INTELLIGENCE_MIN_AUTO_INTERVAL_SECONDS", 3600)))
    except (TypeError, ValueError):
        return 3600


def _analytics_progress_delta_threshold() -> int:
    try:
        return max(1, int(getattr(settings, "ANALYTICS_INTELLIGENCE_MIN_PROGRESS_EVENT_DELTA", 5)))
    except (TypeError, ValueError):
        return 5


def _analytics_auto_last_key(user_id: int) -> str:
    return f"analytics-intelligence:auto:last:{int(user_id)}"


def _analytics_auto_progress_key(user_id: int) -> str:
    return f"analytics-intelligence:auto:progress-events:{int(user_id)}"


def _analytics_auto_should_dispatch(user_id: int, *, reason: str, force: bool = False) -> bool:
    if force:
        return True
    if not _analytics_auto_enabled():
        return False
    normalized_reason = str(reason or "auto").strip().lower()
    important = normalized_reason in {"lesson_published", "render_completed", "manual", "analytics_opened"}
    interval = _analytics_auto_interval_seconds()
    now_ts = int(time.time())
    last_ts = cache.get(_analytics_auto_last_key(user_id))
    try:
        last_ts = int(last_ts or 0)
    except (TypeError, ValueError):
        last_ts = 0
    interval_elapsed = not last_ts or now_ts - last_ts >= interval
    if important:
        return True if normalized_reason in {"lesson_published", "render_completed"} else interval_elapsed
    if normalized_reason.startswith("lesson_progress"):
        progress_key = _analytics_auto_progress_key(user_id)
        try:
            progress_events = int(cache.incr(progress_key))
        except ValueError:
            cache.set(progress_key, 1, timeout=interval * 2)
            progress_events = 1
        if interval_elapsed or progress_events >= _analytics_progress_delta_threshold():
            cache.set(progress_key, 0, timeout=interval * 2)
            return True
        return False
    return interval_elapsed


def _queue_creator_analytics_intelligence_schedule(
    user_id: int | None,
    *,
    reason: str,
    force: bool = False,
) -> bool:
    if not user_id:
        return False
    if not _analytics_auto_should_dispatch(int(user_id), reason=reason, force=force):
        return False
    queue_name = _analytics_intelligence_queue_name()
    try:
        _dispatch_celery_task(
            _ANALYTICS_INTELLIGENCE_SCHEDULE_TASK,
            args=[int(user_id)],
            kwargs={"reason": str(reason or "auto"), "force": bool(force)},
            queue=queue_name,
        )
        cache.set(_analytics_auto_last_key(int(user_id)), int(time.time()), timeout=_analytics_auto_interval_seconds() * 4)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Analytics intelligence schedule dispatch failed user=%s", user_id, exc_info=True)
        return False


class _AnalyticsIntelligenceScheduleRequest:
    def __init__(self, *, user, query_params: dict[str, Any] | None = None):
        self.user = user
        self.query_params = query_params or {}


def schedule_lesson_intelligence(
    project_or_id,
    *,
    reason: str = "auto",
    requested_by_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Best-effort automatic lesson intelligence scheduler."""
    try:
        project_id = int(getattr(project_or_id, "id", project_or_id))
    except (TypeError, ValueError):
        return {"status": "invalid_project"}
    try:
        project = Project.objects.select_related("user").get(pk=project_id)
    except Project.DoesNotExist:
        return {"status": "missing_project", "project_id": project_id}
    if not lesson_intelligence_enabled():
        return {"status": "disabled", "project_id": project_id}

    try:
        lesson_input = build_lesson_intelligence_input(project)
    except (LessonIntelligenceInputTooLarge, LessonIntelligenceInputError) as exc:
        logger.info("Auto lesson intelligence skipped project=%s reason=%s error=%s", project_id, reason, exc.__class__.__name__)
        return {"status": "skipped", "project_id": project_id, "error": exc.__class__.__name__}

    chain = provider_chain_from_settings()
    run_identity = lesson_ollama_run_identity(lesson_input) if progressive_ollama_enabled(chain) else {}
    existing_report = _latest_lesson_report_for_source(
        project,
        lesson_input.source_hash,
        force=force,
        run_key=str(run_identity.get("run_key") or ""),
    )
    if existing_report is not None:
        return {
            "status": "existing",
            "project_id": project_id,
            "report_id": existing_report.id,
            "provider": existing_report.provider,
        }

    requested_by = User.objects.filter(pk=int(requested_by_id)).first() if requested_by_id else project.user
    report = LessonIntelligenceReport.objects.create(
        project=project,
        requested_by=requested_by if requested_by and requested_by.is_authenticated else project.user,
        status="running",
        provider="heuristic",
        provider_chain=chain,
        fallback_used=False,
        source_hash=lesson_input.source_hash,
    )
    try:
        force_metadata = _force_metadata(force)
        if progressive_ollama_enabled(chain):
            queue_name = _lesson_intelligence_queue_name()
            identity_metadata = _identity_metadata(run_identity)
            analysis = analyze_lesson_heuristic_immediate(
                lesson_input,
                chain=chain,
                enhancement_provider="ollama",
                enhancement_status="queued",
            )
            analysis["metadata"] = {
                **dict(analysis.get("metadata") or {}),
                **identity_metadata,
                **force_metadata,
                "auto_scheduled_at": timezone.now().isoformat(),
                "auto_reason": str(reason or "auto"),
                PROGRESSIVE_ENHANCEMENT_KEY: enhancement_metadata(
                    provider="ollama",
                    status="pending",
                    queue=queue_name,
                    extra={
                        **identity_metadata,
                        **force_metadata,
                        "sections": lesson_section_statuses(status="pending", provider="ollama"),
                    },
                ),
            }
        else:
            analysis = analyze_with_provider_chain(lesson_input, chain=chain)
            analysis["metadata"] = {
                **dict(analysis.get("metadata") or {}),
                **force_metadata,
                "auto_scheduled_at": timezone.now().isoformat(),
                "auto_reason": str(reason or "auto"),
            }
        report = apply_analysis_to_report(report, analysis, source_hash=lesson_input.source_hash)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto lesson intelligence failed project=%s report=%s error=%s", project_id, report.id, exc.__class__.__name__)
        report.status = "failed"
        report.error_message = safe_enhancement_error(exc)
        report.save(update_fields=["status", "error_message", "updated_at"])
        return {"status": "failed", "project_id": project_id, "report_id": report.id, "error": exc.__class__.__name__}

    if progressive_ollama_enabled(chain):
        _queue_lesson_intelligence_enhancement(report)
    return {"status": "scheduled", "project_id": project_id, "report_id": report.id, "provider": report.provider}


def schedule_creator_analytics_intelligence(
    user_or_id,
    *,
    reason: str = "auto",
    force: bool = False,
) -> dict[str, Any]:
    """Best-effort automatic creator analytics intelligence scheduler."""
    try:
        user_id = int(getattr(user_or_id, "id", user_or_id))
    except (TypeError, ValueError):
        return {"status": "invalid_user"}
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return {"status": "missing_user", "user_id": user_id}
    if not _is_verified_teacher(user):
        return {"status": "skipped_role", "user_id": user_id}
    if not analytics_intelligence_enabled():
        return {"status": "disabled", "user_id": user_id}

    query_params = {"range": 30, "category": "", "sort": "views"}
    analytics_payload = CreatorAnalyticsView().build_payload(
        _AnalyticsIntelligenceScheduleRequest(user=user, query_params=query_params)
    )
    try:
        analytics_input = build_analytics_intelligence_input(
            user,
            analytics_payload,
            scope="creator",
            output_language="auto",
        )
    except (AnalyticsIntelligenceInputTooLarge, AnalyticsIntelligenceInputError) as exc:
        logger.info("Auto analytics intelligence skipped user=%s reason=%s error=%s", user_id, reason, exc.__class__.__name__)
        return {"status": "skipped", "user_id": user_id, "error": exc.__class__.__name__}

    chain = analytics_provider_chain_from_settings()
    run_identity = analytics_ollama_run_identity(analytics_input) if progressive_analytics_ollama_enabled(chain) else {}
    existing_report = _latest_analytics_report_for_source(
        user,
        analytics_input,
        force=force,
        run_key=str(run_identity.get("run_key") or ""),
    )
    if existing_report is not None:
        return {
            "status": "existing",
            "user_id": user_id,
            "report_id": existing_report.id,
            "provider": existing_report.provider,
        }

    report = AnalyticsIntelligenceReport.objects.create(
        requested_by=user,
        scope=analytics_input.scope,
        status="running",
        provider="heuristic",
        provider_chain=chain,
        fallback_used=False,
        source_hash=analytics_input.source_hash,
        date_range=analytics_input.date_range,
        category_filter=analytics_input.category_filter,
    )
    try:
        force_metadata = _force_metadata(force)
        if progressive_analytics_ollama_enabled(chain):
            queue_name = _analytics_intelligence_queue_name()
            identity_metadata = _identity_metadata(run_identity)
            analysis = analyze_analytics_heuristic_immediate(
                analytics_input,
                chain=chain,
                enhancement_provider="ollama",
                enhancement_status="queued",
            )
            analysis["metadata"] = {
                **dict(analysis.get("metadata") or {}),
                **identity_metadata,
                **force_metadata,
                "auto_scheduled_at": timezone.now().isoformat(),
                "auto_reason": str(reason or "auto"),
                PROGRESSIVE_ENHANCEMENT_KEY: enhancement_metadata(
                    provider="ollama",
                    status="pending",
                    queue=queue_name,
                    extra={**identity_metadata, **force_metadata},
                ),
            }
        else:
            analysis = analyze_analytics_with_provider_chain(analytics_input, chain=chain)
            analysis["metadata"] = {
                **dict(analysis.get("metadata") or {}),
                **force_metadata,
                "auto_scheduled_at": timezone.now().isoformat(),
                "auto_reason": str(reason or "auto"),
            }
        report = apply_analytics_analysis_to_report(report, analysis, source_hash=analytics_input.source_hash)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto analytics intelligence failed user=%s report=%s error=%s", user_id, report.id, exc.__class__.__name__)
        report.status = "failed"
        report.error_message = safe_enhancement_error(exc)
        report.save(update_fields=["status", "error_message", "updated_at"])
        return {"status": "failed", "user_id": user_id, "report_id": report.id, "error": exc.__class__.__name__}

    if progressive_analytics_ollama_enabled(chain):
        _queue_analytics_intelligence_enhancement(report)
    return {"status": "scheduled", "user_id": user_id, "report_id": report.id, "provider": report.provider}


class ProjectLessonIntelligenceView(APIView):
    """GET/POST /api/v1/projects/<project_id>/intelligence/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_run_lesson_intelligence(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        latest = _recover_stale_lesson_enhancement(
            project.lesson_intelligence_reports.order_by("-created_at", "-id").first()
        )
        enabled = lesson_intelligence_enabled()
        current_source_hash = ""
        current_run_key = ""
        if enabled:
            try:
                lesson_input = build_lesson_intelligence_input(
                    project,
                    output_language=request.query_params.get("output_language") or "auto",
                    request_language=request.headers.get("Accept-Language", ""),
                )
                current_source_hash = lesson_input.source_hash
                chain = provider_chain_from_settings()
                if progressive_ollama_enabled(chain):
                    current_run_key = str(lesson_ollama_run_identity(lesson_input).get("run_key") or "")
            except (LessonIntelligenceInputTooLarge, LessonIntelligenceInputError):
                current_source_hash = ""
                current_run_key = ""
        payload = report_response_payload(
            latest,
            enabled=enabled,
            current_source_hash=current_source_hash,
            current_run_key=current_run_key,
        )
        if not enabled:
            payload["message"] = "Lesson Intelligence is disabled."
        return Response(payload, status=status.HTTP_200_OK)

    def post(self, request, project_id):
        try:
            project = Project.objects.select_related("user").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_run_lesson_intelligence(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if not lesson_intelligence_enabled():
            return Response(
                {
                    "enabled": False,
                    "status": "disabled",
                    "error": "Lesson Intelligence is disabled.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            requested_output_language = (
                request.data.get("output_language")
                or request.query_params.get("output_language")
                or "auto"
            )
            lesson_input = build_lesson_intelligence_input(
                project,
                output_language=requested_output_language,
                request_language=request.headers.get("Accept-Language", ""),
            )
        except LessonIntelligenceInputTooLarge as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except LessonIntelligenceInputError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        chain = provider_chain_from_settings()
        force = _truthy_request_value(request.data.get("force"))
        run_identity = lesson_ollama_run_identity(lesson_input) if progressive_ollama_enabled(chain) else {}
        previous_retry_report = _latest_lesson_report_for_source(
            project,
            lesson_input.source_hash,
            force=False,
            run_key=str(run_identity.get("run_key") or ""),
            manual_retry=False,
        )
        existing_report = _latest_lesson_report_for_source(
            project,
            lesson_input.source_hash,
            force=force,
            run_key=str(run_identity.get("run_key") or ""),
            manual_retry=True,
        )
        if existing_report is not None:
            return Response(
                report_response_payload(
                    existing_report,
                    enabled=True,
                    current_source_hash=lesson_input.source_hash,
                    current_run_key=str(run_identity.get("run_key") or ""),
                ),
                status=status.HTTP_200_OK,
            )

        report = LessonIntelligenceReport.objects.create(
            project=project,
            requested_by=request.user if request.user and request.user.is_authenticated else None,
            status="running",
            provider="heuristic",
            provider_chain=chain,
            fallback_used=False,
            source_hash=lesson_input.source_hash,
        )
        try:
            force_metadata = {
                **_retry_attempt_metadata(previous_retry_report, force=force, manual_retry=True),
                **_force_metadata(force),
            }
            if progressive_ollama_enabled(chain):
                queue_name = _lesson_intelligence_queue_name()
                identity_metadata = _identity_metadata(run_identity)
                analysis = analyze_lesson_heuristic_immediate(
                    lesson_input,
                    chain=chain,
                    enhancement_provider="ollama",
                    enhancement_status="queued",
                )
                analysis["metadata"] = {
                    **dict(analysis.get("metadata") or {}),
                    **identity_metadata,
                    **force_metadata,
                    PROGRESSIVE_ENHANCEMENT_KEY: enhancement_metadata(
                        provider="ollama",
                        status="pending",
                        queue=queue_name,
                        extra={
                            **identity_metadata,
                            **force_metadata,
                            "sections": lesson_section_statuses(status="pending", provider="ollama"),
                        },
                    ),
                }
            else:
                analysis = analyze_with_provider_chain(lesson_input, chain=chain)
                analysis["metadata"] = {
                    **dict(analysis.get("metadata") or {}),
                    **force_metadata,
                }
            report = apply_analysis_to_report(report, analysis, source_hash=lesson_input.source_hash)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Lesson intelligence analysis failed project=%s report=%s", project.id, report.id)
            report.status = "failed"
            report.error_message = str(exc or exc.__class__.__name__)[:500]
            report.save(update_fields=["status", "error_message", "updated_at"])
            payload = report_response_payload(
                report,
                enabled=True,
                current_source_hash=lesson_input.source_hash,
                current_run_key=str(run_identity.get("run_key") or ""),
            )
            payload["error"] = "Lesson Intelligence analysis failed."
            return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if progressive_ollama_enabled(chain):
            _queue_lesson_intelligence_enhancement(report)
            report.refresh_from_db()

        return Response(
            report_response_payload(
                report,
                enabled=True,
                current_source_hash=lesson_input.source_hash,
                current_run_key=str(run_identity.get("run_key") or ""),
            ),
            status=status.HTTP_200_OK,
        )


class ProjectDraftDiscardView(APIView):
    """POST /api/v1/projects/<project_id>/draft/discard/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        had_draft = has_project_draft(project)
        project = _discard_project_draft(project)
        payload = {
            **_studio_transcript_response_payload(project, request),
            **_project_moderation_state_payload(project),
            "project": ProjectSerializer(project, context={"request": request}).data,
            "discarded": had_draft,
        }
        return Response(payload, status=status.HTTP_200_OK)


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
        requested_draft = _truthy_request_value(request.data.get("draft_only"))
        draft_rerender = trigger_rerender and (requested_draft or has_dirty_draft(project))
        draft_only = requested_draft and not trigger_rerender
        lang_hint = request.data.get("lang_hint", "auto")
        try:
            pause_sec = max(0.0, float(request.data.get("pause_sec", 2.2)))
        except (TypeError, ValueError):
            pause_sec = 2.2

        if draft_only or draft_rerender:
            try:
                with transaction.atomic():
                    project = Project.objects.select_for_update().get(pk=project.id)
                    draft_data, changed_page_keys = _apply_transcript_draft_action(project, action, request.data)
                    save_project_draft_data(project, draft_data, dirty=True)
            except TranscriptActionError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            project.refresh_from_db()
            rerender_job = None
            rerender_strategy = "none"
            if draft_rerender:
                rerender_job = _queue_transcript_rerender(
                    project=project,
                    request=request,
                    changed_page_keys=changed_page_keys,
                    pause_sec=pause_sec,
                    lang_hint=lang_hint,
                    full_rerender=True,
                    use_draft=True,
                )
                if rerender_job:
                    rerender_strategy = "draft_full"
            payload = {
                "project_id": project.id,
                "action": action,
                "pages": _studio_transcript_pages(project, request),
                "deleted_pages": _studio_deleted_transcript_pages(project, request),
                "changed_page_keys": changed_page_keys,
                "rerender_job": rerender_job,
                "rerender_strategy": rerender_strategy,
                "has_draft": has_project_draft(project),
                "draft_metadata": _studio_draft_metadata(project),
            }
            if changed_page_keys:
                payload["intelligence_auto_scheduled"] = _queue_lesson_intelligence_schedule(
                    project.id,
                    reason="draft_transcript_action",
                    requested_by_id=request.user.id,
                    force=False,
                )
            return Response(payload)

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

        if changed_page_keys and action in {"split_page", "merge_with_next", "merge_with_previous"}:
            project.refresh_from_db(fields=["moderation_status", "moderation_summary"])
            _mark_project_text_moderation_stale(
                project,
                request,
                changed_fields={"original_text", "narration_text"} if action.startswith("merge") else {"narration_text"},
            )

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
            "pages": _project_transcript_timeline(project, context={"request": request}),
            "deleted_pages": _project_deleted_transcript_timeline(project, context={"request": request}),
            "changed_page_keys": changed_page_keys,
            "rerender_job": rerender_job,
            "rerender_strategy": rerender_strategy,
        }
        if changed_page_keys:
            payload["intelligence_auto_scheduled"] = _queue_lesson_intelligence_schedule(
                project.id,
                reason="transcript_action",
                requested_by_id=request.user.id,
                force=False,
            )
        return Response(payload)


class TranscriptPageBackgroundImageView(APIView):
    """Owner/staff image endpoint for transcript page scene backgrounds."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id, page_id=None, kind=None, page_ref=None):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        page_token = page_ref if page_ref is not None else page_id
        clean_kind = str(kind or "").strip().lower()
        if _truthy_request_value(request.query_params.get("draft")):
            rel_path = _draft_page_scene_path_by_ref(project, page_token, clean_kind)
        else:
            try:
                page = _get_project_page(project, page_token, active=True, field_name="page_id")
            except TranscriptActionError:
                return Response({"error": "Transcript page not found."}, status=status.HTTP_404_NOT_FOUND)
            rel_path = _page_scene_path(page, clean_kind)
        if not rel_path:
            raise Http404

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        full_path = _resolve_storage_file(storage_root, rel_path)
        if full_path is None:
            raise Http404

        content_type, _ = mimetypes.guess_type(str(full_path))
        response = _media_file_response(request, full_path, content_type)
        response["Cache-Control"] = "private, max-age=120"
        return response


class TranscriptPageSceneView(APIView):
    """PATCH scene settings for one transcript page."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, project_id, page_id=None, page_ref=None):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        page_token = page_ref if page_ref is not None else page_id

        if _truthy_request_value(request.data.get("draft_only") or request.query_params.get("draft_only")):
            draft_data = ensure_project_draft_data(project)
            draft_page = _draft_page_for_ref(draft_data, page_token)
            if draft_page is None:
                return Response({"error": "Draft transcript page not found."}, status=status.HTTP_404_NOT_FOUND)
            page = _active_page_for_draft_page(project, draft_page)
            scene = _draft_page_scene_for_storage(draft_page, page)
            if "background_mode" in request.data:
                mode = str(request.data.get("background_mode") or "").strip().lower()
                if mode not in SCENE_BACKGROUND_MODES:
                    return Response(
                        {"error": "background_mode must be original, source_background, whiteboard, or custom."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                validation_error = _scene_mode_validation_error(project, scene, mode)
                if validation_error:
                    return Response({"error": validation_error}, status=status.HTTP_400_BAD_REQUEST)
                scene["background_mode"] = mode
            if "background_fit" in request.data:
                fit = str(request.data.get("background_fit") or "").strip().lower()
                if fit not in SCENE_BACKGROUND_FITS:
                    return Response(
                        {"error": "background_fit must be contain, cover, or stretch."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                scene["background_fit"] = fit
            if "text_scale" in request.data:
                scene["text_scale"] = _clean_scene_text_scale(request.data.get("text_scale"), fallback=scene.get("text_scale", 1.0))
            if "highlight_enabled" in request.data:
                scene["highlight_enabled"] = _truthy_request_value(request.data.get("highlight_enabled"))
            if "highlight_style" in request.data:
                style = _clean_scene_highlight_style(request.data.get("highlight_style"), fallback="")
                if not style:
                    return Response(
                        {"error": "highlight_style must be none, box, or bold."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                scene["highlight_style"] = style
            if "highlight_detector" in request.data:
                detector = _clean_scene_highlight_detector(request.data.get("highlight_detector"), fallback="")
                if not detector:
                    return Response(
                        {"error": "highlight_detector must be auto."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                scene["highlight_detector"] = detector
            scene.pop("highlight", None)
            _apply_scene_highlight_spec(scene, _normalize_scene_highlight_spec(scene))
            _set_draft_page_scene(draft_page, scene)
            metadata = draft_data.setdefault("metadata", {})
            metadata["background_dirty"] = True
            metadata["visual_assets_dirty"] = True
            save_project_draft_data(project, draft_data, dirty=True)
            project.refresh_from_db()
            return Response(
                {
                    "project_id": project.id,
                    "page": _draft_page_response(project, draft_page, request),
                    "has_draft": has_project_draft(project),
                    "draft_metadata": _studio_draft_metadata(project),
                }
            )

        try:
            page = _get_project_page(project, page_token, active=True, field_name="page_id")
        except TranscriptActionError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        scene = _page_scene_for_storage(page)
        if "background_mode" in request.data:
            mode = str(request.data.get("background_mode") or "").strip().lower()
            if mode not in SCENE_BACKGROUND_MODES:
                return Response(
                    {"error": "background_mode must be original, source_background, whiteboard, or custom."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validation_error = _scene_mode_validation_error(project, scene, mode)
            if validation_error:
                return Response({"error": validation_error}, status=status.HTTP_400_BAD_REQUEST)
            scene["background_mode"] = mode
        if "background_fit" in request.data:
            fit = str(request.data.get("background_fit") or "").strip().lower()
            if fit not in SCENE_BACKGROUND_FITS:
                return Response(
                    {"error": "background_fit must be contain, cover, or stretch."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scene["background_fit"] = fit
        if "text_scale" in request.data:
            scene["text_scale"] = _clean_scene_text_scale(request.data.get("text_scale"), fallback=scene.get("text_scale", 1.0))
        if "highlight_enabled" in request.data:
            scene["highlight_enabled"] = _truthy_request_value(request.data.get("highlight_enabled"))
        if "highlight_style" in request.data:
            style = _clean_scene_highlight_style(request.data.get("highlight_style"), fallback="")
            if not style:
                return Response(
                    {"error": "highlight_style must be none, box, or bold."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scene["highlight_style"] = style
        if "highlight_detector" in request.data:
            detector = _clean_scene_highlight_detector(request.data.get("highlight_detector"), fallback="")
            if not detector:
                return Response(
                    {"error": "highlight_detector must be auto."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scene["highlight_detector"] = detector
        scene.pop("highlight", None)
        _apply_scene_highlight_spec(scene, _normalize_scene_highlight_spec(scene))

        _set_page_scene(page, scene)
        page.whiteboard_mode = scene["background_mode"] == "whiteboard"
        page.save(update_fields=["editor_document", "whiteboard_mode", "updated_at"])
        return Response({"project_id": project.id, "page": _page_scene_response(page, request)})


def _record_highlight_preview_metric(*, status_key: str, style: str, detector: str, latency_ms: float, fallback_used: bool) -> None:
    now_bucket = timezone.now().strftime("%Y%m%d%H%M")
    keys = [
        "highlight_preview_requests_total",
        f"highlight_preview_requests_total:{status_key}",
        f"highlight_preview_requests_total:style:{style}",
        f"highlight_preview_requests_total:detector:{detector}",
        f"highlight_preview_requests_total:bucket:{now_bucket}",
    ]
    if fallback_used:
        keys.append("highlight_preview_fallback_total")
    for key in keys:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=86400)
    try:
        cache.set("highlight_preview_latency_ms:last", float(latency_ms), timeout=86400)
    except Exception:
        pass


class TranscriptPageHighlightPreviewView(APIView):
    """POST highlight preview for one transcript page scene."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, page_id=None, page_ref=None):
        if not bool(getattr(settings, "HIGHLIGHT_PREVIEW_ENABLED", False)):
            _record_highlight_preview_metric(
                status_key="disabled",
                style="unknown",
                detector="unknown",
                latency_ms=0.0,
                fallback_used=False,
            )
            return Response({"error": "Highlight preview is disabled."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            _record_highlight_preview_metric(
                status_key="forbidden",
                style="unknown",
                detector="unknown",
                latency_ms=0.0,
                fallback_used=False,
            )
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        page_token = page_ref if page_ref is not None else page_id
        raw_draft_only = request.data.get("draft_only")
        if raw_draft_only is None:
            raw_draft_only = request.query_params.get("draft_only")
        if raw_draft_only is None:
            raw_draft_only = True
        draft_only = _truthy_request_value(raw_draft_only)
        style = _clean_scene_highlight_style(request.data.get("style"), fallback="")
        detector = _clean_scene_highlight_detector(request.data.get("detector"), fallback="")
        forensic_debug = _truthy_request_value(request.data.get("forensic_debug"))
        if not style:
            _record_highlight_preview_metric(
                status_key="invalid_style",
                style="invalid",
                detector=detector or "unknown",
                latency_ms=0.0,
                fallback_used=False,
            )
            return Response({"error": "style must be none, box, or bold."}, status=status.HTTP_400_BAD_REQUEST)
        if not detector:
            _record_highlight_preview_metric(
                status_key="invalid_detector",
                style=style or "unknown",
                detector="invalid",
                latency_ms=0.0,
                fallback_used=False,
            )
            return Response({"error": "detector must be auto."}, status=status.HTTP_400_BAD_REQUEST)

        page = None
        draft_page = None
        if draft_only:
            draft_data = ensure_project_draft_data(project)
            draft_page = _draft_page_for_ref(draft_data, page_token)
            if draft_page is None:
                return Response({"error": "Draft transcript page not found."}, status=status.HTTP_404_NOT_FOUND)
            page = _active_page_for_draft_page(project, draft_page)
            scene = _draft_page_scene_for_storage(draft_page, page)
            page_key = str(draft_page.get("page_key") or page_token or "draft")
            display_text = str(draft_page.get("original_text") or draft_page.get("narration_text") or "")
        else:
            try:
                page = _get_project_page(project, page_token, active=True, field_name="page_id")
            except TranscriptActionError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
            scene = _page_scene_for_storage(page)
            page_key = str(page.page_key or page.id)
            display_text = str(page.original_text or page.narration_text or "")

        mode = _clean_scene_mode(scene.get("background_mode"), fallback="original")
        rel_path = ""
        if mode == "custom":
            rel_path = _normalize_rel_storage_path(str(scene.get("custom_background_path") or ""))
        elif mode == "source_background":
            rel_path = _normalize_rel_storage_path(str(scene.get("source_background_path") or ""))
        else:
            rel_path = _normalize_rel_storage_path(str(scene.get("original_background_path") or ""))
        if not rel_path:
            return Response({"error": "No source image available for highlight preview."}, status=status.HTTP_400_BAD_REQUEST)
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        source_file = _resolve_storage_file(storage_root, rel_path)
        if source_file is None:
            return Response({"error": "Preview source image not found."}, status=status.HTTP_400_BAD_REQUEST)

        output_dir = storage_root / str(project.id) / "highlight_previews"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"page_{re.sub(r'[^A-Za-z0-9_-]+', '_', page_key)[:40]}_{uuid.uuid4().hex[:10]}.png"
        output_file = output_dir / output_name

        started = time.perf_counter()
        from .highlight_engine import apply_highlight

        result = apply_highlight(
            image_path=str(source_file),
            text=display_text,
            style=style,
            detector=detector,
            output_path=str(output_file),
            timeout_sec=float(getattr(settings, "HIGHLIGHT_PREVIEW_TIMEOUT_SECONDS", 12.0) or 12.0),
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 2)

        rel_output = str(output_file.relative_to(storage_root)).replace("\\", "/")
        scene["highlight_enabled"] = style != "none"
        scene["highlight_style"] = style
        scene["highlight_detector"] = detector
        scene.pop("highlight", None)
        _apply_scene_highlight_spec(scene, _normalize_scene_highlight_spec(scene))
        scene["highlight_updated_at"] = timezone.now().isoformat()
        scene["highlight_preview_path"] = rel_output

        if draft_only:
            _set_draft_page_scene(draft_page, scene)
            draft_data = ensure_project_draft_data(project)
            target = _draft_page_for_ref(draft_data, page_token)
            if target is not None:
                _set_draft_page_scene(target, scene)
            metadata = draft_data.setdefault("metadata", {})
            metadata["background_dirty"] = True
            save_project_draft_data(project, draft_data, dirty=True)
            page_payload = _draft_page_response(project, target or draft_page, request)
        else:
            _set_page_scene(page, scene)
            page.save(update_fields=["editor_document", "updated_at"])
            page_payload = _page_scene_response(page, request)

        logger.info(
            "Highlight preview completed project_id=%s page_ref=%s style=%s latency_ms=%s fallback_used=%s error_reason=%s",
            project.id,
            page_token,
            style,
            latency_ms,
            bool(result.get("fallback_used")),
            str(result.get("error_reason") or ""),
        )
        effective_latency_ms = float(result.get("latency_ms") or latency_ms)
        _record_highlight_preview_metric(
            status_key="ok" if bool(result.get("success")) else "fallback",
            style=style,
            detector=str(result.get("detector_used") or detector),
            latency_ms=effective_latency_ms,
            fallback_used=bool(result.get("fallback_used")),
        )

        return Response(
            {
                "success": bool(result.get("success", False)),
                "preview_image_url": page_payload.get("editor_document", {}).get("scene", {}).get("highlight_preview_url", ""),
                "fallback_used": bool(result.get("fallback_used")),
                "detector_used": str(result.get("detector_used") or detector),
                "engine_version": str(result.get("engine_version") or ""),
                "latency_ms": effective_latency_ms,
                "regions": list(result.get("regions") or []),
                "debug_info": {
                    "renderer_used": str(result.get("renderer_used") or style),
                    "forensic_debug": bool(forensic_debug),
                    "latency_ms": effective_latency_ms,
                },
                "error_message": str(result.get("error_reason") or ""),
                "page": page_payload,
            },
            status=status.HTTP_200_OK,
        )


class TranscriptPageHighlightPreviewImageView(APIView):
    """Serve transcript-page highlight preview image."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id, page_id=None, page_ref=None):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise Http404
        if not _can_manage_project(request.user, project):
            raise Http404

        page_token = page_ref if page_ref is not None else page_id
        draft = _truthy_request_value(request.query_params.get("draft"))
        rel_path = ""
        if draft:
            draft_data = get_project_draft_data(project)
            draft_page = _draft_page_for_ref(draft_data, page_token)
            if draft_page is not None:
                scene = _draft_page_scene_for_storage(draft_page, _active_page_for_draft_page(project, draft_page))
                rel_path = _normalize_rel_storage_path(str(scene.get("highlight_preview_path") or ""))
        else:
            try:
                page = _get_project_page(project, page_token, active=True, field_name="page_id")
            except TranscriptActionError:
                raise Http404
            scene = _page_scene_for_storage(page)
            rel_path = _normalize_rel_storage_path(str(scene.get("highlight_preview_path") or ""))

        if not rel_path:
            raise Http404
        full_path = _resolve_storage_file(Path(getattr(settings, "STORAGE_ROOT", "storage_local")), rel_path)
        if full_path is None:
            raise Http404
        response = _media_file_response(request, full_path, "image/png")
        response["Cache-Control"] = "private, max-age=120"
        return response


class TranscriptPageBackgroundUploadView(APIView):
    """Upload a custom background image for one transcript page."""

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, page_id=None, page_ref=None):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        page_token = page_ref if page_ref is not None else page_id
        draft_only = _truthy_request_value(request.data.get("draft_only") or request.query_params.get("draft_only"))
        page = None
        draft_data = None
        draft_page = None
        if draft_only:
            draft_data = ensure_project_draft_data(project)
            draft_page = _draft_page_for_ref(draft_data, page_token)
            if draft_page is None:
                return Response({"error": "Draft transcript page not found."}, status=status.HTTP_404_NOT_FOUND)
            page = _active_page_for_draft_page(project, draft_page)
        else:
            try:
                page = _get_project_page(project, page_token, active=True, field_name="page_id")
            except TranscriptActionError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        background_file = request.FILES.get("background_file") or request.FILES.get("image") or request.FILES.get("file")
        if background_file is None:
            return Response({"error": "background_file is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ext = _validate_cover_upload(background_file)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        background_dir = storage_root / "uploads" / str(project.id) / "backgrounds"
        background_dir.mkdir(parents=True, exist_ok=True)
        safe_page_token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(page_token or getattr(page, "id", "") or "draft"))
        saved_path = background_dir / f"page_{safe_page_token[:32]}_{uuid.uuid4().hex[:10]}{ext}"
        _write_uploaded_file(background_file, saved_path)

        if draft_only:
            scene = _draft_page_scene_for_storage(draft_page, page)
            scene["custom_background_path"] = str(saved_path.relative_to(storage_root)).replace("\\", "/")
            scene["background_mode"] = "custom"
            scene["background_fit"] = _clean_scene_fit(request.data.get("background_fit"), fallback=scene.get("background_fit", "contain"))
            scene["text_scale"] = _clean_scene_text_scale(request.data.get("text_scale"), fallback=scene.get("text_scale", 1.0))
            _set_draft_page_scene(draft_page, scene)
            metadata = draft_data.setdefault("metadata", {})
            metadata["background_dirty"] = True
            metadata["visual_assets_dirty"] = True
            save_project_draft_data(project, draft_data, dirty=True)
            project.refresh_from_db()
            return Response(
                {
                    "project_id": project.id,
                    "page": _draft_page_response(project, draft_page, request),
                    "has_draft": has_project_draft(project),
                    "draft_metadata": _studio_draft_metadata(project),
                },
                status=status.HTTP_200_OK,
            )

        scene = _page_scene_for_storage(page)
        scene["custom_background_path"] = str(saved_path.relative_to(storage_root)).replace("\\", "/")
        scene["background_mode"] = "custom"
        scene["background_fit"] = _clean_scene_fit(request.data.get("background_fit"), fallback=scene.get("background_fit", "contain"))
        scene["text_scale"] = _clean_scene_text_scale(request.data.get("text_scale"), fallback=scene.get("text_scale", 1.0))
        _set_page_scene(page, scene)
        page.whiteboard_mode = False
        page.save(update_fields=["editor_document", "whiteboard_mode", "updated_at"])
        _mark_project_visual_moderation_stale(
            project,
            reason="studio_custom_background_changed",
            asset_type="custom_background",
            page=page,
            asset_path=scene["custom_background_path"],
        )
        _run_auto_visual_moderation_for_changed_asset(
            project,
            asset_type="custom_background",
            asset_path=scene["custom_background_path"],
            page=page,
        )
        project.refresh_from_db(fields=["moderation_status", "moderation_summary"])
        return Response(
            {
                "project_id": project.id,
                "page": _page_scene_response(page, request),
                **_project_moderation_state_payload(project),
            },
            status=status.HTTP_200_OK,
        )


class ProjectBackgroundApplyAllView(APIView):
    """Apply selected scene background settings or an uploaded background to every active page."""

    parser_classes = [JSONParser, MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        draft_only = _truthy_request_value(request.data.get("draft_only") or request.query_params.get("draft_only"))
        custom_path = ""
        requested_mode = str(request.data.get("background_mode") or "").strip().lower()
        background_file = request.FILES.get("background_file") or request.FILES.get("image") or request.FILES.get("file")
        if background_file is not None:
            try:
                ext = _validate_cover_upload(background_file)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
            background_dir = storage_root / "uploads" / str(project.id) / "backgrounds"
            background_dir.mkdir(parents=True, exist_ok=True)
            saved_path = background_dir / f"all_{uuid.uuid4().hex[:10]}{ext}"
            _write_uploaded_file(background_file, saved_path)
            custom_path = str(saved_path.relative_to(storage_root)).replace("\\", "/")
        elif request.data.get("source_page_id") and requested_mode != "source_background":
            try:
                source_page = _get_project_page(project, request.data.get("source_page_id"), active=True, field_name="source_page_id")
            except TranscriptActionError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            custom_path = (
                _draft_page_scene_path(project, source_page, "custom")
                if draft_only
                else _page_scene_path(source_page, "custom")
            )

        requested_mode = requested_mode or ("custom" if custom_path else "")
        if requested_mode and requested_mode not in SCENE_BACKGROUND_MODES:
            return Response(
                {"error": "background_mode must be original, source_background, whiteboard, or custom."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_mode == "custom" and not custom_path:
            return Response(
                {"error": "A custom background image is required before applying custom mode to all pages."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        requested_fit = str(request.data.get("background_fit") or "").strip().lower()
        if requested_fit and requested_fit not in SCENE_BACKGROUND_FITS:
            return Response(
                {"error": "background_fit must be contain, cover, or stretch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pages = list(_active_transcript_pages(project))
        if requested_mode == "source_background":
            source_type = _project_lesson_source_type(project)
            if source_type not in SOURCE_BACKGROUND_SUPPORTED_TYPES:
                return Response(
                    {"error": "Source Background is currently available for PPTX lessons only."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            has_any_source_background = any(
                bool(_page_scene_path(page, "source_background")) for page in pages
            )
            if not has_any_source_background:
                return Response(
                    {"error": "Source Background is not available for these pages."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if draft_only:
            draft_data = ensure_project_draft_data(project)
            for page in pages:
                draft_page = _draft_page_for_active_page(draft_data, page)
                if draft_page is None:
                    continue
                scene = _draft_page_scene_for_storage(draft_page, page)
                if custom_path:
                    scene["custom_background_path"] = custom_path
                if requested_mode:
                    scene["background_mode"] = requested_mode
                if requested_fit:
                    scene["background_fit"] = requested_fit
                if "text_scale" in request.data:
                    scene["text_scale"] = _clean_scene_text_scale(request.data.get("text_scale"), fallback=scene.get("text_scale", 1.0))
                _set_draft_page_scene(draft_page, scene)
            metadata = draft_data.setdefault("metadata", {})
            metadata["background_dirty"] = True
            metadata["visual_assets_dirty"] = True
            save_project_draft_data(project, draft_data, dirty=True)
            project.refresh_from_db()
            return Response(
                {
                    "project_id": project.id,
                    "pages": _studio_transcript_pages(project, request),
                    "has_draft": has_project_draft(project),
                    "draft_metadata": _studio_draft_metadata(project),
                }
            )

        for page in pages:
            scene = _page_scene_for_storage(page)
            if custom_path:
                scene["custom_background_path"] = custom_path
            if requested_mode:
                scene["background_mode"] = requested_mode
            if requested_fit:
                scene["background_fit"] = requested_fit
            if "text_scale" in request.data:
                scene["text_scale"] = _clean_scene_text_scale(request.data.get("text_scale"), fallback=scene.get("text_scale", 1.0))
            _set_page_scene(page, scene)
            page.whiteboard_mode = scene["background_mode"] == "whiteboard"
            page.save(update_fields=["editor_document", "whiteboard_mode", "updated_at"])

        if custom_path:
            _mark_project_visual_moderation_stale(
                project,
                reason="studio_custom_background_applied",
                asset_type="custom_background",
                asset_path=custom_path,
            )
            _run_auto_visual_moderation_for_changed_asset(
                project,
                asset_type="custom_background",
                asset_path=custom_path,
                page=pages[0] if pages else None,
            )
            project.refresh_from_db(fields=["moderation_status", "moderation_summary"])

        return Response(
            {
                "project_id": project.id,
                "pages": _project_transcript_timeline(project, context={"request": request}),
                **_project_moderation_state_payload(project),
            }
        )


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

        use_draft = has_dirty_draft(project)
        job = Job.objects.create(project=project, job_type="video_export", status="pending")
        avatar_options = _resolve_avatar_options_for_project(project, request)
        avatar_options = {**avatar_options, "base_job_id": job.id}
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
            _project_render_tts_settings(project, use_draft=use_draft),
        ]
        task_kwargs = {"use_draft": True} if use_draft else {}
        async_result = _dispatch_celery_task(
            _PROCESS_PROJECT_RENDER_TASK,
            args=task_args,
            kwargs=task_kwargs,
            queue=_queue_for_avatar_options(avatar_options),
        )
        job.celery_task_id = async_result.id
        job.save(update_fields=["celery_task_id"])

        data = JobSerializer(job).data
        data["avatar_processing_status"] = "queued" if avatar_options.get("enabled") else "none"
        data["avatar_processing_message"] = (
            "Avatar is still processing and will be added when ready."
            if avatar_options.get("enabled")
            else str(avatar_options.get("disabled_reason") or "")
        )
        return Response(data, status=status.HTTP_202_ACCEPTED)


class ProjectAvatarRerenderView(APIView):
    """POST /api/v1/projects/<project_id>/avatar/rerender/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id):
        try:
            project = Project.objects.select_related("user").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_manage_project(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        force = _truthy_request_value(request.data.get("force") or request.query_params.get("force"))
        current_avatar_status = str(getattr(project, "avatar_processing_status", "") or "none").strip().lower()
        if current_avatar_status in {"queued", "processing"} and not force:
            return Response(
                {
                    "avatar_processing_status": current_avatar_status,
                    "avatar_job_id": str(getattr(project, "avatar_last_job_id", "") or ""),
                    "avatar_runtime_settings": project_avatar_runtime_settings(project),
                    "message": "Avatar rerender is already queued or processing.",
                },
                status=status.HTTP_200_OK,
            )

        base_job = _latest_completed_video_export_job(project)
        if base_job is None or not str(getattr(base_job, "result_url", "") or "").strip():
            return Response(
                {
                    "error": "Base lesson render is not ready.",
                    "avatar_processing_status": current_avatar_status,
                    "message": "Render the lesson before rerendering the avatar overlay.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        sidecar = _playback_sidecar_for_job(str(storage_root), project.id)
        if not sidecar:
            return Response(
                {
                    "error": "Playback assets are not ready.",
                    "avatar_processing_status": current_avatar_status,
                    "message": "The latest base render is missing playback assets.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        ordered_results, segment_error = _avatar_rerender_ordered_results_from_sidecar(
            sidecar=sidecar,
            storage_root=storage_root,
        )
        if segment_error:
            return Response(
                {
                    "error": "Playback assets are incomplete.",
                    "reason": segment_error,
                    "avatar_processing_status": current_avatar_status,
                    "message": "Avatar rerender requires the latest render audio segments.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        avatar_options = _resolve_avatar_options_for_project(project, request)
        if not bool(avatar_options.get("requested", avatar_options.get("enabled", False))):
            return Response(
                {
                    "error": "Avatar is disabled for this lesson.",
                    "avatar_processing_status": current_avatar_status,
                    "avatar_runtime_settings": project_avatar_runtime_settings(project),
                    "message": "Enable the avatar before rerendering the avatar overlay.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not bool(avatar_options.get("enabled")):
            reason = str(
                avatar_options.get("disabled_reason")
                or avatar_options.get("avatar_source_validation_error")
                or avatar_options.get("avatar_moderation_error_code")
                or "avatar_prerequisites_missing"
            )
            message = "Avatar rerender could not start because avatar prerequisites are missing."
            _update_project_avatar_api_state(project, avatar_status="failed", message=message, job_id="")
            return Response(
                {
                    "error": reason,
                    "avatar_processing_status": "failed",
                    "avatar_runtime_settings": project_avatar_runtime_settings(project),
                    "message": message,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        avatar_cfg = {**avatar_options, "base_job_id": int(base_job.id)}
        output_rel_prefix = _avatar_rerender_output_prefix(project.id, base_job, sidecar)
        avatar_job = Job.objects.create(project=project, job_type="avatar_render", status="pending", progress=0)
        handoff_manifest_path = _write_avatar_rerender_handoff_manifest(
            storage_root=storage_root,
            project_id=project.id,
            base_job_id=int(base_job.id),
            payload={
                "schema_version": 1,
                "project_id": int(project.id),
                "base_job_id": int(base_job.id),
                "avatar_job_id": int(avatar_job.id),
                "created_at": timezone.now().isoformat(),
                "ordered_results": ordered_results,
                "avatar_settings": avatar_cfg,
                "source_hashes": {
                    "avatar_source_hash": str(avatar_cfg.get("avatar_source_hash") or ""),
                    "avatar_preview_source_hash": str(avatar_cfg.get("avatar_preview_source_hash") or ""),
                },
                "render_metadata": {
                    "output_rel_prefix": output_rel_prefix,
                    "slide_count": len(ordered_results),
                    "lipsync_engine": str(avatar_cfg.get("lipsync_engine") or ""),
                    "model_version": str(avatar_cfg.get("model_version") or ""),
                    "avatar_only_rerender": True,
                },
                "status": "created",
            },
        )
        _update_project_avatar_api_state(
            project,
            avatar_status="queued",
            message="Avatar is still processing and will be added when ready.",
            job_id=avatar_job.id,
        )
        try:
            async_result = _dispatch_celery_task(
                _AVATAR_OVERLAY_TASK,
                kwargs={
                    "project_id": int(project.id),
                    "teacher_id": int(avatar_cfg.get("teacher_id") or 0),
                    "output_rel_prefix": output_rel_prefix,
                    "avatar_job_id": int(avatar_job.id),
                    "handoff_manifest_path": handoff_manifest_path,
                    "base_job_id": int(base_job.id),
                },
                queue=_avatar_queue_name(),
            )
            task_id = str(getattr(async_result, "id", "") or "")
            if task_id:
                avatar_job.celery_task_id = task_id
                avatar_job.save(update_fields=["celery_task_id", "updated_at"])
        except Exception:
            logger.warning("Avatar-only rerender enqueue failed for project=%s", project.id, exc_info=True)
            avatar_job.status = "failed"
            avatar_job.error_message = "Avatar rerender could not be queued."
            avatar_job.progress = 100
            avatar_job.save(update_fields=["status", "error_message", "progress", "updated_at"])
            _update_project_avatar_api_state(
                project,
                avatar_status="failed",
                message="Avatar rerender could not be queued.",
                job_id=avatar_job.id,
            )
            try:
                from core.notifications import notify_avatar_failed

                notify_avatar_failed(project, avatar_job)
            except Exception:
                logger.warning("Avatar enqueue failure notification hook failed for project=%s", project.id, exc_info=True)
            return Response(
                {
                    "error": "avatar_rerender_enqueue_failed",
                    "avatar_processing_status": "failed",
                    "avatar_job_id": avatar_job.id,
                    "message": "Avatar rerender could not be queued.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        project.refresh_from_db()
        return Response(
            {
                "avatar_processing_status": "queued",
                "avatar_job_id": avatar_job.id,
                "base_job_id": base_job.id,
                "avatar_runtime_settings": project_avatar_runtime_settings(project),
                "message": "Avatar rerender queued.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class JobStatusView(APIView):
    """GET /api/v1/projects/<project_id>/jobs/<job_id>/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id, job_id):
        try:
            job = Job.objects.get(pk=job_id, project_id=project_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        if job.project and not _can_manage_project(request.user, job.project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        data = JobSerializer(job).data
        storage_root = getattr(settings, "STORAGE_ROOT", "storage_local")
        data["language_detection"] = _language_detection_sidecar_for_job(storage_root, int(project_id))
        data["transcript_pages"] = _studio_transcript_pages(job.project, request) if job.project else []
        data["has_draft"] = has_project_draft(job.project) if job.project else False
        data["draft_metadata"] = _studio_draft_metadata(job.project) if job.project else {}
        if job.project:
            data.update(_avatar_playback_state_payload(job.project))
        return Response(data)


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
                "normalized_engine": str(readiness.get("normalized_engine") or _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary)),
                "avatar_engine_selected": str(readiness.get("avatar_engine_selected") or _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary)),
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
                "avatar_moderation_status": profile.avatar_moderation_status,
                "avatar_moderation_summary": profile.avatar_moderation_summary if isinstance(profile.avatar_moderation_summary, dict) else {},
                "avatar_last_moderation_run_id": profile.avatar_last_moderation_run_id,
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
            profile.avatar_image_original = rel_original
            profile.save(update_fields=["avatar_image_original", "updated_at"])
            run_avatar_image_moderation(profile, saved_original, persist=True)
            moderation_gate = avatar_image_moderation_gate(profile)
            if moderation_gate.get("blocked"):
                return _avatar_moderation_block_response(profile, moderation_gate, status_label="avatar_not_prepared")

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
            requested_engine_raw = str(data.get("avatar_lipsync_engine") or "musetalk").strip().lower()
            if requested_engine_raw not in {"musetalk", "liveportrait+musetalk"}:
                requested_engine_raw = "musetalk"
            requested_engine = requested_engine_raw
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
        selected_engine = str(readiness.get("avatar_engine_selected") or _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary))
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
                    "normalized_engine": selected_engine,
                    "avatar_engine_selected": selected_engine,
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
                "normalized_engine": selected_engine,
                "avatar_engine_selected": selected_engine,
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
        if original_abs is not None and original_abs.exists() and original_abs.is_file():
            current_moderation_status = str(profile.avatar_moderation_status or "not_scanned").strip().lower()
            if avatar_image_moderation_auto_enabled() and (force_reprocess or current_moderation_status in {"not_scanned", "skipped", "failed"}):
                run_avatar_image_moderation(profile, original_abs, persist=True)
            moderation_gate = avatar_image_moderation_gate(profile)
            if moderation_gate.get("blocked"):
                return _avatar_moderation_block_response(profile, moderation_gate, status_label="avatar_not_prepared")
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
        selected_engine = str(readiness.get("avatar_engine_selected") or _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary))
        if readiness.get("ready"):
            profile.avatar_image_status = "ready"
            profile.avatar_preview_error = ""
            profile.save(update_fields=["avatar_enabled", "avatar_consent_confirmed", "avatar_image_processed", "avatar_version_hash", "avatar_image_status", "avatar_preview_error", "updated_at"])
            return Response(
                {
                    "status": "avatar_ready",
                    "readiness": readiness,
                    "normalized_engine": selected_engine,
                    "avatar_engine_selected": selected_engine,
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
                "normalized_engine": selected_engine,
                "avatar_engine_selected": selected_engine,
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
        if str(profile.avatar_last_preview_job_id or "") == str(job.id) and payload["preview_status"] in {"ready", "warning"}:
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
        selected_engine = str(readiness.get("avatar_engine_selected") or _normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary))
        payload["normalized_engine"] = selected_engine
        payload["avatar_engine_selected"] = selected_engine
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
        apply_avatar_placement_to_preference(pref, request.data)
        pref.visible = self._to_bool(request.data.get("visible"), pref.visible)
        pref.pinned = self._to_bool(request.data.get("pinned"), pref.pinned)
        pref.save(update_fields=["anchor", "x_percent", "y_percent", "width_percent", "visible", "pinned", "updated_at"])
        return Response(AvatarOverlayPreferenceSerializer(pref).data)


# ---------------------------------------------------------------------------
# Student catalog (public browsing)
# ---------------------------------------------------------------------------

class CategoryListView(APIView):
    """GET /api/v1/categories/ — public list of lesson categories."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cache_key = None
        if not (request.user and request.user.is_authenticated) and _CATALOG_CACHE_TTL_SECONDS > 0:
            cache_key = _catalog_cache_key("categories", request)
            cached = cache.get(cache_key)
            if cached is not None:
                return Response(cached)
        categories = Category.objects.all()
        payload = CategorySerializer(categories, many=True).data
        if cache_key:
            cache.set(cache_key, payload, timeout=_CATALOG_CACHE_TTL_SECONDS)
        return Response(payload)


class CatalogListView(APIView):
    """
    GET /api/v1/catalog/
    Public list of published lessons that have at least one completed render job.
    Supports ?category=<slug> filter.
    Returns only safe metadata — no raw storage paths.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cache_key = None
        if not (request.user and request.user.is_authenticated) and _CATALOG_CACHE_TTL_SECONDS > 0:
            cache_key = _catalog_cache_key("list", request)
            cached = cache.get(cache_key)
            if cached is not None:
                return Response(cached)

        # Public catalog: published + render done + moderation approved/not_scanned.
        # Lessons that are unscanned (not_scanned) are also shown so that projects
        # without auto-moderation enabled are not silently hidden from the catalog.
        # Explicitly rejected/blocked lessons are excluded via moderation_status__in.
        projects = (
            Project.objects.filter(
                is_published=True,
                status="ready",
                moderation_status__in=APPROVED_MODERATION_STATUSES | frozenset({"not_scanned"}),
                jobs__status="done",
            )
            .exclude(moderation_status__in=["admin_rejected", "revision_required"])
            .select_related("user", "category")
            .annotate(
                likes_count=Count("likes", distinct=True),
                comments_count=Count("comments", distinct=True),
                followers_count=Count("user__publisher_followers", distinct=True),
                has_video_export_done=Exists(
                    Job.objects.filter(
                        project_id=OuterRef("pk"),
                        job_type="video_export",
                        status="done",
                    )
                ),
            )
            .distinct()
            .order_by("-created_at")
        )
        category_slug = request.query_params.get("category")
        if category_slug:
            projects = projects.filter(category__slug=category_slug)
        payload = CatalogProjectSerializer(projects, many=True, context={"request": request}).data
        if cache_key:
            cache.set(cache_key, payload, timeout=_CATALOG_CACHE_TTL_SECONDS)
        return Response(payload)


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
        likes = int(getattr(project, "likes_count", 0) or 0)
        comments = int(getattr(project, "comments_count", 0) or 0)
        age_hours = max(1.0, (now_ts - project.created_at.timestamp()) / 3600.0)
        recency_score = round(1000.0 / (1.0 + age_hours), 2)
        popularity_score = round((likes * 7.0) + (comments * 4.0), 2)
        blended_score = round((popularity_score * 0.65) + (recency_score * 0.35), 2)

        data = dict(serializer_data)
        data.update(
            {
                "teacher_id": project.user_id,
                "teacher_username": project.user.username if project.user else "",
                "duration_minutes": max(2, (int(getattr(project, "slides_count", 0) or 0) or 1) * 2),
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
        cache_key = None
        if not (request.user and request.user.is_authenticated) and _CATALOG_CACHE_TTL_SECONDS > 0:
            cache_key = _catalog_cache_key("feed", request)
            cached = cache.get(cache_key)
            if cached is not None:
                return Response(cached)

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
            Project.objects.filter(
                is_published=True,
                status="ready",
                moderation_status__in=APPROVED_MODERATION_STATUSES | frozenset({"not_scanned"}),
                jobs__status="done",
            )
            .exclude(moderation_status__in=["admin_rejected", "revision_required"])
            .select_related("user", "category")
            .annotate(
                likes_count=Count("likes", distinct=True),
                comments_count=Count("comments", distinct=True),
                followers_count=Count("user__publisher_followers", distinct=True),
                slides_count=Count("slides", distinct=True),
                has_video_export_done=Exists(
                    Job.objects.filter(
                        project_id=OuterRef("pk"),
                        job_type="video_export",
                        status="done",
                    )
                ),
            )
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

        payload = {
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
        if cache_key:
            cache.set(cache_key, payload, timeout=_CATALOG_CACHE_TTL_SECONDS)
        return Response(payload)


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
        for progress in progress_qs.select_related("project").order_by("-updated_at")[:25]:
            try:
                progress_pct = float(progress.progress_pct or 0)
            except (TypeError, ValueError):
                progress_pct = 0.0
            if 0 < progress_pct < 1:
                progress_pct *= 100.0
            progress_pct = int(max(0.0, min(100.0, progress_pct)))
            recent_activity.append(
                {
                    "type": "progress",
                    "timestamp": progress.updated_at.isoformat(),
                    "lesson_id": progress.project_id,
                    "lesson_title": progress.project.title,
                    "value": progress_pct,
                    "description": f"A learner reached {progress_pct}% progress.",
                }
            )
        for like in like_qs.select_related("project").order_by("-created_at")[:20]:
            recent_activity.append(
                {
                    "type": "like",
                    "timestamp": like.created_at.isoformat(),
                    "lesson_id": like.project_id,
                    "lesson_title": like.project.title,
                    "value": 1,
                    "description": "A learner liked a lesson.",
                }
            )
        for comment in comment_qs.select_related("project").order_by("-created_at")[:20]:
            recent_activity.append(
                {
                    "type": "comment",
                    "timestamp": comment.created_at.isoformat(),
                    "lesson_id": comment.project_id,
                    "lesson_title": comment.project.title,
                    "value": 1,
                    "description": "A learner commented.",
                }
            )
        recent_activity.sort(key=lambda item: item["timestamp"], reverse=True)
        recent_activity = recent_activity[:30]

        category_interest = (
            progress_qs.values("project__category__slug", "project__category__name")
            .annotate(total=Count("id"))
            .order_by("-total")[:80]
        )
        publisher_interest = (
            progress_qs.values("project__user_id", "project__user__username")
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
                "learner_interest_aggregates": {
                    "top_categories": [
                        {
                            "category_slug": row.get("project__category__slug") or "uncategorized",
                            "category_name": row.get("project__category__name") or "Uncategorized",
                            "watch_events": row["total"],
                        }
                        for row in category_interest
                    ],
                    "top_publishers": [
                        {
                            "publisher_id": row.get("project__user_id"),
                            "publisher_name": row.get("project__user__username") or "Unknown",
                            "watch_events": row["total"],
                        }
                        for row in publisher_interest
                    ],
                    "repeat_watch": {
                        "repeat_viewers": repeat_watch_users,
                        "definition": "Learners with progress across at least 3 distinct lessons in the selected range.",
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
                        "top_categories",
                        "top_publishers",
                    ],
                },
            }
        )


class CreatorAnalyticsView(APIView):
    """
    GET /api/v1/me/analytics/
    Creator-scoped analytics for the signed-in teacher/publisher.

    Staff can call this endpoint, but it remains scoped to their own projects.
    Platform-wide analytics stay behind /api/v1/admin/stats/.
    """

    permission_classes = [permissions.IsAuthenticated]

    def _parse_date(self, raw_value: str | None, default_date):
        if not raw_value:
            return default_date
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d").date()
        except ValueError:
            return default_date

    def _bounded_date_range(self, request):
        today = timezone.now().date()
        raw_range = str(request.query_params.get("range") or "30").strip()
        try:
            range_days = int(raw_range)
        except (TypeError, ValueError):
            range_days = 30
        range_days = range_days if range_days in {7, 30, 90} else 30

        default_from = today - timedelta(days=range_days - 1)
        date_from = self._parse_date(request.query_params.get("from"), default_from)
        date_to = self._parse_date(request.query_params.get("to"), today)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        max_days = 180
        if (date_to - date_from).days > max_days:
            date_from = date_to - timedelta(days=max_days)
        return date_from, date_to, range_days

    def _pct_delta(self, current_value: float, previous_value: float) -> float:
        if previous_value <= 0:
            return 100.0 if current_value > 0 else 0.0
        return round(((current_value - previous_value) / previous_value) * 100.0, 2)

    def _progress_pct(self, raw_value) -> float:
        try:
            numeric = float(raw_value or 0)
        except (TypeError, ValueError):
            numeric = 0.0
        if 0 < numeric < 1:
            numeric *= 100.0
        return max(0.0, min(100.0, numeric))

    def _remember_latest_activity(self, lesson_payload: dict | None, timestamp) -> None:
        if not lesson_payload or not timestamp:
            return
        current = lesson_payload.get("_latest_activity_at")
        if current is None or timestamp > current:
            lesson_payload["_latest_activity_at"] = timestamp

    def _activity_item(self, *, activity_type: str, timestamp, lesson_id: int, lesson_title: str, value=None) -> dict:
        labels = {
            "progress": "Progress",
            "like": "Like",
            "comment": "Comment",
        }
        if activity_type == "progress":
            message = f"A learner made progress on {lesson_title}."
            description = f"A learner reached {int(self._progress_pct(value))}% progress on {lesson_title}."
        elif activity_type == "like":
            message = f"A learner liked {lesson_title}."
            description = message
        elif activity_type == "comment":
            message = f"A learner commented on {lesson_title}."
            description = message
        else:
            message = f"Activity recorded for {lesson_title}."
            description = message

        return {
            "type": activity_type,
            "label": labels.get(activity_type, "Activity"),
            "message": message,
            "description": description,
            "timestamp": timestamp.isoformat() if timestamp else "",
            "lesson_id": lesson_id,
            "lesson_title": lesson_title,
            "value": value,
        }

    def get(self, request):
        if not _is_verified_teacher(request.user):
            return Response(
                {"error": "Only teacher or publisher accounts can view creator analytics."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(self.build_payload(request), status=status.HTTP_200_OK)

    def build_payload(self, request) -> dict[str, Any]:
        date_from, date_to, range_days = self._bounded_date_range(request)
        from_dt = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
        to_dt = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))

        category_slug = (request.query_params.get("category") or "").strip()
        sort_by = (request.query_params.get("sort") or "views").strip().lower()
        supported_sort = ["views", "completion", "watch_time", "likes", "comments", "date"]
        if sort_by not in supported_sort:
            sort_by = "views"

        owned_projects_base = (
            Project.objects.filter(user=request.user)
            .select_related("user", "category")
            .prefetch_related("slides", "jobs", "likes", "comments")
            .distinct()
        )

        category_options = [
            category
            for category in Category.objects.filter(projects__user=request.user).distinct().order_by("name")
        ]

        projects_qs = owned_projects_base
        if category_slug:
            projects_qs = projects_qs.filter(category__slug=category_slug)

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

        progress_rows = list(progress_qs.values("project_id", "user_id", "progress_pct", "updated_at"))
        for row in progress_rows:
            row["progress_pct"] = self._progress_pct(row.get("progress_pct"))
        progress_count = len(progress_rows)
        unique_viewers = len({row["user_id"] for row in progress_rows})
        completed_rows = [row for row in progress_rows if float(row["progress_pct"] or 0) >= 90]
        completion_rate = round((len(completed_rows) / progress_count) * 100.0, 2) if progress_count else 0.0
        average_progress = (
            round(sum(float(row["progress_pct"] or 0) for row in progress_rows) / progress_count, 2)
            if progress_count
            else 0.0
        )

        like_count = like_qs.count()
        comment_count = comment_qs.count()
        engagement_events = progress_count + like_count + comment_count
        estimated_watch_minutes = 0.0
        for row in progress_rows:
            duration = float(duration_map.get(row["project_id"], 8))
            estimated_watch_minutes += duration * (float(row["progress_pct"] or 0) / 100.0)
        estimated_watch_minutes = round(estimated_watch_minutes, 2)

        previous_to = from_dt - timedelta(microseconds=1)
        window_days = max(1, (date_to - date_from).days + 1)
        previous_from = previous_to - timedelta(days=window_days)
        previous_progress_qs = LessonProgress.objects.filter(
            project_id__in=project_ids,
            updated_at__gte=previous_from,
            updated_at__lte=previous_to,
        )
        previous_like_qs = LessonLike.objects.filter(
            project_id__in=project_ids,
            created_at__gte=previous_from,
            created_at__lte=previous_to,
        )
        previous_comment_qs = LessonComment.objects.filter(
            project_id__in=project_ids,
            created_at__gte=previous_from,
            created_at__lte=previous_to,
        )
        prev_views = previous_progress_qs.count()
        prev_unique = previous_progress_qs.values("user_id").distinct().count()
        prev_completion_count = previous_progress_qs.filter(progress_pct__gte=90).count()
        prev_completion_rate = round((prev_completion_count / prev_views) * 100.0, 2) if prev_views else 0.0
        prev_engagement = prev_views + previous_like_qs.count() + previous_comment_qs.count()

        lesson_rollup = {}
        for project in project_list:
            lesson_rollup[project.id] = {
                "lesson_id": project.id,
                "id": project.id,
                "title": project.title,
                "category_slug": project.category.slug if project.category else "",
                "category_name": project.category.name if project.category else "Uncategorized",
                "status": project.status,
                "is_published": bool(project.is_published),
                "has_cover": bool(project.cover_image_processed or project.cover_image_original),
                "missing_cover": not bool(project.cover_image_processed or project.cover_image_original),
                "created_at": project.created_at.isoformat() if project.created_at else "",
                "updated_at": project.updated_at.isoformat() if project.updated_at else "",
                "latest_activity_at": project.updated_at.isoformat() if project.updated_at else "",
                "_latest_activity_at": project.updated_at or project.created_at,
                "views": 0,
                "video_plays": 0,
                "progress_events": 0,
                "unique_viewers": 0,
                "average_progress": 0.0,
                "average_progress_pct": 0.0,
                "progress_pct": 0.0,
                "completion_rate": 0.0,
                "completion_pct": 0.0,
                "completion_count": 0,
                "likes": 0,
                "comments": 0,
                "engagement_events": 0,
                "estimated_watch_minutes": 0.0,
            }

        by_lesson_users = {}
        by_lesson_progress_sum = {}
        for row in progress_rows:
            lesson = lesson_rollup.get(row["project_id"])
            if not lesson:
                continue
            progress_pct = float(row["progress_pct"] or 0)
            lesson["views"] += 1
            lesson["video_plays"] += 1
            lesson["progress_events"] += 1
            lesson["estimated_watch_minutes"] += float(duration_map.get(row["project_id"], 8)) * (progress_pct / 100.0)
            if progress_pct >= 90:
                lesson["completion_count"] += 1
            by_lesson_users.setdefault(row["project_id"], set()).add(row["user_id"])
            by_lesson_progress_sum[row["project_id"]] = by_lesson_progress_sum.get(row["project_id"], 0.0) + progress_pct
            self._remember_latest_activity(lesson, row.get("updated_at"))

        for row in like_qs.values("project_id").annotate(total=Count("id")):
            lesson = lesson_rollup.get(row["project_id"])
            if lesson:
                lesson["likes"] = int(row["total"])
        for row in like_qs.values("project_id", "created_at"):
            self._remember_latest_activity(lesson_rollup.get(row["project_id"]), row.get("created_at"))

        for row in comment_qs.values("project_id").annotate(total=Count("id")):
            lesson = lesson_rollup.get(row["project_id"])
            if lesson:
                lesson["comments"] = int(row["total"])
        for row in comment_qs.values("project_id", "created_at"):
            self._remember_latest_activity(lesson_rollup.get(row["project_id"]), row.get("created_at"))

        lessons_table = []
        for lesson_id, payload in lesson_rollup.items():
            users = by_lesson_users.get(lesson_id, set())
            views = payload["views"]
            payload["unique_viewers"] = len(users)
            payload["average_progress"] = round((by_lesson_progress_sum.get(lesson_id, 0.0) / views), 2) if views else 0.0
            payload["average_progress_pct"] = payload["average_progress"]
            payload["progress_pct"] = payload["average_progress"]
            payload["completion_rate"] = round((payload["completion_count"] / views) * 100.0, 2) if views else 0.0
            payload["completion_pct"] = payload["completion_rate"]
            payload["estimated_watch_minutes"] = round(payload["estimated_watch_minutes"], 2)
            payload["engagement_events"] = views + payload["likes"] + payload["comments"]
            latest_activity_at = payload.pop("_latest_activity_at", None)
            payload["latest_activity_at"] = latest_activity_at.isoformat() if latest_activity_at else payload["updated_at"]
            lessons_table.append(payload)

        if sort_by == "completion":
            lessons_table.sort(
                key=lambda item: (
                    item["completion_rate"],
                    item["average_progress"],
                    item["engagement_events"],
                ),
                reverse=True,
            )
        elif sort_by == "watch_time":
            lessons_table.sort(key=lambda item: item["estimated_watch_minutes"], reverse=True)
        elif sort_by == "likes":
            lessons_table.sort(key=lambda item: item["likes"], reverse=True)
        elif sort_by == "comments":
            lessons_table.sort(key=lambda item: item["comments"], reverse=True)
        elif sort_by == "date":
            lessons_table.sort(key=lambda item: item["created_at"], reverse=True)
        else:
            lessons_table.sort(
                key=lambda item: (
                    item["engagement_events"],
                    item["views"],
                    item["latest_activity_at"],
                ),
                reverse=True,
            )

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
                    "video_plays": 0,
                    "unique_viewers": 0,
                    "average_progress": 0.0,
                    "completion_rate": 0.0,
                    "likes": 0,
                    "comments": 0,
                    "engagement_events": 0,
                    "estimated_watch_minutes": 0.0,
                },
            )
            row["lesson_count"] += 1
            row["views"] += lesson["views"]
            row["video_plays"] += lesson["video_plays"]
            row["unique_viewers"] += lesson["unique_viewers"]
            row["average_progress"] += lesson["average_progress"]
            row["completion_rate"] += lesson["completion_rate"]
            row["likes"] += lesson["likes"]
            row["comments"] += lesson["comments"]
            row["engagement_events"] += lesson["engagement_events"]
            row["estimated_watch_minutes"] += lesson["estimated_watch_minutes"]

        category_table = []
        for item in category_rows.values():
            item["average_progress"] = round(item["average_progress"] / max(1, item["lesson_count"]), 2)
            item["completion_rate"] = round(item["completion_rate"] / max(1, item["lesson_count"]), 2)
            item["estimated_watch_minutes"] = round(item["estimated_watch_minutes"], 2)
            category_table.append(item)
        category_table.sort(key=lambda item: item["engagement_events"], reverse=True)

        trend_points = []
        cursor = date_from
        while cursor <= date_to:
            day_start = timezone.make_aware(datetime.combine(cursor, datetime.min.time()))
            day_end = timezone.make_aware(datetime.combine(cursor, datetime.max.time()))
            day_progress = progress_qs.filter(updated_at__gte=day_start, updated_at__lte=day_end)
            day_likes = like_qs.filter(created_at__gte=day_start, created_at__lte=day_end).count()
            day_comments = comment_qs.filter(created_at__gte=day_start, created_at__lte=day_end).count()
            day_views = day_progress.count()
            trend_points.append(
                {
                    "date": cursor.isoformat(),
                    "views": day_views,
                    "video_plays": day_views,
                    "unique_viewers": day_progress.values("user_id").distinct().count(),
                    "completions": day_progress.filter(progress_pct__gte=90).count(),
                    "likes": day_likes,
                    "comments": day_comments,
                    "engagement": day_views + day_likes + day_comments,
                }
            )
            cursor += timedelta(days=1)

        recent_activity = []
        for progress in progress_qs.select_related("project").order_by("-updated_at")[:25]:
            recent_activity.append(
                self._activity_item(
                    activity_type="progress",
                    timestamp=progress.updated_at,
                    lesson_id=progress.project_id,
                    lesson_title=progress.project.title,
                    value=int(self._progress_pct(progress.progress_pct)),
                )
            )
        for like in like_qs.select_related("project").order_by("-created_at")[:20]:
            recent_activity.append(
                self._activity_item(
                    activity_type="like",
                    timestamp=like.created_at,
                    lesson_id=like.project_id,
                    lesson_title=like.project.title,
                    value=1,
                )
            )
        for comment in comment_qs.select_related("project").order_by("-created_at")[:20]:
            recent_activity.append(
                self._activity_item(
                    activity_type="comment",
                    timestamp=comment.created_at,
                    lesson_id=comment.project_id,
                    lesson_title=comment.project.title,
                    value=1,
                )
            )
        recent_activity.sort(key=lambda item: item["timestamp"], reverse=True)
        recent_activity = recent_activity[:30]

        comment_feedback_limit = max(0, int(getattr(settings, "ANALYTICS_INTELLIGENCE_RECENT_COMMENTS_LIMIT", 20)))
        comment_feedback_chars = max(40, int(getattr(settings, "ANALYTICS_INTELLIGENCE_COMMENT_MAX_CHARS", 280)))
        recent_comment_rows = list(
            comment_qs.select_related("project").order_by("-created_at")[: comment_feedback_limit + 1]
        ) if comment_feedback_limit else []
        recent_comments = []
        comments_text_truncated = False
        for comment in recent_comment_rows[:comment_feedback_limit]:
            raw_text = re.sub(r"\s+", " ", str(comment.text or "")).strip()
            raw_text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", raw_text)
            raw_text = re.sub(r"(?<!\w)@[A-Za-z0-9_.-]{2,}", "@[handle]", raw_text)
            text = raw_text
            if len(text) > comment_feedback_chars:
                text = text[: max(0, comment_feedback_chars - 1)].rstrip() + "..."
                comments_text_truncated = True
            recent_comments.append(
                {
                    "lesson_id": int(comment.project_id),
                    "lesson_title": str(getattr(comment.project, "title", "") or "Untitled lesson")[:200],
                    "text": text,
                    "created_at": comment.created_at.isoformat() if comment.created_at else "",
                }
            )
        comments_count_truncated = len(recent_comment_rows) > comment_feedback_limit

        recent_lessons = sorted(
            lessons_table,
            key=lambda item: item.get("latest_activity_at") or item.get("updated_at") or item.get("created_at") or "",
            reverse=True,
        )[:20]
        lessons_with_activity = [
            lesson for lesson in lessons_table
            if int(lesson.get("views") or 0) > 0
            or int(lesson.get("engagement_events") or 0) > 0
            or float(lesson.get("average_progress") or 0) > 0
        ]
        weak_lessons = sorted(
            lessons_with_activity,
            key=lambda item: (
                float(item.get("completion_rate") or 0),
                float(item.get("average_progress") or 0),
                -int(item.get("views") or 0),
                str(item.get("title") or ""),
            ),
        )[:10]
        strong_lessons = sorted(
            lessons_with_activity,
            key=lambda item: (
                float(item.get("completion_rate") or 0),
                float(item.get("average_progress") or 0),
                int(item.get("engagement_events") or 0),
                int(item.get("views") or 0),
            ),
            reverse=True,
        )[:5]

        return {
            "summary": {
                "total_lessons": len(project_list),
                "published_lessons": sum(1 for project in project_list if project.is_published),
                "draft_lessons": sum(1 for project in project_list if not project.is_published),
                "video_plays": progress_count,
                "total_views": progress_count,
                "unique_viewers": unique_viewers,
                "estimated_watch_time_minutes": estimated_watch_minutes,
                "completion_rate": completion_rate,
                "average_progress": average_progress,
                "engagement_events": engagement_events,
                "likes": like_count,
                "comments": comment_count,
                "trends": {
                    "video_plays_pct": self._pct_delta(float(progress_count), float(prev_views)),
                    "unique_viewers_pct": self._pct_delta(float(unique_viewers), float(prev_unique)),
                    "completion_rate_pct": self._pct_delta(float(completion_rate), float(prev_completion_rate)),
                    "engagement_events_pct": self._pct_delta(float(engagement_events), float(prev_engagement)),
                },
            },
            "charts": {
                "engagement_trend": trend_points,
                "category_popularity": category_table[:12],
            },
            "tables": {
                "top_lessons": lessons_table[:20],
                "recent_lessons": recent_lessons,
                "top_categories": category_table[:12],
            },
            "recent_activity": recent_activity,
            "qualitative_feedback": {
                "recent_comments": recent_comments,
                "truncated": bool(comments_count_truncated or comments_text_truncated),
                "limit": comment_feedback_limit,
                "max_comment_chars": comment_feedback_chars,
            },
            "lesson_quality": {
                "weak_lessons": weak_lessons,
                "strong_lessons": strong_lessons,
                "missing_cover_count": sum(1 for lesson in lessons_table if lesson.get("missing_cover")),
                "with_cover_count": sum(1 for lesson in lessons_table if lesson.get("has_cover")),
                "limitations": [
                    "Lesson intelligence summaries are included only for selected weak/strong lessons.",
                ],
            },
            "filters": {
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
                "range": range_days,
                "category": category_slug,
                "sort": sort_by,
            },
            "options": {
                "categories": CategorySerializer(category_options, many=True).data,
                "supported_ranges": [7, 30, 90],
                "supported_sort": supported_sort,
            },
            "meta": {
                "contract": "creator_analytics_v1",
                "scope": "creator",
                "estimated_metrics": True,
                "comment_feedback_truncated": bool(comments_count_truncated or comments_text_truncated),
                "estimated_fields": [
                    "video_plays",
                    "total_views",
                    "estimated_watch_time_minutes",
                ],
                "missing_metrics": [
                    "exact_play_events",
                    "exact_watch_time_seconds",
                    "viewer_session_count",
                    "satisfaction_score",
                    "revenue",
                ],
            },
        }


class CreatorAnalyticsIntelligenceView(APIView):
    """GET/POST /api/v1/me/analytics/intelligence/"""

    permission_classes = [permissions.IsAuthenticated]

    def _forbidden_response(self):
        return Response(
            {"error": "Only teacher or publisher accounts can analyze creator analytics."},
            status=status.HTTP_403_FORBIDDEN,
        )

    def _creator_payload(self, request) -> dict[str, Any]:
        return CreatorAnalyticsView().build_payload(request)

    def get(self, request):
        if not _is_verified_teacher(request.user):
            return self._forbidden_response()

        enabled = analytics_intelligence_enabled()
        if not enabled:
            payload = analytics_report_response_payload(None, enabled=False)
            payload["message"] = "Analytics Intelligence is disabled."
            return Response(payload, status=status.HTTP_200_OK)

        try:
            analytics_payload = self._creator_payload(request)
            analytics_input = build_analytics_intelligence_input(
                request.user,
                analytics_payload,
                scope="creator",
                output_language=request.query_params.get("output_language") or "auto",
                request_language=request.headers.get("Accept-Language", ""),
            )
        except (AnalyticsIntelligenceInputTooLarge, AnalyticsIntelligenceInputError):
            latest = (
                AnalyticsIntelligenceReport.objects.filter(requested_by=request.user, scope="creator")
                .order_by("-created_at", "-id")
                .first()
            )
            latest = _recover_stale_analytics_enhancement(latest)
            return Response(analytics_report_response_payload(latest, enabled=True), status=status.HTTP_200_OK)

        latest = _recover_stale_analytics_enhancement(
            AnalyticsIntelligenceReport.objects.filter(
                requested_by=request.user,
                scope="creator",
                date_range=analytics_input.date_range,
                category_filter=analytics_input.category_filter,
            )
            .order_by("-created_at", "-id")
            .first()
        )
        payload = analytics_report_response_payload(
            latest,
            enabled=True,
            current_source_hash=analytics_input.source_hash,
            current_run_key=(
                str(analytics_ollama_run_identity(analytics_input).get("run_key") or "")
                if progressive_analytics_ollama_enabled(analytics_provider_chain_from_settings())
                else ""
            ),
        )
        return Response(
            payload,
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        if not _is_verified_teacher(request.user):
            return self._forbidden_response()
        if not analytics_intelligence_enabled():
            return Response(
                {
                    "enabled": False,
                    "status": "disabled",
                    "error": "Analytics Intelligence is disabled.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        analytics_payload = self._creator_payload(request)
        try:
            requested_output_language = (
                request.data.get("output_language")
                or request.query_params.get("output_language")
                or "auto"
            )
            analytics_input = build_analytics_intelligence_input(
                request.user,
                analytics_payload,
                scope="creator",
                output_language=requested_output_language,
                request_language=request.headers.get("Accept-Language", ""),
            )
        except AnalyticsIntelligenceInputTooLarge as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AnalyticsIntelligenceInputError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        chain = analytics_provider_chain_from_settings()
        force = _truthy_request_value(request.data.get("force"))
        run_identity = analytics_ollama_run_identity(analytics_input) if progressive_analytics_ollama_enabled(chain) else {}
        previous_retry_report = _latest_analytics_report_for_source(
            request.user,
            analytics_input,
            force=False,
            run_key=str(run_identity.get("run_key") or ""),
            manual_retry=False,
        )
        existing_report = _latest_analytics_report_for_source(
            request.user,
            analytics_input,
            force=force,
            run_key=str(run_identity.get("run_key") or ""),
            manual_retry=True,
        )
        if existing_report is not None:
            return Response(
                analytics_report_response_payload(
                    existing_report,
                    enabled=True,
                    current_source_hash=analytics_input.source_hash,
                    current_run_key=str(run_identity.get("run_key") or ""),
                ),
                status=status.HTTP_200_OK,
            )

        report = AnalyticsIntelligenceReport.objects.create(
            requested_by=request.user if request.user and request.user.is_authenticated else None,
            scope=analytics_input.scope,
            status="running",
            provider="heuristic",
            provider_chain=chain,
            fallback_used=False,
            source_hash=analytics_input.source_hash,
            date_range=analytics_input.date_range,
            category_filter=analytics_input.category_filter,
        )
        try:
            force_metadata = {
                **_retry_attempt_metadata(previous_retry_report, force=force, manual_retry=True),
                **_force_metadata(force),
            }
            if progressive_analytics_ollama_enabled(chain):
                queue_name = _analytics_intelligence_queue_name()
                identity_metadata = _identity_metadata(run_identity)
                analysis = analyze_analytics_heuristic_immediate(
                    analytics_input,
                    chain=chain,
                    enhancement_provider="ollama",
                    enhancement_status="queued",
                )
                analysis["metadata"] = {
                    **dict(analysis.get("metadata") or {}),
                    **identity_metadata,
                    **force_metadata,
                    PROGRESSIVE_ENHANCEMENT_KEY: enhancement_metadata(
                        provider="ollama",
                        status="pending",
                        queue=queue_name,
                        extra={**identity_metadata, **force_metadata},
                    ),
                }
            else:
                analysis = analyze_analytics_with_provider_chain(analytics_input, chain=chain)
                analysis["metadata"] = {
                    **dict(analysis.get("metadata") or {}),
                    **force_metadata,
                }
            report = apply_analytics_analysis_to_report(
                report,
                analysis,
                source_hash=analytics_input.source_hash,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Analytics intelligence analysis failed user=%s report=%s", request.user.id, report.id)
            report.status = "failed"
            report.error_message = str(exc or exc.__class__.__name__)[:500]
            report.save(update_fields=["status", "error_message", "updated_at"])
            payload = analytics_report_response_payload(
                report,
                enabled=True,
                current_source_hash=analytics_input.source_hash,
                current_run_key=str(run_identity.get("run_key") or ""),
            )
            payload["error"] = "Analytics Intelligence analysis failed."
            return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if progressive_analytics_ollama_enabled(chain):
            _queue_analytics_intelligence_enhancement(report)
            report.refresh_from_db()

        return Response(
            analytics_report_response_payload(
                report,
                enabled=True,
                current_source_hash=analytics_input.source_hash,
                current_run_key=str(run_identity.get("run_key") or ""),
            ),
            status=status.HTTP_200_OK,
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

        job = _latest_completed_video_export_job(project)
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

        avatar_artifact_state = _avatar_artifact_state(project, sidecar)
        avatar_available = bool(avatar_artifact_state.get("available"))
        avatar_rel_path = str(avatar_artifact_state.get("rel_path") or "")
        if avatar_available and avatar_rel_path and _avatar_active_for_project(project):
            avatar_token = generate_media_token(
                job.id,
                "avatar",
                rel_path=avatar_rel_path,
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
            avatar_artifact_state=avatar_artifact_state,
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
        data["avatar_placement"] = data["avatar_overlay"].get("placement") or normalize_avatar_placement()
        data["avatar_active_for_lesson"] = _avatar_active_for_project(project)
        data["avatar_processing_status"] = playback.get("avatar_processing_status", "none")
        data["avatar_processing_message"] = playback.get("avatar_processing_message", "")
        data["avatar_visible"] = playback.get("avatar_visible", True)
        data["avatar_available"] = playback.get("avatar_available", False)
        data["avatar_updated_at"] = playback.get("avatar_updated_at")
        data["avatar_engine_selected"] = playback.get("avatar_engine_selected", "")
        data["normalized_engine"] = playback.get("normalized_engine", data["avatar_engine_selected"])
        data["final_avatar_engine_chain"] = playback.get("final_avatar_engine_chain", [])
        data["avatar_runtime_settings"] = playback.get("avatar_runtime_settings", project_avatar_runtime_settings(project))
        data["avatar_runtime_status"] = playback.get("avatar_runtime_status", _avatar_runtime_status_for_project(project))
        data["playback_status"] = playback.get("playback_status")
        data["mode_debug"] = playback.get("mode_debug")
        data["transcript_pages"] = _project_transcript_timeline(project, context={"request": request})
        data["like_count"] = project.likes.count()
        data["comment_count"] = project.comments.count()
        data["publisher_id"] = project.user_id
        data["publisher_username"] = project.user.username if project.user else ""
        data["publisher_display_name"] = _publisher_public_lesson_display_name(project.user) if project.user else ""
        data["publisher_logo_url"] = _publisher_public_logo_url(request, project.user)
        data["publisher_avatar_url"] = data["publisher_logo_url"]
        data["publisher_follower_count"] = (
            PublisherFollow.objects.filter(publisher=project.user).count()
            if project.user_id
            else 0
        )

        if request.user and request.user.is_authenticated:
            data["user_liked"] = project.likes.filter(user=request.user).exists()
            data["publisher_is_following"] = (
                PublisherFollow.objects.filter(follower=request.user, publisher=project.user).exists()
                if project.user_id
                else False
            )
            progress = project.progress_records.filter(user=request.user).first()
            data["user_progress"] = progress.progress_pct if progress else 0
        else:
            data["user_liked"] = False
            data["publisher_is_following"] = False
            data["user_progress"] = 0

        return Response(data)


# ---------------------------------------------------------------------------
# Student social features (authentication required)
# ---------------------------------------------------------------------------

class UserNotificationListView(APIView):
    """GET /api/v1/me/notifications/ - latest notifications for current user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))
        try:
            offset = int(request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)
        unread_only = str(request.query_params.get("unread_only", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        queryset = (
            Notification.objects.filter(recipient_user=request.user)
            .select_related("actor_user", "project", "project__user", "lesson_comment", "job")
            .order_by("-created_at", "-id")
        )
        if unread_only:
            queryset = queryset.filter(is_read=False)
        total_count = queryset.count()
        notifications = list(queryset[offset:offset + limit])
        next_offset = offset + len(notifications)
        has_more = next_offset < total_count
        return Response(
            {
                "results": NotificationSerializer(
                    notifications,
                    many=True,
                    context={"request": request},
                ).data,
                "count": total_count,
                "limit": limit,
                "offset": offset,
                "next_offset": next_offset if has_more else None,
                "has_more": has_more,
            }
        )


class UserNotificationUnreadCountView(APIView):
    """GET /api/v1/me/notifications/unread-count/."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        unread_count = Notification.objects.filter(recipient_user=request.user, is_read=False).count()
        return Response({"unread_count": unread_count})


class UserNotificationReadView(APIView):
    """POST /api/v1/me/notifications/<id>/read/."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, notification_id):
        notification = (
            Notification.objects.select_related("actor_user", "project", "project__user", "lesson_comment", "job")
            .filter(pk=notification_id, recipient_user=request.user)
            .first()
        )
        if notification is None:
            return Response({"error": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at", "updated_at"])
        return Response(NotificationSerializer(notification, context={"request": request}).data)


class UserNotificationMarkAllReadView(APIView):
    """POST /api/v1/me/notifications/mark-all-read/."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        updated = Notification.objects.filter(recipient_user=request.user, is_read=False).update(
            is_read=True,
            read_at=now,
            updated_at=now,
        )
        return Response({"updated": updated, "unread_count": 0})


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
            _queue_creator_analytics_intelligence_schedule(project.user_id, reason="lesson_like_deleted")
            return Response({"liked": False, "like_count": project.likes.count()})
        _queue_creator_analytics_intelligence_schedule(project.user_id, reason="lesson_like_created")
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
        _queue_creator_analytics_intelligence_schedule(project.user_id, reason="lesson_progress_updated")
        return Response({"progress_pct": pct})


def _public_learning_item_queryset(queryset):
    return (
        queryset.select_related("project", "project__user", "project__category")
        .filter(
            project__is_published=True,
            project__status="ready",
            project__moderation_status__in=APPROVED_MODERATION_STATUSES,
            project__jobs__status="done",
        )
        .distinct()
    )


def _learning_project_payload(project: Project, request) -> dict[str, Any]:
    data = dict(CatalogProjectSerializer(project, context={"request": request}).data)
    progress = project.progress_records.filter(user=request.user).first()
    data["user_progress"] = int(progress.progress_pct) if progress else 0
    data["user_liked"] = project.likes.filter(user=request.user).exists()
    return data


PUBLISHER_PROFILE_ROLES = frozenset({"publisher", "teacher"})


def _safe_user_profile(user):
    try:
        return getattr(user, "profile", None)
    except UserProfile.DoesNotExist:
        return None


def _publisher_role(user) -> str:
    profile = _safe_user_profile(user)
    role = str(getattr(profile, "role", "") or "").strip().lower()
    if role in PUBLISHER_PROFILE_ROLES:
        return role
    if _is_staff_user(user):
        return "publisher"
    return role or "student"


def _is_public_publisher_user(user) -> bool:
    if not user:
        return False
    profile = _safe_user_profile(user)
    return bool(
        _publisher_role(user) in PUBLISHER_PROFILE_ROLES
        and profile is not None
        and getattr(profile, "is_public_profile", False)
    )


def _publisher_display_name(user) -> str:
    profile = _safe_user_profile(user)
    custom_name = str(getattr(profile, "display_name", "") or "").strip()
    return custom_name or user.get_full_name() or user.username


def _publisher_public_lesson_display_name(user) -> str:
    profile = _safe_user_profile(user)
    if profile and getattr(profile, "is_public_profile", False):
        return _publisher_display_name(user)
    return user.get_full_name() or user.username


def _can_view_publisher_profile(request, publisher) -> bool:
    if _is_public_publisher_user(publisher):
        return True
    if _publisher_role(publisher) not in PUBLISHER_PROFILE_ROLES:
        return False
    viewer = getattr(request, "user", None)
    if not viewer or not viewer.is_authenticated:
        return False
    return _is_staff_user(viewer) or int(viewer.id) == int(publisher.id)


def _can_access_publisher_channel(request, publisher) -> bool:
    if _publisher_role(publisher) not in PUBLISHER_PROFILE_ROLES:
        return False
    if _can_view_publisher_profile(request, publisher):
        return True
    return _public_publisher_projects(publisher).exists()


def _publisher_public_logo_url(request, publisher) -> str:
    profile = _safe_user_profile(publisher)
    if not profile or not getattr(profile, "is_public_profile", False):
        return ""
    if not getattr(profile, "logo_image_processed", ""):
        return ""
    return _profile_asset_url_for_request(
        request,
        publisher.id,
        "logo",
        _profile_asset_version(profile),
    )


def _public_publisher_projects(publisher):
    return (
        Project.objects.filter(
            user=publisher,
            is_published=True,
            status="ready",
            moderation_status__in=APPROVED_MODERATION_STATUSES | frozenset({"not_scanned"}),
            jobs__status="done",
        )
        .exclude(moderation_status__in=["admin_rejected", "revision_required"])
        .select_related("user", "category")
        .prefetch_related("jobs", "likes", "comments")
        .distinct()
    )


def _public_playlist_items_queryset(queryset=None):
    base = queryset if queryset is not None else PlaylistItem.objects.all()
    return (
        base.filter(
            project__is_published=True,
            project__status="ready",
            project__moderation_status__in=APPROVED_MODERATION_STATUSES | frozenset({"not_scanned"}),
            project__jobs__status="done",
        )
        .exclude(project__moderation_status__in=["admin_rejected", "revision_required"])
        .select_related("project", "project__user", "project__category")
        .prefetch_related("project__jobs", "project__likes", "project__comments")
        .distinct()
        .order_by("order", "created_at")
    )


def _playlist_items_queryset(queryset=None):
    base = queryset if queryset is not None else PlaylistItem.objects.all()
    return (
        base.select_related("project", "project__user", "project__category")
        .prefetch_related("project__jobs", "project__likes", "project__comments")
        .order_by("order", "created_at")
    )


def _playlist_payload_for_public(playlist, request):
    playlist.visible_items = list(_public_playlist_items_queryset(PlaylistItem.objects.filter(playlist=playlist)))
    return PlaylistPublicSerializer(playlist, context={"request": request}).data


def _playlist_context_publisher_payload(user) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "display_name": _publisher_public_lesson_display_name(user),
    }


def _playlist_context_playlist_payload(playlist) -> dict[str, Any]:
    return {
        "id": playlist.id,
        "title": playlist.title,
        "publisher": _playlist_context_publisher_payload(playlist.user),
    }


def _playlist_context_item_payload(item, request, current_project_id: int) -> dict[str, Any]:
    return {
        "project": CatalogProjectSerializer(item.project, context={"request": request}).data,
        "order": item.order,
        "is_current": int(item.project_id) == int(current_project_id),
    }


def _publisher_context_projects(project: Project, *, include_private: bool):
    base = (
        Project.objects.filter(user=project.user)
        .exclude(pk=project.pk)
        .select_related("user", "category")
        .prefetch_related("jobs", "likes", "comments")
        .distinct()
    )
    if not include_private:
        base = _public_publisher_projects(project.user).exclude(pk=project.pk)
    else:
        base = base.filter(jobs__status="done").distinct()

    same_category = base.none()
    other_projects = base
    if project.category_id:
        same_category = base.filter(category_id=project.category_id)
        other_projects = base.exclude(category_id=project.category_id)

    same_category_projects = list(same_category.order_by("-created_at", "-id")[:8])
    if len(same_category_projects) >= 8:
        return same_category_projects
    remaining = 8 - len(same_category_projects)
    return same_category_projects + list(other_projects.order_by("-created_at", "-id")[:remaining])


class CatalogPlaylistContextView(APIView):
    """GET /api/v1/catalog/<project_id>/playlist-context/ - Watch page context."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, project_id):
        try:
            project = Project.objects.select_related("user", "category").prefetch_related("jobs").get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)

        lesson_is_public = _is_public_lesson(project)
        can_manage = _can_manage_project(getattr(request, "user", None), project)
        if not lesson_is_public and not can_manage:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not lesson_is_public and not _project_has_completed_render(project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not project.user_id:
            return Response({"mode": "publisher", "playlist": None, "items": []})

        if lesson_is_public:
            playlist_item = (
                PlaylistItem.objects.filter(
                    project=project,
                    playlist__is_public=True,
                    playlist__user=project.user,
                )
                .select_related("playlist", "playlist__user")
                .order_by("order", "-playlist__updated_at", "-playlist__created_at", "playlist_id")
                .first()
            )
            if playlist_item:
                playlist = playlist_item.playlist
                visible_items = list(_public_playlist_items_queryset(PlaylistItem.objects.filter(playlist=playlist)))
                if any(int(item.project_id) == int(project.id) for item in visible_items):
                    return Response({
                        "mode": "playlist",
                        "playlist": _playlist_context_playlist_payload(playlist),
                        "items": [
                            _playlist_context_item_payload(item, request, project.id)
                            for item in visible_items
                        ],
                    })

        fallback_projects = _publisher_context_projects(project, include_private=can_manage)
        return Response({
            "mode": "publisher",
            "playlist": None,
            "items": CatalogProjectSerializer(fallback_projects, many=True, context={"request": request}).data,
        })


def _publisher_profile_payload(publisher, request, *, latest_limit: int = 0) -> dict[str, Any]:
    profile = _safe_user_profile(publisher)
    public_lessons = _public_publisher_projects(publisher)
    details_visible = _can_view_publisher_profile(request, publisher)
    is_following = False
    if details_visible and request.user and request.user.is_authenticated:
        is_following = PublisherFollow.objects.filter(follower=request.user, publisher=publisher).exists()
    total_views = LessonProgress.objects.filter(project__in=public_lessons).count()
    total_likes = LessonLike.objects.filter(project__in=public_lessons).count()
    asset_version = _profile_asset_version(profile) if profile else ""
    banner_url = ""
    logo_url = ""
    if details_visible and profile and getattr(profile, "banner_image_processed", ""):
        banner_url = _profile_asset_url_for_request(request, publisher.id, "banner", asset_version)
    if details_visible and profile and getattr(profile, "logo_image_processed", ""):
        logo_url = _profile_asset_url_for_request(request, publisher.id, "logo", asset_version)
    payload = {
        "id": publisher.id,
        "username": publisher.username,
        "display_name": _publisher_display_name(publisher) if details_visible else _publisher_public_lesson_display_name(publisher),
        "bio": (getattr(profile, "bio", "") or "") if details_visible else "",
        "banner_url": banner_url,
        "logo_url": logo_url,
        "avatar_url": "",
        "website_url": (getattr(profile, "website_url", "") or "") if details_visible else "",
        "contact_email": (getattr(profile, "contact_email", "") or "") if details_visible else "",
        "social_links": (
            getattr(profile, "social_links", {})
            if details_visible and isinstance(getattr(profile, "social_links", {}), dict)
            else {}
        ),
        "is_public_profile": bool(getattr(profile, "is_public_profile", False)),
        "profile_private": not details_visible,
        "role": _publisher_role(publisher),
        "follower_count": PublisherFollow.objects.filter(publisher=publisher).count(),
        "lesson_count": public_lessons.count(),
        "is_following": is_following,
        "total_views": total_views,
        "total_likes": total_likes,
        "stats": {
            "total_views": total_views,
            "total_likes": total_likes,
        },
    }
    if latest_limit:
        payload["latest_lessons"] = CatalogProjectSerializer(
            public_lessons.order_by("-created_at")[:latest_limit],
            many=True,
            context={"request": request},
        ).data
    return payload


class PublisherFollowToggleView(APIView):
    """POST /api/v1/users/<user_id>/follow/ - toggle a publisher follow."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, user_id):
        try:
            publisher = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_public_publisher_user(publisher):
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        if int(request.user.id) == int(publisher.id):
            return Response({"error": "You cannot follow yourself."}, status=status.HTTP_400_BAD_REQUEST)

        follow, created = PublisherFollow.objects.get_or_create(follower=request.user, publisher=publisher)
        if created:
            is_following = True
        else:
            follow.delete()
            is_following = False
        return Response({
            "is_following": is_following,
            "follower_count": PublisherFollow.objects.filter(publisher=publisher).count(),
        })


class PublisherProfileView(APIView):
    """GET /api/v1/users/<user_id>/profile/ - public publisher profile."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, user_id):
        try:
            publisher = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_publisher_channel(request, publisher):
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_publisher_profile_payload(publisher, request, latest_limit=3))


class PublisherLessonsView(APIView):
    """GET /api/v1/users/<user_id>/lessons/ - public publisher lessons."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, user_id):
        try:
            publisher = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_publisher_channel(request, publisher):
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)

        can_view_private = bool(
            request.user
            and request.user.is_authenticated
            and (_is_staff_user(request.user) or int(request.user.id) == int(publisher.id))
        )
        if can_view_private:
            projects = (
                Project.objects.filter(user=publisher)
                .select_related("user", "category")
                .prefetch_related("jobs", "likes", "comments")
                .distinct()
            )
        else:
            projects = _public_publisher_projects(publisher)

        sort = str(request.query_params.get("sort") or "date").strip().lower()
        order = str(request.query_params.get("order") or "").strip().lower()
        if sort not in {"date", "name"}:
            sort = "date"
        if order not in {"asc", "desc"}:
            order = "asc" if sort == "name" else "desc"
        order_field = "title" if sort == "name" else "created_at"
        if order == "desc":
            order_field = f"-{order_field}"
        projects = projects.order_by(order_field, "id" if order == "asc" else "-id")
        return Response({
            "results": CatalogProjectSerializer(projects, many=True, context={"request": request}).data
        })


class PlaylistListCreateView(APIView):
    """GET/POST /api/v1/playlists/ - current publisher's Studio playlists."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can manage playlists."}, status=status.HTTP_403_FORBIDDEN)
        playlists = (
            Playlist.objects.filter(user=request.user)
            .select_related("user")
            .prefetch_related(Prefetch("items", queryset=_playlist_items_queryset()))
            .order_by("-updated_at", "-created_at")
        )
        return Response({"results": PlaylistSerializer(playlists, many=True, context={"request": request}).data})

    def post(self, request):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can create playlists."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PlaylistSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        playlist = serializer.save(user=request.user)
        return Response(PlaylistSerializer(playlist, context={"request": request}).data, status=status.HTTP_201_CREATED)


class PlaylistSaveToggleView(APIView):
    """POST /api/v1/playlists/<id>/save/ - toggle a saved public playlist."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, playlist_id):
        try:
            playlist = Playlist.objects.get(pk=playlist_id, is_public=True)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)

        saved, created = SavedPlaylist.objects.get_or_create(user=request.user, playlist=playlist)
        if created:
            is_saved = True
        else:
            saved.delete()
            is_saved = False

        return Response({
            "is_saved": is_saved,
            "save_count": SavedPlaylist.objects.filter(playlist=playlist).count(),
        })


class UserSavedPlaylistsView(APIView):
    """GET /api/v1/me/saved-playlists/ - current user's saved public playlists."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        saved_rows = list(
            SavedPlaylist.objects.filter(user=request.user, playlist__is_public=True)
            .select_related("playlist")
            .order_by("-created_at", "-id")
        )
        playlist_ids = [row.playlist_id for row in saved_rows]
        playlists = (
            Playlist.objects.filter(id__in=playlist_ids, is_public=True)
            .select_related("user")
            .prefetch_related(Prefetch("items", queryset=_public_playlist_items_queryset(), to_attr="visible_items"))
        )
        playlists_by_id = {playlist.id: playlist for playlist in playlists}
        results = []
        for saved_row in saved_rows:
            playlist = playlists_by_id.get(saved_row.playlist_id)
            if not playlist:
                continue
            row = PlaylistPublicSerializer(playlist, context={"request": request}).data
            row["saved_at"] = saved_row.created_at
            results.append(row)
        return Response({"results": results})


class PlaylistDetailView(APIView):
    """GET public playlist detail; PATCH/DELETE current owner's Studio playlist."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, playlist_id):
        try:
            playlist = Playlist.objects.select_related("user", "user__profile").get(pk=playlist_id)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)

        can_view_private = bool(
            request.user
            and request.user.is_authenticated
            and (_is_staff_user(request.user) or int(request.user.id) == int(playlist.user_id))
        )
        if not playlist.is_public and not can_view_private:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)
        if can_view_private:
            playlist = (
                Playlist.objects.filter(pk=playlist.pk)
                .select_related("user")
                .prefetch_related(Prefetch("items", queryset=_playlist_items_queryset()))
                .first()
            )
            return Response(PlaylistSerializer(playlist, context={"request": request}).data)
        return Response(_playlist_payload_for_public(playlist, request))

    def patch(self, request, playlist_id):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can update playlists."}, status=status.HTTP_403_FORBIDDEN)
        try:
            playlist = Playlist.objects.get(pk=playlist_id, user=request.user)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PlaylistSerializer(playlist, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        playlist = serializer.save()
        return Response(PlaylistSerializer(playlist, context={"request": request}).data)

    def delete(self, request, playlist_id):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can delete playlists."}, status=status.HTTP_403_FORBIDDEN)
        try:
            playlist = Playlist.objects.get(pk=playlist_id, user=request.user)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)
        playlist.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlaylistItemCreateView(APIView):
    """POST /api/v1/playlists/<id>/items/ - add an owned lesson to a playlist."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, playlist_id):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can update playlists."}, status=status.HTTP_403_FORBIDDEN)
        try:
            playlist = Playlist.objects.get(pk=playlist_id, user=request.user)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            project_id = int(request.data.get("project_id") or 0)
        except (TypeError, ValueError):
            project_id = 0
        if not project_id:
            return Response({"error": "project_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _is_staff_user(request.user) and int(project.user_id or 0) != int(request.user.id):
            return Response({"error": "You can only add your own lessons to playlists."}, status=status.HTTP_403_FORBIDDEN)

        existing = PlaylistItem.objects.filter(playlist=playlist, project=project).first()
        if existing:
            playlist = (
                Playlist.objects.filter(pk=playlist.pk)
                .select_related("user")
                .prefetch_related(Prefetch("items", queryset=_playlist_items_queryset()))
                .first()
            )
            return Response(PlaylistSerializer(playlist, context={"request": request}).data)

        max_order = PlaylistItem.objects.filter(playlist=playlist).aggregate(Max("order")).get("order__max")
        PlaylistItem.objects.create(playlist=playlist, project=project, order=(max_order or 0) + 1)
        playlist = (
            Playlist.objects.filter(pk=playlist.pk)
            .select_related("user")
            .prefetch_related(Prefetch("items", queryset=_playlist_items_queryset()))
            .first()
        )
        return Response(PlaylistSerializer(playlist, context={"request": request}).data, status=status.HTTP_201_CREATED)


class PlaylistItemDeleteView(APIView):
    """DELETE /api/v1/playlists/<id>/items/<project_id>/ - remove a lesson."""
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, playlist_id, project_id):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can update playlists."}, status=status.HTTP_403_FORBIDDEN)
        try:
            playlist = Playlist.objects.get(pk=playlist_id, user=request.user)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)
        PlaylistItem.objects.filter(playlist=playlist, project_id=project_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlaylistItemReorderView(APIView):
    """PATCH /api/v1/playlists/<id>/items/reorder/ - set simple numeric order."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, playlist_id):
        if not _is_verified_teacher(request.user):
            return Response({"error": "Only teacher or publisher accounts can update playlists."}, status=status.HTTP_403_FORBIDDEN)
        try:
            playlist = Playlist.objects.get(pk=playlist_id, user=request.user)
        except Playlist.DoesNotExist:
            return Response({"error": "Playlist not found."}, status=status.HTTP_404_NOT_FOUND)
        project_ids = request.data.get("project_ids")
        if not isinstance(project_ids, list):
            return Response({"error": "project_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            requested_ids = [int(project_id) for project_id in project_ids]
        except (TypeError, ValueError):
            return Response({"error": "project_ids must contain numeric lesson ids."}, status=status.HTTP_400_BAD_REQUEST)

        current_items = list(PlaylistItem.objects.filter(playlist=playlist).order_by("order", "created_at"))
        current_ids = [item.project_id for item in current_items]
        if set(requested_ids) != set(current_ids) or len(requested_ids) != len(current_ids):
            return Response({"error": "project_ids must include each playlist lesson exactly once."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            items_by_project = {item.project_id: item for item in current_items}
            for index, project_id in enumerate(requested_ids):
                item = items_by_project[project_id]
                if item.order != index:
                    item.order = index
                    item.save(update_fields=["order"])
        playlist = (
            Playlist.objects.filter(pk=playlist.pk)
            .select_related("user")
            .prefetch_related(Prefetch("items", queryset=_playlist_items_queryset()))
            .first()
        )
        return Response(PlaylistSerializer(playlist, context={"request": request}).data)


class PublisherPlaylistsView(APIView):
    """GET /api/v1/users/<user_id>/playlists/ - public channel playlists."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, user_id):
        try:
            publisher = User.objects.select_related("profile").get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_access_publisher_channel(request, publisher):
            return Response({"error": "Publisher not found."}, status=status.HTTP_404_NOT_FOUND)
        playlists = (
            Playlist.objects.filter(user=publisher, is_public=True)
            .select_related("user")
            .prefetch_related(Prefetch("items", queryset=_public_playlist_items_queryset(), to_attr="visible_items"))
            .order_by("-updated_at", "-created_at")
        )
        return Response({"results": PlaylistPublicSerializer(playlists, many=True, context={"request": request}).data})


class UserFollowingView(APIView):
    """GET /api/v1/me/following/ - current user's followed publishers."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        follows = (
            PublisherFollow.objects.filter(follower=request.user)
            .select_related("publisher", "publisher__profile")
            .order_by("-created_at")
        )
        results = []
        for follow in follows:
            publisher = follow.publisher
            if not _is_public_publisher_user(publisher):
                continue
            row = _publisher_profile_payload(publisher, request, latest_limit=3)
            row["followed_at"] = follow.created_at
            results.append(row)
        return Response({"results": results})


class UserHistoryView(APIView):
    """GET /api/v1/me/history/ — current user's watched public lessons."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        progress_rows = _public_learning_item_queryset(
            LessonProgress.objects.filter(user=request.user)
        ).order_by("-updated_at")
        results = [
            {
                "id": row.id,
                "project_id": row.project_id,
                "progress_pct": int(row.progress_pct or 0),
                "updated_at": row.updated_at,
                "last_watched_at": row.updated_at,
                "lesson": _learning_project_payload(row.project, request),
            }
            for row in progress_rows
        ]
        return Response({"results": results})


class UserLikedLessonsView(APIView):
    """GET /api/v1/me/liked-lessons/ — current user's liked public lessons."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        like_rows = _public_learning_item_queryset(
            LessonLike.objects.filter(user=request.user)
        ).order_by("-created_at")
        results = [
            {
                "id": row.id,
                "project_id": row.project_id,
                "liked_at": row.created_at,
                "lesson": _learning_project_payload(row.project, request),
            }
            for row in like_rows
        ]
        return Response({"results": results})


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
        try:
            from core.notifications import notify_lesson_commented

            notify_lesson_commented(comment)
        except Exception:
            logger.warning("Comment notification hook failed for comment=%s", comment.id, exc_info=True)
        _queue_creator_analytics_intelligence_schedule(project.user_id, reason="lesson_comment_created")
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
            req = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5.0) as resp:
                body = resp.read().decode("utf-8")
            result = json.loads(body)
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
            synth_req = Request(
                f"{service_url}/synthesize",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(synth_req, timeout=20.0) as resp:
                synth_body = resp.read().decode("utf-8")
            synth_result = json.loads(synth_body)
            if not isinstance(synth_result, dict):
                raise ValueError("invalid_synthesize_payload")
            audio_url = str(synth_result.get("audio_url") or "").strip()
            if not audio_url:
                raise ValueError("missing_audio_url")

            audio_req = Request(audio_url, headers={"Accept": "audio/mpeg"}, method="GET")
            with urlopen(audio_req, timeout=20.0) as audio_resp:
                audio_bytes = audio_resp.read(self.MAX_AUDIO_BYTES + 1)
                content_type = str(audio_resp.headers.get("Content-Type") or "audio/mpeg").split(";", 1)[0]
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
