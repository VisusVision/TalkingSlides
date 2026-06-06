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

from ai_agents.models import AdminReviewRequest, ModerationReport, PublicationBlockEvent  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(
    owner: User,
    *,
    title: str = "Moderation visibility lesson",
    moderation_status: str = "approved",
    published: bool = True,
) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status=moderation_status,
        is_published=published,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/lesson.mp4",
    )
    return project


@pytest.mark.django_db
def test_student_can_report_lesson():
    publisher = _make_user("report_owner", role="publisher")
    student = _make_user("report_student", role="student")
    project = _make_project(publisher)

    response = _client(student).post(
        f"/api/v1/projects/{project.id}/report/",
        {"category": "wrong_information", "message": "The explanation uses the wrong formula."},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["category"] == "wrong_information"
    assert response.data["deduped"] is False
    assert response.data["admin_review_request_id"]
    assert ModerationReport.objects.filter(project=project, reporter=student, publisher=publisher).count() == 1
    assert AdminReviewRequest.objects.filter(project=project, status="open").exists()


@pytest.mark.django_db
def test_duplicate_report_is_deduped_within_window():
    publisher = _make_user("report_dupe_owner", role="publisher")
    student = _make_user("report_dupe_student", role="student")
    project = _make_project(publisher)
    client = _client(student)

    first = client.post(
        f"/api/v1/projects/{project.id}/report/",
        {"category": "copyright", "message": "This appears copied."},
        format="json",
    )
    duplicate = client.post(
        f"/api/v1/projects/{project.id}/report/",
        {"category": "copyright", "message": "Same concern again."},
        format="json",
    )

    assert first.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.data["deduped"] is True
    assert ModerationReport.objects.filter(project=project, reporter=student, category="copyright").count() == 1
    assert AdminReviewRequest.objects.filter(project=project, status="open").count() == 1


@pytest.mark.django_db
def test_publisher_can_report_other_publishers_lesson():
    owner = _make_user("report_other_owner", role="publisher")
    reporter = _make_user("report_other_publisher", role="publisher")
    project = _make_project(owner)

    response = _client(reporter).post(
        f"/api/v1/projects/{project.id}/report/",
        {"category": "inappropriate_content", "message": "Please review this example."},
        format="json",
    )

    assert response.status_code == 201
    assert ModerationReport.objects.filter(project=project, reporter=reporter, publisher=owner).exists()


@pytest.mark.django_db
def test_publisher_sees_request_changes_note_on_own_lesson():
    publisher = _make_user("note_owner", role="publisher")
    staff = _make_user("note_staff", is_staff=True)
    project = _make_project(publisher)
    note = "Please replace the copyrighted clip."

    response = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/request-changes/",
        {"reason": note, "unpublish": False},
        format="json",
    )
    summary = _client(publisher).get(f"/api/v1/projects/{project.id}/moderation/")

    assert response.status_code == 200
    assert summary.status_code == 200
    assert summary.data["moderation_status"] == "revision_required"
    assert summary.data["admin_note"] == note


@pytest.mark.django_db
def test_admin_block_hides_public_lesson_immediately():
    publisher = _make_user("block_owner", role="publisher")
    staff = _make_user("block_staff", is_staff=True)
    project = _make_project(publisher)

    public_before = _client().get(f"/api/v1/catalog/{project.id}/")
    response = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/block/",
        {"reason": "Unsafe instruction."},
        format="json",
    )
    public_after = _client().get(f"/api/v1/catalog/{project.id}/")
    project.refresh_from_db()

    assert public_before.status_code == 200
    assert response.status_code == 200
    assert project.moderation_status == "admin_rejected"
    assert project.manual_moderation_status == "blocked"
    assert project.moderation_blocked_until_review is True
    assert project.is_published is False
    assert public_after.status_code == 404
    assert PublicationBlockEvent.objects.filter(project=project, resolved=False).exists()


@pytest.mark.django_db
def test_admin_request_changes_creates_publisher_note():
    publisher = _make_user("changes_owner", role="publisher")
    staff = _make_user("changes_staff", is_staff=True)
    project = _make_project(publisher)
    note = "Fix the misleading ownership claim."

    response = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/request-changes/",
        {"reason": note, "unpublish": True},
        format="json",
    )
    project.refresh_from_db()

    assert response.status_code == 200
    assert project.moderation_status == "revision_required"
    assert project.manual_moderation_status == "request_changes"
    assert project.moderation_blocked_until_review is True
    assert project.is_published is False
    assert project.moderation_summary["publisher_admin_note"] == note


