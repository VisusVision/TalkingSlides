"""Real row-lock regression tests; never a production configuration."""
import os
from .settings_test import *  # noqa: F401,F403

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ.get("VISUAL_TEST_DB", "visual_drafts_test"),
    "USER": os.environ.get("VISUAL_TEST_DB_USER", "postgres"),
    "PASSWORD": os.environ.get("VISUAL_TEST_DB_PASSWORD", "postgres"),
    "HOST": os.environ.get("VISUAL_TEST_DB_HOST", "127.0.0.1"),
    "PORT": os.environ.get("VISUAL_TEST_DB_PORT", "5432"),
}}
