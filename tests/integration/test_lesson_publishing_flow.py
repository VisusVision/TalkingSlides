# pyright: reportMissingImports=false
"""
Integration tests for lesson publishing, dashboard visibility, and moderation gating.

Coverage (Part D requirements):
- Owner dashboard/project list includes own moderation-pending draft
- Public catalog excludes unpublished lessons
- Public catalog excludes rejected/blocked moderated lessons
- Owner can see rejected/blocked lesson in dashboard with status
- Publish blocked for render-ready lesson with not_scanned moderation
- Publish blocked for render-ready lesson with pending moderation
- Publish allowed for render-ready lesson with approved moderation
- Publish blocked with clear error for rejected/blocked lesson
- Anonymous cannot see drafts
- Staff can review other users' projects read-only
"""

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


def _make_staff(username: str):
    user = User.objects.create_user(username=username, password="pass", is_staff=True)
    UserProfile.objects.create(user=user, role="teacher")
    return user


class _DummySession(dict):
    session_key = "test-session"

    def save(self):
        self.session_key = self.session_key or "test-session"


def _enable_avatar_for_teacher(user):
    profile = user.profile
    profile.avatar_enabled = True
    profile.avatar_consent_confirmed = True
    profile.avatar_image_processed = f"avatars/{user.username}/processed.png"
    profile.avatar_source_valid = True
    profile.avatar_moderation_status = "approved"
    profile.save(
        update_fields=[
            "avatar_enabled",
            "avatar_consent_confirmed",
            "avatar_image_processed",
            "avatar_source_valid",
            "avatar_moderation_status",
        ]
    )


