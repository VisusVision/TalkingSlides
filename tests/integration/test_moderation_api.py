# pyright: reportMissingImports=false

import os
import sys
from datetime import timedelta
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
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from ai_agents import views as moderation_views  # noqa: E402
from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun  # noqa: E402
from ai_agents.serializers import _safe_storage_relative_path  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


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


def _add_visual_finding(
    run: AgentRun,
    *,
    asset_type: str = "cover",
    content_type: str = "image",
    category: str = "provider_unavailable",
    decision: str = "needs_admin_review",
    severity: str = "medium",
    location: dict | None = None,
) -> AgentFinding:
    base_location = {"project_id": run.project_id, "asset_type": asset_type, "ui_anchor": f"asset-{asset_type}"}
    if location:
        base_location.update(location)
    return AgentFinding.objects.create(
        run=run,
        agent_slug="visual_safety_provider_unavailable",
        agent_version="provider-required:v1",
        content_type=content_type,
        object_type=asset_type,
        object_id=asset_type,
        location=base_location,
        category=category,
        severity=severity,
        confidence=0.75,
        decision=decision,
        user_message="Original technical user message.",
        admin_message="Original technical admin message.",
        evidence_excerpt="semantic_visual_provider_unavailable",
        provider="visual_safety_provider_unavailable",
    )


def _add_visual_run(project: Project, *, final_decision: str = "needs_admin_review") -> AgentRun:
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision=final_decision,
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    return run


def test_safe_storage_relative_path_extracts_known_storage_suffixes(settings, tmp_path):
    storage_root = tmp_path / "storage_local"
    settings.STORAGE_ROOT = str(storage_root)
    storage_cover = storage_root / "uploads" / "1" / "cover.png"

    assert _safe_storage_relative_path(str(storage_cover)) == "uploads/1/cover.png"
    assert _safe_storage_relative_path("/tmp/pytest-current/uploads/1/cover.png") == "uploads/1/cover.png"
    assert _safe_storage_relative_path(r"C:\tmp\pytest-current\uploads\1\cover.png") == "uploads/1/cover.png"
    assert _safe_storage_relative_path("storage_local/uploads/1/cover.png") == "uploads/1/cover.png"
    assert _safe_storage_relative_path("profiles/9/banner.png") == "profiles/9/banner.png"
    assert _safe_storage_relative_path(r"C:\tmp\pytest-current\avatars\9\uploads\avatar.png") == "avatars/9/uploads/avatar.png"
    assert _safe_storage_relative_path("/tmp/pytest-current/cover.png") == ""


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
def test_rescan_uses_latest_saved_draft_text(monkeypatch):
    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION", True, raising=False)
    owner = _make_user("mod_api_draft_rescan_owner")
    project = _make_project(owner, moderation_status="approved")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="slide-1",
        original_text="I will kill you tomorrow.",
        narration_text="I will kill you tomorrow.",
    )
    unsafe_result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)
    assert unsafe_result["moderation_status"] == "revision_required"

    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_AUTO_ENABLED", False, raising=False)
    save_response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "draft_only": True,
            "pages": [{"id": page.id, "original_text": "Safe saved draft text.", "narration_text": "Safe saved draft text."}],
        },
        format="json",
    )
    assert save_response.status_code == 200

    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    response = _client(owner).post(_rescan_url(project), {"phase": "manual_rescan"}, format="json")

    project.refresh_from_db()
    assert response.status_code == 200
    assert response.data["draft_rescan"] is True
    assert response.data["draft_metadata"]["moderation_status"] == "approved"
    assert project.draft_data["metadata"]["moderation_status"] == "approved"
    summary_response = _client(owner).get(_summary_url(project))
    assert summary_response.data["findings"] == []


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

    assert response.status_code == 409
    assert response.data["error"] == "A review request is already open. Please wait for an admin response."
    assert AdminReviewRequest.objects.filter(project=project, status="open").count() == 1


@pytest.mark.django_db
def test_blocked_lesson_owner_can_request_admin_recheck():
    owner = _make_user("mod_api_blocked_recheck_owner")
    project = _make_project(owner, moderation_status="admin_rejected")
    project.manual_moderation_status = "blocked"
    project.manual_moderation_reason = "Admin block."
    project.moderation_blocked_until_review = True
    project.save(
        update_fields=[
            "manual_moderation_status",
            "manual_moderation_reason",
            "moderation_blocked_until_review",
            "updated_at",
        ]
    )

    response = _client(owner).post(_review_url(project), {"message": "I updated the lesson."}, format="json")

    project.refresh_from_db()
    review = AdminReviewRequest.objects.get(project=project)
    assert response.status_code == 201
    assert review.status == "open"
    assert project.moderation_status == "needs_admin_review"
    assert project.manual_moderation_status == "blocked"
    assert project.moderation_blocked_until_review is True


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


