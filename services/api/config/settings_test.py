"""
Test settings for API integration/unit tests.

This module imports base settings but overrides runtime dependencies
so tests can run without external Postgres/Redis services.
"""

import os

# Base settings require POSTGRES_HOST to be present at import time.
os.environ.setdefault("POSTGRES_HOST", "localhost")

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "api-tests",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

