# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun  # noqa: E402
from ai_agents import views as moderation_views  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "teacher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _make_project(owner: User, *, moderation_status: str = "not_scanned") -> Project:
    return Project.objects.create(
        title=f"{owner.username} project",
        user=owner,
        status="ready",
        moderation_status=moderation_status,
        moderation_summary={"message": "Stored moderation message."},
    )


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _summary_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/moderation/"


def _rescan_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/moderation/rescan/"


def _review_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/moderation/request-admin-review/"


def _add_run_with_finding(project: Project) -> AgentFinding:
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
    page = TranscriptPage.objects.create(
        project=project,
        order=2,
        source_slide_index=2,
        split_index=0,
        page_key="slide-3",
        original_text="Unsafe text is not exposed by the API.",
        narration_text="Narration",
    )
    return AgentFinding.objects.create(
        run=run,
        agent_slug="text_moderation_local_rules",
        agent_version="local-rules:v1",
        content_type="text",
        object_type="transcript_page",
        object_id=str(page.id),
        location={
            "project_id": project.id,
            "transcript_page_id": page.id,
            "page_key": page.page_key,
            "slide_order": page.order,
            "field_name": "original_text",
            "ui_anchor": f"transcript-page-{page.id}",
        },
        category="violence",
        severity="high",
        confidence=0.91,
        decision="block",
        user_message="Please revise this section.",
        admin_message="Admin-only details.",
        evidence_excerpt="Short excerpt for staff.",
        provider="local_rules",
        provider_raw={"internal": "not public"},
    )


@pytest.mark.django_db
def test_owner_can_get_moderation_summary():
    owner = _make_user("mod_api_owner")
    project = _make_project(owner, moderation_status="approved")

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["project_id"] == project.id
    assert response.data["moderation_status"] == "approved"
    assert response.data["can_publish"] is True
    assert response.data["message"] == "Stored moderation message."


@pytest.mark.django_db
def test_summary_exposes_editor_text_and_visual_stale_markers():
    owner = _make_user("mod_api_stale_owner")
    project = _make_project(owner, moderation_status="not_scanned")
    project.moderation_summary = {
        "message": "Studio text changed. Moderation needs to run again.",
        "editor_text_changed": {"status": "needs_rescan", "changed_fields": ["original_text"]},
        "visual_asset_scan": {"status": "needs_rescan", "asset_type": "cover"},
    }
    project.save(update_fields=["moderation_summary"])

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["editor_text_changed"]["status"] == "needs_rescan"
    assert response.data["visual_asset_scan"]["asset_type"] == "cover"


@pytest.mark.django_db
def test_non_owner_cannot_get_private_project_moderation_summary_unless_staff():
    owner = _make_user("mod_api_private_owner")
    other = _make_user("mod_api_private_other")
    staff = _make_user("mod_api_private_staff", is_staff=True)
    project = _make_project(owner)

    denied = _client(other).get(_summary_url(project))
    allowed = _client(staff).get(_summary_url(project))

    assert denied.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_staff_can_see_admin_fields():
    owner = _make_user("mod_api_staff_owner")
    staff = _make_user("mod_api_staff_user", is_staff=True)
    project = _make_project(owner, moderation_status="revision_required")
    _add_run_with_finding(project)

    response = _client(staff).get(_summary_url(project))

    finding = response.data["findings"][0]
    assert response.status_code == 200
    assert finding["confidence"] == 0.91
    assert finding["admin_message"] == "Admin-only details."
    assert finding["evidence_excerpt"] == "Short excerpt for staff."
    assert finding["provider"] == "local_rules"
    assert finding["agent_slug"] == "text_moderation_local_rules"
    assert finding["location"]["page_key"] == "slide-3"


