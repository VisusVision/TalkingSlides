import os
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

from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import UserProfile  # noqa: E402
from tests.integration.schema_skip import skip_if_column_missing  # noqa: E402

pytestmark = pytest.mark.django_db


def test_avatar_profile_patch_accepts_json_payload(monkeypatch):
    skip_if_column_missing("core_userprofile", "avatar_lipsync_engine")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_patch_json_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher", avatar_enabled=False, avatar_motion_preset="natural")

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/users/{user.id}/avatar/",
        {
            "avatar_enabled": True,
            "avatar_consent_confirmed": True,
            "avatar_motion_preset": "expressive",
            "avatar_lipsync_engine": "musetalk",
        },
        format="json",
    )
    force_authenticate(request, user=user)

    response = views.AvatarProfileView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    assert response.data["status"] == "updated"

    profile = UserProfile.objects.get(user=user)
    assert profile.avatar_enabled is True
    assert profile.avatar_consent_confirmed is True
    assert profile.avatar_motion_preset == "expressive"
    assert profile.avatar_lipsync_engine == "musetalk"
    assert "avatar_setup_status" in response.data
    assert response.data["action_required"] == response.data["avatar_setup_status"]["action_required"]


def test_avatar_profile_patch_persists_consent_and_returns_setup_status(monkeypatch):
    skip_if_column_missing("core_userprofile", "avatar_consent_confirmed")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_patch_consent_{suffix}", password="pass")
    UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=False,
        avatar_consent_confirmed=False,
        avatar_motion_preset="natural",
    )

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/users/{user.id}/avatar/",
        {
            "avatar_enabled": True,
            "avatar_consent_confirmed": True,
            "avatar_lipsync_engine": "musetalk",
        },
        format="json",
    )
    force_authenticate(request, user=user)

    response = views.AvatarProfileView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    profile = UserProfile.objects.get(user=user)
    assert profile.avatar_consent_confirmed is True
    assert profile.avatar_enabled is True
    assert response.data["avatar_setup_status"]["state"] == "missing_portrait"
    assert response.data["action_required"] == "upload_portrait"


def test_avatar_profile_patch_accepts_text_plain_json_payload(monkeypatch):
    skip_if_column_missing("core_userprofile", "avatar_lipsync_engine")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_patch_plain_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher", avatar_enabled=False, avatar_motion_preset="natural")

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/users/{user.id}/avatar/",
        '{"avatar_enabled": true, "avatar_consent_confirmed": true, "avatar_motion_preset": "natural", "avatar_lipsync_engine": "musetalk"}',
        content_type="text/plain",
    )
    force_authenticate(request, user=user)

    response = views.AvatarProfileView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    assert response.data["status"] == "updated"

    profile = UserProfile.objects.get(user=user)
    assert profile.avatar_enabled is True
    assert profile.avatar_consent_confirmed is True
    assert profile.avatar_motion_preset == "natural"
    assert profile.avatar_lipsync_engine == "musetalk"
