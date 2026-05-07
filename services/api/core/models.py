"""
Core app models for AI_ACADEMY.

Entities:
  UserProfile  - user role management (teacher, publisher, or student)
  VoiceProfile - TTS voice config linked to a Teacher
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

from django.db import models
from django.db.models import Q
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


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
    RENDER_PROFILE_CHOICES = [
        ("fast", "Fast"),
        ("balanced", "Balanced"),
        ("quality", "Quality"),
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
    render_profile = models.CharField(
        max_length=20,
        choices=RENDER_PROFILE_CHOICES,
        default="balanced",
    )
    avatar_enabled_override = models.BooleanField(null=True, blank=True)
    tts_settings = models.JSONField(default=default_project_tts_settings, blank=True)
    # When True the lesson is listed in the public student catalog.
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


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
    request_id = models.CharField(max_length=120, blank=True, db_index=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["project", "job_type", "request_id"],
                condition=Q(request_id__gt=""),
                name="uq_job_project_type_request_id_nonempty",
            ),
        ]

    def __str__(self):
        return f"{self.job_type} [{self.status}] - {self.pk}"


class JobCheckpoint(models.Model):
    """Persist pipeline stage checkpoints for resumable/recoverable render flows."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
        ("failed", "Failed"),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="checkpoints")
    stage_name = models.CharField(max_length=80)
    stage_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["job", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["job", "stage_name"], name="uq_job_checkpoint_stage"),
        ]
        indexes = [
            models.Index(fields=["job", "stage_status"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self):
        return f"JobCheckpoint job={self.job_id} stage={self.stage_name} status={self.stage_status}"


class JobActionAudit(models.Model):
    """Immutable audit trail for sensitive job actions (cancel/retry/admin ops)."""

    ACTION_CHOICES = [
        ("cancel_requested", "Cancel Requested"),
        ("cancel_rejected", "Cancel Rejected"),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="action_audits")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="job_action_audits")
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="job_action_audits")
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["project", "action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
        ]

    def __str__(self):
        return f"JobActionAudit job={self.job_id} action={self.action}"


class LessonProgress(models.Model):
    """Per-user lesson watch progress (authenticated students only)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lesson_progress")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="progress_records")
    progress_pct = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "project")]

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
