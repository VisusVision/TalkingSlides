"""
Core app models for AI_ACADEMY.

Entities:
  UserProfile  - user role management (teacher, publisher, or student)
  VoiceProfile - TTS voice config linked to a Teacher
  SiteHelpContent - admin-editable public help content
  Category     - lesson category for the student catalog
  Project      - a lesson/course project owned by a Teacher
  Slide        - individual slide within a Project
  Job          - async processing job (TTS render, export, etc.)
  LessonProgress - per-user watch progress (authenticated students)
  LessonLike   - per-user lesson like (authenticated students)
  LessonComment - public comment on a lesson (authenticated students)

Migration note:
After updating this file run:
    python manage.py makemigrations && python manage.py migrate
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.contrib.auth.models import User
from django.utils.text import slugify


class UserProfile(models.Model):
    """Profile for managing user roles like Teacher and Student."""

    ROLE_CHOICES = [
        ("teacher", "Teacher"),
        ("publisher", "Publisher"),
        ("student", "Student"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="student")
    bio = models.TextField(blank=True)
    display_name = models.CharField(max_length=200, blank=True)
    banner_image_original = models.CharField(max_length=500, blank=True)
    banner_image_processed = models.CharField(max_length=500, blank=True)
    logo_image_original = models.CharField(max_length=500, blank=True)
    logo_image_processed = models.CharField(max_length=500, blank=True)
    website_url = models.URLField(blank=True)
    contact_email = models.EmailField(blank=True)
    social_links = models.JSONField(default=dict, blank=True)
    is_public_profile = models.BooleanField(default=False, db_index=True)
    avatar_image_original = models.CharField(max_length=500, blank=True)
    avatar_image_processed = models.CharField(max_length=500, blank=True)
    avatar_video_original = models.CharField(max_length=500, blank=True)
    avatar_video_processed = models.CharField(max_length=500, blank=True)
    avatar_reference_type = models.CharField(max_length=20, default="image")
    avatar_image_status = models.CharField(max_length=30, default="idle")
    avatar_model_version = models.CharField(max_length=80, default="liveportrait+musetalk:v1")
    avatar_enabled = models.BooleanField(default=False)
    avatar_last_rendered_at = models.DateTimeField(null=True, blank=True)
    avatar_consent_confirmed = models.BooleanField(default=False)
    avatar_preview_video = models.CharField(max_length=500, blank=True)
    avatar_overlay_default_position = models.CharField(max_length=40, default="top-right")
    avatar_overlay_size = models.CharField(max_length=30, default="medium")
    avatar_overlay_visible = models.BooleanField(default=True)
    avatar_motion_preset = models.CharField(max_length=40, default="natural")
    avatar_lipsync_engine = models.CharField(max_length=40, default="musetalk")
    avatar_quality_preset = models.CharField(max_length=40, default="high")
    avatar_engine_primary = models.CharField(max_length=40, default="liveportrait")
    avatar_engine_fallback = models.CharField(max_length=40, default="sadtalker,wav2lip")
    avatar_last_preview_status = models.CharField(max_length=30, default="idle")
    avatar_last_preview_job_id = models.CharField(max_length=255, blank=True)
    avatar_last_preview_path = models.CharField(max_length=500, blank=True)
    avatar_preview_error = models.TextField(blank=True)
    avatar_version_hash = models.CharField(max_length=80, blank=True)
    avatar_source_valid = models.BooleanField(default=False)
    avatar_source_validation_error = models.TextField(blank=True)
    avatar_source_hash = models.CharField(max_length=64, blank=True)
    avatar_source_image_hash = models.CharField(max_length=64, blank=True)
    avatar_source_video_hash = models.CharField(max_length=64, blank=True)
    avatar_source_reference_type = models.CharField(max_length=20, blank=True)
    avatar_preview_source_hash = models.CharField(max_length=64, blank=True)
    avatar_preview_stale = models.BooleanField(default=False)
    avatar_moderation_status = models.CharField(max_length=30, default="not_scanned")
    avatar_moderation_summary = models.JSONField(default=dict, blank=True)
    avatar_last_moderation_run_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class SiteHelpContent(models.Model):
    """Admin-managed public help content for the frontend Help page."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True)
    body = models.TextField()
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=80, blank=True)
    company_name = models.CharField(max_length=200, blank=True)
    company_address = models.TextField(blank=True)
    support_url = models.URLField(blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = "site help content"
        verbose_name_plural = "site help content"

    def __str__(self):
        return self.title


class VoiceProfile(models.Model):
    """TTS voice settings for a Teacher."""

    PROVIDER_CHOICES = [
        ("xtts_v2", "XTTS v2 (Coqui, default)"),
        ("gtts", "gTTS (Google, fallback)"),
        ("openai", "OpenAI"),
        ("elevenlabs", "ElevenLabs"),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="voice_profile"
    )
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES, default="xtts_v2")
    voice_id = models.CharField(max_length=100, blank=True, help_text="Provider-specific voice ID")
    speed = models.FloatField(default=1.0)
    pitch = models.FloatField(default=1.0)
    language = models.CharField(max_length=10, default="en")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.provider}"