@pytest.mark.django_db
def test_summary_uses_effective_blocked_status_for_unresolved_visual_provider_unavailable(tmp_path):
    owner = _make_user("mod_api_visual_provider_owner")
    project = _make_project(owner, moderation_status="approved")
    cover = tmp_path / "uploads" / str(project.id) / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"cover")
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    run = _add_visual_run(project)
    finding = _add_visual_finding(run, asset_type="cover", location={"image_path": str(cover)})

    response = _client(owner).get(_summary_url(project))

    issue = response.data["visual_issues"][0]
    assert response.status_code == 200
    assert response.data["raw_moderation_status"] == "approved"
    assert response.data["moderation_status"] == "needs_admin_review"
    assert response.data["can_publish"] is False
    assert response.data["publish_blocked_by_moderation"] is True
    assert response.data["publish_block_reason"] == "visual_moderation_rejected"
    assert "Moderation approved this lesson" not in response.data["message"]
    assert response.data["message"].startswith("We could not complete the visual safety scan")
    assert issue["asset_kind"] == "cover"
    assert issue["asset_label"] == "Lesson cover"
    assert issue["reason_title"] == "Visual safety scan unavailable"
    assert issue["technical_reason"] == "semantic_visual_provider_unavailable"
    assert issue["preview_url"].endswith(f"/api/v1/projects/{project.id}/moderation-preview/{finding.id}/")


@pytest.mark.django_db
def test_summary_does_not_call_unresolved_unsafe_visual_approved():
    owner = _make_user("mod_api_visual_unsafe_owner")
    project = _make_project(owner, moderation_status="approved")
    run = _add_visual_run(project, final_decision="block")
    _add_visual_finding(
        run,
        asset_type="slide_image",
        category="graphic_content",
        decision="block",
        severity="high",
        location={"slide_order": 2, "page_key": "slide-3"},
    )

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["moderation_status"] == "revision_required"
    assert response.data["can_publish"] is False
    assert response.data["visual_issues"][0]["reason_title"] == "Unsafe visual detected"
    assert response.data["visual_issues"][0]["asset_label"] == "Slide 3 image"
    assert "approved" not in response.data["message"].lower()


@pytest.mark.django_db
def test_visual_issue_metadata_identifies_asset_locations():
    owner = _make_user("mod_api_visual_metadata_owner")
    project = _make_project(owner, moderation_status="needs_admin_review")
    run = _add_visual_run(project)
    _add_visual_finding(run, asset_type="cover")
    _add_visual_finding(run, asset_type="custom_background", location={"page_key": "slide-2", "slide_order": 1})
    _add_visual_finding(run, asset_type="slide_image", location={"page_key": "slide-3", "slide_order": 2})
    _add_visual_finding(
        run,
        asset_type="video_frame",
        content_type="video_frame",
        location={"timestamp_seconds": 12.0, "timestamp_label": "00:00:12", "frame_path": "moderation/video_frames/frame.jpg"},
    )

    response = _client(owner).get(_summary_url(project))

    by_kind = {issue["asset_kind"]: issue for issue in response.data["visual_issues"]}
    assert response.status_code == 200
    assert by_kind["cover"]["asset_label"] == "Lesson cover"
    assert by_kind["cover"]["issue_id"] == by_kind["cover"]["finding_id"]
    assert by_kind["cover"]["issue_type"] == "visual"
    assert by_kind["cover"]["moderation_state"] == "needs_admin_review"
    assert by_kind["cover"]["source_kind"] == "lesson_cover"
    assert by_kind["cover"]["source_label"] == "Lesson cover"
    assert by_kind["custom_background"]["asset_label"] == "Custom background image"
    assert by_kind["custom_background"]["source_kind"] == "scene_background"
    assert by_kind["custom_background"]["source_label"] == "Scene background"
    assert by_kind["slide_image"]["slide_number"] == 3
    assert by_kind["slide_image"]["slide_index"] == 2
    assert by_kind["slide_image"]["source_kind"] == "slide_image"
    assert by_kind["slide_image"]["source_label"] == "Slide 3 image"
    assert by_kind["slide_image"]["asset_label"] == "Slide 3 image"
    assert by_kind["video_frame"]["timestamp_seconds"] == 12.0
    assert by_kind["video_frame"]["source_kind"] == "video_frame"
    assert by_kind["video_frame"]["asset_label"] == "Video frame at 00:00:12"


