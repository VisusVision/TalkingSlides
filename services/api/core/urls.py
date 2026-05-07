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
  AvatarPreviewStatusView,
  AvatarPreviewDeleteView,
  AvatarOverlayPreferenceView,
  AvatarCompatProfileView,
  AvatarCompatUploadView,
  AvatarCompatPreviewView,
  AvatarCompatPreviewStatusView,
  AvatarCompatReadinessView,
  AvatarProfileView,
  AuthProvidersView,
    CatalogDetailView,
  CatalogFeedView,
    CatalogListView,
    CategoryListView,
    GoogleLoginView,
    GoogleRedirectCallbackView,
    GoogleRedirectStartView,
    JobStatusView,
    JobCancelView,
    JobRetryView,
    JobEventsAuthTicketView,
    JobEventsStreamView,
    JobViewSet,
    LessonCommentsView,
    LessonLikeView,
    LessonProgressView,
    LoginView,
    LogoutView,
    MediaServeView,
    MediaStreamView,
    MeView,
    PlaybackSessionHeartbeatView,
    PlaybackTokenView,
    ProjectCoverImageView,
    ProjectDetailView,
    ProjectRerenderView,
    ProjectSlideImageView,
    ProjectTranscriptActionView,
    ProjectTranscriptView,
    ProjectUploadView,
    PrometheusMetricsView,
    SystemOrphanCleanupRunView,
    AutoscalePolicyView,
    RenderMetricsView,
    RenderCapacityView,
    SlideViewSet,
    TTSPreviewAudioView,
    TTSPreviewView,
    TTSPronunciationSuggestionsView,
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
    path("auth/providers/", AuthProvidersView.as_view(), name="auth-providers"),
    path("auth/google/", GoogleLoginView.as_view(), name="auth-google"),
    path("auth/google/redirect/start/", GoogleRedirectStartView.as_view(), name="auth-google-redirect-start"),
    path("auth/google/redirect/callback/", GoogleRedirectCallbackView.as_view(), name="auth-google-redirect-callback"),

    # Secure media streaming (token-gated, public)
    path("stream/<str:token>/", MediaStreamView.as_view(), name="media-stream"),

# Playback token issuance - no login required for published lessons
    path("projects/<int:project_id>/playback-token/", PlaybackTokenView.as_view(), name="playback-token"),
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
    path("catalog/<int:project_id>/", CatalogDetailView.as_view(), name="catalog-detail"),
    path("categories/", CategoryListView.as_view(), name="category-list"),

    # Admin analytics dashboard
    path("admin/stats/", AdminStatsDashboardView.as_view(), name="admin-stats-dashboard"),
    path("system/render-capacity/", RenderCapacityView.as_view(), name="render-capacity"),
    path("system/render-metrics/", RenderMetricsView.as_view(), name="render-metrics"),
    path("system/autoscale-policy/", AutoscalePolicyView.as_view(), name="autoscale-policy"),
    path("system/metrics/prometheus/", PrometheusMetricsView.as_view(), name="prometheus-metrics"),
    path("system/orphan-cleanup/run/", SystemOrphanCleanupRunView.as_view(), name="system-orphan-cleanup-run"),

    # Student social features
    path("catalog/<int:project_id>/like/", LessonLikeView.as_view(), name="lesson-like"),
    path("catalog/<int:project_id>/progress/", LessonProgressView.as_view(), name="lesson-progress"),
    path("catalog/<int:project_id>/comments/", LessonCommentsView.as_view(), name="lesson-comments"),

    # TTS preview (Phase 1 — no audio synthesis, no Celery)
    path("tts/preview/", TTSPreviewView.as_view(), name="tts-preview"),
    path("tts/preview-audio/", TTSPreviewAudioView.as_view(), name="tts-preview-audio"),
    path(
        "tts/pronunciation-suggestions/",
        TTSPronunciationSuggestionsView.as_view(),
        name="tts-pronunciation-suggestions",
    ),

    # Teacher pipeline
    path("projects/", ProjectUploadView.as_view(), name="project-upload"),
    path("projects/<int:project_id>/", ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<int:project_id>/transcript/", ProjectTranscriptView.as_view(), name="project-transcript"),
    path("projects/<int:project_id>/slides/<int:slide_index>/image/", ProjectSlideImageView.as_view(), name="project-slide-image"),
    path("projects/<int:project_id>/transcript/actions/", ProjectTranscriptActionView.as_view(), name="project-transcript-actions"),
    path("projects/<int:project_id>/rerender/", ProjectRerenderView.as_view(), name="project-rerender"),
    path("projects/<int:project_id>/jobs/<int:job_id>/", JobStatusView.as_view(), name="job-status"),
    path("projects/<int:project_id>/jobs/<int:job_id>/cancel/", JobCancelView.as_view(), name="job-cancel"),
    path("projects/<int:project_id>/jobs/<int:job_id>/retry/", JobRetryView.as_view(), name="job-retry"),
    path("projects/<int:project_id>/jobs/<int:job_id>/events/ticket/", JobEventsAuthTicketView.as_view(), name="job-status-events-ticket"),
    path("projects/<int:project_id>/jobs/<int:job_id>/events/", JobEventsStreamView.as_view(), name="job-status-events"),
    path("users/<int:user_id>/voice/", VoiceUploadView.as_view(), name="voice-upload"),
    path("users/<int:user_id>/avatar/", AvatarProfileView.as_view(), name="avatar-profile"),
    path("users/<int:user_id>/avatar/prepare/", AvatarPrepareView.as_view(), name="avatar-prepare"),
    path("users/<int:user_id>/avatar/preview/", AvatarPreviewRegenerateView.as_view(), name="avatar-preview"),
    path("users/<int:user_id>/avatar/preview/<int:job_id>/", AvatarPreviewStatusView.as_view(), name="avatar-preview-status"),
    path("users/<int:user_id>/avatar/preview/status/<int:job_id>/", AvatarPreviewStatusView.as_view(), name="avatar-preview-status-v2"),
    path("users/<int:user_id>/avatar/preview/delete/", AvatarPreviewDeleteView.as_view(), name="avatar-preview-delete"),
    path("projects/<int:project_id>/avatar-overlay/", AvatarOverlayPreferenceView.as_view(), name="avatar-overlay-preference"),
    path("avatar/profile", AvatarCompatProfileView.as_view(), name="avatar-compat-profile"),
    path("avatar/upload", AvatarCompatUploadView.as_view(), name="avatar-compat-upload"),
    path("avatar/preview", AvatarCompatPreviewView.as_view(), name="avatar-compat-preview"),
    path("avatar/preview/<int:job_id>", AvatarCompatPreviewStatusView.as_view(), name="avatar-compat-preview-status"),
    path("avatar/readiness", AvatarCompatReadinessView.as_view(), name="avatar-compat-readiness"),

    # Router-managed CRUD
    path("", include(router.urls)),
]
