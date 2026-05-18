from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import (
    AvatarRenderJob,
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
    UserProfile,
    VoiceProfile,
)

admin.site.site_header = "AI Academy Control Panel"
admin.site.site_title = "AI Academy Admin"
admin.site.index_title = "Operations Dashboard"


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "User Profile"
    fk_name = "user"
    fields = (
        "role",
        "is_public_profile",
        "display_name",
        "bio",
        "website_url",
        "contact_email",
        "social_links",
        "avatar_enabled",
        "avatar_consent_confirmed",
        "avatar_image_status",
        "avatar_model_version",
        "avatar_motion_preset",
        "avatar_lipsync_engine",
        "avatar_quality_preset",
        "avatar_last_rendered_at",
    )


class VoiceProfileInline(admin.StackedInline):
    model = VoiceProfile
    can_delete = True
    verbose_name_plural = "Voice Profile"
    fk_name = "user"
    fields = ("provider", "voice_id", "language", "speed", "pitch")
    extra = 0


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline, VoiceProfileInline)

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return []
        return super().get_inline_instances(request, obj)


# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(VoiceProfile)
class VoiceProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "voice_id", "language", "created_at")
    search_fields = ("user__username", "voice_id")
    list_filter = ("provider",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "user__username")
    raw_id_fields = ("user",)


class PlaylistItemInline(admin.TabularInline):
    model = PlaylistItem
    extra = 0
    raw_id_fields = ("project",)
    fields = ("project", "order", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_public", "created_at", "updated_at")
    list_filter = ("is_public", "created_at")
    search_fields = ("title", "description", "user__username")
    raw_id_fields = ("user",)
    inlines = (PlaylistItemInline,)


@admin.register(PlaylistItem)
class PlaylistItemAdmin(admin.ModelAdmin):
    list_display = ("playlist", "project", "order", "created_at")
    raw_id_fields = ("playlist", "project")
    search_fields = ("playlist__title", "project__title")


@admin.register(SavedPlaylist)
class SavedPlaylistAdmin(admin.ModelAdmin):
    list_display = ("user", "playlist", "created_at")
    raw_id_fields = ("user", "playlist")
    search_fields = ("user__username", "playlist__title")
    list_filter = ("created_at",)


@admin.register(Slide)
class SlideAdmin(admin.ModelAdmin):
    list_display = ("project", "order", "title", "duration_seconds")
    search_fields = ("project__title",)


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("project", "job_type", "status", "progress", "created_at")
    list_filter = ("job_type", "status")
    search_fields = ("project__title", "celery_task_id")
    readonly_fields = ("celery_task_id", "result_url", "srt_url", "error_message", "created_at", "updated_at")


@admin.register(LessonIntelligenceReport)
class LessonIntelligenceReportAdmin(admin.ModelAdmin):
    list_display = ("project", "provider", "status", "fallback_used", "complexity_level", "created_at")
    list_filter = ("provider", "status", "fallback_used", "complexity_level")
    search_fields = ("project__title", "summary", "short_description", "source_hash")
    raw_id_fields = ("project", "requested_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created_at")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(SiteHelpContent)
class SiteHelpContentAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "is_published", "updated_at")
    search_fields = (
        "title",
        "body",
        "contact_email",
        "contact_phone",
        "company_name",
        "company_address",
    )
    list_filter = ("is_published",)
    prepopulated_fields = {"slug": ("title",)}


@admin.register(LessonLike)
class LessonLikeAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "created_at")
    raw_id_fields = ("user", "project")


@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "progress_pct", "updated_at")
    raw_id_fields = ("user", "project")


@admin.register(LessonComment)
class LessonCommentAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "text", "created_at")
    raw_id_fields = ("user", "project")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient_user", "event_type", "title", "is_read", "created_at")
    list_filter = ("event_type", "is_read", "created_at")
    search_fields = ("recipient_user__username", "actor_user__username", "title", "body")
    raw_id_fields = ("recipient_user", "actor_user", "project", "lesson_comment", "job")
    readonly_fields = ("created_at", "updated_at", "read_at")


@admin.register(PublisherFollow)
class PublisherFollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "publisher", "created_at")
    raw_id_fields = ("follower", "publisher")
    search_fields = ("follower__username", "publisher__username")
    list_filter = ("created_at",)


@admin.register(AvatarRenderJob)
class AvatarRenderJobAdmin(admin.ModelAdmin):
    list_display = ("lesson", "teacher", "engine_used", "render_status", "created_at")
    list_filter = ("render_status", "engine_used")
    search_fields = ("lesson__title", "teacher__username", "source_image_hash", "tts_audio_hash")
    raw_id_fields = ("lesson", "teacher")