@pytest.mark.django_db
def test_publisher_safe_summary_does_not_expose_provider_raw_or_admin_message():
    owner = _make_user("mod_api_safe_owner")
    project = _make_project(owner, moderation_status="revision_required")
    _add_run_with_finding(project)

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    finding = response.data["findings"][0]
    assert "admin_message" not in finding
    assert "provider_raw" not in finding
    assert "evidence_excerpt" not in finding
    assert "confidence" not in finding


@pytest.mark.django_db
def test_owner_can_trigger_rescan_and_project_becomes_pending(monkeypatch):
    monkeypatch.delenv("CELERY_RENDER_QUEUE", raising=False)
    owner = _make_user("mod_api_rescan_owner")
    project = _make_project(owner, moderation_status="revision_required")
    sent = {}

    def fake_dispatch(project_id, *, triggered_by_user_id, phase):
        sent.update({"project_id": project_id, "triggered_by_user_id": triggered_by_user_id, "phase": phase})
        return SimpleNamespace(id="task-moderation-1")

    monkeypatch.setattr(moderation_views, "_dispatch_moderation_task", fake_dispatch)

    response = _client(owner).post(_rescan_url(project), {"phase": "manual_rescan"}, format="json")

    project.refresh_from_db()
    assert response.status_code == 202
    assert response.data["task_id"] == "task-moderation-1"
    assert response.data["queue"] == "render"
    assert project.moderation_status == "pending"
    assert sent == {"project_id": project.id, "triggered_by_user_id": owner.id, "phase": "manual_rescan"}


@pytest.mark.django_db
def test_rescan_uses_default_phase_manual_rescan(monkeypatch):
    monkeypatch.delenv("CELERY_RENDER_QUEUE", raising=False)
    owner = _make_user("mod_api_default_rescan_owner")
    project = _make_project(owner, moderation_status="failed")
    sent = {}
    monkeypatch.setattr(
        moderation_views,
        "_dispatch_moderation_task",
        lambda project_id, *, triggered_by_user_id, phase: sent.setdefault("phase", phase) or SimpleNamespace(id="task-default"),
    )

    response = _client(owner).post(_rescan_url(project), {}, format="json")

    assert response.status_code == 202
    assert response.data["phase"] == "manual_rescan"
    assert response.data["queue"] == "render"
    assert sent["phase"] == "manual_rescan"


def test_rescan_dispatch_uses_render_queue_by_default(monkeypatch):
    monkeypatch.delenv("CELERY_RENDER_QUEUE", raising=False)
    captured = {}

    class FakeSignature:
        def apply_async(self, **kwargs):
            captured["apply_async"] = kwargs
            return SimpleNamespace(id="task-default-render")

    class FakeCeleryApp:
        def signature(self, task_name, *, args, kwargs):
            captured["signature"] = {"task_name": task_name, "args": args, "kwargs": kwargs}
            return FakeSignature()

    monkeypatch.setattr(moderation_views, "_celery_app", FakeCeleryApp())

    result = moderation_views._dispatch_moderation_task(
        123,
        triggered_by_user_id=456,
        phase="manual_rescan",
    )

    assert result.id == "task-default-render"
    assert captured["signature"] == {
        "task_name": "worker.tasks.run_project_moderation",
        "args": [123],
        "kwargs": {"triggered_by_user_id": 456, "phase": "manual_rescan"},
    }
    assert captured["apply_async"] == {"queue": "render"}


def test_rescan_dispatch_respects_celery_render_queue(monkeypatch):
    monkeypatch.setenv("CELERY_RENDER_QUEUE", "moderation-render")
    captured = {}

    class FakeSignature:
        def apply_async(self, **kwargs):
            captured["apply_async"] = kwargs
            return SimpleNamespace(id="task-custom-render")

    class FakeCeleryApp:
        def signature(self, task_name, *, args, kwargs):
            return FakeSignature()

    monkeypatch.setattr(moderation_views, "_celery_app", FakeCeleryApp())

    result = moderation_views._dispatch_moderation_task(
        123,
        triggered_by_user_id=None,
        phase="manual_rescan",
    )

    assert result.id == "task-custom-render"
    assert captured["apply_async"] == {"queue": "moderation-render"}


