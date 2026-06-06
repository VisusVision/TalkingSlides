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
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "teacher", is_staff: bool = False, is_superuser: bool = False) -> User:
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


def _make_project(owner: User, *, moderation_status: str = "revision_required") -> Project:
    return Project.objects.create(
        title=f"{owner.username} review lesson",
        user=owner,
        status="ready",
        moderation_status=moderation_status,
    )


def _add_run(project: Project, *, final_decision: str = "block") -> AgentRun:
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="source_scan",
        status="done",
        final_decision=final_decision,
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    AgentFinding.objects.create(
        run=run,
        agent_slug="text_moderation_local_rules",
        agent_version="local-rules:v1",
        content_type="text",
        object_type="transcript_page",
        object_id="1",
        location={"project_id": project.id, "page_key": "slide-1", "field_name": "narration_text"},
        category="violence",
        severity="high",
        confidence=0.96,
        decision="block",
        user_message="Revise this section.",
        admin_message="Staff-only moderation details.",
        provider="local_rules",
    )
    return run


def _make_review(
    project: Project,
    *,
    status: str = "open",
    reviewed_by: User | None = None,
) -> AdminReviewRequest:
    return AdminReviewRequest.objects.create(
        project=project,
        run=AgentRun.objects.filter(project=project).order_by("-id").first(),
        requested_by=project.user,
        reviewed_by=reviewed_by,
        status=status,
        publisher_message="Please review this educational lesson.",
        admin_response="Reviewed." if status != "open" else "",
        reviewed_at=timezone.now() if status != "open" else None,
    )


def _list_url(status: str | None = None) -> str:
    base = "/api/v1/admin/moderation/review-requests/"
    return f"{base}?status={status}" if status is not None else base


def _approve_url(review: AdminReviewRequest) -> str:
    return f"/api/v1/admin/moderation/review-requests/{review.id}/approve/"


def _reject_url(review: AdminReviewRequest) -> str:
    return f"/api/v1/admin/moderation/review-requests/{review.id}/reject/"


def _response_url(review: AdminReviewRequest) -> str:
    return f"/api/v1/admin/moderation/review-requests/{review.id}/response/"


