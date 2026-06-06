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

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from rest_framework.authtoken.models import Token  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import UserProfile  # noqa: E402
from core.views import LoginView  # noqa: E402


def _make_user(username: str = "auth_regression_user") -> User:
    user = User.objects.create_user(username=username, password="pass12345", email=f"{username}@example.test")
    UserProfile.objects.create(user=user, role="student")
    return user


def _client_with_token(token_key: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token_key}")
    return client


@pytest.mark.django_db
def test_logout_revokes_old_token_for_auth_me():
    user = _make_user("auth_logout_revokes")
    token = Token.objects.create(user=user)
    client = _client_with_token(token.key)

    logout_response = client.post("/api/v1/auth/logout/", {}, format="json")

    assert logout_response.status_code == 200
    assert not Token.objects.filter(key=token.key).exists()

    me_response = client.get("/api/v1/auth/me/")
    assert me_response.status_code in {401, 403}


@pytest.mark.django_db
def test_deleted_token_cannot_access_auth_me():
    user = _make_user("auth_deleted_token")
    token = Token.objects.create(user=user)
    token_key = token.key
    token.delete()

    response = _client_with_token(token_key).get("/api/v1/auth/me/")

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_auth_me_requires_authentication():
    response = APIClient().get("/api/v1/auth/me/")

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_login_throttle_configuration_regression():
    throttle_rates = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})

    assert getattr(LoginView, "throttle_scope", None) == "login"
    assert "login" in throttle_rates
    assert throttle_rates["login"]


@pytest.mark.django_db
def test_repeated_logout_with_old_token_fails_closed_without_recreating_token():
    user = _make_user("auth_repeated_logout")
    token = Token.objects.create(user=user)
    client = _client_with_token(token.key)

    first = client.post("/api/v1/auth/logout/", {}, format="json")
    second = client.post("/api/v1/auth/logout/", {}, format="json")

    assert first.status_code == 200
    assert second.status_code in {401, 403}
    assert not Token.objects.filter(key=token.key).exists()


@pytest.mark.django_db
def test_password_login_reuses_existing_token_until_logout():
    cache.clear()
    user = _make_user("auth_login_reuses_token")

    client = APIClient()
    first = client.post("/api/v1/auth/login/", {"username": user.username, "password": "pass12345"}, format="json")
    second = client.post("/api/v1/auth/login/", {"username": user.username, "password": "pass12345"}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["token"] == second.data["token"]
    assert Token.objects.filter(user=user).count() == 1
