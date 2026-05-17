"""
DRF serializers for AI_ACADEMY core models.
"""

from collections.abc import Mapping
from copy import deepcopy
import math
from pathlib import Path

from django.conf import settings
from rest_framework import serializers
from django.contrib.auth.models import User
from core.avatar_readiness import normalize_avatar_engine
from core.avatar_image_moderation import avatar_image_moderation_gate
from core.avatar_placement import (
    normalize_avatar_placement,
    placement_from_overlay_preference,
    project_avatar_placement,
)
from core.avatar_runtime_settings import project_avatar_runtime_settings
from core.models import (
    AvatarRenderJob,
    AvatarOverlayPreference,
    Category,
    Job,
    LessonSegment,
    LessonComment,
    Notification,
    Playlist,
    PlaylistItem,
    Project,
    SavedPlaylist,
    SiteHelpContent,
    Slide,
    TranscriptPage,
    TranslatedSubtitleTrack,
    UserProfile,
    VoiceProfile,
    default_project_tts_settings,
)


def _normalize_rel_storage_path(raw_path: str) -> str:
    rel_path = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel_path:
        return ""
    if rel_path == ".." or rel_path.startswith("../") or "/../" in rel_path:
        return ""
    return rel_path


def _project_cover_rel_path(project: Project) -> str:
    return _normalize_rel_storage_path(
        getattr(project, "cover_image_processed", "")
        or getattr(project, "cover_image_original", "")
    )