@pytest.mark.django_db
def test_summary_includes_latest_visual_issues_when_latest_text_run_passed(tmp_path):
    owner = _make_user("mod_api_latest_visual_owner")
    project = _make_project(owner, moderation_status="approved")
    cover = tmp_path / "uploads" / str(project.id) / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"cover")
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    visual_run = _add_visual_run(project, final_decision="needs_admin_review")
    finding = _add_visual_finding(visual_run, asset_type="cover", location={"image_path": str(cover)})
    text_run = AgentRun.objects.create(
        project=project,
        triggered_by=owner,
        purpose="moderation",
        phase="source_scan_draft",
        status="done",
        final_decision="allow",
    )
    project.last_moderation_run_id = text_run.id
    project.save(update_fields=["last_moderation_run_id"])

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["latest_run_id"] == text_run.id
    assert response.data["publish_block"]["latest_visual_run_id"] == visual_run.id
    assert response.data["visual_issues"][0]["finding_id"] == finding.id
    assert response.data["visual_issues"][0]["source_kind"] == "lesson_cover"
    assert response.data["visual_issues"][0]["source_label"] == "Lesson cover"
    assert response.data["findings"][0]["finding_id"] == finding.id


@pytest.mark.django_db
def test_summary_does_not_surface_stale_cover_issue_for_replaced_asset():
    owner = _make_user("mod_api_stale_cover_owner")
    project = _make_project(owner, moderation_status="needs_admin_review")
    current_cover = f"uploads/{project.id}/current-cover.png"
    stale_cover = f"uploads/{project.id}/old-blocked-cover.png"
    project.cover_image_original = current_cover
    project.cover_image_processed = current_cover
    project.moderation_summary = {
        "message": "Visual moderation requires admin review before publication.",
        "visual_asset_scan": {
            "status": "needs_rescan",
            "stale": True,
            "needs_rescan": True,
            "asset_type": "cover",
            "asset_path": stale_cover,
            "reason": "studio_cover_changed",
        },
    }
    project.save(update_fields=["cover_image_original", "cover_image_processed", "moderation_summary", "updated_at"])
    visual_run = _add_visual_run(project, final_decision="needs_admin_review")
    _add_visual_finding(visual_run, asset_type="cover", location={"image_path": stale_cover})

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["visual_issues"] == []
    assert response.data["findings"] == []


@pytest.mark.django_db
def test_new_cover_change_after_admin_approval_surfaces_cover_issue_only():
    owner = _make_user("mod_api_admin_approved_new_cover_owner")
    manual_at = timezone.now()
    changed_at = manual_at + timedelta(minutes=1)
    project = _make_project(owner, moderation_status="needs_admin_review")
    cover_path = f"uploads/{project.id}/new-cover.png"
    project.cover_image_original = cover_path
    project.cover_image_processed = cover_path
    project.manual_moderation_status = "approved"
    project.manual_moderation_at = manual_at
    project.moderation_summary = {
        "message": "Visual asset changed in Studio. Visual moderation needs to run again.",
        "visual_asset_scan": {
            "status": "needs_rescan",
            "stale": True,
            "needs_rescan": True,
            "asset_type": "cover",
            "asset_path": cover_path,
            "changed_at": changed_at.isoformat(),
            "reason": "studio_cover_changed",
        },
    }
    project.save(
        update_fields=[
            "cover_image_original",
            "cover_image_processed",
            "manual_moderation_status",
            "manual_moderation_at",
            "moderation_summary",
            "updated_at",
        ]
    )
    run = _add_visual_run(project, final_decision="needs_admin_review")
    cover_finding = _add_visual_finding(run, asset_type="cover", location={"image_path": cover_path})
    _add_visual_finding(run, asset_type="slide_image", location={"page_key": "slide-1", "slide_order": 0})

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert [
        (issue.get("finding_id"), issue.get("source_kind"), issue.get("moderation_state"))
        for issue in response.data["visual_issues"]
    ] == [(cover_finding.id, "lesson_cover", "needs_admin_review")]
    assert not any(str(issue.get("id", "")).startswith("pending-") for issue in response.data["visual_issues"])


@pytest.mark.django_db
def test_current_admin_approval_suppresses_stale_pending_cover_marker():
    owner = _make_user("mod_api_current_admin_approved_cover_owner")
    changed_at = timezone.now()
    manual_at = changed_at + timedelta(minutes=1)
    project = _make_project(owner, moderation_status="admin_approved")
    project.manual_moderation_status = "approved"
    project.manual_moderation_at = manual_at
    project.moderation_summary = {
        "message": "Admin approved this lesson for publishing.",
        "visual_asset_scan": {
            "status": "needs_rescan",
            "stale": True,
            "needs_rescan": True,
            "asset_type": "cover",
            "asset_path": f"uploads/{project.id}/previous-cover.png",
            "changed_at": changed_at.isoformat(),
            "reason": "studio_cover_changed",
        },
    }
    project.save(update_fields=["manual_moderation_status", "manual_moderation_at", "moderation_summary", "updated_at"])

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["visual_issues"] == []