@pytest.mark.django_db
def test_admin_approve_restores_public_eligibility_after_review_request_changes():
    publisher = _make_user("approve_owner", role="publisher")
    staff = _make_user("approve_staff", is_staff=True)
    project = _make_project(publisher)

    request_changes = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/request-changes/",
        {"reason": "Revise the cited source.", "unpublish": False},
        format="json",
    )
    hidden = _client().get(f"/api/v1/catalog/{project.id}/")
    approve = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/approve/",
        {"reason": "Revision accepted."},
        format="json",
    )
    hidden_after_approve = _client().get(f"/api/v1/catalog/{project.id}/")
    project.refresh_from_db()

    assert request_changes.status_code == 200
    assert hidden.status_code == 404
    assert approve.status_code == 200
    assert project.moderation_status == "admin_approved"
    assert project.is_published is False
    assert hidden_after_approve.status_code == 404

    publish = _client(publisher).patch(f"/api/v1/projects/{project.id}/", {"is_published": True}, format="json")
    visible = _client().get(f"/api/v1/catalog/{project.id}/")
    project.refresh_from_db()

    assert publish.status_code == 200
    assert project.is_published is True
    assert visible.status_code == 200


@pytest.mark.django_db
def test_non_admin_cannot_call_admin_moderation_actions():
    publisher = _make_user("non_admin_owner", role="publisher")
    student = _make_user("non_admin_student", role="student")
    project = _make_project(publisher)

    response = _client(student).post(
        f"/api/v1/moderation/projects/{project.id}/block/",
        {"reason": "Not allowed."},
        format="json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_block_is_sticky_against_publisher_rescan():
    publisher = _make_user("sticky_owner", role="publisher")
    staff = _make_user("sticky_staff", is_staff=True)
    project = _make_project(publisher)

    block = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/block/",
        {"reason": "Unsafe example."},
        format="json",
    )
    rescan = _client(publisher).post(
        f"/api/v1/projects/{project.id}/moderation/rescan/",
        {"phase": "manual_rescan"},
        format="json",
    )
    project.refresh_from_db()

    assert block.status_code == 200
    assert rescan.status_code == 409
    assert project.moderation_status == "admin_rejected"
    assert project.manual_moderation_status == "blocked"
    assert project.is_published is False
    assert _client().get(f"/api/v1/catalog/{project.id}/").status_code == 404


@pytest.mark.django_db
def test_admin_approve_clears_block_without_forcing_unpublished_lesson_public():
    publisher = _make_user("approve_private_owner", role="publisher")
    staff = _make_user("approve_private_staff", is_staff=True)
    project = _make_project(publisher, published=False, moderation_status="admin_rejected")
    project.manual_moderation_status = "blocked"
    project.manual_moderation_reason = "Manual block."
    project.moderation_blocked_until_review = True
    project.save(update_fields=["manual_moderation_status", "manual_moderation_reason", "moderation_blocked_until_review"])
    PublicationBlockEvent.objects.create(
        project=project,
        blocked_by="admin_manual_action",
        reason_category="manual_admin_block",
        highest_severity="high",
        message_to_user="Manual block.",
    )

    response = _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/approve/",
        {"reason": "Allowed."},
        format="json",
    )
    project.refresh_from_db()

    assert response.status_code == 200
    assert project.moderation_status == "admin_approved"
    assert project.manual_moderation_status == "approved"
    assert project.moderation_blocked_until_review is False
    assert project.is_published is False
    assert PublicationBlockEvent.objects.filter(project=project, resolved=False).count() == 0
    assert _client().get(f"/api/v1/catalog/{project.id}/").status_code == 404


@pytest.mark.django_db
def test_auto_rejected_and_manual_blocked_projects_remain_in_admin_queues():
    publisher = _make_user("queue_owner", role="publisher")
    staff = _make_user("queue_staff", is_staff=True)
    auto_project = _make_project(publisher, title="Auto rejected", moderation_status="revision_required")
    blocked_project = _make_project(publisher, title="Manual blocked")

    _client(staff).post(
        f"/api/v1/moderation/projects/{blocked_project.id}/block/",
        {"reason": "Manual block."},
        format="json",
    )

    auto_queue = _client(staff).get("/api/v1/admin/moderation/review-requests/?queue=auto_rejected")
    blocked_queue = _client(staff).get("/api/v1/admin/moderation/review-requests/?queue=rejected_blocked")

    assert auto_queue.status_code == 200
    assert blocked_queue.status_code == 200
    assert auto_project.id in {row["project_id"] for row in auto_queue.data}
    assert blocked_project.id in {row["project_id"] for row in blocked_queue.data}


@pytest.mark.django_db
def test_staff_can_review_blocked_lesson_while_student_cannot_watch():
    publisher = _make_user("blocked_review_owner", role="publisher")
    staff = _make_user("blocked_review_staff", is_staff=True)
    student = _make_user("blocked_review_student")
    project = _make_project(publisher)

    _client(staff).post(
        f"/api/v1/moderation/projects/{project.id}/block/",
        {"reason": "Manual block."},
        format="json",
    )

    assert _client(student).get(f"/api/v1/catalog/{project.id}/").status_code == 404
    review = _client(staff).get(f"/api/v1/catalog/{project.id}/")
    assert review.status_code == 200
    assert review.data["id"] == project.id