def _create_published_ready_lesson(
    teacher,
    *,
    title: str,
    avatar_enabled: bool = False,
    avatar_processing_status: str = "none",
    moderation_status: str = "approved",
):
    project = Project.objects.create(
        title=title,
        user=teacher,
        status="ready",
        moderation_status=moderation_status,
        is_published=True,
        avatar_enabled_override=avatar_enabled,
        avatar_processing_status=avatar_processing_status,
        avatar_visible=True,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
    )
    return project


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_can_publish_and_unpublish_own_project():
    teacher = _make_teacher("publish_owner_teacher")
    project = Project.objects.create(
        title="Draft lesson",
        user=teacher,
        status="ready",
        moderation_status="approved",
    )

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
    unpublished = Project.objects.create(
        title="Unpublished Done",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=False,
    )
    published_pending = Project.objects.create(
        title="Published Pending",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    published_done = Project.objects.create(
        title="Published Done",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    published_done_not_ready = Project.objects.create(
        title="Published Done Not Ready",
        user=teacher,
        status="processing",
        moderation_status="approved",
        is_published=True,
    )

    Job.objects.create(project=unpublished, job_type="video_export", status="done", result_url="unpublished.mp4")
    Job.objects.create(project=published_pending, job_type="video_export", status="pending")
    Job.objects.create(project=published_done, job_type="video_export", status="done", result_url="published.mp4")
    Job.objects.create(project=published_done_not_ready, job_type="video_export", status="done", result_url="not-ready.mp4")

    request = APIRequestFactory().get("/api/v1/catalog/")
    response = views.CatalogListView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert published_done.id in ids
    assert unpublished.id not in ids
    assert published_pending.id not in ids
    assert published_done_not_ready.id not in ids


@pytest.mark.django_db
def test_published_lesson_visible_before_avatar_completes():
    teacher = _make_teacher("avatar_publish_teacher")
    _enable_avatar_for_teacher(teacher)
    project = _create_published_ready_lesson(
        teacher,
        title="Queued Avatar Published Lesson",
        avatar_enabled=True,
        avatar_processing_status="queued",
        moderation_status="approved",
    )

    factory = APIRequestFactory()
    catalog_response = views.CatalogListView.as_view()(factory.get("/api/v1/catalog/"))

    assert catalog_response.status_code == 200
    assert project.id in {item["id"] for item in catalog_response.data}

    token_request = factory.get(f"/api/v1/projects/{project.id}/playback-token/")
    token_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        token_response = views.PlaybackTokenView.as_view()(token_request, project_id=project.id)

    assert token_response.status_code == 200
    assert token_response.data["video_url"]
    assert token_response.data["avatar_processing_status"] == "queued"
    assert token_response.data["avatar_available"] is False
    assert token_response.data["avatar_overlay"]["enabled"] is False
    assert token_response.data["avatar_overlay"]["stream_url"] == ""


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
    detail_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        detail_response = views.CatalogDetailView.as_view()(detail_request, project_id=project.id)

    assert detail_response.status_code == 200
    assert detail_response.data["id"] == project.id
    assert detail_response.data["stream_url"]

    token_request = factory.get(f"/api/v1/projects/{project.id}/playback-token/")
    force_authenticate(token_request, user=teacher)
    token_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        token_response = views.PlaybackTokenView.as_view()(token_request, project_id=project.id)

    assert token_response.status_code == 200
    assert token_response.data["video_url"]


@pytest.mark.django_db
def test_legacy_lesson_without_avatar_state():
    teacher = _make_teacher("legacy_no_avatar_teacher")
    project = _create_published_ready_lesson(
        teacher,
        title="Legacy Lesson Without Avatar State",
        avatar_enabled=False,
        avatar_processing_status="none",
        moderation_status="approved",
    )

    factory = APIRequestFactory()
    catalog_response = views.CatalogListView.as_view()(factory.get("/api/v1/catalog/"))

    assert catalog_response.status_code == 200
    assert project.id in {item["id"] for item in catalog_response.data}

    detail_request = factory.get(f"/api/v1/catalog/{project.id}/")
    detail_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        detail_response = views.CatalogDetailView.as_view()(detail_request, project_id=project.id)

    assert detail_response.status_code == 200
    assert detail_response.data["stream_url"]
    assert detail_response.data["avatar_processing_status"] == "none"
    assert detail_response.data["avatar_available"] is False
    assert detail_response.data["avatar_overlay"]["enabled"] is False

    token_request = factory.get(f"/api/v1/projects/{project.id}/playback-token/")
    token_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        token_response = views.PlaybackTokenView.as_view()(token_request, project_id=project.id)

    assert token_response.status_code == 200
    assert token_response.data["video_url"]
    assert token_response.data["avatar_available"] is False
    assert token_response.data["avatar_overlay"]["enabled"] is False


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
    detail_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        detail_response = views.CatalogDetailView.as_view()(detail_request, project_id=project.id)

    assert detail_response.status_code == 200
    assert detail_response.data["stream_url"]


# ---------------------------------------------------------------------------
# New tests (Part D requirements)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_dashboard_includes_moderation_pending_draft():
    """Owner dashboard must show own lessons even when moderation_status is pending."""
    teacher = _make_teacher("dash_pending_teacher")
    draft = Project.objects.create(
        title="Pending Moderation Draft",
        user=teacher,
        status="draft",
        moderation_status="pending",
        is_published=False,
    )
    Job.objects.create(project=draft, job_type="video_export", status="pending")

    request = APIRequestFactory().get("/api/v1/projects/")
    force_authenticate(request, user=teacher)
    response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert draft.id in ids, "Owner dashboard must include moderation-pending drafts"
    row = next(item for item in response.data if item["id"] == draft.id)
    assert row["moderation_status"] == "pending"
    assert row["is_published"] is False


@pytest.mark.django_db
def test_owner_dashboard_includes_rejected_lesson_with_status():
    """Owner must be able to see admin_rejected lesson in dashboard (not hidden)."""
    teacher = _make_teacher("dash_rejected_teacher")
    rejected = Project.objects.create(
        title="Rejected Lesson",
        user=teacher,
        status="ready",
        moderation_status="admin_rejected",
        is_published=False,
    )
    Job.objects.create(project=rejected, job_type="video_export", status="done", result_url="rejected.mp4")

    request = APIRequestFactory().get("/api/v1/projects/")
    force_authenticate(request, user=teacher)
    response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert rejected.id in ids, "Owner dashboard must include rejected lessons so teacher can see the status"
    row = next(item for item in response.data if item["id"] == rejected.id)
    assert row["moderation_status"] == "admin_rejected"


@pytest.mark.django_db
def test_catalog_excludes_rejected_moderated_lesson():
    """Public catalog must NOT include admin_rejected lessons even if published."""
    teacher = _make_teacher("catalog_rejected_teacher")
    rejected_published = Project.objects.create(
        title="Rejected But Published",
        user=teacher,
        status="ready",
        moderation_status="admin_rejected",
        is_published=True,
    )
    Job.objects.create(
        project=rejected_published, job_type="video_export", status="done", result_url="rejected.mp4"
    )

    request = APIRequestFactory().get("/api/v1/catalog/")
    response = views.CatalogListView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert rejected_published.id not in ids, "Catalog must exclude admin_rejected lessons"


@pytest.mark.django_db
def test_catalog_excludes_revision_required_lesson():
    """Public catalog must NOT include revision_required lessons even if published."""
    teacher = _make_teacher("catalog_revision_teacher")
    needs_revision = Project.objects.create(
        title="Needs Revision But Published",
        user=teacher,
        status="ready",
        moderation_status="revision_required",
        is_published=True,
    )
    Job.objects.create(
        project=needs_revision, job_type="video_export", status="done", result_url="revision.mp4"
    )

    request = APIRequestFactory().get("/api/v1/catalog/")
    response = views.CatalogListView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert needs_revision.id not in ids, "Catalog must exclude revision_required lessons"


@pytest.mark.django_db
def test_catalog_excludes_not_scanned_published_lesson():
    """Public catalog must hide published, render-ready lessons until moderation approves them."""
    teacher = _make_teacher("catalog_unscanned_teacher")
    unscanned = Project.objects.create(
        title="Unscanned But Published",
        user=teacher,
        status="ready",
        moderation_status="not_scanned",
        is_published=True,
    )
    Job.objects.create(
        project=unscanned, job_type="video_export", status="done", result_url="unscanned.mp4"
    )

    request = APIRequestFactory().get("/api/v1/catalog/")
    response = views.CatalogListView.as_view()(request)

    assert response.status_code == 200
    ids = {item["id"] for item in response.data}
    assert unscanned.id not in ids, "Catalog must exclude not_scanned published lessons"


@pytest.mark.django_db
def test_publish_blocked_for_render_ready_not_scanned_lesson():
    """Publish must be blocked for a render-ready lesson with not_scanned moderation."""
    teacher = _make_teacher("pub_not_scanned_teacher")
    project = Project.objects.create(
        title="Unscanned Ready Lesson",
        user=teacher,
        status="ready",
        moderation_status="not_scanned",
        is_published=False,
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 400, (
        f"Publish should be blocked for not_scanned lesson. Got {response.status_code}: {response.data}"
    )
    assert response.data["reason"] == "moderation_required"
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_publish_blocked_for_render_ready_pending_moderation_lesson():
    """Publish must be blocked for a render-ready lesson with pending moderation."""
    teacher = _make_teacher("pub_pending_mod_teacher")
    project = Project.objects.create(
        title="Pending Moderation Ready Lesson",
        user=teacher,
        status="ready",
        moderation_status="pending",
        is_published=False,
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 400, (
        f"Publish should be blocked for pending moderation lesson. Got {response.status_code}: {response.data}"
    )
    assert response.data["reason"] == "moderation_processing"
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_publish_blocked_for_admin_rejected_lesson():
    """Publish must be blocked with a clear 400 error when lesson is admin_rejected."""
    teacher = _make_teacher("pub_rejected_teacher")
    project = Project.objects.create(
        title="Admin Rejected Ready Lesson",
        user=teacher,
        status="ready",
        moderation_status="admin_rejected",
        is_published=False,
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 400, (
        f"Publish should be blocked for admin_rejected lesson. Got {response.status_code}: {response.data}"
    )
    assert "moderation" in str(response.data).lower() or "reason" in response.data, (
        "Error response should mention moderation or include a reason field"
    )
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_publish_blocked_for_revision_required_lesson():
    """Publish must be blocked with a clear 400 error when lesson needs revision."""
    teacher = _make_teacher("pub_revision_teacher")
    project = Project.objects.create(
        title="Revision Required Ready Lesson",
        user=teacher,
        status="ready",
        moderation_status="revision_required",
        is_published=False,
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 400, (
        f"Publish should be blocked for revision_required lesson. Got {response.status_code}: {response.data}"
    )
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_anonymous_cannot_see_drafts():
    """Anonymous users must receive 404 for unpublished lessons."""
    teacher = _make_teacher("anon_draft_teacher")
    project = Project.objects.create(
        title="Anon Draft Test",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=False,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="anon-draft.mp4")

    # Anonymous catalog list must not include this lesson.
    list_response = views.CatalogListView.as_view()(
        APIRequestFactory().get("/api/v1/catalog/")
    )
    assert project.id not in {item["id"] for item in list_response.data}

    # Anonymous catalog detail must return 404.
    detail_response = views.CatalogDetailView.as_view()(
        APIRequestFactory().get(f"/api/v1/catalog/{project.id}/"),
        project_id=project.id,
    )
    assert detail_response.status_code == 404


@pytest.mark.django_db
def test_staff_can_review_any_project_read_only():
    """Staff users can review another user's project but cannot edit it."""
    teacher = _make_teacher("staff_proj_teacher")
    staff = _make_staff("staff_proj_staff")
    project = Project.objects.create(
        title="Staff Visible Draft",
        user=teacher,
        is_published=False,
        moderation_status="pending",
    )

    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/")
    force_authenticate(request, user=staff)
    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["id"] == project.id

    patch_request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(patch_request, user=staff)
    patch_response = views.ProjectDetailView.as_view()(patch_request, project_id=project.id)

    assert patch_response.status_code == 403


@pytest.mark.django_db
def test_publish_blocked_when_render_not_ready():
    """Publish must be blocked when render is not ready regardless of moderation."""
    teacher = _make_teacher("pub_not_ready_teacher")
    project = Project.objects.create(
        title="Not Ready Draft",
        user=teacher,
        status="draft",  # not "ready"
        moderation_status="approved",
        is_published=False,
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 400, (
        f"Publish should be blocked when render is not ready. Got {response.status_code}: {response.data}"
    )
    project.refresh_from_db()
    assert project.is_published is False


@pytest.mark.django_db
def test_owner_cannot_see_other_teachers_drafts():
    """A teacher must not see another teacher's private drafts."""
    owner = _make_teacher("isolation_owner")
    other = _make_teacher("isolation_other")
    Project.objects.create(title="Owner Draft", user=owner, is_published=False)

    request = APIRequestFactory().get("/api/v1/projects/")
    force_authenticate(request, user=other)
    response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 200
    owner_ids = {item["id"] for item in response.data if item.get("user_name") == owner.username}
    assert len(owner_ids) == 0, "Teacher must not see another teacher's drafts"