def _project_cover_url(project: Project, context: dict | None) -> str:
    if not _project_cover_rel_path(project):
        return ""

    url_path = f"/api/v1/projects/{project.id}/cover/"
    request = (context or {}).get("request")
    if request is not None:
        try:
            return request.build_absolute_uri(url_path)
        except Exception:
            pass

    api_public_base_url = str(getattr(settings, "API_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if api_public_base_url:
        return f"{api_public_base_url}{url_path}"
    return url_path


def _request_can_view_project_draft(project: Project, context: dict | None) -> bool:
    request = (context or {}).get("request")
    user = getattr(request, "user", None) if request is not None else None
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if bool(getattr(user, "is_staff", False)) or bool(getattr(user, "is_superuser", False)):
        return True
    return bool(getattr(project, "user_id", None) and int(project.user_id) == int(user.id))


def _project_draft_cover_rel_path(project: Project, context: dict | None) -> str:
    if not _request_can_view_project_draft(project, context):
        return ""
    draft_data = getattr(project, "draft_data", None)
    if not isinstance(draft_data, Mapping):
        return ""
    metadata = draft_data.get("metadata")
    project_draft = draft_data.get("project")
    if not (isinstance(metadata, Mapping) and metadata.get("dirty") and isinstance(project_draft, Mapping)):
        return ""
    draft_rel = _normalize_rel_storage_path(
        str(project_draft.get("cover_image_processed") or project_draft.get("cover_image_original") or "")
    )
    return draft_rel if draft_rel and draft_rel != _project_cover_rel_path(project) else ""


def _project_draft_cover_url(project: Project, context: dict | None) -> str:
    if not _project_draft_cover_rel_path(project, context):
        return ""

    url_path = f"/api/v1/projects/{project.id}/cover/?draft=1"
    request = (context or {}).get("request")
    if request is not None:
        try:
            return request.build_absolute_uri(url_path)
        except Exception:
            pass

    api_public_base_url = str(getattr(settings, "API_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if api_public_base_url:
        return f"{api_public_base_url}{url_path}"
    return url_path


def _project_avatar_artifact_exists(project: Project) -> bool:
    rel_path = _normalize_rel_storage_path(getattr(project, "avatar_output_path", ""))
    if not rel_path:
        return False
    try:
        full_path = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / rel_path
        return full_path.exists() and full_path.is_file()
    except Exception:
        return False


SCENE_BACKGROUND_MODES = {"original", "whiteboard", "custom", "source_background"}
SCENE_BACKGROUND_FITS = {"contain", "cover", "stretch"}
SOURCE_BACKGROUND_SUPPORTED_TYPES = {"pptx"}


def _transcript_background_url(page: TranscriptPage, kind: str, context: dict | None) -> str:
    if kind not in {"original", "custom", "source", "source_background"}:
        return ""
    url_kind = "source" if kind in {"source", "source_background"} else kind
    url_path = f"/api/v1/projects/{page.project_id}/transcript-pages/{page.id}/background/{url_kind}/"
    request = (context or {}).get("request")
    if request is not None:
        try:
            return request.build_absolute_uri(url_path)
        except Exception:
            pass

    api_public_base_url = str(getattr(settings, "API_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if api_public_base_url:
        return f"{api_public_base_url}{url_path}"
    return url_path


def _scene_path(scene: Mapping, key: str) -> str:
    return _normalize_rel_storage_path(str(scene.get(key) or ""))


def _scene_source_type(page: TranscriptPage, scene: Mapping) -> str:
    raw_source_type = str(scene.get("source_type") or "").strip().lower().lstrip(".")
    if raw_source_type:
        return raw_source_type
    try:
        storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
        upload_dir = storage_root / "uploads" / str(page.project_id)
        if upload_dir.exists():
            lesson_files = sorted(upload_dir.glob("lesson.*"))
            if lesson_files:
                return lesson_files[0].suffix.lower().lstrip(".")
    except Exception:
        pass
    if _scene_path(scene, "source_background_path"):
        return "pptx"
    return ""


def transcript_page_editor_document_for_response(page: TranscriptPage, context: dict | None = None) -> dict:
    """Return editor_document with publisher-safe scene URLs instead of storage paths."""
    raw_document = getattr(page, "editor_document", None)
    document = deepcopy(raw_document) if isinstance(raw_document, Mapping) else {}
    raw_scene = document.get("scene")
    scene = deepcopy(raw_scene) if isinstance(raw_scene, Mapping) else {}

    mode = str(scene.get("background_mode") or "").strip().lower()
    if mode not in SCENE_BACKGROUND_MODES:
        mode = "whiteboard" if bool(getattr(page, "whiteboard_mode", False)) else "original"

    fit = str(scene.get("background_fit") or "contain").strip().lower()
    if fit not in SCENE_BACKGROUND_FITS:
        fit = "contain"

    try:
        text_scale = float(scene.get("text_scale", 1.0))
    except (TypeError, ValueError):
        text_scale = 1.0
    text_scale = max(0.75, min(text_scale, 2.0))

    original_path = _scene_path(scene, "original_background_path")
    custom_path = _scene_path(scene, "custom_background_path")
    source_background_path = _scene_path(scene, "source_background_path")
    source_type = _scene_source_type(page, scene)
    source_background_available = bool(source_type in SOURCE_BACKGROUND_SUPPORTED_TYPES and source_background_path)
    source_background_warnings = scene.get("source_background_warnings")
    if not isinstance(source_background_warnings, list):
        source_background_warnings = []
    safe_scene = {
        str(key): deepcopy(value)
        for key, value in scene.items()
        if str(key)
        not in {
            "original_background_path",
            "custom_background_path",
            "source_background_path",
            "original_background_url",
            "custom_background_url",
            "source_background_url",
            "source_background_details",
        }
        and not str(key).endswith("_path")
    }
    safe_scene.update(
        {
            "background_mode": mode,
            "background_fit": fit,
            "text_scale": text_scale,
            "original_background_url": _transcript_background_url(page, "original", context) if original_path else "",
            "custom_background_url": _transcript_background_url(page, "custom", context) if custom_path else "",
            "source_background_url": _transcript_background_url(page, "source", context) if source_background_available else "",
            "has_original_background": bool(original_path),
            "has_custom_background": bool(custom_path),
            "has_source_background": source_background_available,
            "source_background_generated": source_background_available,
            "source_background_available": source_background_available,
            "source_type": source_type,
            "source_background_warnings": [
                str(warning).strip() for warning in source_background_warnings if str(warning or "").strip()
            ],
        }
    )
    document["scene"] = safe_scene
    return document


PROJECT_TTS_PROVIDER_PREFERENCES = {"auto", "xtts_v2", "gtts"}
PROJECT_TTS_NORMALIZATION_MODES = {"loose", "strict"}
PROJECT_TTS_UNKNOWN_WORD_STRATEGIES = {"keep", "phonetic"}
PROJECT_TTS_OVERRIDE_CATEGORIES = {"technical", "abbreviation", "mixed_word"}
PROJECT_TTS_SETTING_KEYS = {
    "provider_preference",
    "normalization_enabled",
    "normalization_mode",
    "unknown_word_strategy",
    "overrides",
    "speech_speed",
    "volume_gain_db",
    "pause_seconds",
}
PROJECT_TTS_MAX_OVERRIDE_ENTRIES = 200
PROJECT_TTS_MAX_OVERRIDE_TERM_CHARS = 120
PROJECT_TTS_MAX_OVERRIDE_REPLACEMENT_CHARS = 200


def _fresh_project_tts_settings() -> dict:
    return deepcopy(default_project_tts_settings())


def _contains_control_char(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _raise_tts_settings_error(field: str, message: str) -> None:
    raise serializers.ValidationError({field: message})


def _clean_enum(value, *, field: str, allowed: set[str], strict: bool):
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in allowed:
            return cleaned
    if strict:
        _raise_tts_settings_error(field, f"must be one of: {', '.join(sorted(allowed))}")
    return None


def _clean_bool(value, *, field: str, strict: bool):
    if isinstance(value, bool):
        return value
    if strict:
        _raise_tts_settings_error(field, "must be a boolean")
    return None


def _clean_number(
    value,
    *,
    field: str,
    minimum: float,
    maximum: float,
    allow_null: bool = False,
    strict: bool,
):
    if value is None and allow_null:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if strict:
            _raise_tts_settings_error(field, f"must be a number between {minimum:g} and {maximum:g}")
        return None
    cleaned = float(value)
    if not math.isfinite(cleaned) or cleaned < minimum or cleaned > maximum:
        if strict:
            _raise_tts_settings_error(field, f"must be between {minimum:g} and {maximum:g}")
        return None
    return int(cleaned) if cleaned.is_integer() and isinstance(value, int) else cleaned


def _clean_override_map(value, *, category: str, strict: bool):
    if not isinstance(value, Mapping):
        if strict:
            _raise_tts_settings_error(f"overrides.{category}", "must be an object")
        return None

    cleaned: dict[str, str] = {}
    for raw_term, raw_replacement in value.items():
        if not isinstance(raw_term, str):
            if strict:
                _raise_tts_settings_error(f"overrides.{category}", "override terms must be strings")
            continue
        if not isinstance(raw_replacement, str):
            if strict:
                _raise_tts_settings_error(f"overrides.{category}.{raw_term}", "replacement must be a string")
            continue
        term = raw_term.strip()
        replacement = raw_replacement.strip()
        if not term or not replacement:
            if strict:
                _raise_tts_settings_error(f"overrides.{category}", "override terms and replacements cannot be empty")
            continue
        if _contains_control_char(term) or _contains_control_char(replacement):
            if strict:
                _raise_tts_settings_error(f"overrides.{category}.{term}", "control characters are not allowed")
            continue
        if len(term) > PROJECT_TTS_MAX_OVERRIDE_TERM_CHARS:
            if strict:
                _raise_tts_settings_error(
                    f"overrides.{category}.{term[:20]}",
                    f"term must be {PROJECT_TTS_MAX_OVERRIDE_TERM_CHARS} characters or less",
                )
            continue
        if len(replacement) > PROJECT_TTS_MAX_OVERRIDE_REPLACEMENT_CHARS:
            if strict:
                _raise_tts_settings_error(
                    f"overrides.{category}.{term}",
                    f"replacement must be {PROJECT_TTS_MAX_OVERRIDE_REPLACEMENT_CHARS} characters or less",
                )
            continue
        cleaned[term] = replacement
    return cleaned


def _apply_project_tts_settings_values(result: dict, raw: Mapping, *, strict: bool) -> dict:
    unknown_keys = set(raw.keys()) - PROJECT_TTS_SETTING_KEYS
    if strict and unknown_keys:
        _raise_tts_settings_error("unknown_keys", f"unsupported keys: {', '.join(sorted(unknown_keys))}")

    if "provider_preference" in raw:
        value = _clean_enum(
            raw.get("provider_preference"),
            field="provider_preference",
            allowed=PROJECT_TTS_PROVIDER_PREFERENCES,
            strict=strict,
        )
        if value is not None:
            result["provider_preference"] = value

    if "normalization_enabled" in raw:
        value = _clean_bool(raw.get("normalization_enabled"), field="normalization_enabled", strict=strict)
        if value is not None:
            result["normalization_enabled"] = value

    if "normalization_mode" in raw:
        value = _clean_enum(
            raw.get("normalization_mode"),
            field="normalization_mode",
            allowed=PROJECT_TTS_NORMALIZATION_MODES,
            strict=strict,
        )
        if value is not None:
            result["normalization_mode"] = value

    if "unknown_word_strategy" in raw:
        value = _clean_enum(
            raw.get("unknown_word_strategy"),
            field="unknown_word_strategy",
            allowed=PROJECT_TTS_UNKNOWN_WORD_STRATEGIES,
            strict=strict,
        )
        if value is not None:
            result["unknown_word_strategy"] = value

    if "speech_speed" in raw:
        value = _clean_number(raw.get("speech_speed"), field="speech_speed", minimum=0.5, maximum=1.5, strict=strict)
        if value is not None:
            result["speech_speed"] = value

    if "volume_gain_db" in raw:
        value = _clean_number(raw.get("volume_gain_db"), field="volume_gain_db", minimum=-12, maximum=12, strict=strict)
        if value is not None:
            result["volume_gain_db"] = value

    if "pause_seconds" in raw:
        value = _clean_number(
            raw.get("pause_seconds"),
            field="pause_seconds",
            minimum=0,
            maximum=10,
            allow_null=True,
            strict=strict,
        )
        if value is not None or raw.get("pause_seconds") is None:
            result["pause_seconds"] = value

    if "overrides" in raw:
        raw_overrides = raw.get("overrides")
        if not isinstance(raw_overrides, Mapping):
            if strict:
                _raise_tts_settings_error("overrides", "must be an object")
        else:
            unknown_categories = set(raw_overrides.keys()) - PROJECT_TTS_OVERRIDE_CATEGORIES
            if strict and unknown_categories:
                _raise_tts_settings_error(
                    "overrides",
                    f"unsupported categories: {', '.join(sorted(unknown_categories))}",
                )
            for category in PROJECT_TTS_OVERRIDE_CATEGORIES:
                if category not in raw_overrides:
                    continue
                cleaned = _clean_override_map(raw_overrides.get(category), category=category, strict=strict)
                if cleaned is not None:
                    result["overrides"][category] = cleaned

    if strict:
        total_overrides = sum(len(result["overrides"].get(category, {})) for category in PROJECT_TTS_OVERRIDE_CATEGORIES)
        if total_overrides > PROJECT_TTS_MAX_OVERRIDE_ENTRIES:
            _raise_tts_settings_error(
                "overrides",
                f"must contain {PROJECT_TTS_MAX_OVERRIDE_ENTRIES} entries or fewer",
            )

    return result


def canonical_project_tts_settings(value=None) -> dict:
    result = _fresh_project_tts_settings()
    if isinstance(value, Mapping):
        _apply_project_tts_settings_values(result, value, strict=False)
    return result


def merge_project_tts_settings_patch(current, patch) -> dict:
    if not isinstance(patch, Mapping):
        _raise_tts_settings_error("tts_settings", "must be an object")
    result = canonical_project_tts_settings(current)
    return _apply_project_tts_settings_values(result, patch, strict=True)


class VoiceProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceProfile
        fields = ["id", "provider", "voice_id", "language", "speed", "pitch", "created_at"]
        read_only_fields = ["created_at"]


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "id",
            "role",
            "bio",
            "avatar_image_original",
            "avatar_image_processed",
            "avatar_video_original",
            "avatar_video_processed",
            "avatar_reference_type",
            "avatar_image_status",
            "avatar_model_version",
            "avatar_enabled",
            "avatar_last_rendered_at",
            "avatar_consent_confirmed",
            "avatar_preview_video",
            "avatar_overlay_default_position",
            "avatar_overlay_size",
            "avatar_overlay_visible",
            "avatar_motion_preset",
            "avatar_lipsync_engine",
            "avatar_quality_preset",
            "avatar_engine_primary",
            "avatar_engine_fallback",
            "avatar_last_preview_status",
            "avatar_last_preview_job_id",
            "avatar_last_preview_path",
            "avatar_preview_error",
            "avatar_version_hash",
            "avatar_source_valid",
            "avatar_source_validation_error",
            "avatar_source_hash",
            "avatar_source_image_hash",
            "avatar_source_video_hash",
            "avatar_source_reference_type",
            "avatar_preview_source_hash",
            "avatar_preview_stale",
            "avatar_moderation_status",
            "avatar_moderation_summary",
            "avatar_last_moderation_run_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class SiteHelpContentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteHelpContent
        fields = [
            "title",
            "slug",
            "body",
            "contact_email",
            "contact_phone",
            "company_name",
            "company_address",
            "support_url",
            "updated_at",
        ]
        read_only_fields = fields


class CurrentUserProfileSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, allow_blank=True, required=False, trim_whitespace=True)
    last_name = serializers.CharField(max_length=150, allow_blank=True, required=False, trim_whitespace=True)
    bio = serializers.CharField(allow_blank=True, required=False, trim_whitespace=True)

    def to_representation(self, user):
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "student"})
        first_name = user.first_name or ""
        last_name = user.last_name or ""
        display_name = user.get_full_name() or user.username
        return {
            "id": user.id,
            "username": user.username,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": display_name,
            "bio": profile.bio or "",
            "role": profile.role,
        }

    def update(self, user, validated_data):
        user_update_fields = []
        if "first_name" in validated_data:
            user.first_name = validated_data["first_name"]
            user_update_fields.append("first_name")
        if "last_name" in validated_data:
            user.last_name = validated_data["last_name"]
            user_update_fields.append("last_name")
        if user_update_fields:
            user.save(update_fields=user_update_fields)

        if "bio" in validated_data:
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "student"})
            profile.bio = validated_data["bio"]
            profile.save(update_fields=["bio", "updated_at"])
        return user


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    voice_profile = VoiceProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "profile", "voice_profile", "is_staff", "is_superuser"]


class SlideSerializer(serializers.ModelSerializer):
    class Meta:
        model = Slide
        fields = [
            "id", "project", "order", "title", "narration_text",
            "audio_file", "image_file", "duration_seconds", "created_at",
        ]
        read_only_fields = ["created_at"]


class JobSerializer(serializers.ModelSerializer):
    """Job status/result. Includes project_id so the frontend can poll correctly."""
    project_id = serializers.IntegerField(source="project.id", read_only=True, allow_null=True)

    class Meta:
        model = Job
        fields = [
            "id", "project_id", "status", "progress",
            "result_url", "srt_url", "error_message",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class ProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["title"]


class ProjectSerializer(serializers.ModelSerializer):
    """Read-only project with embedded latest job info."""
    user_name = serializers.CharField(source="user.username", read_only=True, default="")
    category_id = serializers.IntegerField(source="category.id", read_only=True, allow_null=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default="")
    category_slug = serializers.CharField(source="category.slug", read_only=True, default="")
    cover_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    draft_cover_url = serializers.SerializerMethodField()
    draft_thumbnail_url = serializers.SerializerMethodField()
    latest_job = serializers.SerializerMethodField()
    avatar_active = serializers.SerializerMethodField()
    avatar_available = serializers.SerializerMethodField()
    avatar_engine_selected = serializers.SerializerMethodField()
    final_avatar_engine_chain = serializers.SerializerMethodField()
    avatar_placement = serializers.SerializerMethodField()
    avatar_runtime_settings = serializers.SerializerMethodField()
    avatar_runtime_status = serializers.SerializerMethodField()
    tts_settings = serializers.SerializerMethodField()
    has_draft = serializers.SerializerMethodField()
    draft_metadata = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "user", "user_name", "title", "description",
            "cover_url", "thumbnail_url", "draft_cover_url", "draft_thumbnail_url", "tts_settings",
            "status", "moderation_status", "moderation_summary", "last_moderation_run_id",
            "is_published", "avatar_enabled_override", "avatar_active", "avatar_processing_status",
            "avatar_processing_message", "avatar_visible", "avatar_available", "avatar_last_job_id",
            "avatar_updated_at", "avatar_engine_selected", "final_avatar_engine_chain", "avatar_placement",
            "avatar_runtime_settings", "avatar_runtime_status", "category_id", "category_name",
            "category_slug", "has_draft", "draft_metadata", "created_at", "updated_at", "latest_job",
        ]
        read_only_fields = [
            "id", "user", "user_name", "description",
            "cover_url", "thumbnail_url", "draft_cover_url", "draft_thumbnail_url", "tts_settings", "status",
            "moderation_status", "moderation_summary", "last_moderation_run_id",
            "avatar_processing_status", "avatar_processing_message", "avatar_available",
            "avatar_last_job_id", "avatar_updated_at", "avatar_engine_selected", "final_avatar_engine_chain", "avatar_placement",
            "avatar_runtime_settings", "avatar_runtime_status",
            "category_id", "category_name", "category_slug", "has_draft", "draft_metadata",
            "created_at", "updated_at", "latest_job",
        ]

    def get_latest_job(self, obj):
        job = obj.jobs.filter(job_type="video_export").order_by("-created_at", "-id").first()
        if job is None:
            return None
        return JobSerializer(job).data

    def get_avatar_active(self, obj):
        profile = getattr(obj.user, "profile", None) if obj.user else None
        moderation_gate = avatar_image_moderation_gate(profile) if profile is not None else {"blocked": False}
        profile_enabled = bool(
            profile
            and profile.avatar_enabled
            and profile.avatar_consent_confirmed
            and not bool(moderation_gate.get("blocked"))
            and profile.avatar_image_processed
        )
        if obj.avatar_enabled_override is None:
            return profile_enabled
        return bool(obj.avatar_enabled_override and profile_enabled)

    def get_avatar_available(self, obj):
        return bool(
            str(getattr(obj, "avatar_processing_status", "") or "") == "ready"
            and _project_avatar_artifact_exists(obj)
        )

    def get_avatar_engine_selected(self, obj):
        latest = obj.avatar_render_jobs.exclude(render_status="pending").order_by("-created_at").first()
        if latest is not None:
            metadata = latest.metadata if isinstance(latest.metadata, Mapping) else {}
            selected = metadata.get("avatar_engine_selected") or metadata.get("normalized_engine") or latest.engine_used
            if selected and str(selected) != "none":
                return str(selected)
        profile = getattr(obj.user, "profile", None) if obj.user else None
        if profile is None:
            return ""
        return normalize_avatar_engine(profile.avatar_lipsync_engine or profile.avatar_engine_primary)

    def get_final_avatar_engine_chain(self, obj):
        latest = obj.avatar_render_jobs.exclude(render_status="pending").order_by("-created_at").first()
        if latest is None:
            return []
        metadata = latest.metadata if isinstance(latest.metadata, Mapping) else {}
        chain = metadata.get("final_avatar_engine_chain") or metadata.get("fallback_chain_used") or latest.fallback_chain_used
        return list(chain or [])

    def get_avatar_placement(self, obj):
        return project_avatar_placement(obj)

    def get_avatar_runtime_settings(self, obj):
        return project_avatar_runtime_settings(obj)

    def get_avatar_runtime_status(self, obj):
        latest = obj.avatar_render_jobs.exclude(render_status="pending").order_by("-created_at").first()
        metadata = latest.metadata if latest is not None and isinstance(latest.metadata, Mapping) else {}
        source_kind = str(metadata.get("musetalk_source_kind") or "")
        selected = str(metadata.get("avatar_engine_selected") or metadata.get("normalized_engine") or getattr(latest, "engine_used", "") or "")
        static_fallback = bool(metadata.get("liveportrait_fallback_used")) or source_kind in {"static_fallback", "static_source"}
        musetalk_only = selected == "musetalk_only_fast" or source_kind == "static_source"
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
            "musetalk_only_used": musetalk_only,
            "musetalk_source_kind": source_kind,
            "restoration_failed": bool(metadata.get("restoration_failed")),
            "warning": warning,
        }

    def get_tts_settings(self, obj):
        draft_data = getattr(obj, "draft_data", None)
        if isinstance(draft_data, Mapping):
            project_draft = draft_data.get("project")
            metadata = draft_data.get("metadata")
            if (
                isinstance(project_draft, Mapping)
                and isinstance(metadata, Mapping)
                and metadata.get("dirty")
                and isinstance(project_draft.get("tts_settings"), Mapping)
            ):
                return canonical_project_tts_settings(project_draft.get("tts_settings"))
        return canonical_project_tts_settings(getattr(obj, "tts_settings", None))

    def get_has_draft(self, obj):
        metadata = getattr(obj, "draft_data", {}).get("metadata") if isinstance(getattr(obj, "draft_data", None), Mapping) else {}
        return bool(isinstance(metadata, Mapping) and metadata.get("dirty"))

    def get_draft_metadata(self, obj):
        metadata = getattr(obj, "draft_data", {}).get("metadata") if isinstance(getattr(obj, "draft_data", None), Mapping) else {}
        return dict(metadata) if isinstance(metadata, Mapping) and metadata.get("dirty") else {}

    def get_cover_url(self, obj):
        return _project_cover_url(obj, self.context)

    def get_thumbnail_url(self, obj):
        return _project_cover_url(obj, self.context)

    def get_draft_cover_url(self, obj):
        return _project_draft_cover_url(obj, self.context)

    def get_draft_thumbnail_url(self, obj):
        return _project_draft_cover_url(obj, self.context)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "description", "created_at"]
        read_only_fields = ["id", "slug", "created_at"]


