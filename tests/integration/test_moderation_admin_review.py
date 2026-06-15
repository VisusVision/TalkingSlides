# pyright: reportMissingImports=false

import os
import sys
import inspect
from pathlib import Path
from io import StringIO

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib import admin as django_admin  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from ai_agents.admin import AdminReviewRequestAdmin  # noqa: E402
from ai_agents.models import (  # noqa: E402
    AdminReviewRequest,
    AgentFinding,
    AgentRun,
    ModerationReport,
    PublicationBlockEvent,
)
from ai_agents.policies import project_can_publish  # noqa: E402
from ai_agents.views import _complete_admin_review_request  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "teacher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_project(owner: User, *, moderation_status: str = "revision_required") -> Project:
    return Project.objects.create(
        title=f"{owner.username} moderation project",
        user=owner,
        status="ready",
        moderation_status=moderation_status,
    )


def _add_run_with_finding(project: Project, *, timestamp_seconds=None) -> AgentRun:
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="source_scan",
        status="done",
        final_decision="block",
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    location = {
        "project_id": project.id,
        "transcript_page_id": 321,
        "page_key": "slide-4",
        "slide_order": 3,
        "field_name": "narration_text",
        "ui_anchor": "transcript-page-321",
    }
    if timestamp_seconds is not None:
        location["timestamp_seconds"] = timestamp_seconds
        location["timestamp_label"] = "1:12"
    AgentFinding.objects.create(
        run=run,
        agent_slug="text_moderation_local_rules",
        agent_version="local-rules:v1",
        content_type="text",
        object_type="transcript_page",
        object_id="321",
        location=location,
        category="violence",
        severity="critical",
        confidence=0.98,
        decision="block",
        user_message="Please revise this section.",
        admin_message="Direct violent threat detected.",
        evidence_excerpt="Short staff-only excerpt.",
        provider="local_rules",
        provider_raw={"internal": "not returned"},
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="text_moderation_local_rules",
        agent_version="local-rules:v1",
        content_type="text",
        object_type="project",
        object_id=str(project.id),
        location={"project_id": project.id, "field_name": "title", "ui_anchor": "project-title"},
        category="profanity",
        severity="medium",
        confidence=0.72,
        decision="warn",
        user_message="Check the title wording.",
        admin_message="Staff context.",
        evidence_excerpt="Title excerpt.",
        provider="local_rules",
        provider_raw={"internal": "not returned"},
    )
    return run


def _make_review(project: Project, *, status: str = "open") -> AdminReviewRequest:
    run = AgentRun.objects.filter(project=project).order_by("-id").first()
    return AdminReviewRequest.objects.create(
        project=project,
        run=run,
        requested_by=project.user,
        status=status,
        publisher_message="This is educational historical context.",
    )


def _admin_request(user: User):
    request = RequestFactory().post("/admin/ai_agents/adminreviewrequest/")
    request.user = user
    return request


def _review_admin(monkeypatch):
    model_admin = AdminReviewRequestAdmin(AdminReviewRequest, django_admin.site)
    messages = []
    monkeypatch.setattr(model_admin, "message_user", lambda request, message, *args, **kwargs: messages.append(message))
    return model_admin, messages


def _list_url() -> str:
    return "/api/v1/admin/moderation/review-requests/"


def _tab_url(tab: str, item_filter: str = "all") -> str:
    return f"/api/v1/admin/moderation/review-requests/?tab={tab}&filter={item_filter}"


def _detail_url(review: AdminReviewRequest) -> str:
    return f"/api/v1/admin/moderation/review-requests/{review.id}/"


def _approve_url(review: AdminReviewRequest) -> str:
    return f"/api/v1/admin/moderation/review-requests/{review.id}/approve/"


def _reject_url(review: AdminReviewRequest) -> str:
    return f"/api/v1/admin/moderation/review-requests/{review.id}/reject/"


