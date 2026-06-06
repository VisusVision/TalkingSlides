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
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from ai_agents.policies import project_can_publish  # noqa: E402
from core import views  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _publish(project: Project, user: User):
    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=user)
    return views.ProjectDetailView.as_view()(request, project_id=project.id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("moderation_status", "expected"),
    [
        ("not_scanned", False),
        ("pending", False),
        ("approved", True),
        ("revision_required", False),
        ("needs_admin_review", False),
        ("admin_approved", True),
        ("admin_rejected", False),
        ("failed", False),
    ],
)
def test_project_can_publish_requires_ready_and_unblocked_moderation(moderation_status, expected):
    teacher = _make_teacher(f"gate_helper_{moderation_status}")
    project = Project.objects.create(
        title=f"Gate {moderation_status}",
        user=teacher,
        status="ready",
        moderation_status=moderation_status,
    )

    assert project_can_publish(project) is expected


@pytest.mark.django_db
def test_project_can_publish_rejects_approved_draft_project():
    teacher = _make_teacher("gate_helper_draft")
    project = Project.objects.create(
        title="Approved draft",
        user=teacher,
        status="draft",
        moderation_status="approved",
    )

    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_publish_patch_rejects_revision_required_project():
    teacher = _make_teacher("gate_revision_required")
    project = Project.objects.create(
        title="Blocked lesson",
        user=teacher,
        status="ready",
        moderation_status="revision_required",
    )

    response = _publish(project, teacher)

    assert response.status_code == 400
    assert response.data["detail"] == (
        "This lesson cannot be published because moderation has not approved it. "
        "Please revise the content or request an admin review."
    )
    assert response.data["moderation_status"] == "revision_required"
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_publish_patch_allows_approved_project():
    teacher = _make_teacher("gate_approved")
    project = Project.objects.create(
        title="Approved lesson",
        user=teacher,
        status="ready",
        moderation_status="approved",
    )

    response = _publish(project, teacher)

    assert response.status_code == 200
    assert response.data["is_published"] is True
    project.refresh_from_db()
    assert project.is_published is True


@pytest.mark.django_db
def test_publish_patch_rejects_pending_moderation_project():
    teacher = _make_teacher("gate_pending")
    project = Project.objects.create(
        title="Pending moderation lesson",
        user=teacher,
        status="ready",
        moderation_status="pending",
    )

    response = _publish(project, teacher)

    assert response.status_code == 400
    assert response.data["detail"] == "Moderation in progress. Publishing is temporarily blocked."
    assert response.data["reason"] == "moderation_processing"


@pytest.mark.django_db
def test_publish_patch_allows_admin_approved_project():
    teacher = _make_teacher("gate_admin_approved")
    project = Project.objects.create(
        title="Admin approved lesson",
        user=teacher,
        status="ready",
        moderation_status="admin_approved",
    )

    response = _publish(project, teacher)

    assert response.status_code == 200
    assert response.data["is_published"] is True
    project.refresh_from_db()
    assert project.is_published is True