class CatalogProjectSerializer(serializers.ModelSerializer):
    """Public-facing lesson metadata. Never includes raw storage paths."""

    category_name = serializers.CharField(source="category.name", read_only=True, default="")
    category_slug = serializers.CharField(source="category.slug", read_only=True, default="")
    teacher_name = serializers.SerializerMethodField()
    teacher_id = serializers.SerializerMethodField()
    teacher_username = serializers.SerializerMethodField()
    like_count = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()
    follower_count = serializers.SerializerMethodField()
    is_following_publisher = serializers.SerializerMethodField()
    has_video = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "title", "description",
            "category_name", "category_slug",
            "teacher_id", "teacher_name", "teacher_username",
            "like_count", "comment_count", "follower_count", "is_following_publisher",
            "has_video",
            "cover_url", "thumbnail_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_teacher_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return ""

    def get_teacher_id(self, obj):
        return obj.user_id

    def get_teacher_username(self, obj):
        return obj.user.username if obj.user else ""

    def get_like_count(self, obj):
        return obj.likes.count()

    def get_comment_count(self, obj):
        return obj.comments.count()

    def get_follower_count(self, obj):
        if not obj.user_id:
            return 0
        return obj.user.publisher_followers.count()

    def get_is_following_publisher(self, obj):
        request = self.context.get("request") if hasattr(self, "context") else None
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not obj.user_id:
            return False
        return obj.user.publisher_followers.filter(follower=user).exists()

    def get_has_video(self, obj):
        return obj.jobs.filter(job_type="video_export", status="done").exists()

    def get_cover_url(self, obj):
        return _project_cover_url(obj, self.context)

    def get_thumbnail_url(self, obj):
        return _project_cover_url(obj, self.context)


