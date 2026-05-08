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

from core.models import UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student", is_staff: bool = False, is_superuser: bool = False) -> User:
    user = User.objects.create_user(
        username=username,
        password="pass",
        is_staff=is_staff,
        is_superuser=is_superuser,
    )
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_student_and_anonymous_cannot_access_analytics_api():
    student = _make_user("role_nav_student", role="student")

    student_response = _client(student).get("/api/v1/admin/stats/")
    anonymous_response = _client().get("/api/v1/admin/stats/")

    assert student_response.status_code == 403
    assert anonymous_response.status_code in {401, 403}


@pytest.mark.django_db
def test_staff_can_access_analytics_api():
    staff = _make_user("role_nav_staff", role="teacher", is_staff=True)

    response = _client(staff).get("/api/v1/admin/stats/")

    assert response.status_code == 200
    assert "summary" in response.data


@pytest.mark.django_db
def test_student_and_anonymous_cannot_access_studio_project_api():
    student = _make_user("role_nav_studio_student", role="student")

    student_response = _client(student).get("/api/v1/projects/")
    anonymous_response = _client().get("/api/v1/projects/")

    assert student_response.status_code == 403
    assert anonymous_response.status_code in {401, 403}


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["teacher", "publisher"])
def test_teacher_and_publisher_can_access_studio_project_api(role: str):
    user = _make_user(f"role_nav_{role}", role=role)

    response = _client(user).get("/api/v1/projects/")

    assert response.status_code == 200
    assert response.data == []
