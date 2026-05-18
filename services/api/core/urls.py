"""
URL routing for the core app (mounted at /api/v1/ in config/urls.py).

Auth:
  POST   /api/v1/auth/login/                          LoginView
  POST   /api/v1/auth/logout/                         LogoutView
  GET    /api/v1/auth/me/                             MeView

Secure media:
  GET    /api/v1/stream/<token>/                      MediaStreamView  (public, token-gated)
  GET    /api/v1/projects/<id>/playback-token/        PlaybackTokenView (published lessons only)
  GET    /api/v1/media/<path>                         MediaServeView   (staff/admin only)

Student catalog (public):
  GET    /api/v1/catalog/                             CatalogListView
  GET    /api/v1/catalog/<id>/                        CatalogDetailView
  GET    /api/v1/categories/                          CategoryListView

Student social (auth required):
  POST   /api/v1/catalog/<id>/like/                   LessonLikeView
  POST   /api/v1/catalog/<id>/progress/               LessonProgressView
  GET    /api/v1/catalog/<id>/comments/               LessonCommentsView
  POST   /api/v1/catalog/<id>/comments/               LessonCommentsView

Teacher pipeline:
  POST   /api/v1/projects/                            ProjectUploadView
  GET    /api/v1/projects/                            ProjectUploadView (list)
  GET    /api/v1/projects/<project_id>/               ProjectDetailView
  DELETE /api/v1/projects/<project_id>/               ProjectDetailView
  POST   /api/v1/projects/<project_id>/rerender/      ProjectRerenderView
  POST   /api/v1/projects/<project_id>/avatar/rerender/ ProjectAvatarRerenderView
  GET    /api/v1/projects/<project_id>/jobs/<job_id>/ JobStatusView
  POST   /api/v1/users/<user_id>/voice/               VoiceUploadView

TTS preview (Phase 1):
  POST   /api/v1/tts/preview/                         TTSPreviewView
  POST   /api/v1/tts/preview-audio/                   TTSPreviewAudioView
  POST   /api/v1/tts/pronunciation-suggestions/       TTSPronunciationSuggestionsView

Router:
  /api/v1/users/   UserViewSet
  /api/v1/slides/  SlideViewSet
  /api/v1/jobs/    JobViewSet
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
  AdminStatsDashboardView,
  AvatarPreviewRegenerateView,
  AvatarPrepareView,
  ProjectAvatarRerenderView,
  AvatarPreviewStatusView,
  AvatarPreviewDeleteView,
  AvatarOverlayPreferenceView,
  AvatarProfileView,
  AuthProvidersView,
    CatalogDetailView,
  CatalogFeedView,
  CatalogPlaylistContextView,
    CatalogListView,
    CategoryListView,
    CreatorAnalyticsView,
    CurrentUserProfileAssetsView,
    CurrentUserProfileView,
    GoogleLoginView,
    GoogleRedirectCallbackView,
    GoogleRedirectStartView,
    JobStatusView,
    JobViewSet,
    LessonCommentsView,
    LessonLikeView,
    LessonProgressView,
    LoginView,
    LogoutView,
    MediaServeView,
    MediaStreamView,
    MeView,
    HelpContentView,
    PlaybackSessionHeartbeatView,
    PlaybackTokenView,
    PlaylistDetailView,
    PlaylistItemCreateView,
    PlaylistItemDeleteView,
    PlaylistItemReorderView,
    PlaylistListCreateView,
    PlaylistSaveToggleView,
    ProjectSubtitleTrackListView,
    ProjectCoverImageView,
    ProjectBackgroundApplyAllView,
    ProjectDraftDiscardView,
    ProjectLessonIntelligenceView,
    ProjectDetailView,
    ProjectRerenderView,
    ProjectTranscriptActionView,
    PublisherPlaylistsView,
    PublisherFollowToggleView,
    PublisherLessonsView,
    PublisherProfileView,
    TranscriptPageBackgroundImageView,
    TranscriptPageBackgroundUploadView,
    TranscriptPageHighlightPreviewImageView,
    TranscriptPageHighlightPreviewView,
    TranscriptPageSceneView,
    ProjectTranscriptView,
    ProjectUploadView,
    SlideViewSet,
    TTSPreviewAudioView,
    TTSPreviewView,
    TTSPronunciationSuggestionsView,
    UserFollowingView,
    UserHistoryView,
    UserLikedLessonsView,
    UserNotificationListView,
    UserNotificationMarkAllReadView,
    UserNotificationReadView,
    UserNotificationUnreadCountView,
    UserProfileAssetView,
    UserSavedPlaylistsView,
    StudioPreviewTokenView,
    UserViewSet,
    VoiceUploadView,
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"slides", SlideViewSet, basename="slide")
router.register(r"jobs", JobViewSet, basename="job")

urlpatterns = [
    # Auth
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("me/profile/", CurrentUserProfileView.as_view(), name="me-profile"),
    path("me/profile-assets/", CurrentUserProfileAssetsView.as_view(), name="me-profile-assets"),
    path("me/analytics/", CreatorAnalyticsView.as_view(), name="creator-analytics"),
    path("help/", HelpContentView.as_view(), name="help-content"),
    path("auth/providers/", AuthProvidersView.as_view(), name="auth-providers"),
    path("auth/google/", GoogleLoginView.as_view(), name="auth-google"),
    path("auth/google/redirect/start/", GoogleRedirectStartView.as_view(), name="auth-google-redirect-start"),
    path("auth/google/redirect/callback/", GoogleRedirectCallbackView.as_view(), name="auth-google-redirect-callback"),

    # Secure media streaming (token-gated, public)
    path("stream/<str:token>/", MediaStreamView.as_view(), name="media-stream"),

# Playback token issuance - no login required for published lessons
    path("projects/<int:project_id>/playback-token/", PlaybackTokenView.as_view(), name="playback-token"),
    path("projects/<int:project_id>/studio-preview-token/", StudioPreviewTokenView.as_view(), name="studio-preview-token"),
    path("projects/<int:project_id>/subtitle-tracks/", ProjectSubtitleTrackListView.as_view(), name="project-subtitle-tracks"),
    path("projects/<int:project_id>/cover/", ProjectCoverImageView.as_view(), name="project-cover"),
    path(
      "projects/<int:project_id>/playback-session/heartbeat/",
      PlaybackSessionHeartbeatView.as_view(),
      name="playback-session-heartbeat",
    ),

    # Raw media access (staff/admin debugging only)
    path("media/<path:filepath>", MediaServeView.as_view(), name="media-serve"),

    # Student catalog (public)
    path("catalog/", CatalogListView.as_view(), name="catalog-list"),
    path("catalog/feed/", CatalogFeedView.as_view(), name="catalog-feed"),
    path("catalog/<int:project_id>/playlist-context/", CatalogPlaylistContextView.as_view(), name="catalog-playlist-context"),
    path("catalog/<int:project_id>/", CatalogDetailView.as_view(), name="catalog-detail"),
    path("categories/", CategoryListView.as_view(), name="category-list"),

    # Admin analytics dashboard
    path("admin/stats/", AdminStatsDashboardView.as_view(), name="admin-stats-dashboard"),

    # Student social features
    path("catalog/<int:project_id>/like/", LessonLikeView.as_view(), name="lesson-like"),
    path("catalog/<int:project_id>/progress/", LessonProgressView.as_view(), name="lesson-progress"),
    path("catalog/<int:project_id>/comments/", LessonCommentsView.as_view(), name="lesson-comments"),
    path("me/history/", UserHistoryView.as_view(), name="user-history"),
    path("me/liked-lessons/", UserLikedLessonsView.as_view(), name="user-liked-lessons"),
    path("me/notifications/", UserNotificationListView.as_view(), name="user-notifications"),
    path("me/notifications/unread-count/", UserNotificationUnreadCountView.as_view(), name="user-notifications-unread-count"),
    path("me/notifications/<int:notification_id>/read/", UserNotificationReadView.as_view(), name="user-notifications-read"),
    path("me/notifications/mark-all-read/", UserNotificationMarkAllReadView.as_view(), name="user-notifications-mark-all-read"),
    path("me/following/", UserFollowingView.as_view(), name="user-following"),
    path("me/saved-playlists/", UserSavedPlaylistsView.as_view(), name="user-saved-playlists"),
    path("users/<int:user_id>/follow/", PublisherFollowToggleView.as_view(), name="publisher-follow"),
    path("users/<int:user_id>/profile/", PublisherProfileView.as_view(), name="publisher-profile"),
    path("users/<int:user_id>/profile-assets/<str:kind>/", UserProfileAssetView.as_view(), name="publisher-profile-asset"),
    path("users/<int:user_id>/lessons/", PublisherLessonsView.as_view(), name="publisher-lessons"),
    path("users/<int:user_id>/playlists/", PublisherPlaylistsView.as_view(), name="publisher-playlists"),
    path("playlists/", PlaylistListCreateView.as_view(), name="playlist-list"),
    path("playlists/<int:playlist_id>/save/", PlaylistSaveToggleView.as_view(), name="playlist-save"),
    path("playlists/<int:playlist_id>/", PlaylistDetailView.as_view(), name="playlist-detail"),
    path("playlists/<int:playlist_id>/items/", PlaylistItemCreateView.as_view(), name="playlist-items"),
    path("playlists/<int:playlist_id>/items/<int:project_id>/", PlaylistItemDeleteView.as_view(), name="playlist-item-delete"),
    path("playlists/<int:playlist_id>/items/reorder/", PlaylistItemReorderView.as_view(), name="playlist-items-reorder"),

    # TTS preview (Phase 1 — no audio synthesis, no Celery)
    path("tts/preview/", TTSPreviewView.as_view(), name="tts-preview"),
    path("tts/preview-audio/", TTSPreviewAudioView.as_view(), name="tts-preview-audio"),
    path(
        "tts/pronunciation-suggestions/",
        TTSPronunciationSuggestionsView.as_view(),
        name="tts-pronunciation-suggestions",
    ),

    # Teacher pipeline
    path("", include("ai_agents.urls")),
    path("projects/", ProjectUploadView.as_view(), name="project-upload"),
    path("projects/<int:project_id>/", ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<int:project_id>/draft/discard/", ProjectDraftDiscardView.as_view(), name="project-draft-discard"),
    path("projects/<int:project_id>/intelligence/", ProjectLessonIntelligenceView.as_view(), name="project-lesson-intelligence"),
    path("projects/<int:project_id>/intelligence/analyze/", ProjectLessonIntelligenceView.as_view(), name="project-lesson-intelligence-analyze"),
    path("projects/<int:project_id>/transcript/", ProjectTranscriptView.as_view(), name="project-transcript"),
    path("projects/<int:project_id>/transcript/actions/", ProjectTranscriptActionView.as_view(), name="project-transcript-actions"),
    path("projects/<int:project_id>/transcript-pages/<int:page_id>/scene/", TranscriptPageSceneView.as_view(), name="project-transcript-page-scene"),
    path("projects/<int:project_id>/transcript-pages/<str:page_ref>/scene/", TranscriptPageSceneView.as_view(), name="project-transcript-page-scene-ref"),
    path("projects/<int:project_id>/transcript-pages/<int:page_id>/highlight-preview/", TranscriptPageHighlightPreviewView.as_view(), name="project-transcript-page-highlight-preview"),
    path("projects/<int:project_id>/transcript-pages/<str:page_ref>/highlight-preview/", TranscriptPageHighlightPreviewView.as_view(), name="project-transcript-page-highlight-preview-ref"),
    path("projects/<int:project_id>/transcript-pages/<int:page_id>/highlight-preview-image/", TranscriptPageHighlightPreviewImageView.as_view(), name="project-transcript-page-highlight-preview-image"),
    path("projects/<int:project_id>/transcript-pages/<str:page_ref>/highlight-preview-image/", TranscriptPageHighlightPreviewImageView.as_view(), name="project-transcript-page-highlight-preview-image-ref"),
    path("projects/<int:project_id>/transcript-pages/<int:page_id>/background/", TranscriptPageBackgroundUploadView.as_view(), name="project-transcript-page-background"),
    path("projects/<int:project_id>/transcript-pages/<str:page_ref>/background/", TranscriptPageBackgroundUploadView.as_view(), name="project-transcript-page-background-ref"),
    path("projects/<int:project_id>/transcript-pages/<int:page_id>/background/<str:kind>/", TranscriptPageBackgroundImageView.as_view(), name="project-transcript-page-background-image"),
    path("projects/<int:project_id>/transcript-pages/<str:page_ref>/background/<str:kind>/", TranscriptPageBackgroundImageView.as_view(), name="project-transcript-page-background-image-ref"),
    path("projects/<int:project_id>/background/apply-all/", ProjectBackgroundApplyAllView.as_view(), name="project-background-apply-all"),
    path("projects/<int:project_id>/rerender/", ProjectRerenderView.as_view(), name="project-rerender"),
    path("projects/<int:project_id>/avatar/rerender/", ProjectAvatarRerenderView.as_view(), name="project-avatar-rerender"),
    path("projects/<int:project_id>/jobs/<int:job_id>/", JobStatusView.as_view(), name="job-status"),
    path("users/<int:user_id>/voice/", VoiceUploadView.as_view(), name="voice-upload"),
    path("users/<int:user_id>/avatar/", AvatarProfileView.as_view(), name="avatar-profile"),
    path("users/<int:user_id>/avatar/prepare/", AvatarPrepareView.as_view(), name="avatar-prepare"),
    path("users/<int:user_id>/avatar/preview/", AvatarPreviewRegenerateView.as_view(), name="avatar-preview"),
    path("users/<int:user_id>/avatar/preview/<int:job_id>/", AvatarPreviewStatusView.as_view(), name="avatar-preview-status"),
    path("users/<int:user_id>/avatar/preview/status/<int:job_id>/", AvatarPreviewStatusView.as_view(), name="avatar-preview-status-v2"),
    path("users/<int:user_id>/avatar/preview/delete/", AvatarPreviewDeleteView.as_view(), name="avatar-preview-delete"),
    path("projects/<int:project_id>/avatar-overlay/", AvatarOverlayPreferenceView.as_view(), name="avatar-overlay-preference"),

    # Router-managed CRUD
    path("", include(router.urls)),
]
