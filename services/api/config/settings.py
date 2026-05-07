"""
Django settings for AI_ACADEMY API.

Reads configuration from environment variables.
PostgreSQL is mandatory; SQLite fallback is disabled.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core settings
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-key-change-me")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(",")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    # Project apps
    "core",
]

MIDDLEWARE = [
    "core.middleware.StructuredRequestLoggingMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.PlaybackSecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# dev only: allow all origins (NOT SECURE for production)
CORS_ALLOW_ALL_ORIGINS = True

# ---------------------------------------------------------------------------
# Database - reads individual POSTGRES_* env vars (Docker env_file does NOT
# expand ${VAR} shell references, so DATABASE_URL construction is done here).
# PostgreSQL is mandatory in this repository. SQLite is disabled.
# ---------------------------------------------------------------------------
_pg_host = os.environ.get("POSTGRES_HOST")

if not _pg_host:
    raise RuntimeError(
        "POSTGRES_HOST is required. SQLite fallback is disabled; use Docker Postgres settings."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "academy_db"),
        "USER": os.environ.get("POSTGRES_USER", "academy_user"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": _pg_host,
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
# ---------------------------------------------------------------------------
# Cache / Celery
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_RENDER_QUEUE = os.environ.get("CELERY_RENDER_QUEUE", "render")
CELERY_RENDER_FAST_QUEUE = os.environ.get("CELERY_RENDER_FAST_QUEUE", "render_fast")
CELERY_RENDER_QUALITY_QUEUE = os.environ.get("CELERY_RENDER_QUALITY_QUEUE", "render_quality")
CELERY_AVATAR_QUEUE = os.environ.get("CELERY_AVATAR_QUEUE", "avatar")
RENDER_ADMISSION_QUALITY_QUEUE_LIMIT = int(os.environ.get("RENDER_ADMISSION_QUALITY_QUEUE_LIMIT", "25"))
RENDER_ADMISSION_FAST_QUEUE_LIMIT = int(os.environ.get("RENDER_ADMISSION_FAST_QUEUE_LIMIT", "80"))
RENDER_ADMISSION_BALANCED_QUEUE_LIMIT = int(os.environ.get("RENDER_ADMISSION_BALANCED_QUEUE_LIMIT", "120"))
RENDER_ADMISSION_AVATAR_QUEUE_LIMIT = int(os.environ.get("RENDER_ADMISSION_AVATAR_QUEUE_LIMIT", "20"))
RENDER_ETA_SECONDS_FAST = int(os.environ.get("RENDER_ETA_SECONDS_FAST", "45"))
RENDER_ETA_SECONDS_BALANCED = int(os.environ.get("RENDER_ETA_SECONDS_BALANCED", "120"))
RENDER_ETA_SECONDS_QUALITY = int(os.environ.get("RENDER_ETA_SECONDS_QUALITY", "240"))
RENDER_ETA_SECONDS_AVATAR = int(os.environ.get("RENDER_ETA_SECONDS_AVATAR", "360"))

# Autoscale policy thresholds (queue depth + p95 latency).
# These are recommendation thresholds for orchestration layers (KEDA/HPA/worker manager).
AUTOSCALE_FAST_QUEUE_DEPTH_UP = int(os.environ.get("AUTOSCALE_FAST_QUEUE_DEPTH_UP", "12"))
AUTOSCALE_FAST_QUEUE_DEPTH_DOWN = int(os.environ.get("AUTOSCALE_FAST_QUEUE_DEPTH_DOWN", "2"))
AUTOSCALE_FAST_P95_UP_SECONDS = int(os.environ.get("AUTOSCALE_FAST_P95_UP_SECONDS", "90"))
AUTOSCALE_FAST_P95_DOWN_SECONDS = int(os.environ.get("AUTOSCALE_FAST_P95_DOWN_SECONDS", "35"))
AUTOSCALE_FAST_MIN_REPLICAS = int(os.environ.get("AUTOSCALE_FAST_MIN_REPLICAS", "2"))
AUTOSCALE_FAST_MAX_REPLICAS = int(os.environ.get("AUTOSCALE_FAST_MAX_REPLICAS", "24"))

AUTOSCALE_BALANCED_QUEUE_DEPTH_UP = int(os.environ.get("AUTOSCALE_BALANCED_QUEUE_DEPTH_UP", "10"))
AUTOSCALE_BALANCED_QUEUE_DEPTH_DOWN = int(os.environ.get("AUTOSCALE_BALANCED_QUEUE_DEPTH_DOWN", "2"))
AUTOSCALE_BALANCED_P95_UP_SECONDS = int(os.environ.get("AUTOSCALE_BALANCED_P95_UP_SECONDS", "180"))
AUTOSCALE_BALANCED_P95_DOWN_SECONDS = int(os.environ.get("AUTOSCALE_BALANCED_P95_DOWN_SECONDS", "90"))
AUTOSCALE_BALANCED_MIN_REPLICAS = int(os.environ.get("AUTOSCALE_BALANCED_MIN_REPLICAS", "1"))
AUTOSCALE_BALANCED_MAX_REPLICAS = int(os.environ.get("AUTOSCALE_BALANCED_MAX_REPLICAS", "16"))

AUTOSCALE_QUALITY_QUEUE_DEPTH_UP = int(os.environ.get("AUTOSCALE_QUALITY_QUEUE_DEPTH_UP", "6"))
AUTOSCALE_QUALITY_QUEUE_DEPTH_DOWN = int(os.environ.get("AUTOSCALE_QUALITY_QUEUE_DEPTH_DOWN", "1"))
AUTOSCALE_QUALITY_P95_UP_SECONDS = int(os.environ.get("AUTOSCALE_QUALITY_P95_UP_SECONDS", "320"))
AUTOSCALE_QUALITY_P95_DOWN_SECONDS = int(os.environ.get("AUTOSCALE_QUALITY_P95_DOWN_SECONDS", "200"))
AUTOSCALE_QUALITY_MIN_REPLICAS = int(os.environ.get("AUTOSCALE_QUALITY_MIN_REPLICAS", "1"))
AUTOSCALE_QUALITY_MAX_REPLICAS = int(os.environ.get("AUTOSCALE_QUALITY_MAX_REPLICAS", "10"))

AUTOSCALE_AVATAR_QUEUE_DEPTH_UP = int(os.environ.get("AUTOSCALE_AVATAR_QUEUE_DEPTH_UP", "4"))
AUTOSCALE_AVATAR_QUEUE_DEPTH_DOWN = int(os.environ.get("AUTOSCALE_AVATAR_QUEUE_DEPTH_DOWN", "0"))
AUTOSCALE_AVATAR_P95_UP_SECONDS = int(os.environ.get("AUTOSCALE_AVATAR_P95_UP_SECONDS", "600"))
AUTOSCALE_AVATAR_P95_DOWN_SECONDS = int(os.environ.get("AUTOSCALE_AVATAR_P95_DOWN_SECONDS", "360"))
AUTOSCALE_AVATAR_MIN_REPLICAS = int(os.environ.get("AUTOSCALE_AVATAR_MIN_REPLICAS", "1"))
AUTOSCALE_AVATAR_MAX_REPLICAS = int(os.environ.get("AUTOSCALE_AVATAR_MAX_REPLICAS", "8"))

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ---------------------------------------------------------------------------
# Storage root for lesson uploads and rendered output
# ---------------------------------------------------------------------------
STORAGE_ROOT = os.environ.get(
    "STORAGE_ROOT",
    str(BASE_DIR.parent.parent / "storage_local"),
)

# Optional public API origin used for absolute URL generation when request
# context is unavailable in serializers.
API_PUBLIC_BASE_URL = os.environ.get("API_PUBLIC_BASE_URL", "").strip().rstrip("/")

# ---------------------------------------------------------------------------
# Media token settings (secure video streaming)
# ---------------------------------------------------------------------------
# Secret used to HMAC-sign short-lived media playback tokens.
# Override via env var in production.
MEDIA_TOKEN_SECRET = os.environ.get(
    "MEDIA_TOKEN_SECRET", "media-token-dev-secret-change-in-prod"
)
# How long (seconds) a playback token is valid. Default 4 hours.
MEDIA_TOKEN_TTL_SECONDS = int(os.environ.get("MEDIA_TOKEN_TTL_SECONDS", "14400"))

# ---------------------------------------------------------------------------
# Lecture playback protection settings
# ---------------------------------------------------------------------------
LECTURE_WATERMARK_ENABLED = os.environ.get("LECTURE_WATERMARK_ENABLED", "1").lower() in {
    "1", "true", "yes", "on"
}
LECTURE_VISIBILITY_LOCK_ENABLED = os.environ.get("LECTURE_VISIBILITY_LOCK_ENABLED", "1").lower() in {
    "1", "true", "yes", "on"
}
DRM_ENABLED = os.environ.get("DRM_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
# External DRM provider metadata. Only non-secret player initialization values
# should be exposed from these settings to the browser.
DRM_PROVIDER_NAME = os.environ.get("DRM_PROVIDER_NAME", "external")
DRM_PREFERRED_SYSTEM = os.environ.get("DRM_PREFERRED_SYSTEM", "").strip().lower()
DRM_KEY_SYSTEM = os.environ.get("DRM_KEY_SYSTEM", "")
DRM_LICENSE_URL = os.environ.get("DRM_LICENSE_URL", "")
DRM_CERTIFICATE_URL = os.environ.get("DRM_CERTIFICATE_URL", "")
DRM_ASSET_ID_PREFIX = os.environ.get("DRM_ASSET_ID_PREFIX", "lesson-")
DRM_CONTENT_ID_PREFIX = os.environ.get("DRM_CONTENT_ID_PREFIX", "project-")
DRM_PLAYBACK_SESSION_PREFIX = os.environ.get("DRM_PLAYBACK_SESSION_PREFIX", "playback")
DRM_WIDEVINE_ENABLED = os.environ.get("DRM_WIDEVINE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DRM_WIDEVINE_KEY_SYSTEM = os.environ.get("DRM_WIDEVINE_KEY_SYSTEM", "com.widevine.alpha")
DRM_WIDEVINE_LICENSE_URL = os.environ.get("DRM_WIDEVINE_LICENSE_URL", "")
DRM_WIDEVINE_CERTIFICATE_URL = os.environ.get("DRM_WIDEVINE_CERTIFICATE_URL", "")
DRM_WIDEVINE_CONTENT_TYPE = os.environ.get("DRM_WIDEVINE_CONTENT_TYPE", "video/mp4")
DRM_PLAYREADY_ENABLED = os.environ.get("DRM_PLAYREADY_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DRM_PLAYREADY_KEY_SYSTEM = os.environ.get("DRM_PLAYREADY_KEY_SYSTEM", "com.microsoft.playready")
DRM_PLAYREADY_LICENSE_URL = os.environ.get("DRM_PLAYREADY_LICENSE_URL", "")
DRM_PLAYREADY_CERTIFICATE_URL = os.environ.get("DRM_PLAYREADY_CERTIFICATE_URL", "")
DRM_PLAYREADY_CONTENT_TYPE = os.environ.get("DRM_PLAYREADY_CONTENT_TYPE", "video/mp4")
DRM_FAIRPLAY_ENABLED = os.environ.get("DRM_FAIRPLAY_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DRM_FAIRPLAY_KEY_SYSTEM = os.environ.get("DRM_FAIRPLAY_KEY_SYSTEM", "com.apple.fps.1_0")
DRM_FAIRPLAY_LICENSE_URL = os.environ.get("DRM_FAIRPLAY_LICENSE_URL", "")
DRM_FAIRPLAY_CERTIFICATE_URL = os.environ.get("DRM_FAIRPLAY_CERTIFICATE_URL", "")
DRM_FAIRPLAY_CONTENT_TYPE = os.environ.get("DRM_FAIRPLAY_CONTENT_TYPE", "application/vnd.apple.mpegurl")
DRM_STREAMING_ENABLED = os.environ.get("DRM_STREAMING_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
DRM_HLS_ENCRYPTION_ENABLED = os.environ.get("DRM_HLS_ENCRYPTION_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DRM_HLS_KEY_ROTATION_SECONDS = int(os.environ.get("DRM_HLS_KEY_ROTATION_SECONDS", "0"))
PROMETHEUS_METRICS_TOKEN = os.environ.get("PROMETHEUS_METRICS_TOKEN", "").strip()

# Lesson protection policy controls.
LESSON_PROTECTION_DEFAULT_MODE = os.environ.get("LESSON_PROTECTION_DEFAULT_MODE", "secure_stream")
LESSON_PROTECTION_ALLOW_MP4_FALLBACK = os.environ.get("LESSON_PROTECTION_ALLOW_MP4_FALLBACK", "1").lower() in {"1", "true", "yes", "on"}
LESSON_PROTECTION_FORCE_WATERMARK_FOR_PROTECTED = os.environ.get("LESSON_PROTECTION_FORCE_WATERMARK_FOR_PROTECTED", "1").lower() in {"1", "true", "yes", "on"}
LESSON_PROTECTION_TOKEN_TTL_PUBLIC_SECONDS = int(os.environ.get("LESSON_PROTECTION_TOKEN_TTL_PUBLIC_SECONDS", str(MEDIA_TOKEN_TTL_SECONDS)))
LESSON_PROTECTION_TOKEN_TTL_SECURE_SECONDS = int(os.environ.get("LESSON_PROTECTION_TOKEN_TTL_SECURE_SECONDS", str(MEDIA_TOKEN_TTL_SECONDS)))
LESSON_PROTECTION_TOKEN_TTL_DRM_SECONDS = int(os.environ.get("LESSON_PROTECTION_TOKEN_TTL_DRM_SECONDS", "7200"))
LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM = os.environ.get("LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM", "1").lower() in {"1", "true", "yes", "on"}
LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM = os.environ.get("LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM", "1").lower() in {"1", "true", "yes", "on"}
LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION = os.environ.get("LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION", "1").lower() in {"1", "true", "yes", "on"}
LESSON_PROTECTION_CONCURRENCY_POLICY = os.environ.get("LESSON_PROTECTION_CONCURRENCY_POLICY", "deny_new").strip().lower()
LESSON_PROTECTION_MULTI_TAB_ENFORCEMENT = os.environ.get("LESSON_PROTECTION_MULTI_TAB_ENFORCEMENT", "1").lower() in {"1", "true", "yes", "on"}
LESSON_PROTECTION_INACTIVITY_TTL_SECONDS = int(os.environ.get("LESSON_PROTECTION_INACTIVITY_TTL_SECONDS", "2700"))
LESSON_PROTECTION_HIDDEN_GRACE_SECONDS = int(os.environ.get("LESSON_PROTECTION_HIDDEN_GRACE_SECONDS", "300"))
LESSON_PROTECTION_RISK_WINDOW_SECONDS = int(os.environ.get("LESSON_PROTECTION_RISK_WINDOW_SECONDS", "10"))
LESSON_PROTECTION_SEGMENT_BURST_THRESHOLD = int(os.environ.get("LESSON_PROTECTION_SEGMENT_BURST_THRESHOLD", "45"))
LESSON_PROTECTION_RISK_MEDIUM_THRESHOLD = int(os.environ.get("LESSON_PROTECTION_RISK_MEDIUM_THRESHOLD", "3"))
LESSON_PROTECTION_RISK_HIGH_THRESHOLD = int(os.environ.get("LESSON_PROTECTION_RISK_HIGH_THRESHOLD", "5"))

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# CORS (permissive in dev)
# ---------------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = DEBUG

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
GOOGLE_AUTH_ENABLED = os.environ.get("GOOGLE_AUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
GOOGLE_REDIRECT_SUCCESS_URL = os.environ.get("GOOGLE_REDIRECT_SUCCESS_URL", "").strip()

# ---------------------------------------------------------------------------
# Optional TTS LLM pronunciation suggestions (Studio assistance only)
# ---------------------------------------------------------------------------
TTS_LLM_SUGGESTIONS_ENABLED = os.environ.get("TTS_LLM_SUGGESTIONS_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
TTS_LLM_PROVIDER = os.environ.get("TTS_LLM_PROVIDER", "ollama").strip().lower()
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
OLLAMA_PRONUNCIATION_MODEL = os.environ.get("OLLAMA_PRONUNCIATION_MODEL", "llama3.1:8b").strip()
TTS_LLM_SUGGESTION_TIMEOUT_SECONDS = float(os.environ.get("TTS_LLM_SUGGESTION_TIMEOUT_SECONDS", "8"))
TTS_LLM_MAX_TERMS = int(os.environ.get("TTS_LLM_MAX_TERMS", "20"))
TTS_LLM_CONTEXT_MAX_CHARS = int(os.environ.get("TTS_LLM_CONTEXT_MAX_CHARS", "1000"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

