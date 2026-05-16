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
from rest_framework.test import APIClient  # noqa: E402

from core.models import SiteHelpContent, UserProfile  # noqa: E402


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_user(username: str, *, role: str = "student", bio: str = "") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role, bio=bio)
    return user


@pytest.mark.django_db
def test_help_endpoint_returns_published_content():
    SiteHelpContent.objects.create(
        title="Draft help",
        slug="draft-help",
        body="Internal draft only",
        contact_email="draft@example.com",
        is_published=False,
    )
    SiteHelpContent.objects.create(
        title="Published help",
        slug="published-help",
        body="Public support instructions",
        contact_email="support@example.com",
        contact_phone="+1 555 0100",
        company_name="AI Academy",
        company_address="123 Learning Street",
        support_url="https://example.com/support",
        is_published=True,
    )

    response = _client().get("/api/v1/help/")

    assert response.status_code == 200
    assert response.data["title"] == "Published help"
    assert response.data["slug"] == "published-help"
    assert response.data["body"] == "Public support instructions"
    assert response.data["contact_email"] == "support@example.com"
    assert response.data["contact_phone"] == "+1 555 0100"
    assert response.data["company_name"] == "AI Academy"
    assert response.data["company_address"] == "123 Learning Street"
    assert response.data["support_url"] == "https://example.com/support"
    assert response.data["is_default"] is False
    assert "is_published" not in response.data


@pytest.mark.django_db
def test_help_endpoint_fallback_works_if_no_published_content():
    response = _client().get("/api/v1/help/")

    assert response.status_code == 200
    assert response.data["is_default"] is True
    assert response.data["title"] == "Help and Support"
    assert "Contact support" in response.data["body"]


@pytest.mark.django_db
def test_help_endpoint_does_not_return_unpublished_content():
    SiteHelpContent.objects.create(
        title="Unpublished help",
        slug="unpublished-help",
        body="This content should stay hidden",
        contact_email="hidden@example.com",
        is_published=False,
    )

    response = _client().get("/api/v1/help/")

    assert response.status_code == 200
    assert response.data["is_default"] is True
    assert response.data["title"] != "Unpublished help"
    assert "hidden@example.com" not in str(response.data)


@pytest.mark.django_db
def test_authenticated_user_can_update_own_public_profile_fields():
    user = _make_user("current_profile_owner", role="publisher", bio="Old bio")

    response = _client(user).patch(
        "/api/v1/me/profile/",
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "bio": "Publishes computing lessons.",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["first_name"] == "Ada"
    assert response.data["last_name"] == "Lovelace"
    assert response.data["display_name"] == "Ada Lovelace"
    assert response.data["bio"] == "Publishes computing lessons."

    user.refresh_from_db()
    user.profile.refresh_from_db()
    assert user.first_name == "Ada"
    assert user.last_name == "Lovelace"
    assert user.profile.bio == "Publishes computing lessons."


@pytest.mark.django_db
def test_user_cannot_update_another_user_public_profile():
    actor = _make_user("current_profile_actor", role="publisher", bio="Actor bio")
    target = _make_user("current_profile_target", role="publisher", bio="Target bio")

    response = _client(actor).patch(
        f"/api/v1/users/{target.id}/profile/",
        {"first_name": "Changed", "last_name": "Elsewhere", "bio": "Changed bio"},
        format="json",
    )

    assert response.status_code == 405
    target.refresh_from_db()
    target.profile.refresh_from_db()
    assert target.first_name == ""
    assert target.last_name == ""
    assert target.profile.bio == "Target bio"