@pytest.mark.django_db
def test_staff_and_superuser_can_list_review_requests():
    owner = _make_user("history_owner")
    staff = _make_user("history_staff", is_staff=True)
    superuser = _make_user("history_super", is_superuser=True)
    project = _make_project(owner)
    review = _make_review(project)

    staff_response = _client(staff).get(_list_url())
    super_response = _client(superuser).get(_list_url())

    assert staff_response.status_code == 200
    assert super_response.status_code == 200
    assert [row["id"] for row in staff_response.data] == [review.id]
    assert [row["id"] for row in super_response.data] == [review.id]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("username", "role"),
    [
        ("history_publisher_forbidden", "publisher"),
        ("history_student_forbidden", "student"),
    ],
)
def test_non_staff_cannot_list_review_history(username: str, role: str):
    user = _make_user(username, role=role)
    owner = _make_user(f"{username}_owner")
    _make_review(_make_project(owner))

    response = _client(user).get(_list_url("all"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_cannot_list_review_history():
    owner = _make_user("history_anon_owner")
    _make_review(_make_project(owner))

    response = _client().get(_list_url("all"))

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_review_status_filters_return_open_approved_rejected_and_all():
    owner = _make_user("history_filter_owner")
    staff = _make_user("history_filter_staff", is_staff=True)
    open_project = _make_project(owner)
    approved_project = _make_project(owner, moderation_status="admin_approved")
    rejected_project = _make_project(owner, moderation_status="admin_rejected")
    _add_run(open_project)
    _add_run(approved_project, final_decision="needs_admin_review")
    _add_run(rejected_project, final_decision="block")
    open_review = _make_review(open_project, status="open")
    approved_review = _make_review(approved_project, status="approved", reviewed_by=staff)
    rejected_review = _make_review(rejected_project, status="rejected", reviewed_by=staff)
    client = _client(staff)

    default_rows = client.get(_list_url()).data
    approved_rows = client.get(_list_url("approved")).data
    rejected_rows = client.get(_list_url("rejected")).data
    all_rows = client.get(_list_url("all")).data

    assert [row["id"] for row in default_rows] == [open_review.id]
    assert [row["id"] for row in approved_rows] == [approved_review.id]
    assert [row["id"] for row in rejected_rows] == [rejected_review.id]
    assert {row["id"] for row in all_rows} == {open_review.id, approved_review.id, rejected_review.id}


@pytest.mark.django_db
def test_review_history_payload_includes_summary_fields():
    owner = _make_user("history_payload_owner")
    staff = _make_user("history_payload_staff", is_staff=True)
    project = _make_project(owner)
    run = _add_run(project)
    review = _make_review(project)

    response = _client(staff).get(_list_url())

    row = response.data[0]
    assert response.status_code == 200
    assert row["id"] == review.id
    assert row["requested_by_id"] == owner.id
    assert row["requested_by_username"] == owner.username
    assert row["publisher_username"] == owner.username
    assert row["latest_run_id"] == run.id
    assert row["latest_final_decision"] == "block"
    assert row["finding_count"] == 1
    assert row["categories_summary"] == {"violence": 1}
    assert row["severities_summary"] == {"high": 1}
    assert row["requested_at"] == row["created_at"]
    assert row["updated_at"]


@pytest.mark.django_db
def test_publisher_cannot_approve_or_reject_but_admin_can():
    owner = _make_user("history_action_owner", role="publisher")
    admin = _make_user("history_action_admin", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)

    publisher_approve = _client(owner).post(_approve_url(review), {}, format="json")
    publisher_reject = _client(owner).post(_reject_url(review), {}, format="json")
    admin_approve = _client(admin).post(_approve_url(review), {"admin_response": "Approved."}, format="json")

    assert publisher_approve.status_code == 403
    assert publisher_reject.status_code == 403
    assert admin_approve.status_code == 200
    assert admin_approve.data["status"] == "approved"


@pytest.mark.django_db
def test_admin_can_send_response_without_changing_review_status():
    owner = _make_user("history_response_owner", role="publisher")
    admin = _make_user("history_response_admin", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    review = _make_review(project)

    response = _client(admin).post(
        _response_url(review),
        {"admin_response": "Please revise Slide 1 and resubmit."},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "open"
    assert response.data["admin_response"] == "Please revise Slide 1 and resubmit."
    review.refresh_from_db()
    project.refresh_from_db()
    assert review.status == "open"
    assert review.admin_response == "Please revise Slide 1 and resubmit."
    assert project.moderation_status == "needs_admin_review"


@pytest.mark.django_db
def test_publisher_cannot_send_admin_response():
    owner = _make_user("history_response_forbidden_owner", role="publisher")
    project = _make_project(owner)
    review = _make_review(project)

    response = _client(owner).post(
        _response_url(review),
        {"admin_response": "Publisher cannot write this."},
        format="json",
    )

    assert response.status_code == 403
    review.refresh_from_db()
    assert review.admin_response == ""


@pytest.mark.django_db
def test_publisher_moderation_summary_includes_latest_admin_response():
    owner = _make_user("history_response_visible_owner", role="publisher")
    admin = _make_user("history_response_visible_admin", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    review = _make_review(project)
    _client(admin).post(
        _response_url(review),
        {"admin_response": "This response is visible before approval."},
        format="json",
    )

    response = _client(owner).get(f"/api/v1/projects/{project.id}/moderation/")

    assert response.status_code == 200
    assert response.data["admin_review"]["id"] == review.id
    assert response.data["admin_review"]["status"] == "open"
    assert response.data["admin_review"]["admin_response"] == "This response is visible before approval."


@pytest.mark.django_db
def test_publisher_can_still_request_admin_review_for_own_lesson():
    publisher = _make_user("history_resubmit_publisher", role="publisher")
    project = _make_project(publisher, moderation_status="revision_required")

    response = _client(publisher).post(
        f"/api/v1/projects/{project.id}/moderation/request-admin-review/",
        {"message": "Please review."},
        format="json",
    )

    assert response.status_code == 201
    assert AdminReviewRequest.objects.filter(project=project, status="open").exists()
