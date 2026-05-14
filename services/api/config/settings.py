"""
Django settings for AI_ACADEMY API.

Reads configuration from environment variables; falls back to SQLite
for local development when DATABASE_URL is not set.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"
DEV_MEDIA_TOKEN_SECRET = "media-token-dev-secret-change-in-prod"


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_from(env, name: str, default: bool = False) -> bool:
    raw_value = env.get(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw_value: str | None) -> list[str]:
    if raw_value is None:
        return []
    return [item.strip() for item in str(raw_value).split(",") if item.strip()]


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return list(default or [])
    return _split_csv(raw_value)


def validate_production_settings(
    *,
    env=None,
    debug: bool | None = None,
    secret_key: str | None = None,
    postgres_host: str | None = None,
    media_token_secret: str | None = None,
    allowed_hosts: list[str] | None = None,
    cors_allowed_origins: list[str] | None = None,
    cors_allow_all_origins: bool | None = None,
) -> None:
    """Fail fast for configuration that is unsafe when DEBUG=False."""

    env = os.environ if env is None else env
    resolved_debug = DEBUG if debug is None else bool(debug)
    if resolved_debug:
        return

    resolved_secret_key = str(
        secret_key if secret_key is not None else env.get("SECRET_KEY", SECRET_KEY)
    ).strip()
    resolved_postgres_host = str(
        postgres_host if postgres_host is not None else env.get("POSTGRES_HOST", _pg_host)
    ).strip()
    resolved_media_token_secret = str(
        media_token_secret
        if media_token_secret is not None
        else env.get("MEDIA_TOKEN_SECRET", MEDIA_TOKEN_SECRET)
    ).strip()
    resolved_allowed_hosts = list(
        allowed_hosts if allowed_hosts is not None else _split_csv(env.get("ALLOWED_HOSTS"))
    )
    resolved_cors_origins = list(
        cors_allowed_origins
        if cors_allowed_origins is not None
        else _split_csv(env.get("CORS_ALLOWED_ORIGINS"))
    )
    requested_cors_allow_all = _env_bool_from(env, "CORS_ALLOW_ALL_ORIGINS", default=False)
    resolved_cors_allow_all = bool(cors_allow_all_origins) if cors_allow_all_origins is not None else False

    errors: list[str] = []
    if not resolved_secret_key or resolved_secret_key == DEV_SECRET_KEY:
        errors.append("SECRET_KEY must be set to a non-development value when DEBUG=False.")
    if not resolved_postgres_host:
        errors.append("POSTGRES_HOST must be set when DEBUG=False; production cannot fall back to SQLite.")
    if not resolved_media_token_secret or resolved_media_token_secret == DEV_MEDIA_TOKEN_SECRET:
        errors.append("MEDIA_TOKEN_SECRET must be set to a non-development value when DEBUG=False.")
    if not env.get("ALLOWED_HOSTS") or not resolved_allowed_hosts:
        errors.append("ALLOWED_HOSTS must be explicitly set when DEBUG=False.")
    if "*" in resolved_allowed_hosts and not (
        _env_bool_from(env, "ALLOW_WILDCARD_HOSTS", default=False)
        or _env_bool_from(env, "DJANGO_ALLOW_WILDCARD_HOSTS", default=False)
    ):
        errors.append("ALLOWED_HOSTS='*' is not allowed when DEBUG=False unless ALLOW_WILDCARD_HOSTS=true.")
    if requested_cors_allow_all or resolved_cors_allow_all:
        errors.append("CORS_ALLOW_ALL_ORIGINS must not be true when DEBUG=False.")
    if not env.get("CORS_ALLOWED_ORIGINS") or not resolved_cors_origins:
        errors.append("CORS_ALLOWED_ORIGINS must be explicitly set when DEBUG=False.")

    if errors:
        raise ImproperlyConfigured("Invalid production settings: " + " ".join(errors))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core settings
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
DEBUG = _env_bool("DEBUG", default=True)
ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")
CORS_ALLOWED_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS")
_CORS_ALLOW_ALL_ORIGINS_REQUESTED = _env_bool("CORS_ALLOW_ALL_ORIGINS", default=DEBUG)
CORS_ALLOW_ALL_ORIGINS = bool(DEBUG and _CORS_ALLOW_ALL_ORIGINS_REQUESTED)

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
    "ai_agents",
]

MIDDLEWARE = [
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

# ---------------------------------------------------------------------------
# Database – reads individual POSTGRES_* env vars (Docker env_file does NOT
# expand ${VAR} shell references, so DATABASE_URL construction is done here).
# Falls back to SQLite when POSTGRES_HOST is not set (plain local dev).
# ---------------------------------------------------------------------------
_pg_host = os.environ.get("POSTGRES_HOST")

if _pg_host:
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
else:
    # SQLite fallback – used when running `python manage.py runserver` locally
    # Docker test runs can mount source read-only, so allow the pytest database
    # file to live in a writable temp directory without changing runtime DBs.
    _sqlite_test_name = os.environ.get("SQLITE_TEST_DATABASE_PATH")
    _sqlite_options = {}
    if _sqlite_test_name:
        _sqlite_options["TEST"] = {"NAME": _sqlite_test_name}

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            **_sqlite_options,
        }
    }

# ---------------------------------------------------------------------------
# Cache / Celery
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_RENDER_QUEUE = os.environ.get("CELERY_RENDER_QUEUE", "render")
CELERY_AVATAR_QUEUE = os.environ.get("CELERY_AVATAR_QUEUE", "avatar")

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
_STATICFILES_BACKEND = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG or _env_bool("DJANGO_DISABLE_STATIC_COMPRESSION", default=False)
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": _STATICFILES_BACKEND,
    },
}

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
    "MEDIA_TOKEN_SECRET", DEV_MEDIA_TOKEN_SECRET
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
SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", default=not DEBUG)
SESSION_COOKIE_SECURE = True if not DEBUG else _env_bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = True if not DEBUG else _env_bool("CSRF_COOKIE_SECURE", default=False)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000" if not DEBUG else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True if not DEBUG else _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = True if not DEBUG else _env_bool("SECURE_HSTS_PRELOAD", default=False)
SECURE_REFERRER_POLICY = os.environ.get("SECURE_REFERRER_POLICY", "same-origin")
X_FRAME_OPTIONS = os.environ.get("X_FRAME_OPTIONS", "DENY")

validate_production_settings(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    postgres_host=_pg_host,
    media_token_secret=MEDIA_TOKEN_SECRET,
    allowed_hosts=ALLOWED_HOSTS,
    cors_allowed_origins=CORS_ALLOWED_ORIGINS,
    cors_allow_all_origins=CORS_ALLOW_ALL_ORIGINS,
)

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
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434").strip().rstrip("/")
OLLAMA_PRONUNCIATION_MODEL = os.environ.get("OLLAMA_PRONUNCIATION_MODEL", "llama3.1:8b").strip()
TTS_LLM_SUGGESTION_TIMEOUT_SECONDS = float(os.environ.get("TTS_LLM_SUGGESTION_TIMEOUT_SECONDS", "8"))
TTS_LLM_MAX_TERMS = int(os.environ.get("TTS_LLM_MAX_TERMS", "20"))
TTS_LLM_CONTEXT_MAX_CHARS = int(os.environ.get("TTS_LLM_CONTEXT_MAX_CHARS", "1000"))

# ---------------------------------------------------------------------------
# Subtitle translation providers. Enabled by default, but paid/external API
# providers are skipped unless explicit endpoint/key/provider settings are set.
# ---------------------------------------------------------------------------
SUBTITLE_TRANSLATION_ENABLED = os.environ.get("SUBTITLE_TRANSLATION_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
SUBTITLE_TRANSLATION_PROVIDER = os.environ.get("SUBTITLE_TRANSLATION_PROVIDER", "auto").strip().lower()
SUBTITLE_TRANSLATION_PROVIDER_CHAIN = os.environ.get("SUBTITLE_TRANSLATION_PROVIDER_CHAIN", "api,ollama,libretranslate,argos,mock").strip()
SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK = os.environ.get("SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK", "true").lower() in {
    "1", "true", "yes", "on"
}
SUBTITLE_TRANSLATION_TARGET_LANGUAGES = os.environ.get("SUBTITLE_TRANSLATION_TARGET_LANGUAGES", "").strip()
SUBTITLE_TRANSLATION_TIMEOUT_SECONDS = float(os.environ.get("SUBTITLE_TRANSLATION_TIMEOUT_SECONDS", "20"))
SUBTITLE_TRANSLATION_API_PROVIDER = os.environ.get("SUBTITLE_TRANSLATION_API_PROVIDER", "").strip()
SUBTITLE_TRANSLATION_API_BASE_URL = os.environ.get("SUBTITLE_TRANSLATION_API_BASE_URL", "").strip()
SUBTITLE_TRANSLATION_API_KEY = os.environ.get("SUBTITLE_TRANSLATION_API_KEY", "").strip()
SUBTITLE_TRANSLATION_API_MODEL = os.environ.get("SUBTITLE_TRANSLATION_API_MODEL", "").strip()
SUBTITLE_PUBLIC_REQUESTS_ENABLED = os.environ.get("SUBTITLE_PUBLIC_REQUESTS_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST = os.environ.get(
    "SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST",
    "en,ar,tr,fr,de,es,it,pt,ru,zh,ja,ko,hi,ur,id,fa",
).strip()
SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR = int(os.environ.get("SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR", "10"))
SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR = int(
    os.environ.get("SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR", "5")
)
SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS = int(os.environ.get("SUBTITLE_PUBLIC_REQUEST_LOCK_SECONDS", "300"))
SUBTITLE_PUBLIC_REQUEST_MAX_ACTIVE_PER_PROJECT = int(os.environ.get("SUBTITLE_PUBLIC_REQUEST_MAX_ACTIVE_PER_PROJECT", "3"))
SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK = os.environ.get(
    "SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK",
    "true" if DEBUG else "false",
).lower() in {"1", "true", "yes", "on"}
OLLAMA_TRANSLATION_ENABLED = os.environ.get("OLLAMA_TRANSLATION_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
OLLAMA_TRANSLATION_BASE_URL = os.environ.get(
    "OLLAMA_TRANSLATION_BASE_URL",
    os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
).strip().rstrip("/")
OLLAMA_TRANSLATION_MODEL = os.environ.get("OLLAMA_TRANSLATION_MODEL", "qwen2.5:7b-instruct").strip()
OLLAMA_TRANSLATION_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TRANSLATION_TIMEOUT_SECONDS", "60"))
OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH = int(os.environ.get("OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH", "40"))
OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH = int(os.environ.get("OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH", "6000"))
LIBRETRANSLATE_BASE_URL = os.environ.get("LIBRETRANSLATE_BASE_URL", "http://localhost:5000").strip().rstrip("/")
LIBRETRANSLATE_API_KEY = os.environ.get("LIBRETRANSLATE_API_KEY", "").strip()
ARGOS_TRANSLATE_ENABLED = os.environ.get("ARGOS_TRANSLATE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
ARGOS_TRANSLATE_PACKAGES_DIR = os.environ.get("ARGOS_TRANSLATE_PACKAGES_DIR", "").strip()
ARGOS_TRANSLATE_AUTO_INSTALL = os.environ.get("ARGOS_TRANSLATE_AUTO_INSTALL", "false").lower() in {
    "1", "true", "yes", "on"
}

# Optional moderation LLM provider (local Ollama only)
# ---------------------------------------------------------------------------
AI_AGENTS_LOCAL_LLM_ENABLED = os.environ.get("AI_AGENTS_LOCAL_LLM_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
AI_AGENTS_OLLAMA_BASE_URL = os.environ.get("AI_AGENTS_OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
AI_AGENTS_TEXT_MODEL = os.environ.get("AI_AGENTS_TEXT_MODEL", "qwen2.5:7b-instruct").strip()
AI_AGENTS_LLM_TIMEOUT_SECONDS = float(os.environ.get("AI_AGENTS_LLM_TIMEOUT_SECONDS", "8"))

# Optional translation-to-English moderation bridge. Disabled by default and
# advisory-only; local language rules remain the primary moderation path.
TRANSLATION_MODERATION_ENABLED = os.environ.get("TRANSLATION_MODERATION_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
TRANSLATION_MODERATION_PROVIDER = os.environ.get("TRANSLATION_MODERATION_PROVIDER", "none").strip().lower()
TRANSLATION_MODERATION_TIMEOUT_SECONDS = float(os.environ.get("TRANSLATION_MODERATION_TIMEOUT_SECONDS", "20"))
TRANSLATION_MODERATION_TARGET_LANGUAGE = os.environ.get("TRANSLATION_MODERATION_TARGET_LANGUAGE", "en").strip().lower()
TRANSLATION_MODERATION_BASE_URL = os.environ.get(
    "TRANSLATION_MODERATION_BASE_URL",
    "http://libretranslate:5000",
).strip().rstrip("/")

# ---------------------------------------------------------------------------
# Feature-flagged source moderation automation
# ---------------------------------------------------------------------------
SOURCE_MODERATION_AUTO_ENABLED = os.environ.get("SOURCE_MODERATION_AUTO_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION = os.environ.get(
    "SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION",
    "true",
).lower() in {"1", "true", "yes", "on"}
SOURCE_MODERATION_PHASE = os.environ.get("SOURCE_MODERATION_PHASE", "source_scan").strip() or "source_scan"

# ---------------------------------------------------------------------------
# Feature-flagged local visual asset moderation automation
# ---------------------------------------------------------------------------
VISUAL_MODERATION_AUTO_ENABLED = os.environ.get("VISUAL_MODERATION_AUTO_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION = os.environ.get(
    "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION",
    "false",
).lower() in {"1", "true", "yes", "on"}
VISUAL_MODERATION_PHASE = os.environ.get("VISUAL_MODERATION_PHASE", "visual_asset_scan").strip() or "visual_asset_scan"
VISUAL_MODERATION_SCAN_COVER = os.environ.get("VISUAL_MODERATION_SCAN_COVER", "true").lower() in {
    "1", "true", "yes", "on"
}
VISUAL_MODERATION_SCAN_SLIDES = os.environ.get("VISUAL_MODERATION_SCAN_SLIDES", "true").lower() in {
    "1", "true", "yes", "on"
}
VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = os.environ.get(
    "VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION",
    "false",
).lower() in {"1", "true", "yes", "on"}

VISUAL_SAFETY_PROVIDER = os.environ.get("VISUAL_SAFETY_PROVIDER", "none").strip().lower() or "none"
VISUAL_SAFETY_CLASSIFIER_ENABLED = os.environ.get("VISUAL_SAFETY_CLASSIFIER_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
VISUAL_SAFETY_TIMEOUT_SECONDS = float(os.environ.get("VISUAL_SAFETY_TIMEOUT_SECONDS", "20") or "20")
VISUAL_SAFETY_MAX_IMAGE_BYTES = int(os.environ.get("VISUAL_SAFETY_MAX_IMAGE_BYTES", "10485760") or "10485760")

AZURE_CONTENT_SAFETY_ENABLED = os.environ.get("AZURE_CONTENT_SAFETY_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
AZURE_CONTENT_SAFETY_ENDPOINT = os.environ.get("AZURE_CONTENT_SAFETY_ENDPOINT", "").strip().rstrip("/")
AZURE_CONTENT_SAFETY_KEY = os.environ.get("AZURE_CONTENT_SAFETY_KEY", "").strip()
AZURE_CONTENT_SAFETY_API_VERSION = os.environ.get("AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01").strip() or "2024-09-01"
AZURE_CONTENT_SAFETY_CATEGORIES = os.environ.get(
    "AZURE_CONTENT_SAFETY_CATEGORIES",
    "sexual,violence,self_harm,hate",
).strip() or "sexual,violence,self_harm,hate"
AZURE_CONTENT_SAFETY_BLOCK_SEVERITY = int(os.environ.get("AZURE_CONTENT_SAFETY_BLOCK_SEVERITY", "4") or "4")

AVATAR_IMAGE_MODERATION_AUTO_ENABLED = os.environ.get(
    "AVATAR_IMAGE_MODERATION_AUTO_ENABLED",
    "false",
).lower() in {"1", "true", "yes", "on"}
AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION = os.environ.get(
    "AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION",
    "true",
).lower() in {"1", "true", "yes", "on"}
AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL = os.environ.get(
    "AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL",
    "false",
).lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Feature-flagged local OCR slide moderation automation
# ---------------------------------------------------------------------------
OCR_MODERATION_AUTO_ENABLED = os.environ.get("OCR_MODERATION_AUTO_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
OCR_MODERATION_BLOCK_RENDER_ON_REJECTION = os.environ.get(
    "OCR_MODERATION_BLOCK_RENDER_ON_REJECTION",
    "false",
).lower() in {"1", "true", "yes", "on"}
OCR_MODERATION_PHASE = os.environ.get("OCR_MODERATION_PHASE", "ocr_slide_scan").strip() or "ocr_slide_scan"
OCR_MODERATION_SCAN_SLIDES = os.environ.get("OCR_MODERATION_SCAN_SLIDES", "true").lower() in {
    "1", "true", "yes", "on"
}
OCR_MODERATION_PROVIDER = os.environ.get("OCR_MODERATION_PROVIDER", "noop").strip().lower() or "noop"

AZURE_OCR_ENABLED = os.environ.get("AZURE_OCR_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
AZURE_OCR_ENDPOINT = os.environ.get("AZURE_OCR_ENDPOINT", "").strip().rstrip("/")
AZURE_OCR_KEY = os.environ.get("AZURE_OCR_KEY", "").strip()
AZURE_OCR_API_VERSION = os.environ.get("AZURE_OCR_API_VERSION", "2024-02-29-preview").strip()
AZURE_OCR_MODEL = os.environ.get("AZURE_OCR_MODEL", "prebuilt-read").strip() or "prebuilt-read"
AZURE_OCR_TIMEOUT_SECONDS = float(os.environ.get("AZURE_OCR_TIMEOUT_SECONDS", "30"))
AZURE_OCR_MAX_IMAGE_BYTES = int(os.environ.get("AZURE_OCR_MAX_IMAGE_BYTES", "10485760"))
AZURE_OCR_LANG_HINTS = os.environ.get("AZURE_OCR_LANG_HINTS", "en,tr,ar").strip()

# ---------------------------------------------------------------------------
# Feature-flagged post-render video frame moderation audit
# ---------------------------------------------------------------------------
VIDEO_FRAME_AUDIT_AUTO_ENABLED = os.environ.get("VIDEO_FRAME_AUDIT_AUTO_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
VIDEO_FRAME_AUDIT_PHASE = os.environ.get("VIDEO_FRAME_AUDIT_PHASE", "video_frame_audit").strip() or "video_frame_audit"
VIDEO_FRAME_AUDIT_EVERY_SECONDS = float(os.environ.get("VIDEO_FRAME_AUDIT_EVERY_SECONDS", "10"))
VIDEO_FRAME_AUDIT_MAX_FRAMES = int(os.environ.get("VIDEO_FRAME_AUDIT_MAX_FRAMES", "5"))
VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK = os.environ.get("VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK", "true").lower() in {
    "1", "true", "yes", "on"
}
VIDEO_FRAME_AUDIT_RUN_OCR = os.environ.get("VIDEO_FRAME_AUDIT_RUN_OCR", "false").lower() in {
    "1", "true", "yes", "on"
}
VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = os.environ.get(
    "VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION",
    "false",
).lower() in {"1", "true", "yes", "on"}
VIDEO_FRAME_AUDIT_RETAIN_FRAMES = os.environ.get("VIDEO_FRAME_AUDIT_RETAIN_FRAMES", "false").lower() in {
    "1", "true", "yes", "on"
}
VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS = int(os.environ.get("VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS", "7"))
VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS = os.environ.get(
    "VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS",
    "true",
).lower() in {"1", "true", "yes", "on"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