def default_project_tts_settings():
    """Return a fresh project-level TTS settings payload."""
    return {
        "provider_preference": "auto",
        "normalization_enabled": True,
        "normalization_mode": "loose",
        "unknown_word_strategy": "keep",
        "overrides": {
            "technical": {},
            "abbreviation": {},
            "mixed_word": {},
        },
        "speech_speed": 1.0,
        "volume_gain_db": 0,
        "pause_seconds": None,
    }


class Category(models.Model):
    """Lesson category for the student-facing catalog."""

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Project(models.Model):
    """A lesson / course project containing one or more slides."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("archived", "Archived"),
    ]
    MODERATION_STATUS_CHOICES = [
        ("not_scanned", "Not scanned"),
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("revision_required", "Revision required"),
        ("needs_admin_review", "Needs admin review"),
        ("admin_approved", "Admin approved"),
        ("admin_rejected", "Admin rejected"),
        ("failed", "Failed"),
    ]
    MANUAL_MODERATION_STATUS_CHOICES = [
        ("", "No manual decision"),
        ("approved", "Approved"),
        ("blocked", "Blocked"),
        ("rejected", "Rejected"),
        ("request_changes", "Request changes"),
        ("needs_review", "Needs review"),
    ]
    AVATAR_PROCESSING_STATUS_CHOICES = [
        ("none", "None"),
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    cover_image_original = models.CharField(max_length=500, blank=True)
    cover_image_processed = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    moderation_status = models.CharField(
        max_length=30,
        choices=MODERATION_STATUS_CHOICES,
        default="not_scanned",
        db_index=True,
    )
    moderation_summary = models.JSONField(default=dict, blank=True)
    last_moderation_run_id = models.PositiveIntegerField(null=True, blank=True)
    manual_moderation_status = models.CharField(
        max_length=30,
        choices=MANUAL_MODERATION_STATUS_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    manual_moderation_reason = models.TextField(blank=True)
    manual_moderation_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="manual_moderation_decisions",
    )
    manual_moderation_at = models.DateTimeField(null=True, blank=True)
    moderation_blocked_until_review = models.BooleanField(default=False, db_index=True)
    latest_publisher_change_at = models.DateTimeField(null=True, blank=True)
    latest_review_requested_at = models.DateTimeField(null=True, blank=True)
    avatar_enabled_override = models.BooleanField(null=True, blank=True)
    avatar_processing_status = models.CharField(
        max_length=20,
        choices=AVATAR_PROCESSING_STATUS_CHOICES,
        default="none",
    )
    avatar_processing_message = models.TextField(blank=True)
    avatar_last_job_id = models.CharField(max_length=255, blank=True)
    avatar_visible = models.BooleanField(default=True)
    avatar_output_path = models.CharField(max_length=500, blank=True)
    avatar_updated_at = models.DateTimeField(null=True, blank=True)
    tts_settings = models.JSONField(default=default_project_tts_settings, blank=True)
    draft_data = models.JSONField(default=dict, blank=True)
    # When True the lesson is listed in the public student catalog.
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["is_published", "status", "moderation_status", "-created_at"],
                name="c_proj_pub_stat_mod_cr_idx",
            ),
            models.Index(
                fields=["user", "-created_at"],
                name="c_proj_user_created_idx",
            ),
        ]

    def __str__(self):
        return self.title


def _user_can_own_playlist(user) -> bool:
    if not user:
        return False
    if user.is_staff or user.is_superuser:
        return True
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return False
    return str(getattr(profile, "role", "") or "").lower() in {"publisher", "teacher"}


class Playlist(models.Model):
    """Publisher-owned grouping of lessons for channel presentation."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="playlists")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        indexes = [
            models.Index(fields=["user", "is_public"]),
            models.Index(fields=["user", "updated_at"]),
        ]

    def clean(self):
        if self.user_id and not _user_can_own_playlist(self.user):
            raise ValidationError("Only teacher, publisher, staff, or admin accounts can own playlists.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class PlaylistItem(models.Model):
    """Ordered lesson membership within a publisher playlist."""

    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="items")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="playlist_items")
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["playlist", "order", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["playlist", "project"], name="unique_playlist_project"),
        ]
        indexes = [
            models.Index(fields=["playlist", "order"]),
        ]

    def clean(self):
        if not self.playlist_id or not self.project_id:
            return
        owner = self.playlist.user
        if owner and (owner.is_staff or owner.is_superuser):
            return
        if not self.project.user_id or int(self.project.user_id) != int(self.playlist.user_id):
            raise ValidationError("Playlist items must belong to the playlist owner.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.playlist_id}:{self.order}:{self.project_id}"


class SavedPlaylist(models.Model):
    """Student/library save for a public publisher playlist."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_playlists")
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="saved_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "playlist"], name="unique_saved_playlist"),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["playlist", "created_at"]),
        ]

    def clean(self):
        if self.playlist_id and not self.playlist.is_public:
            raise ValidationError("Only public playlists can be saved.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user_id}:{self.playlist_id}"


class Slide(models.Model):
    """One slide within a Project."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="slides")
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=300, blank=True)
    narration_text = models.TextField(help_text="Script for TTS narration")
    audio_file = models.FileField(upload_to="audio/", blank=True, null=True)
    image_file = models.FileField(upload_to="slides/", blank=True, null=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["project", "order"]

    def __str__(self):
        return f"{self.project.title} - Slide {self.order}"


class Job(models.Model):
    """Async processing job (Celery task) for TTS rendering, export, etc."""

    JOB_TYPE_CHOICES = [
        ("tts_render", "TTS Render"),
        ("video_export", "Video Export"),
        ("pptx_export", "PPTX Export"),
        ("sync", "Data Sync"),
        ("avatar_preprocess", "Avatar Preprocess"),
        ("avatar_render", "Avatar Render"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    project = models.ForeignKey(
        Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs"
    )
    job_type = models.CharField(max_length=50, choices=JOB_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    celery_task_id = models.CharField(max_length=255, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    result_url = models.CharField(max_length=500, blank=True)
    srt_url = models.CharField(max_length=500, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project", "job_type", "status", "-created_at"],
                name="c_job_proj_type_stat_cr_idx",
            ),
            models.Index(
                fields=["project", "-created_at"],
                name="c_job_proj_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.job_type} [{self.status}] - {self.pk}"


class LessonIntelligenceReport(models.Model):
    """Advisory lesson-quality analysis for publisher Studio workflows."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="lesson_intelligence_reports",
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lesson_intelligence_reports",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    provider = models.CharField(max_length=40, default="heuristic")
    provider_chain = models.JSONField(default=list, blank=True)
    fallback_used = models.BooleanField(default=False)
    source_hash = models.CharField(max_length=64, db_index=True)
    summary = models.TextField(blank=True)
    short_description = models.TextField(blank=True)
    complexity_level = models.CharField(max_length=20, blank=True)
    complexity_score = models.PositiveSmallIntegerField(default=0)
    complexity_reasons = models.JSONField(default=list, blank=True)
    clarity_warnings = models.JSONField(default=list, blank=True)
    page_suggestions = models.JSONField(default=list, blank=True)
    expanded_narration_suggestions = models.JSONField(default=list, blank=True)
    suggested_tags = models.JSONField(default=list, blank=True)
    limitations = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["project", "-created_at"], name="c_lir_project_created_idx"),
            models.Index(fields=["project", "source_hash"], name="c_lir_project_hash_idx"),
        ]

    def save(self, *args, **kwargs):
        self.provider = str(self.provider or "heuristic").strip().lower()
        self.status = str(self.status or "pending").strip().lower()
        if self.status not in {choice[0] for choice in self.STATUS_CHOICES}:
            self.status = "pending"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"LessonIntelligenceReport project={self.project_id} provider={self.provider} status={self.status}"


class AnalyticsIntelligenceReport(models.Model):
    """Advisory creator analytics analysis for publisher dashboards."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]
    RISK_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    ]

    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_intelligence_reports",
    )
    scope = models.CharField(max_length=20, default="creator", db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    provider = models.CharField(max_length=40, default="heuristic")
    provider_chain = models.JSONField(default=list, blank=True)
    fallback_used = models.BooleanField(default=False)
    source_hash = models.CharField(max_length=64, db_index=True)
    date_range = models.JSONField(default=dict, blank=True)
    category_filter = models.CharField(max_length=120, blank=True)
    summary = models.TextField(blank=True)
    health_score = models.PositiveSmallIntegerField(default=0)
    risk_level = models.CharField(max_length=20, choices=RISK_CHOICES, default="medium")
    insights = models.JSONField(default=list, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    lesson_actions = models.JSONField(default=list, blank=True)
    category_actions = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    limitations = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["requested_by", "-created_at"], name="c_air_user_created_idx"),
            models.Index(fields=["requested_by", "scope", "source_hash"], name="c_air_user_scope_hash_idx"),
            models.Index(fields=["scope", "-created_at"], name="c_air_scope_created_idx"),
        ]

    def save(self, *args, **kwargs):
        self.provider = str(self.provider or "heuristic").strip().lower()
        self.status = str(self.status or "pending").strip().lower()
        if self.status not in {choice[0] for choice in self.STATUS_CHOICES}:
            self.status = "pending"
        self.scope = str(self.scope or "creator").strip().lower()
        self.risk_level = str(self.risk_level or "medium").strip().lower()
        if self.risk_level not in {choice[0] for choice in self.RISK_CHOICES}:
            self.risk_level = "medium"
        self.health_score = max(0, min(100, int(self.health_score or 0)))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"AnalyticsIntelligenceReport user={self.requested_by_id} provider={self.provider} status={self.status}"


class LessonProgress(models.Model):
    """Per-user lesson watch progress (authenticated students only)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_progress")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="progress_records")
    progress_pct = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "project")]
        indexes = [
            models.Index(
                fields=["project", "-updated_at"],
                name="c_lp_proj_updated_idx",
            ),
            models.Index(
                fields=["user", "-updated_at"],
                name="c_lp_user_updated_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.project.title} - {self.progress_pct}%"


class LessonLike(models.Model):
    """Per-user lesson like (authenticated students only)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_likes")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "project")]

    def __str__(self):
        return f"{self.user.username} likes {self.project.title}"


class LessonComment(models.Model):
    """Public comment on a lesson (authenticated students only)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_comments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} on {self.project.title}"


class Notification(models.Model):
    """In-app notification for authenticated users."""

    class EventType(models.TextChoices):
        STUDENT_FOLLOWED_PUBLISHER_NEW_LESSON = (
            "student_followed_publisher_new_lesson",
            "Followed publisher posted a new lesson",
        )
        PUBLISHER_COMMENT_ON_LESSON = (
            "publisher_comment_on_lesson",
            "Someone commented on my lesson",
        )
        PUBLISHER_LESSON_RENDER_DONE = (
            "publisher_lesson_render_done",
            "Lesson render completed",
        )
        PUBLISHER_LESSON_RENDER_FAILED = (
            "publisher_lesson_render_failed",
            "Lesson render failed",
        )
        PUBLISHER_AVATAR_RENDER_DONE = (
            "publisher_avatar_render_done",
            "Avatar render completed",
        )
        PUBLISHER_AVATAR_RENDER_FAILED = (
            "publisher_avatar_render_failed",
            "Avatar render failed",
        )
        PUBLISHER_LESSON_MODERATION_ACTION = (
            "publisher_lesson_moderation_action",
            "Lesson moderation action",
        )

    recipient_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    actor_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    event_type = models.CharField(max_length=80, choices=EventType.choices, db_index=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    lesson_comment = models.ForeignKey(
        LessonComment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    job = models.ForeignKey(
        Job,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    action_url = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient_user", "is_read", "created_at"]),
            models.Index(fields=["recipient_user", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} -> {self.recipient_user_id}"


class PublisherFollow(models.Model):
    """Follower relationship between a learner and a publisher/teacher account."""

    follower = models.ForeignKey(User, on_delete=models.CASCADE, related_name="following_publishers")
    publisher = models.ForeignKey(User, on_delete=models.CASCADE, related_name="publisher_followers")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("follower", "publisher")]
        constraints = [
            models.CheckConstraint(
                check=~Q(follower_id=F("publisher_id")),
                name="publisherfollow_no_self_follow",
            )
        ]
        ordering = ["-created_at"]

    def clean(self):
        if self.follower_id and self.publisher_id and self.follower_id == self.publisher_id:
            raise ValidationError("Users cannot follow themselves.")
        if not self.publisher_id:
            return
        publisher = self.publisher
        try:
            profile = publisher.profile
        except UserProfile.DoesNotExist:
            profile = None
        role = str(getattr(profile, "role", "") or "").lower()
        if not (publisher.is_staff or publisher.is_superuser or role in {"publisher", "teacher"}):
            raise ValidationError("Target user is not a publisher.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.follower.username} follows {self.publisher.username}"


class TranscriptPage(models.Model):
    """Per-project transcript page used by render and manage/editor workflows."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="transcript_pages")
    order = models.PositiveIntegerField(default=0)
    source_slide_index = models.PositiveIntegerField(default=0)
    split_index = models.PositiveIntegerField(default=0)
    page_key = models.CharField(max_length=64)
    original_text = models.TextField(blank=True)
    narration_text = models.TextField(blank=True)
    rich_text_html = models.TextField(blank=True)
    editor_document = models.JSONField(default=dict, blank=True)
    subtitle_chunks = models.JSONField(default=list, blank=True)
    chunk_timeline = models.JSONField(default=list, blank=True)
    whiteboard_mode = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    start_seconds = models.FloatField(null=True, blank=True)
    end_seconds = models.FloatField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "order"]
        unique_together = [("project", "page_key")]

    def __str__(self):
        return f"{self.project.title} [{self.page_key}]"


class TranslatedSubtitleTrack(models.Model):
    """Metadata for translated subtitle sidecar files derived from original display cues."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    ]

    PROVIDER_CHOICES = [
        ("mock", "Mock"),
        ("deepl", "DeepL"),
        ("google", "Google"),
        ("openai", "OpenAI"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="translated_subtitle_tracks")
    job = models.ForeignKey(Job, on_delete=models.SET_NULL, null=True, blank=True, related_name="translated_subtitle_tracks")
    language_code = models.CharField(max_length=16)
    language_label = models.CharField(max_length=80, blank=True)
    source_language_code = models.CharField(max_length=16, blank=True)
    provider = models.CharField(max_length=40, choices=PROVIDER_CHOICES, default="mock")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    srt_path = models.CharField(max_length=500, blank=True)
    vtt_path = models.CharField(max_length=500, blank=True)
    cue_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "language_code"]
        constraints = [
            models.UniqueConstraint(fields=["project", "language_code"], name="unique_subtitle_track_language_per_project"),
        ]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["language_code"]),
        ]

    def save(self, *args, **kwargs):
        self.language_code = str(self.language_code or "").strip().lower()
        self.source_language_code = str(self.source_language_code or "").strip().lower()
        self.provider = str(self.provider or "mock").strip().lower()
        self.status = str(self.status or "pending").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"TranslatedSubtitleTrack project={self.project_id} lang={self.language_code} status={self.status}"


class AvatarRenderJob(models.Model):
    """Tracked avatar rendering execution with deterministic input hashes."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    lesson = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="avatar_render_jobs",
    )
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="avatar_render_jobs",
    )
    avatar_version = models.CharField(max_length=80, default="liveportrait+musetalk:v1")
    source_image_hash = models.CharField(max_length=64)
    tts_audio_hash = models.CharField(max_length=64)
    lesson_text_hash = models.CharField(max_length=64, blank=True)
    slide_hash = models.CharField(max_length=64, blank=True)
    engine_used = models.CharField(max_length=40, default="none")
    render_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    render_error = models.TextField(blank=True)
    output_path = models.CharField(max_length=500, blank=True)
    fallback_chain_used = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["lesson", "teacher", "render_status"]),
            models.Index(fields=["source_image_hash", "tts_audio_hash"]),
        ]

    def __str__(self):
        return f"AvatarRenderJob lesson={self.lesson_id} status={self.render_status}"


