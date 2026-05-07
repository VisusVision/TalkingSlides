import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402
from core import views  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


@pytest.mark.django_db
def test_resolve_avatar_options_includes_composite_fallback_allowed_boolean(monkeypatch):
    if not _table_has_column("core_userprofile", "avatar_lipsync_engine"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_opts_{suffix}", password="pass")
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
    profile.role = "teacher"
    profile.avatar_enabled = True
    profile.avatar_consent_confirmed = True
    profile.avatar_image_processed = f"avatars/{user.id}/hash/processed.png"
    profile.save(
        update_fields=[
            "role",
            "avatar_enabled",
            "avatar_consent_confirmed",
            "avatar_image_processed",
        ]
    )

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_LESSON", "1")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_FALLBACK", "0")

    project = Project.objects.create(title="Avatar options", user=user)
    options = views._resolve_avatar_options_for_project(project, SimpleNamespace(data={}))

    assert "composite_fallback_allowed" in options
    assert isinstance(options["composite_fallback_allowed"], bool)
    assert options["composite_fallback_allowed"] is False


def test_composite_fallback_allowed_requires_runtime_lesson_and_feature_flags(monkeypatch):
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_LESSON", "1")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_FALLBACK", "1")
    assert views._composite_fallback_allowed() is True

    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_FALLBACK", "0")
    assert views._composite_fallback_allowed() is False

    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_FALLBACK", "1")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_LESSON", "0")
    assert views._composite_fallback_allowed() is False

    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_LESSON", "1")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "")
    assert views._composite_fallback_allowed() is False