@pytest.mark.django_db
def test_visual_issue_preview_metadata_uses_scoped_preview_urls_or_fallback(tmp_path):
    owner = _make_user("mod_api_visual_preview_owner")
    project = _make_project(owner, moderation_status="needs_admin_review")
    cover = tmp_path / "uploads" / str(project.id) / "cover.png"
    background = tmp_path / "uploads" / str(project.id) / "background.png"
    slide = tmp_path / "uploads" / str(project.id) / "slide.png"
    for path in (cover, background, slide):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preview")
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    run = _add_visual_run(project)
    cover_finding = _add_visual_finding(run, asset_type="cover", location={"image_path": str(cover)})
    background_finding = _add_visual_finding(
        run,
        asset_type="custom_background",
        location={"image_path": str(background), "page_key": "slide-2", "slide_order": 1},
    )
    slide_finding = _add_visual_finding(
        run,
        asset_type="slide_image",
        location={"image_path": str(slide), "page_key": "slide-3", "slide_order": 2},
    )
    missing_finding = _add_visual_finding(run, asset_type="video_frame", content_type="video_frame")

    response = _client(owner).get(_summary_url(project))

    by_id = {issue["finding_id"]: issue for issue in response.data["visual_issues"]}
    assert response.status_code == 200
    assert by_id[cover_finding.id]["preview_url"].endswith(f"/api/v1/projects/{project.id}/moderation-preview/{cover_finding.id}/")
    assert by_id[background_finding.id]["preview_url"].endswith(f"/api/v1/projects/{project.id}/moderation-preview/{background_finding.id}/")
    assert by_id[slide_finding.id]["preview_url"].endswith(f"/api/v1/projects/{project.id}/moderation-preview/{slide_finding.id}/")
    assert by_id[slide_finding.id]["slide_number"] == 3
    assert "preview_url" not in by_id[missing_finding.id]
    assert by_id[missing_finding.id]["preview_unavailable_reason"] == "video_frame_unavailable"


@pytest.mark.django_db
def test_text_finding_does_not_appear_in_visual_issues():
    owner = _make_user("mod_api_text_not_visual_owner")
    project = _make_project(owner, moderation_status="revision_required")
    _add_run_with_finding(project)

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    assert response.data["visual_issues"] == []


@pytest.mark.django_db
def test_provider_unavailable_visual_issue_uses_scan_unavailable_wording():
    owner = _make_user("mod_api_provider_unavailable_owner")
    project = _make_project(owner, moderation_status="needs_admin_review")
    run = _add_visual_run(project, final_decision="needs_admin_review")
    finding = _add_visual_finding(run, asset_type="cover")

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    by_id = {issue["finding_id"]: issue for issue in response.data["visual_issues"]}
    issue = by_id[finding.id]
    assert issue["reason_title"] == "Visual safety scan unavailable"
    assert issue["decision"] == "needs_admin_review"
    assert issue["publisher_reason_message"].startswith("We could not complete the visual safety scan")
    assert "Unsafe visual detected" not in issue["reason_title"]
    assert "blocked by visual moderation" not in issue["publisher_reason_message"]


@pytest.mark.django_db
def test_true_unsafe_visual_issue_keeps_unsafe_wording():
    owner = _make_user("mod_api_unsafe_visual_owner")
    project = _make_project(owner, moderation_status="revision_required")
    run = _add_visual_run(project, final_decision="block")
    finding = _add_visual_finding(
        run,
        asset_type="cover",
        category="sexual",
        decision="block",
        severity="high",
    )

    response = _client(owner).get(_summary_url(project))

    assert response.status_code == 200
    by_id = {issue["finding_id"]: issue for issue in response.data["visual_issues"]}
    issue = by_id[finding.id]
    assert issue["reason_title"] == "Unsafe visual detected"
    assert issue["decision"] == "block"


@pytest.mark.django_db
def test_project_detail_includes_visual_issues_for_owner_and_staff():
    owner = _make_user("mod_api_project_visual_owner")
    staff = _make_user("mod_api_project_visual_staff", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")
    project.cover_image_original = "uploads/project-detail-cover.png"
    project.cover_image_processed = "uploads/project-detail-cover.png"
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    run = _add_visual_run(project, final_decision="needs_admin_review")
    finding = _add_visual_finding(
        run,
        asset_type="cover",
        location={"image_path": "uploads/project-detail-cover.png"},
    )

    owner_response = _client(owner).get(f"/api/v1/projects/{project.id}/")
    staff_response = _client(staff).get(f"/api/v1/projects/{project.id}/")

    assert owner_response.status_code == 200
    assert staff_response.status_code == 200
    for response in (owner_response, staff_response):
        assert response.data["visual_issues"][0]["finding_id"] == finding.id
        assert response.data["visual_issues"][0]["asset_kind"] == "cover"
        assert response.data["visual_issues"][0]["preview_url"].endswith(
            f"/api/v1/projects/{project.id}/moderation-preview/{finding.id}/"
        )