@pytest.mark.django_db
def test_non_staff_cannot_list_review_requests():
    owner = _make_user("admin_review_list_nonstaff_owner")
    project = _make_project(owner)
    _make_review(project)

    response = _client(owner).get(_list_url())

    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_can_list_review_requests():
    owner = _make_user("admin_review_list_owner")
    staff = _make_user("admin_review_list_staff", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)

    response = _client(staff).get(_list_url())

    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["id"] == review.id


@pytest.mark.django_db
def test_staff_list_includes_project_request_and_status_fields():
    owner = _make_user("admin_review_fields_owner")
    staff = _make_user("admin_review_fields_staff", is_staff=True)
    project = _make_project(owner)
    _add_run_with_finding(project)
    review = _make_review(project)

    response = _client(staff).get(_list_url())

    row = response.data[0]
    assert response.status_code == 200
    assert row["id"] == review.id
    assert row["project_id"] == project.id
    assert row["project_title"] == project.title
    assert row["requested_by_username"] == owner.username
    assert row["status"] == "open"
    assert row["moderation_status"] == "revision_required"
    assert row["publisher_message"] == "This is educational historical context."
    assert row["admin_response"] == ""
    assert row["highest_severity"] == "critical"
    assert row["highest_category"] == "violence"
    assert row["primary_asset_label"] == ""
    assert row["latest_findings_summary"][0]["location_label"] == "slide-4 narration text"
    assert row["visual_issues"] == []
    assert "asset_kind" not in row["latest_findings_summary"][0]
    assert row["latest_findings_summary"][0]["reason_title"] == "Violence"
    assert "visual" not in row["finding_badges"]


