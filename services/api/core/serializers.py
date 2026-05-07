"""
DRF serializers for AI_ACADEMY core models.
"""

from collections.abc import Mapping
from copy import deepcopy
import math

from django.conf import settings
from rest_framework import serializers
from django.contrib.auth.models import User
from core.models import (
    AvatarRenderJob,
    AvatarOverlayPreference,
    Category,
    Job,
    LessonSegment,
    LessonComment,
    Project,
    Slide,
    TranscriptPage,
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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


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
            "id", "project_id", "request_id", "status", "progress",
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
    latest_job = serializers.SerializerMethodField()
    avatar_active = serializers.SerializerMethodField()
    tts_settings = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "user", "user_name", "title", "description",
            "cover_url", "thumbnail_url", "tts_settings",
            "status", "render_profile", "is_published", "avatar_enabled_override", "avatar_active", "category_id", "category_name", "category_slug", "created_at", "updated_at", "latest_job",
        ]
        read_only_fields = [
            "id", "user", "user_name", "description",
            "cover_url", "thumbnail_url", "tts_settings", "status", "category_id", "category_name", "category_slug", "created_at", "updated_at", "latest_job",
        ]

    def get_latest_job(self, obj):
        job = obj.jobs.order_by("-created_at").first()
        if job is None:
            return None
        return JobSerializer(job).data

    def get_avatar_active(self, obj):
        profile = getattr(obj.user, "profile", None) if obj.user else None
        profile_enabled = bool(
            profile
            and profile.avatar_enabled
            and profile.avatar_consent_confirmed
            and profile.avatar_image_processed
        )
        if obj.avatar_enabled_override is None:
            return profile_enabled
        return bool(obj.avatar_enabled_override and profile_enabled)

    def get_tts_settings(self, obj):
        return canonical_project_tts_settings(getattr(obj, "tts_settings", None))

    def get_cover_url(self, obj):
        return _project_cover_url(obj, self.context)

    def get_thumbnail_url(self, obj):
        return _project_cover_url(obj, self.context)


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
    like_count = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()
    has_video = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "title", "description",
            "category_name", "category_slug",
            "teacher_name",
            "like_count", "comment_count",
            "has_video",
            "cover_url", "thumbnail_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_teacher_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return ""

    def get_like_count(self, obj):
        return obj.likes.count()

    def get_comment_count(self, obj):
        return obj.comments.count()

    def get_has_video(self, obj):
        return obj.jobs.filter(status="done").exists()

    def get_cover_url(self, obj):
        return _project_cover_url(obj, self.context)

    def get_thumbnail_url(self, obj):
        return _project_cover_url(obj, self.context)


class LessonCommentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = LessonComment
        fields = ["id", "username", "text", "created_at"]
        read_only_fields = ["id", "username", "created_at"]


class TranscriptPageSerializer(serializers.ModelSerializer):
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
            "visible",
            "pinned",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "lesson", "updated_at"]


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
