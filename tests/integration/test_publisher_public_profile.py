# pyright: reportMissingImports=false

import os
import sys
from io import BytesIO
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
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import override_settings  # noqa: E402
from PIL import Image  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import UserProfile  # noqa: E402


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_user(
    username: str,
    *,
    role: str = "publisher",
    is_staff: bool = False,
    is_public_profile: bool = False,
) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(
        user=user,
        role=role,
        bio=f"{username} bio",
        display_name=f"{username} display",
        website_url="https://example.com",
        contact_email=f"{username}@example.com",
        social_links={"youtube": "https://youtube.com/example"},
        is_public_profile=is_public_profile,
    )
    return user


def _image_file(name: str, *, size=(800, 400), color=(40, 120, 200)) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_new_profile_fields_default_safely_private():
    user = User.objects.create_user(username="profile_defaults", password="pass")
    profile = UserProfile.objects.create(user=user)

    assert profile.display_name == ""
    assert profile.banner_image_original == ""
    assert profile.banner_image_processed == ""
    assert profile.logo_image_original == ""
    assert profile.logo_image_processed == ""
    assert profile.website_url == ""
    assert profile.contact_email == ""
    assert profile.social_links == {}
    assert profile.is_public_profile is False


@pytest.mark.django_db
def test_owner_can_patch_own_public_profile_metadata():
    user = _make_user("profile_patch_owner", is_public_profile=False)

    response = _client(user).patch(
        "/api/v1/me/profile/",
        {
            "first_name": "Grace",
            "last_name": "Hopper",
            "display_name": "Compiler Academy",
            "bio": "Computer science lessons.",
            "website_url": "https://publisher.example.com",
            "contact_email": "contact@example.com",
            "social_links": {
                "youtube": "https://youtube.com/@compiler",
                "github": "",
            },
            "is_public_profile": True,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["display_name"] == "Compiler Academy"
    assert response.data["website_url"] == "https://publisher.example.com"
    assert response.data["contact_email"] == "contact@example.com"
    assert response.data["social_links"] == {"youtube": "https://youtube.com/@compiler"}
    assert response.data["is_public_profile"] is True

    user.profile.refresh_from_db()
    assert user.profile.display_name == "Compiler Academy"
    assert user.profile.is_public_profile is True


@pytest.mark.django_db
def test_social_links_validation_strips_empty_and_rejects_nested_values():
    user = _make_user("profile_social_validation")

    ok_response = _client(user).patch(
        "/api/v1/me/profile/",
        {"social_links": {"instagram": "https://instagram.com/example", "linkedin": ""}},
        format="json",
    )
    assert ok_response.status_code == 200
    assert ok_response.data["social_links"] == {"instagram": "https://instagram.com/example"}

    nested_response = _client(user).patch(
        "/api/v1/me/profile/",
        {"social_links": {"youtube": {"url": "https://youtube.com/example"}}},
        format="json",
    )
    assert nested_response.status_code == 400

    unsafe_response = _client(user).patch(
        "/api/v1/me/profile/",
        {"social_links": {"mastodon": "https://social.example.com/@profile"}},
        format="json",
    )
    assert unsafe_response.status_code == 400


@pytest.mark.django_db
def test_anonymous_cannot_see_private_profile_contact_or_social_data():
    publisher = _make_user("profile_private_anon", is_public_profile=False)

    response = _client().get(f"/api/v1/users/{publisher.id}/profile/")

    assert response.status_code == 404
    assert "profile_private_anon@example.com" not in str(response.data)
    assert "youtube.com" not in str(response.data)


@pytest.mark.django_db
def test_owner_and_staff_can_see_private_profile_metadata():
    publisher = _make_user("profile_private_owner", is_public_profile=False)
    staff = _make_user("profile_private_staff", is_staff=True, role="publisher")

    owner_response = _client(publisher).get(f"/api/v1/users/{publisher.id}/profile/")
    staff_response = _client(staff).get(f"/api/v1/users/{publisher.id}/profile/")

    assert owner_response.status_code == 200
    assert staff_response.status_code == 200
    assert owner_response.data["contact_email"] == "profile_private_owner@example.com"
    assert staff_response.data["social_links"] == {"youtube": "https://youtube.com/example"}


@pytest.mark.django_db
def test_public_profile_returns_safe_banner_logo_social_and_contact_fields():
    publisher = _make_user("profile_public_payload", is_public_profile=True)
    publisher.profile.banner_image_original = "profiles/1/banner_original.png"
    publisher.profile.banner_image_processed = "profiles/1/banner_processed.jpg"
    publisher.profile.logo_image_original = "profiles/1/logo_original.png"
    publisher.profile.logo_image_processed = "profiles/1/logo_processed.jpg"
    publisher.profile.save(
        update_fields=[
            "banner_image_original",
            "banner_image_processed",
            "logo_image_original",
            "logo_image_processed",
            "updated_at",
        ]
    )

    response = _client().get(f"/api/v1/users/{publisher.id}/profile/")

    assert response.status_code == 200
    assert response.data["banner_url"].endswith(f"/api/v1/users/{publisher.id}/profile-assets/banner/?v={int(publisher.profile.updated_at.timestamp())}")
    assert response.data["logo_url"].endswith(f"/api/v1/users/{publisher.id}/profile-assets/logo/?v={int(publisher.profile.updated_at.timestamp())}")
    assert response.data["contact_email"] == "profile_public_payload@example.com"
    assert response.data["social_links"] == {"youtube": "https://youtube.com/example"}
    assert "banner_image_processed" not in response.data


@pytest.mark.django_db
def test_banner_and_logo_upload_works_for_owner(tmp_path):
    publisher = _make_user("profile_upload_owner")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(publisher).post(
            "/api/v1/me/profile-assets/",
            {
                "banner_file": _image_file("banner.png", size=(1200, 600)),
                "logo_file": _image_file("logo.png", size=(640, 640), color=(220, 80, 90)),
            },
            format="multipart",
        )

    assert response.status_code == 200
    assert response.data["banner_url"]
    assert response.data["logo_url"]
    assert "profiles/" not in str(response.data)

    publisher.profile.refresh_from_db()
    assert publisher.profile.banner_image_original.startswith(f"profiles/{publisher.id}/")
    assert publisher.profile.banner_image_processed == f"profiles/{publisher.id}/banner_processed.jpg"
    assert publisher.profile.logo_image_original.startswith(f"profiles/{publisher.id}/")
    assert publisher.profile.logo_image_processed == f"profiles/{publisher.id}/logo_processed.jpg"
    assert (tmp_path / publisher.profile.banner_image_processed).exists()
    assert (tmp_path / publisher.profile.logo_image_processed).exists()


@pytest.mark.django_db
def test_non_owner_cannot_upload_or_change_another_profile_assets(tmp_path):
    actor = _make_user("profile_asset_actor")
    target = _make_user("profile_asset_target")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(actor).post(
            f"/api/v1/users/{target.id}/profile-assets/banner/",
            {"banner_file": _image_file("banner.png")},
            format="multipart",
        )

    assert response.status_code == 405
    target.profile.refresh_from_db()
    assert target.profile.banner_image_processed == ""


@pytest.mark.django_db
def test_public_processed_profile_assets_are_served_to_anonymous_users(tmp_path):
    publisher = _make_user("profile_asset_public", is_public_profile=True)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        upload_response = _client(publisher).post(
            "/api/v1/me/profile-assets/",
            {
                "banner_file": _image_file("banner.png", size=(1200, 600)),
                "logo_file": _image_file("logo.png", size=(640, 640)),
            },
            format="multipart",
        )
        assert upload_response.status_code == 200
        banner_response = _client().get(f"/api/v1/users/{publisher.id}/profile-assets/banner/")
        logo_response = _client().get(f"/api/v1/users/{publisher.id}/profile-assets/logo/")

    assert banner_response.status_code == 200
    assert logo_response.status_code == 200
    assert banner_response["Content-Type"] == "image/jpeg"
    assert logo_response["Content-Type"] == "image/jpeg"
    assert "public" in banner_response["Cache-Control"]


@pytest.mark.django_db
def test_private_profile_assets_return_404_for_anonymous_users(tmp_path):
    publisher = _make_user("profile_asset_private", is_public_profile=False)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        upload_response = _client(publisher).post(
            "/api/v1/me/profile-assets/",
            {"banner_file": _image_file("banner.png", size=(1200, 600))},
            format="multipart",
        )
        assert upload_response.status_code == 200
        response = _client().get(f"/api/v1/users/{publisher.id}/profile-assets/banner/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_profile_apis_do_not_expose_raw_profile_asset_storage_paths(tmp_path):
    publisher = _make_user("profile_no_raw_paths", is_public_profile=True)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        upload_response = _client(publisher).post(
            "/api/v1/me/profile-assets/",
            {"banner_file": _image_file("banner.png", size=(1200, 600))},
            format="multipart",
        )
        public_response = _client().get(f"/api/v1/users/{publisher.id}/profile/")

    assert upload_response.status_code == 200
    assert public_response.status_code == 200
    combined_payload = f"{upload_response.data} {public_response.data}"
    assert "profiles/" not in combined_payload
    assert "banner_image_original" not in combined_payload
    assert "banner_image_processed" not in combined_payload


@pytest.mark.django_db
def test_existing_avatar_profile_serializer_fields_still_work():
    publisher = _make_user("profile_avatar_existing")
    publisher.profile.avatar_image_processed = "avatars/source/avatar_processed.png"
    publisher.profile.avatar_enabled = True
    publisher.profile.save(update_fields=["avatar_image_processed", "avatar_enabled", "updated_at"])

    response = _client(publisher).get("/api/v1/auth/me/")

    assert response.status_code == 200
    assert response.data["profile"]["avatar_image_processed"] == "avatars/source/avatar_processed.png"
    assert response.data["profile"]["avatar_enabled"] is True
