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
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import Job, Project, UserProfile  # noqa: E402


pytestmark = pytest.mark.django_db


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_user(username: str, *, role: str = "publisher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(
        user=user,
        role=role,
        display_name=username,
        bio=f"{username} bio",
        is_public_profile=True,
    )
    return user


def _make_ready_lesson(owner: User, title: str, moderation_status: str) -> Project:
    project = Project.objects.create(
        user=owner,
        title=title,
        status="ready",
        moderation_status=moderation_status,
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


def _titles(rows) -> set[str]:
    return {str(row.get("title") or "") for row in rows}


def test_public_catalog_excludes_not_scanned_by_default():
    publisher = _make_user("modvis_catalog_publisher")
    approved = _make_ready_lesson(publisher, "Approved public lesson", "approved")
    hidden = _make_ready_lesson(publisher, "Unscanned hidden lesson", "not_scanned")
    cache.clear()

    with override_settings(PUBLIC_ALLOW_NOT_SCANNED_LESSONS=False):
        response = _client().get("/api/v1/catalog/")

    titles = _titles(response.data)
    assert response.status_code == 200
    assert approved.title in titles
    assert hidden.title not in titles


def test_public_catalog_can_explicitly_allow_not_scanned_for_local_use():
    publisher = _make_user("modvis_catalog_flag_publisher")
    hidden = _make_ready_lesson(publisher, "Unscanned local lesson", "not_scanned")
    cache.clear()

    with override_settings(PUBLIC_ALLOW_NOT_SCANNED_LESSONS=True):
        response = _client().get("/api/v1/catalog/")

    assert response.status_code == 200
    assert hidden.title in _titles(response.data)


def test_public_channel_excludes_not_scanned_by_default():
    publisher = _make_user("modvis_channel_publisher")
    approved = _make_ready_lesson(publisher, "Approved channel lesson", "approved")
    hidden = _make_ready_lesson(publisher, "Unscanned channel lesson", "not_scanned")
    cache.clear()

    with override_settings(PUBLIC_ALLOW_NOT_SCANNED_LESSONS=False):
        response = _client().get(f"/api/v1/users/{publisher.id}/lessons/")

    titles = _titles(response.data["results"])
    assert response.status_code == 200
    assert approved.title in titles
    assert hidden.title not in titles


def test_owner_and_staff_project_lists_can_still_see_not_scanned_lessons():
    publisher = _make_user("modvis_owner_publisher")
    staff = _make_user("modvis_staff", role="teacher", is_staff=True)
    hidden = _make_ready_lesson(publisher, "Owner visible unscanned lesson", "not_scanned")

    with override_settings(PUBLIC_ALLOW_NOT_SCANNED_LESSONS=False):
        owner_response = _client(publisher).get("/api/v1/projects/")
        staff_response = _client(staff).get("/api/v1/projects/")

    assert owner_response.status_code == 200
    assert staff_response.status_code == 200
    assert hidden.title in _titles(owner_response.data)
    assert hidden.title in _titles(staff_response.data)
