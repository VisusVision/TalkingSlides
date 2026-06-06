# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import UserProfile  # noqa: E402


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_profile_custom_name_and_photo_win():
    user = User.objects.create_user(username="identity_custom", email="custom@example.com", password="pass")
    UserProfile.objects.create(
        user=user,
        role="publisher",
        display_name="Custom Publisher",
        logo_image_processed="profiles/custom/logo.png",
    )
    cache.set(f"auth-google-picture:{user.id}", "https://lh3.googleusercontent.com/google-photo", timeout=60)

    response = _client(user).get("/api/v1/auth/me/")

    assert response.status_code == 200
    assert response.data["display_name"] == "Custom Publisher"
    assert f"/api/v1/users/{user.id}/profile-assets/logo/" in response.data["profile_photo_url"]


@pytest.mark.django_db
def test_google_name_and_photo_fallback_work():
    user = User.objects.create_user(
        username="identity_google",
        email="google@example.com",
        first_name="Google",
        last_name="Fallback",
        password="pass",
    )
    UserProfile.objects.create(user=user, role="student")
    cache.set(f"auth-google-picture:{user.id}", "https://lh3.googleusercontent.com/google-photo", timeout=60)

    response = _client(user).get("/api/v1/auth/me/")

    assert response.status_code == 200
    assert response.data["display_name"] == "Google Fallback"
    assert response.data["profile_photo_url"] == "https://lh3.googleusercontent.com/google-photo"


@pytest.mark.django_db
def test_initials_fallback_when_no_photo():
    user = User.objects.create_user(username="identity_initials", email="initials@example.com", password="pass")
    UserProfile.objects.create(user=user, role="student")

    response = _client(user).get("/api/v1/auth/me/")

    assert response.status_code == 200
    assert response.data["profile_photo_url"] == ""
    assert response.data["profile_initials"] == "ID"