@pytest.mark.django_db
def test_staff_list_surfaces_visual_provider_unavailable_reason_and_asset():
    owner = _make_user("admin_review_visual_owner")
    staff = _make_user("admin_review_visual_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    run = AgentRun.objects.create(
        project=project,
        triggered_by=owner,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="needs_admin_review",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_safety_provider_unavailable",
        agent_version="provider-required:v1",
        content_type="image",
        object_type="custom_background",
        object_id="custom_background",
        location={"project_id": project.id, "asset_type": "custom_background", "page_key": "slide-2"},
        category="provider_unavailable",
        severity="medium",
        confidence=0.75,
        decision="needs_admin_review",
        user_message="Technical fallback.",
        admin_message="Technical fallback.",
        evidence_excerpt="semantic_visual_provider_unavailable",
        provider="visual_safety_provider_unavailable",
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    _make_review(project)

    response = _client(staff).get(_list_url())

    row = next(item for item in response.data if item["project_id"] == project.id)
    issue = row["visual_issues"][0]
    assert response.status_code == 200
    assert row["primary_reason_title"] == "Visual safety scan unavailable"
    assert row["primary_asset_label"] == "Custom background image"
    assert issue["asset_kind"] == "custom_background"
    assert issue["asset_label"] == "Custom background image"
    assert issue["source_kind"] == "scene_background"
    assert issue["source_label"] == "Scene background"
    assert issue["admin_reason_message"].startswith("The semantic visual safety provider did not return")


@pytest.mark.django_db
def test_staff_list_serializes_cover_and_slide_visual_targets_without_text_fallbacks():
    owner = _make_user("admin_review_visual_targets_owner")
    staff = _make_user("admin_review_visual_targets_staff", is_staff=True)
    project = _make_project(owner, moderation_status="revision_required")
    run = AgentRun.objects.create(
        project=project,
        triggered_by=owner,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="block",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_moderation",
        agent_version="local-rules:v1",
        content_type="image",
        object_type="cover",
        object_id=str(project.id),
        location={"project_id": project.id, "asset_type": "cover", "ui_anchor": "lesson-cover"},
        category="graphic_content",
        severity="high",
        confidence=0.91,
        decision="block",
        user_message="Replace the cover.",
        admin_message="Unsafe cover image.",
        provider="local_image_rules",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_moderation",
        agent_version="local-rules:v1",
        content_type="image",
        object_type="slide_image",
        object_id="2",
        location={"project_id": project.id, "asset_type": "slide_image", "slide_order": 2, "page_key": "slide-3"},
        category="violence",
        severity="high",
        confidence=0.9,
        decision="block",
        user_message="Replace slide image.",
        admin_message="Unsafe slide image.",
        provider="local_image_rules",
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    _make_review(project)

    response = _client(staff).get(_list_url())

    row = next(item for item in response.data if item["project_id"] == project.id)
    issues = {issue["asset_kind"]: issue for issue in row["visual_issues"]}
    assert response.status_code == 200
    assert set(issues) == {"cover", "slide_image"}
    assert issues["cover"]["asset_label"] == "Lesson cover"
    assert issues["cover"]["source_kind"] == "lesson_cover"
    assert issues["slide_image"]["asset_label"] == "Slide 3 image"
    assert issues["slide_image"]["source_kind"] == "slide_image"
    assert issues["slide_image"]["source_label"] == "Slide 3 image"
    assert issues["slide_image"]["slide_number"] == 3


@pytest.mark.django_db
def test_moderation_open_tab_returns_review_auto_block_and_reports():
    owner = _make_user("admin_review_open_tab_owner")
    staff = _make_user("admin_review_open_tab_staff", is_staff=True)
    review_project = _make_project(owner, moderation_status="needs_admin_review")
    auto_project = _make_project(owner, moderation_status="revision_required")
    report_project = _make_project(owner, moderation_status="admin_approved")
    _make_review(review_project)
    ModerationReport.objects.create(
        reporter=staff,
        project=report_project,
        publisher=owner,
        category="copyright",
        message="This may be copied.",
        status="open",
    )

    response = _client(staff).get(_tab_url("open"))

    assert response.status_code == 200
    project_ids = {row["project_id"] for row in response.data}
    assert {review_project.id, auto_project.id, report_project.id}.issubset(project_ids)
    assert all("allowed_actions" in row for row in response.data)


@pytest.mark.django_db
def test_moderation_history_tab_and_filters_return_terminal_items():
    owner = _make_user("admin_review_history_tab_owner")
    staff = _make_user("admin_review_history_tab_staff", is_staff=True)
    approved_project = _make_project(owner, moderation_status="admin_approved")
    rejected_project = _make_project(owner, moderation_status="admin_rejected")
    approved_review = _make_review(approved_project, status="approved")
    rejected_review = _make_review(rejected_project, status="rejected")

    history = _client(staff).get(_tab_url("history"))
    approved = _client(staff).get(_tab_url("history", "approved"))
    rejected = _client(staff).get(_tab_url("history", "rejected_blocked"))

    assert history.status_code == 200
    assert {approved_review.id, rejected_review.id}.issubset(
        {row["id"] for row in history.data if row["source_type"] == "review_request"}
    )
    assert any(row["id"] == approved_review.id for row in approved.data)
    assert any(row["id"] == rejected_review.id for row in rejected.data)


@pytest.mark.django_db
def test_moderation_paginated_tab_returns_limited_results_with_total():
    owner = _make_user("admin_review_page_owner")
    staff = _make_user("admin_review_page_staff", is_staff=True)
    reviews = []
    for index in range(5):
        project = _make_project(owner, moderation_status="approved")
        project.title = f"Paginated moderation item {index}"
        project.save(update_fields=["title"])
        _add_run_with_finding(project)
        reviews.append(_make_review(project))

    response = _client(staff).get(f"{_tab_url('open')}&limit=2&offset=0")

    assert response.status_code == 200
    assert response.data["count"] == 5
    assert response.data["total"] == 5
    assert response.data["limit"] == 2
    assert response.data["offset"] == 0
    assert response.data["has_more"] is True
    assert len(response.data["results"]) == 2
    assert {row["id"] for row in response.data["results"]}.issubset({review.id for review in reviews})


@pytest.mark.django_db
def test_moderation_search_finds_older_item_not_in_first_page():
    owner = _make_user("admin_review_search_old_owner")
    staff = _make_user("admin_review_search_old_staff", is_staff=True)
    old_project = _make_project(owner, moderation_status="approved")
    old_project.title = "Ancient Algebra Moderation Needle"
    old_project.save(update_fields=["title"])
    _add_run_with_finding(old_project)
    old_review = _make_review(old_project)

    for index in range(4):
        project = _make_project(owner, moderation_status="approved")
        project.title = f"Newer moderation item {index}"
        project.save(update_fields=["title"])
        _add_run_with_finding(project)
        _make_review(project)

    first_page = _client(staff).get(f"{_tab_url('open')}&limit=2&offset=0")
    search_page = _client(staff).get(f"{_tab_url('open')}&limit=2&offset=0&q=Ancient%20Algebra")

    assert first_page.status_code == 200
    assert old_review.id not in {row["id"] for row in first_page.data["results"]}
    assert search_page.status_code == 200
    assert search_page.data["count"] == 1
    assert [row["id"] for row in search_page.data["results"]] == [old_review.id]


@pytest.mark.django_db
def test_moderation_paginated_filters_apply_server_side():
    owner = _make_user("admin_review_filter_server_owner")
    staff = _make_user("admin_review_filter_server_staff", is_staff=True)
    violent_project = _make_project(owner, moderation_status="approved")
    violent_project.title = "Violence category lesson"
    violent_project.save(update_fields=["title"])
    _add_run_with_finding(violent_project)
    violent_review = _make_review(violent_project)

    copyright_project = _make_project(owner, moderation_status="approved")
    copyright_project.title = "Copyright category lesson"
    copyright_project.save(update_fields=["title"])
    copyright_run = AgentRun.objects.create(
        project=copyright_project,
        triggered_by=owner,
        purpose="moderation",
        phase="source_scan",
        status="done",
        final_decision="needs_admin_review",
    )
    copyright_project.last_moderation_run_id = copyright_run.id
    copyright_project.save(update_fields=["last_moderation_run_id"])
    AgentFinding.objects.create(
        run=copyright_run,
        agent_slug="text_moderation_local_rules",
        agent_version="local-rules:v1",
        content_type="text",
        object_type="project",
        object_id=str(copyright_project.id),
        location={"project_id": copyright_project.id, "field_name": "title"},
        category="copyright",
        severity="medium",
        confidence=0.88,
        decision="needs_admin_review",
        user_message="Possible ownership concern.",
        admin_message="Possible ownership concern.",
        provider="local_rules",
    )
    copyright_review = _make_review(copyright_project)

    response = _client(staff).get(f"{_tab_url('open')}&limit=10&offset=0&category=violence")

    assert response.status_code == 200
    result_ids = {row["id"] for row in response.data["results"]}
    assert violent_review.id in result_ids
    assert copyright_review.id not in result_ids


@pytest.mark.django_db
def test_staff_can_approve_open_request():
    owner = _make_user("admin_review_approve_owner")
    staff = _make_user("admin_review_approve_staff", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)

    response = _client(staff).post(
        _approve_url(review),
        {"admin_response": "Reviewed and approved as educational context."},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "approved"
    assert response.data["admin_response"] == "Reviewed and approved as educational context."


@pytest.mark.django_db
def test_approve_sets_request_status_approved():
    owner = _make_user("admin_review_status_owner")
    staff = _make_user("admin_review_status_staff", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)

    _client(staff).post(_approve_url(review), {}, format="json")

    review.refresh_from_db()
    assert review.status == "approved"
    assert review.reviewed_by == staff
    assert review.reviewed_at is not None


@pytest.mark.django_db
def test_approve_sets_project_moderation_status_admin_approved():
    owner = _make_user("admin_review_project_owner")
    staff = _make_user("admin_review_project_staff", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)

    _client(staff).post(_approve_url(review), {}, format="json")

    project.refresh_from_db()
    assert project.moderation_status == "admin_approved"


@pytest.mark.django_db
def test_approve_marks_related_publication_block_events_resolved():
    owner = _make_user("admin_review_block_owner")
    staff = _make_user("admin_review_block_staff", is_staff=True)
    project = _make_project(owner)
    run = _add_run_with_finding(project)
    review = _make_review(project)
    block = PublicationBlockEvent.objects.create(
        project=project,
        run=run,
        blocked_by="text_moderation_local_rules",
        reason_category="violence",
        highest_severity="critical",
        message_to_user="Please revise this section.",
    )

    _client(staff).post(_approve_url(review), {}, format="json")

    block.refresh_from_db()
    assert block.resolved is True
    assert block.resolved_by == staff
    assert block.resolved_at is not None


@pytest.mark.django_db
def test_admin_approve_visual_review_clears_blocked_api_state():
    owner = _make_user("admin_review_visual_approve_owner")
    staff = _make_user("admin_review_visual_approve_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    run = AgentRun.objects.create(
        project=project,
        triggered_by=owner,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="needs_admin_review",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_safety_provider_unavailable",
        agent_version="provider-required:v1",
        content_type="image",
        object_type="cover",
        object_id="cover",
        location={"project_id": project.id, "asset_type": "cover"},
        category="provider_unavailable",
        severity="medium",
        confidence=0.75,
        decision="needs_admin_review",
        evidence_excerpt="semantic_visual_provider_unavailable",
        provider="visual_safety_provider_unavailable",
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    review = _make_review(project)
    block = PublicationBlockEvent.objects.create(
        project=project,
        run=run,
        blocked_by="visual_safety_provider_unavailable",
        reason_category="provider_unavailable",
        highest_severity="medium",
        message_to_user="Visual needs review.",
    )

    approve_response = _client(staff).post(_approve_url(review), {"admin_response": "Reviewed safe."}, format="json")
    summary_response = _client(owner).get(f"/api/v1/projects/{project.id}/moderation/")

    project.refresh_from_db()
    block.refresh_from_db()
    assert approve_response.status_code == 200
    assert block.resolved is True
    assert project.moderation_status == "admin_approved"
    assert project_can_publish(project) is True
    assert summary_response.data["moderation_status"] == "admin_approved"
    assert summary_response.data["can_publish"] is True
    assert summary_response.data["publish_blocked_by_moderation"] is False


@pytest.mark.django_db
def test_staff_can_reject_open_request():
    owner = _make_user("admin_review_reject_owner")
    staff = _make_user("admin_review_reject_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    review = _make_review(project)

    response = _client(staff).post(
        _reject_url(review),
        {"admin_response": "Please revise the violent section on Slide 4."},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "rejected"
    assert response.data["admin_response"] == "Please revise the violent section on Slide 4."


@pytest.mark.django_db
def test_reject_sets_request_status_rejected():
    owner = _make_user("admin_review_reject_status_owner")
    staff = _make_user("admin_review_reject_status_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    review = _make_review(project)

    _client(staff).post(_reject_url(review), {}, format="json")

    review.refresh_from_db()
    assert review.status == "rejected"
    assert review.reviewed_by == staff
    assert review.reviewed_at is not None


@pytest.mark.django_db
def test_reject_sets_project_moderation_status_admin_rejected():
    owner = _make_user("admin_review_reject_project_owner")
    staff = _make_user("admin_review_reject_project_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    review = _make_review(project)

    _client(staff).post(_reject_url(review), {}, format="json")

    project.refresh_from_db()
    assert project.moderation_status == "admin_rejected"


@pytest.mark.django_db
def test_cannot_approve_or_reject_non_open_request():
    owner = _make_user("admin_review_closed_owner")
    staff = _make_user("admin_review_closed_staff", is_staff=True)
    approved_project = _make_project(owner, moderation_status="admin_approved")
    rejected_project = _make_project(owner, moderation_status="admin_rejected")
    approved_review = _make_review(approved_project, status="approved")
    rejected_review = _make_review(rejected_project, status="rejected")

    approve_response = _client(staff).post(_approve_url(approved_review), {}, format="json")
    reject_response = _client(staff).post(_reject_url(rejected_review), {}, format="json")

    assert approve_response.status_code == 400
    assert reject_response.status_code == 400
    assert "Only open" in approve_response.data["error"]
    assert "Only open" in reject_response.data["error"]


def test_admin_review_lock_query_does_not_select_related_nullable_relations():
    source = inspect.getsource(_complete_admin_review_request)

    assert "AdminReviewRequest.objects.select_for_update()\n            .filter(" in source
    assert "AdminReviewRequest.objects.select_for_update()\n            .select_related(" not in source


@pytest.mark.django_db
def test_admin_approved_project_passes_existing_publish_gate():
    owner = _make_user("admin_review_publish_gate_owner")
    staff = _make_user("admin_review_publish_gate_staff", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)

    _client(staff).post(_approve_url(review), {}, format="json")

    project.refresh_from_db()
    assert project.moderation_status == "admin_approved"
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_admin_approve_after_recheck_clears_manual_block():
    owner = _make_user("admin_review_recheck_owner")
    staff = _make_user("admin_review_recheck_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    project.manual_moderation_status = "blocked"
    project.manual_moderation_reason = "Blocked before recheck."
    project.moderation_blocked_until_review = True
    project.save(
        update_fields=[
            "manual_moderation_status",
            "manual_moderation_reason",
            "moderation_blocked_until_review",
            "updated_at",
        ]
    )
    review = _make_review(project)

    response = _client(staff).post(_approve_url(review), {"admin_response": "Fixed."}, format="json")

    project.refresh_from_db()
    assert response.status_code == 200
    assert project.moderation_status == "admin_approved"
    assert project.manual_moderation_status == "approved"
    assert project.moderation_blocked_until_review is False
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_admin_detail_includes_useful_finding_location_fields():
    owner = _make_user("admin_review_detail_owner")
    staff = _make_user("admin_review_detail_staff", is_staff=True)
    project = _make_project(owner)
    _add_run_with_finding(project, timestamp_seconds=72.4)
    review = _make_review(project)

    response = _client(staff).get(_detail_url(review))

    finding = response.data["findings"][0]
    assert response.status_code == 200
    assert response.data["project_moderation"]["findings"][0]["admin_message"] == "Staff context."
    assert finding["slide_order"] == 3
    assert finding["page_key"] == "slide-4"
    assert finding["timestamp_seconds"] == 72.4
    assert finding["timestamp_label"] == "1:12"
    assert finding["ui_anchor"] == "transcript-page-321"
    assert finding["location_label"] == "1:12"
    assert response.data["open_project_studio_hint"] == f"/studio?lesson={project.id}&review=1"
    assert response.data["open_watch_timestamp_hint"] == f"/watch?lesson={project.id}&review=1&t=72.4"
    assert "provider_raw" not in response.data["findings"][0]


@pytest.mark.django_db
def test_non_staff_cannot_approve_or_reject():
    owner = _make_user("admin_review_action_nonstaff_owner")
    other = _make_user("admin_review_action_nonstaff_other")
    project = _make_project(owner)
    review = _make_review(project)

    approve_response = _client(other).post(_approve_url(review), {}, format="json")
    reject_response = _client(other).post(_reject_url(review), {}, format="json")

    assert approve_response.status_code == 403
    assert reject_response.status_code == 403
    review.refresh_from_db()
    assert review.status == "open"


@pytest.mark.django_db
def test_django_admin_approve_action_sets_project_admin_approved(monkeypatch):
    owner = _make_user("admin_action_approve_owner")
    staff = _make_user("admin_action_approve_staff", is_staff=True)
    project = _make_project(owner)
    review = _make_review(project)
    model_admin, messages = _review_admin(monkeypatch)

    model_admin.approve_selected_open_requests(_admin_request(staff), AdminReviewRequest.objects.filter(pk=review.pk))

    review.refresh_from_db()
    project.refresh_from_db()
    assert review.status == "approved"
    assert review.reviewed_by == staff
    assert review.reviewed_at is not None
    assert project.moderation_status == "admin_approved"
    assert "Approved 1" in messages[0]


@pytest.mark.django_db
def test_django_admin_reject_action_sets_project_admin_rejected(monkeypatch):
    owner = _make_user("admin_action_reject_owner")
    staff = _make_user("admin_action_reject_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    review = _make_review(project)
    model_admin, messages = _review_admin(monkeypatch)

    model_admin.reject_selected_open_requests(_admin_request(staff), AdminReviewRequest.objects.filter(pk=review.pk))

    review.refresh_from_db()
    project.refresh_from_db()
    assert review.status == "rejected"
    assert review.reviewed_by == staff
    assert review.reviewed_at is not None
    assert project.moderation_status == "admin_rejected"
    assert "Rejected 1" in messages[0]


@pytest.mark.django_db
def test_django_admin_approve_action_resolves_publication_block_events(monkeypatch):
    owner = _make_user("admin_action_block_owner")
    staff = _make_user("admin_action_block_staff", is_staff=True)
    project = _make_project(owner)
    run = _add_run_with_finding(project)
    review = _make_review(project)
    block = PublicationBlockEvent.objects.create(
        project=project,
        run=run,
        blocked_by="text_moderation_local_rules",
        reason_category="violence",
        highest_severity="critical",
        message_to_user="Please revise this section.",
    )
    model_admin, _messages = _review_admin(monkeypatch)

    model_admin.approve_selected_open_requests(_admin_request(staff), AdminReviewRequest.objects.filter(pk=review.pk))

    block.refresh_from_db()
    assert block.resolved is True
    assert block.resolved_by == staff
    assert block.resolved_at is not None


@pytest.mark.django_db
def test_django_admin_actions_skip_non_open_requests(monkeypatch):
    owner = _make_user("admin_action_skip_owner")
    staff = _make_user("admin_action_skip_staff", is_staff=True)
    project = _make_project(owner, moderation_status="admin_rejected")
    review = _make_review(project, status="rejected")
    model_admin, messages = _review_admin(monkeypatch)

    model_admin.approve_selected_open_requests(_admin_request(staff), AdminReviewRequest.objects.filter(pk=review.pk))

    review.refresh_from_db()
    project.refresh_from_db()
    assert review.status == "rejected"
    assert project.moderation_status == "admin_rejected"
    assert "skipped 1" in messages[0]


@pytest.mark.django_db
def test_run_moderation_scan_sync_command_prints_useful_output():
    owner = _make_user("admin_command_scan_owner")
    project = _make_project(owner, moderation_status="not_scanned")
    project.description = "This lesson contains shit that should be revised."
    project.save(update_fields=["description"])
    stdout = StringIO()

    call_command("run_moderation_scan", project_id=project.id, sync=True, stdout=stdout)

    project.refresh_from_db()
    output = stdout.getvalue()
    assert project.moderation_status == "revision_required"
    assert "Project:" in output
    assert "Old moderation_status: not_scanned" in output
    assert "New moderation_status: revision_required" in output
    assert "Latest run id:" in output
    assert "Final decision: block" in output
    assert "Finding count: 1" in output
    assert "Categories: profanity=1" in output
    assert "Severities: high=1" in output
    assert "create_moderation_review_request" in output
