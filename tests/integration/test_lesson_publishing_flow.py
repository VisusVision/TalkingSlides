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
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402


def _make_studio_user(username: str, role: str = "teacher"):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _make_teacher(username: str):
    return _make_studio_user(username, role="teacher")


@pytest.mark.django_db
def test_owner_can_publish_and_unpublish_own_project():
    teacher = _make_teacher("publish_owner_teacher")
    project = Project.objects.create(title="Draft lesson", user=teacher)

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["is_published"] is True
    project.refresh_from_db()
    assert project.is_published is True

    request = factory.patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": False},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["is_published"] is False
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_non_owner_cannot_publish_project():
    owner = _make_teacher("publish_forbidden_owner")
    other = _make_teacher("publish_forbidden_other")
    project = Project.objects.create(title="Private lesson", user=owner)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=other)

    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 403
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_public_catalog_requires_published_done_project():
    teacher = _make_teacher("catalog_publish_teacher")
    unpublished = Project.objects.create(title="Unpublished Done", user=teacher, is_published=False)
    published_pending = Project.objects.create(title="Published Pending", user=teacher, is_published=True)
    published_done = Project.objects.create(title="Published Done", user=teacher, is_published=True)

    Job.objects.create(project=unpublished, job_type="video_export", status="done", result_url="unpublished.mp4")
    Job.objects.create(project=published_pending, job_type="video_export", status="pending")
    Job.objects.create(project=published_done, job_type="video_export", status="done", result_url="published.mp4")

    request = APIRequestFactory().get("/api/v1/catalog/")
    response = views.CatalogListView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert published_done.id in ids
    assert unpublished.id not in ids
    assert published_pending.id not in ids


@pytest.mark.django_db
def test_teacher_project_list_includes_own_unpublished_processing_project():
    teacher = _make_teacher("studio_list_teacher")
    own_draft = Project.objects.create(title="Own Draft", user=teacher, is_published=False)
    Job.objects.create(project=own_draft, job_type="video_export", status="pending")

    request = APIRequestFactory().get("/api/v1/projects/")
    force_authenticate(request, user=teacher)

    response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert own_draft.id in ids
    row = next(item for item in response.data if item["id"] == own_draft.id)
    assert row["is_published"] is False
    assert row["latest_job"]["status"] == "pending"


@pytest.mark.django_db
def test_owner_can_preview_unpublished_done_lesson():
    teacher = _make_teacher("draft_preview_owner")
    project = Project.objects.create(title="Private Ready Preview", user=teacher, is_published=False)
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="draft.mp4")

    factory = APIRequestFactory()

    anonymous_detail = views.CatalogDetailView.as_view()(
        factory.get(f"/api/v1/catalog/{project.id}/"),
        project_id=project.id,
    )
    assert anonymous_detail.status_code == 404

    detail_request = factory.get(f"/api/v1/catalog/{project.id}/")
    force_authenticate(detail_request, user=teacher)
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        detail_response = views.CatalogDetailView.as_view()(detail_request, project_id=project.id)

    assert detail_response.status_code == 200
    assert detail_response.data["id"] == project.id
    assert detail_response.data["stream_url"]

    token_request = factory.get(f"/api/v1/projects/{project.id}/playback-token/")
    force_authenticate(token_request, user=teacher)
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        token_response = views.PlaybackTokenView.as_view()(token_request, project_id=project.id)

    assert token_response.status_code == 200
    assert token_response.data["video_url"]


@pytest.mark.django_db
def test_publisher_owner_can_manage_and_preview_own_project():
    publisher = _make_studio_user("publisher_preview_owner", role="publisher")
    project = Project.objects.create(title="Publisher Draft Preview", user=publisher, is_published=False)
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="publisher-draft.mp4")

    factory = APIRequestFactory()
    list_request = factory.get("/api/v1/projects/")
    force_authenticate(list_request, user=publisher)
    list_response = views.ProjectUploadView.as_view()(list_request)

    assert list_response.status_code == 200
    assert any(item["id"] == project.id for item in list_response.data)

    detail_request = factory.get(f"/api/v1/catalog/{project.id}/")
    force_authenticate(detail_request, user=publisher)
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        detail_response = views.CatalogDetailView.as_view()(detail_request, project_id=project.id)

    assert detail_response.status_code == 200
    assert detail_response.data["stream_url"]
