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
from ai_agents.policies import project_can_publish, visual_moderation_blocks_publish  # noqa: E402
from core import views  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_ready_project(username: str, *, moderation_status: str = "approved") -> Project:
    return Project.objects.create(
        title=f"Visual gate {username}",
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


def _finding(
    run: AgentRun,
    *,
    decision: str = "needs_admin_review",
    severity: str = "medium",
) -> AgentFinding:
    return AgentFinding.objects.create(
        run=run,
        agent_slug="visual_moderation_local_image_rules",
        agent_version="local-image-rules:v1",
        content_type="image",
        object_type="slide_image",
        object_id="1",
        location={"asset_type": "slide_image", "slide_order": 1},
        category="graphic_content",
        severity=severity,
        confidence=0.9,
        decision=decision,
        user_message="Visual asset should be reviewed.",
        admin_message="Visual asset metadata validation flagged this file.",
        provider="local_image_rules",
        provider_raw={"format": "PNG"},
    )


@pytest.mark.django_db
def test_visual_publish_gate_disabled_keeps_existing_publish_behavior(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = False
    project = _make_ready_project("disabled")
    run = _visual_run(project, final_decision="needs_admin_review")
    _finding(run)

    response = _publish(project, project.user)

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.is_published is True


@pytest.mark.django_db
def test_visual_publish_gate_enabled_no_visual_run_does_not_block(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("no_run")

    assert visual_moderation_blocks_publish(project) is False
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_visual_publish_gate_enabled_clean_latest_run_allows_publish(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("clean_run")
    _visual_run(project, final_decision="allow")

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
def test_visual_publish_gate_enabled_serious_latest_finding_blocks_publish(settings, decision, severity):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project(f"blocked_{decision}_{severity}")
    run = _visual_run(project, final_decision="needs_admin_review")
    _finding(run, decision=decision, severity=severity)

    response = _publish(project, project.user)

    assert response.status_code == 400
    assert response.data["reason"] == "visual_moderation_rejected"
    assert response.data["detail"] == "This lesson cannot be published until visual moderation findings are resolved."
    assert response.data["finding_count"] == 1
    assert response.data["latest_visual_run_id"] == run.id
    project.refresh_from_db()
    assert project.is_published is False
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_newer_clean_visual_run_clears_older_visual_block(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("newer_clean")
    blocked_run = _visual_run(project, final_decision="needs_admin_review")
    _finding(blocked_run)
    clean_run = _visual_run(project, final_decision="allow")

    response = _publish(project, project.user)

    assert clean_run.id > blocked_run.id
    assert response.status_code == 200
    project.refresh_from_db()
    assert project.is_published is True


@pytest.mark.django_db
def test_text_moderation_block_still_blocks_with_visual_gate_enabled(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("text_block", moderation_status="revision_required")
    _visual_run(project, final_decision="allow")

    response = _publish(project, project.user)

    assert response.status_code == 400
    assert response.data["reason"] == "moderation_rejected"
    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_visual_publish_gate_does_not_mutate_project_moderation_status(settings):
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    project = _make_ready_project("status_unchanged", moderation_status="approved")
    run = _visual_run(project, final_decision="needs_admin_review")
    _finding(run)

    response = _publish(project, project.user)

    assert response.status_code == 400
    project.refresh_from_db()
    assert project.moderation_status == "approved"