class PlaylistItemSerializer(serializers.ModelSerializer):
    project = ProjectSerializer(read_only=True)
    project_id = serializers.IntegerField(source="project.id", read_only=True)

    class Meta:
        model = PlaylistItem
        fields = ["id", "project_id", "project", "order", "created_at"]
        read_only_fields = fields


class PlaylistSerializer(serializers.ModelSerializer):
    items = PlaylistItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    save_count = serializers.SerializerMethodField()

    class Meta:
        model = Playlist
        fields = [
            "id",
            "user",
            "title",
            "description",
            "is_public",
            "item_count",
            "is_saved",
            "save_count",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "item_count", "is_saved", "save_count", "items", "created_at", "updated_at"]

    def get_item_count(self, obj):
        return obj.items.count()

    def get_is_saved(self, obj):
        request = self.context.get("request") if hasattr(self, "context") else None
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return SavedPlaylist.objects.filter(user=user, playlist=obj).exists()

    def get_save_count(self, obj):
        return obj.saved_by.count()


class PlaylistPublicSerializer(serializers.ModelSerializer):
    publisher_id = serializers.IntegerField(source="user.id", read_only=True)
    publisher_name = serializers.SerializerMethodField()
    publisher_username = serializers.CharField(source="user.username", read_only=True, default="")
    item_count = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    save_count = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()

    class Meta:
        model = Playlist
        fields = [
            "id",
            "title",
            "description",
            "is_public",
            "publisher_id",
            "publisher_name",
            "publisher_username",
            "item_count",
            "cover_url",
            "is_saved",
            "save_count",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _items(self, obj):
        if hasattr(obj, "visible_items"):
            return list(obj.visible_items)
        return list(obj.items.select_related("project", "project__user", "project__category").all())

    def get_publisher_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return ""

    def get_item_count(self, obj):
        return len(self._items(obj))

    def get_cover_url(self, obj):
        first_item = next((item for item in self._items(obj) if item.project_id), None)
        if not first_item:
            return ""
        return _project_cover_url(first_item.project, self.context)

    def get_is_saved(self, obj):
        request = self.context.get("request") if hasattr(self, "context") else None
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return SavedPlaylist.objects.filter(user=user, playlist=obj).exists()

    def get_save_count(self, obj):
        return obj.saved_by.count()

    def get_items(self, obj):
        return [
            {
                "id": item.id,
                "project_id": item.project_id,
                "order": item.order,
                "created_at": item.created_at,
                "project": CatalogProjectSerializer(item.project, context=self.context).data,
            }
            for item in self._items(obj)
        ]


class LessonCommentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = LessonComment
        fields = ["id", "username", "text", "created_at"]
        read_only_fields = ["id", "username", "created_at"]


NOTIFICATION_SAFE_METADATA_KEYS = {
    "project_id",
    "lesson_id",
    "comment_id",
    "job_id",
    "avatar_job_id",
    "base_job_id",
    "status",
    "event",
    "is_published",
}
NOTIFICATION_PUBLIC_MODERATION_STATUSES = {"approved", "admin_approved", "not_scanned"}


def _notification_safe_metadata(metadata) -> dict:
    if not isinstance(metadata, Mapping):
        return {}
    safe = {}
    for raw_key, value in metadata.items():
        key = str(raw_key or "").strip()
        if key not in NOTIFICATION_SAFE_METADATA_KEYS:
            continue
        if isinstance(value, bool) or value is None:
            safe[key] = value
        elif isinstance(value, int):
            safe[key] = value
        elif isinstance(value, str):
            compact = value.strip()[:120]
            if "/" in compact or "\\" in compact or "storage" in compact.lower():
                continue
            safe[key] = compact
    return safe


def _notification_project_public(project: Project | None) -> bool:
    if project is None:
        return False
    if not bool(getattr(project, "is_published", False)):
        return False
    if str(getattr(project, "status", "") or "") != "ready":
        return False
    if str(getattr(project, "moderation_status", "") or "") not in NOTIFICATION_PUBLIC_MODERATION_STATUSES:
        return False
    return project.jobs.filter(job_type="video_export", status="done").exists()


class NotificationSerializer(serializers.ModelSerializer):
    actor_display_name = serializers.SerializerMethodField()
    action_url = serializers.SerializerMethodField()
    project = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "event_type",
            "title",
            "body",
            "action_url",
            "metadata",
            "is_read",
            "read_at",
            "created_at",
            "actor_display_name",
            "project",
        ]
        read_only_fields = fields

    def get_actor_display_name(self, obj):
        actor = getattr(obj, "actor_user", None)
        if actor is None:
            return ""
        return actor.get_full_name() or actor.username

    def _can_expose_project(self, obj) -> bool:
        project = getattr(obj, "project", None)
        if project is None:
            return True

        request = self.context.get("request") if hasattr(self, "context") else None
        user = getattr(request, "user", None)
        owns_project = bool(user and getattr(user, "is_authenticated", False) and project.user_id == user.id)
        staff_access = bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))
        public_new_lesson = (
            obj.event_type == Notification.EventType.STUDENT_FOLLOWED_PUBLISHER_NEW_LESSON
            and _notification_project_public(project)
        )
        return bool(owns_project or staff_access or public_new_lesson)

    def get_action_url(self, obj):
        if not self._can_expose_project(obj):
            return ""
        return str(getattr(obj, "action_url", "") or "")

    def get_project(self, obj):
        project = getattr(obj, "project", None)
        if project is None or not self._can_expose_project(obj):
            return None
        return {
            "id": project.id,
            "title": project.title,
        }

    def get_metadata(self, obj):
        metadata = _notification_safe_metadata(getattr(obj, "metadata", None))
        if not self._can_expose_project(obj):
            metadata.pop("project_id", None)
            metadata.pop("lesson_id", None)
        return metadata