class AvatarOverlayPreference(models.Model):
    """Persist per-user per-lesson avatar overlay preferences for player UI."""

    ANCHOR_CHOICES = [
        ("top-right", "Top Right"),
        ("top-left", "Top Left"),
        ("bottom-right", "Bottom Right"),
        ("bottom-left", "Bottom Left"),
        ("custom", "Custom"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="avatar_overlay_preferences")
    lesson = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="avatar_overlay_preferences")
    anchor = models.CharField(max_length=20, choices=ANCHOR_CHOICES, default="top-right")
    x_percent = models.FloatField(default=72.0)
    y_percent = models.FloatField(default=8.0)
    width_percent = models.FloatField(default=24.0)
    visible = models.BooleanField(default=True)
    pinned = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "lesson")]

    def __str__(self):
        return f"AvatarOverlayPreference user={self.user_id} lesson={self.lesson_id}"


class LessonSegment(models.Model):
    """Normalized per-segment lesson render artifacts for modular rerender flows."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="lesson_segments")
    segment_order = models.PositiveIntegerField(default=0)
    segment_text = models.TextField(blank=True)
    segment_slide_path = models.CharField(max_length=500, blank=True)
    segment_tts_path = models.CharField(max_length=500, blank=True)
    segment_avatar_path = models.CharField(max_length=500, blank=True)
    segment_pause_seconds = models.FloatField(default=2.2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "segment_order"]
        unique_together = [("project", "segment_order")]

    def __str__(self):
        return f"LessonSegment project={self.project_id} order={self.segment_order}"
