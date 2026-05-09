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

from ai_agents.models import AgentFinding, AgentRun  # noqa: E402
from ai_agents.policies import (  # noqa: E402
    project_can_publish,
    video_frame_audit_blocks_publish,
    video_frame_audit_publication_block_payload,
)
from core import views  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_ready_project(username: str, *, moderation_status: str = "approved") -> Project:
    return Project.objects.create(
        title=f"Video frame gate {username}",
        user=_make_teacher(username),
        status="ready",
        moderation_status=moderation_status,
        is_published=False,
    )


def _publish(project: Project, user: User):
    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/",
        {"is_published": True},
        format="json",
    )
    force_authenticate(request, user=user)
    return views.ProjectDetailView.as_view()(request, project_id=project.id)


def _video_frame_run(project: Project, *, final_decision: str = "allow", status: str = "done") -> AgentRun:
    return AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="video_frame_audit",
        status=status,
        final_decision=final_decision,
        summary={"final_decision": final_decision, "sampled_frame_count": 1},
    )


def _video_finding(
    run: AgentRun,
    *,
    decision: str = "needs_admin_review",
    severity: str = "medium",
    category: str = "graphic_content",
) -> AgentFinding:
    return AgentFinding.objects.create(
        run=run,
        agent_slug="video_frame_local_image_rules",
        agent_version="local-image-rules:v1",
        content_type="video_frame",
        object_type="video_frame",
        object_id="frame-1",
        location={
            "asset_type": "video_frame",
            "frame_path": "storage_local/moderation/video_frames/1/1/frame_0000.jpg",
            "timestamp_seconds": 0.0,
            "timestamp_label": "00:00:00",
        },
        category=category,
        severity=severity,
        confidence=0.9,
        decision=decision,
        user_message="Video frame audit should be reviewed.",
        admin_message="Video frame audit flagged this frame.",
        provider="local_image_rules",
        provider_raw={"format": "PNG"},
    )


def _visual_run(project: Project, *, final_decision: str = "allow", status: str = "done") -> AgentRun:
    return AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="visual_asset_scan",
        status=status,
        final_decision=final_decision,
        summary={"final_decision": final_decision},
    )


def _visual_finding(run: AgentRun) -> AgentFinding:
    return AgentFinding.objects.create(
        run=run,
        agent_slug="visual_moderation_local_image_rules",
        agent_version="local-image-rules:v1",
        content_type="image",
        object_type="slide_image",
        object_id="1",
        location={"asset_type": "slide_image", "slide_order": 1},
        category="graphic_content",
        severity="high",
        confidence=0.9,
        decision="needs_admin_review",
        user_message="Visual asset should be reviewed.",
        admin_message="Visual asset metadata validation flagged this file.",
        provider="local_image_rules",
        provider_raw={"format": "PNG"},
    )


@pytest.mark.django_db
def test_video_frame_publish_gate_disabled_keeps_existing_publish_behavior(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = False
    project = _make_ready_project("disabled")
    run = _video_frame_run(project, final_decision="needs_admin_review")
    _video_finding(run)

    response = _publish(project, project.user)

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.is_published is True


@pytest.mark.django_db
def test_video_frame_publish_gate_enabled_no_run_does_not_block(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("no_run")

    assert video_frame_audit_blocks_publish(project) is False
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_video_frame_publish_gate_enabled_clean_latest_run_allows_publish(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("clean_run")
    _video_frame_run(project, final_decision="allow")

    response = _publish(project, project.user)

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.is_published is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("decision", "severity"),
    [
        ("block", "medium"),
        ("revision_required", "medium"),
        ("needs_admin_review", "medium"),
        ("warn", "critical"),
    ],
)
def test_video_frame_publish_gate_enabled_serious_latest_finding_blocks_publish(settings, decision, severity):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project(f"blocked_{decision}_{severity}")
    run = _video_frame_run(project, final_decision="needs_admin_review")
    _video_finding(run, decision=decision, severity=severity, category="graphic_content")

    response = _publish(project, project.user)

    assert response.status_code == 400
    assert response.data["reason"] == "video_frame_audit_rejected"
    assert response.data["detail"] == "This lesson cannot be published until video frame audit findings are resolved."
    assert response.data["message"] == "This lesson cannot be published until video frame audit findings are resolved."
    assert response.data["finding_count"] == 1
    assert response.data["latest_run_id"] == run.id
    assert response.data["highest_category"] == "graphic_content"
    assert response.data["highest_severity"] == severity
    project.refresh_from_db()
    assert project.is_published is False
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_newer_clean_video_frame_run_clears_older_block(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("newer_clean")
    blocked_run = _video_frame_run(project, final_decision="needs_admin_review")
    _video_finding(blocked_run)
    clean_run = _video_frame_run(project, final_decision="allow")

    response = _publish(project, project.user)

    assert clean_run.id > blocked_run.id
    assert response.status_code == 200
    project.refresh_from_db()
    assert project.is_published is True


@pytest.mark.django_db
def test_text_moderation_block_still_blocks_with_video_frame_gate_enabled(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("text_block", moderation_status="revision_required")
    _video_frame_run(project, final_decision="allow")

    response = _publish(project, project.user)

    assert response.status_code == 400
    assert response.data["reason"] == "moderation_rejected"
    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_visual_publish_gate_still_blocks_independently(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("visual_block")
    visual_run = _visual_run(project, final_decision="needs_admin_review")
    _visual_finding(visual_run)
    _video_frame_run(project, final_decision="allow")

    response = _publish(project, project.user)

    assert response.status_code == 400
    assert response.data["reason"] == "visual_moderation_rejected"
    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_video_frame_publish_gate_does_not_mutate_project_moderation_status(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("status_unchanged", moderation_status="approved")
    run = _video_frame_run(project, final_decision="needs_admin_review")
    _video_finding(run)

    response = _publish(project, project.user)

    assert response.status_code == 400
    project.refresh_from_db()
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_project_can_publish_allows_approved_text_only_project(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("text_only", moderation_status="approved")

    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_video_frame_block_payload_includes_reason_and_finding_info(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("payload")
    run = _video_frame_run(project, final_decision="allow")
    _video_finding(run, decision="needs_admin_review", severity="high", category="graphic_content")
    _video_finding(run, decision="warn", severity="critical", category="dangerous_instruction")

    payload = video_frame_audit_publication_block_payload(project)

    assert payload["blocked"] is True
    assert payload["reason"] == "video_frame_audit_rejected"
    assert payload["finding_count"] == 2
    assert payload["latest_run_id"] == run.id
    assert payload["highest_category"] == "dangerous_instruction"
    assert payload["highest_severity"] == "critical"