@pytest.mark.django_db
def test_rescan_dispatch_failure_marks_project_failed(monkeypatch):
    monkeypatch.delenv("CELERY_RENDER_QUEUE", raising=False)
    owner = _make_user("mod_api_rescan_failure_owner")
    project = _make_project(owner, moderation_status="revision_required")

    def failing_dispatch(project_id, *, triggered_by_user_id, phase):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(moderation_views, "_dispatch_moderation_task", failing_dispatch)

    response = _client(owner).post(_rescan_url(project), {"phase": "manual_rescan"}, format="json")

    project.refresh_from_db()
    assert response.status_code == 503
    assert response.data["error"] == "Could not start moderation scan."
    assert response.data["queue"] == "render"
    assert project.moderation_status == "failed"
    assert project.moderation_summary["message"] == "Could not start moderation scan."
    assert project.moderation_summary["phase"] == "manual_rescan"


@pytest.mark.django_db
def test_non_owner_cannot_trigger_rescan():
    owner = _make_user("mod_api_rescan_forbidden_owner")
    other = _make_user("mod_api_rescan_forbidden_other")
    project = _make_project(owner, moderation_status="revision_required")

    response = _client(other).post(_rescan_url(project), {}, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_can_request_admin_review_for_revision_required_project():
    owner = _make_user("mod_api_review_owner")
    project = _make_project(owner, moderation_status="revision_required")

    response = _client(owner).post(
        _review_url(project),
        {"message": "This is a historical lesson and the AI misunderstood the context."},
        format="json",
    )

    project.refresh_from_db()
    review = AdminReviewRequest.objects.get(project=project)
    assert response.status_code == 201
    assert response.data["id"] == review.id
    assert response.data["status"] == "open"
    assert review.publisher_message.startswith("This is a historical lesson")
    assert project.moderation_status == "needs_admin_review"


@pytest.mark.django_db
def test_duplicate_open_admin_review_request_is_rejected():
    owner = _make_user("mod_api_duplicate_review_owner")
    project = _make_project(owner, moderation_status="revision_required")
    AdminReviewRequest.objects.create(project=project, requested_by=owner, status="open")

    response = _client(owner).post(_review_url(project), {"message": "Please review."}, format="json")

    assert response.status_code == 400
    assert "already exists" in response.data["error"]
    assert AdminReviewRequest.objects.filter(project=project, status="open").count() == 1


@pytest.mark.django_db
def test_cannot_request_admin_review_for_approved_project():
    owner = _make_user("mod_api_approved_review_owner")
    project = _make_project(owner, moderation_status="approved")

    response = _client(owner).post(_review_url(project), {"message": ""}, format="json")

    assert response.status_code == 400
    assert response.data["moderation_status"] == "approved"
    assert AdminReviewRequest.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_staff_can_request_admin_review():
    owner = _make_user("mod_api_staff_review_owner")
    staff = _make_user("mod_api_staff_review_user", is_staff=True)
    project = _make_project(owner, moderation_status="failed")

    response = _client(staff).post(_review_url(project), {"message": "Staff escalation."}, format="json")

    assert response.status_code == 201
    review = AdminReviewRequest.objects.get(project=project)
    assert review.requested_by == staff


@pytest.mark.django_db
def test_api_includes_useful_location_labels_for_transcript_findings():
    owner = _make_user("mod_api_location_owner")
    project = _make_project(owner, moderation_status="revision_required")
    _add_run_with_finding(project)

    response = _client(owner).get(_summary_url(project))

    finding = response.data["findings"][0]
    assert response.status_code == 200
    assert finding["location_label"] == "slide-3 original text"
    assert finding["ui_anchor"].startswith("transcript-page-")
    assert finding["slide_order"] == 2
    assert finding["page_key"] == "slide-3"
    assert finding["content_type"] == "text"
    assert finding["object_type"] == "transcript_page"
