import os
import hashlib
import sys
import uuid
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import UserProfile, VoiceProfile  # noqa: E402

pytestmark = pytest.mark.django_db


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def test_avatar_prepare_returns_setup_not_prepared_when_requirements_missing(monkeypatch):
    if not _table_has_column("core_userprofile", "avatar_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_prepare_missing_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher", avatar_enabled=False, avatar_consent_confirmed=False)

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/prepare/", {}, format="json")
    force_authenticate(request, user=user)

    response = views.AvatarPrepareView.as_view()(request, user_id=user.id)

    assert response.status_code == 400
    assert response.data.get("error_code") == "setup_not_prepared"
    missing = response.data.get("missing_requirements") or []
    assert "missing_avatar_image_original" in missing
    assert "missing_voice_profile" in missing


def test_avatar_prepare_marks_ready_when_assets_exist(monkeypatch):
    if not _table_has_column("core_userprofile", "avatar_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")
    if not _table_has_column("core_userprofile", "avatar_source_valid"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_prepare_ready_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/7/hash/original.png",
        avatar_image_processed="avatars/7/hash/processed.png",
    )
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_prepare_{suffix}")

    storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
    original_abs = storage_root / profile.avatar_image_original
    processed_abs = storage_root / profile.avatar_image_processed
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    original_abs.write_bytes(b"original")
    processed_abs.write_bytes(b"processed")
    processed_hash = hashlib.sha256(b"processed").hexdigest()

    def fake_refresh_avatar_source_validation(profile, **_kwargs):
        profile.avatar_source_valid = True
        profile.avatar_source_validation_error = ""
        profile.avatar_source_hash = processed_hash
        profile.avatar_source_image_hash = processed_hash
        profile.avatar_source_reference_type = "image"
        profile.avatar_preview_stale = False
        profile.save(
            update_fields=[
                "avatar_source_valid",
                "avatar_source_validation_error",
                "avatar_source_hash",
                "avatar_source_image_hash",
                "avatar_source_reference_type",
                "avatar_preview_stale",
                "updated_at",
            ]
        )
        return {"valid": True, "source_hash": processed_hash, "reference_type": "image"}

    monkeypatch.setattr(views, "refresh_avatar_source_validation", fake_refresh_avatar_source_validation)

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/prepare/", {}, format="json")
    force_authenticate(request, user=user)

    response = views.AvatarPrepareView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    assert response.data.get("status") == "avatar_ready"
    assert bool((response.data.get("readiness") or {}).get("ready")) is True
    assert response.data["normalized_engine"] == "liveportrait+musetalk"
    assert response.data["avatar_engine_selected"] == "liveportrait+musetalk"