class TranscriptPageSerializer(serializers.ModelSerializer):
    editor_document = serializers.SerializerMethodField()

    class Meta:
        model = TranscriptPage
        fields = [
            "id",
            "project",
            "order",
            "source_slide_index",
            "split_index",
            "page_key",
            "original_text",
            "narration_text",
            "rich_text_html",
            "editor_document",
            "subtitle_chunks",
            "chunk_timeline",
            "whiteboard_mode",
            "is_active",
            "deleted_at",
            "start_seconds",
            "end_seconds",
            "duration_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_editor_document(self, obj):
        return transcript_page_editor_document_for_response(obj, self.context)


class TranslatedSubtitleTrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranslatedSubtitleTrack
        fields = [
            "id",
            "project",
            "job",
            "language_code",
            "language_label",
            "source_language_code",
            "provider",
            "status",
            "cue_count",
            "error_message",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AvatarRenderJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = AvatarRenderJob
        fields = [
            "id",
            "lesson",
            "teacher",
            "avatar_version",
            "source_image_hash",
            "tts_audio_hash",
            "lesson_text_hash",
            "slide_hash",
            "engine_used",
            "render_status",
            "render_error",
            "output_path",
            "fallback_chain_used",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AvatarOverlayPreferenceSerializer(serializers.ModelSerializer):
    position = serializers.SerializerMethodField()
    size = serializers.SerializerMethodField()
    x = serializers.SerializerMethodField()
    y = serializers.SerializerMethodField()
    width = serializers.SerializerMethodField()
    avatar_placement = serializers.SerializerMethodField()

    class Meta:
        model = AvatarOverlayPreference
        fields = [
            "id",
            "user",
            "lesson",
            "anchor",
            "x_percent",
            "y_percent",
            "width_percent",
            "position",
            "size",
            "x",
            "y",
            "width",
            "avatar_placement",
            "visible",
            "pinned",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "lesson", "updated_at"]

    def _placement(self, obj):
        return placement_from_overlay_preference(obj)

    def get_position(self, obj):
        return self._placement(obj)["position"]

    def get_size(self, obj):
        return self._placement(obj)["size"]

    def get_x(self, obj):
        return self._placement(obj)["x"]

    def get_y(self, obj):
        return self._placement(obj)["y"]

    def get_width(self, obj):
        return self._placement(obj)["width"]

    def get_avatar_placement(self, obj):
        return normalize_avatar_placement(self._placement(obj))


class LessonSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonSegment
        fields = [
            "id",
            "project",
            "segment_order",
            "segment_text",
            "segment_slide_path",
            "segment_tts_path",
            "segment_avatar_path",
            "segment_pause_seconds",
            "status",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
