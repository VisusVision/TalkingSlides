# pyright: reportMissingImports=false

import os
import sys
from io import StringIO
from pathlib import Path

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
from django.core.management import call_command  # noqa: E402

from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun, PublicationBlockEvent  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _run_smoke_command(**options) -> str:
    stdout = StringIO()
    call_command("create_moderation_smoke_project", stdout=stdout, **options)
    return stdout.getvalue()


@pytest.mark.django_db
def test_create_smoke_project_clean_with_scan_becomes_approved():
    owner = _make_teacher("smoke_clean_owner")

    output = _run_smoke_command(kind="clean", user_id=owner.id, scan=True, title="Smoke Clean Test")

    project = Project.objects.get(title="Smoke Clean Test")
    assert project.user == owner
    assert project.moderation_status == "approved"
    assert TranscriptPage.objects.filter(project=project).count() == 1
    assert "Project:" in output
    assert "Latest AgentRun id:" in output


@pytest.mark.django_db
def test_create_smoke_project_profanity_with_scan_creates_revision_required_and_finding():
    owner = _make_teacher("smoke_profanity_owner")

    _run_smoke_command(kind="profanity", user_id=owner.id, scan=True, title="Smoke Profanity Test")

    project = Project.objects.get(title="Smoke Profanity Test")
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="profanity", decision="block").exists()


@pytest.mark.django_db
def test_create_smoke_project_violence_with_scan_creates_revision_required_and_block_event():
    owner = _make_teacher("smoke_violence_owner")

    _run_smoke_command(kind="violence", user_id=owner.id, scan=True, title="Smoke Violence Test")

    project = Project.objects.get(title="Smoke Violence Test")
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="violence", decision="block").exists()
    assert PublicationBlockEvent.objects.filter(project=project, resolved=False).exists()


@pytest.mark.django_db
def test_create_smoke_project_with_request_review_creates_admin_review_request_when_reviewable():
    owner = _make_teacher("smoke_review_owner")

    output = _run_smoke_command(
        kind="ambiguous-review",
        user_id=owner.id,
        scan=True,
        request_review=True,
        review_message="AI misunderstood educational context.",
        title="Smoke Review Test",
    )

    project = Project.objects.get(title="Smoke Review Test")
    review = AdminReviewRequest.objects.get(project=project)
    assert project.moderation_status == "needs_admin_review"
    assert review.status == "open"
    assert review.publisher_message == "AI misunderstood educational context."
    assert f"AdminReviewRequest: {review.id} - open" in output


@pytest.mark.django_db
def test_create_smoke_project_scan_does_not_require_celery(monkeypatch):
    owner = _make_teacher("smoke_no_celery_owner")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:1/0")

    output = _run_smoke_command(kind="clean", user_id=owner.id, scan=True, title="Smoke No Celery Test")

    project = Project.objects.get(title="Smoke No Celery Test")
    assert project.moderation_status == "approved"
    assert "Moderation status after scan: approved" in output


@pytest.mark.django_db
def test_create_smoke_project_output_includes_project_and_latest_run_id():
    owner = _make_teacher("smoke_output_owner")

    output = _run_smoke_command(kind="profanity", user_id=owner.id, scan=True, title="Smoke Output Test")

    project = Project.objects.get(title="Smoke Output Test")
    run = AgentRun.objects.get(project=project)
    assert f"Project: {project.id} - Smoke Output Test" in output
    assert f"Owner: {owner.id} - {owner.username}" in output
    assert f"Latest AgentRun id: {run.id}" in output
    assert "Finding count: 1" in output
    assert "Categories: profanity=1" in output
