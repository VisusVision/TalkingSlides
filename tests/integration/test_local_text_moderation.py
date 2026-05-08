# pyright: reportMissingImports=false

import json
import os
import sys
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

from ai_agents.models import AgentFinding, AgentRun, PublicationBlockEvent  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents import orchestrator as orchestrator_module  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Moderation lesson", description: str = "") -> Project:
    return Project.objects.create(
        title=title,
        description=description,
        user=_make_teacher(username),
        status="ready",
    )


def _add_page(project: Project, original_text: str = "", narration_text: str = "", page_key: str = "slide-1") -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key=page_key,
        original_text=original_text,
        narration_text=narration_text,
    )


def _run(project: Project) -> dict:
    return ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)


@pytest.mark.django_db
def test_clean_text_becomes_approved():
    project = _make_project(
        "local_mod_clean_teacher",
        title="Biology basics",
        description="A calm lesson about cells and ecosystems.",
    )
    _add_page(project, original_text="Photosynthesis converts light into energy.")

    result = _run(project)

    project.refresh_from_db()
    assert result["status"] == "done"
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "approved"
    assert AgentFinding.objects.filter(run_id=project.last_moderation_run_id).count() == 0


@pytest.mark.django_db
def test_text_moderation_creates_agent_run():
    project = _make_project("local_mod_run_teacher")

    result = _run(project)

    run = AgentRun.objects.get(pk=result["run_id"])
    assert run.project_id == project.id
    assert run.purpose == "moderation"
    assert run.phase == "source_scan"
    assert run.status == "done"


@pytest.mark.django_db
def test_text_moderation_creates_agent_finding_for_unsafe_text():
    project = _make_project("local_mod_finding_teacher")
    page = _add_page(project, original_text="I will kill you if you continue.")

    _run(project)

    finding = AgentFinding.objects.get(run__project=project)
    assert finding.category == "violence"
    assert finding.decision == "block"
    assert finding.location["transcript_page_id"] == page.id
    assert finding.location["field_name"] == "original_text"
    assert finding.location["ui_anchor"] == f"transcript-page-{page.id}"


@pytest.mark.django_db
def test_obvious_profanity_becomes_revision_required():
    project = _make_project("local_mod_profanity_teacher", description="This lesson says fuck repeatedly.")

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_obvious_violent_threat_becomes_revision_required():
    project = _make_project("local_mod_threat_teacher")
    _add_page(project, narration_text="I will kill you tomorrow.")

    _run(project)

    project.refresh_from_db()
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="violence", decision="block").exists()


@pytest.mark.django_db
def test_neutral_historical_war_educational_text_is_not_directly_blocked():
    project = _make_project(
        "local_mod_history_teacher",
        title="History of World War II",
        description="This history lesson covers war, battles, diplomacy, and why soldiers were killed.",
    )

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "approved"
    assert AgentFinding.objects.filter(run__project=project).count() == 0


@pytest.mark.django_db
def test_ambiguous_educational_violence_becomes_needs_admin_review():
    project = _make_project(
        "local_mod_ambiguous_history_teacher",
        title="History lesson about war crimes",
        description="This educational history lesson discusses genocide and executions during the war.",
    )

    result = _run(project)

    project.refresh_from_db()
    assert result["final_decision"] == "needs_admin_review"
    assert project.moderation_status == "needs_admin_review"
    finding = AgentFinding.objects.filter(run__project=project, category="violence").first()
    assert finding is not None
    assert finding.category == "violence"
    assert finding.decision == "needs_admin_review"


@pytest.mark.django_db
def test_last_moderation_run_id_is_set():
    project = _make_project("local_mod_last_run_teacher")

    result = _run(project)

    project.refresh_from_db()
    assert project.last_moderation_run_id == result["run_id"]
    assert AgentRun.objects.filter(pk=project.last_moderation_run_id, project=project).exists()


@pytest.mark.django_db
def test_publication_block_event_created_when_revision_required():
    project = _make_project("local_mod_block_event_teacher", description="This has shit in it.")

    _run(project)

    event = PublicationBlockEvent.objects.get(project=project)
    assert event.blocked_by == "text_moderation_local_rules"
    assert event.reason_category == "profanity"
    assert event.highest_severity == "high"
    assert event.resolved is False


@pytest.mark.django_db
def test_moderation_summary_is_frontend_safe():
    project = _make_project("local_mod_summary_teacher", description="This has fucking unsafe wording.")

    _run(project)

    project.refresh_from_db()
    summary = project.moderation_summary
    serialized = json.dumps(summary, sort_keys=True).lower()
    assert summary["moderation_status"] == "revision_required"
    assert summary["findings"][0]["category"] == "profanity"
    assert "provider_raw" not in serialized
    assert "admin_message" not in serialized
    assert "evidence_excerpt" not in serialized
    assert "fucking" not in serialized


@pytest.mark.django_db
def test_failed_moderation_task_sets_project_failed_safely(monkeypatch):
    project = _make_project("local_mod_failure_teacher")

    class BrokenTextModerationAgent:
        def __init__(self, *args, **kwargs):
            pass

        def scan_project(self, project):
            raise RuntimeError("local moderation failed")

    monkeypatch.setattr(orchestrator_module, "TextModerationAgent", BrokenTextModerationAgent)

    result = worker_tasks.run_project_moderation.run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    run = AgentRun.objects.get(pk=result["run_id"])
    assert result["status"] == "failed"
    assert project.moderation_status == "failed"
    assert project.last_moderation_run_id == run.id
    assert run.status == "failed"
    assert "local moderation failed" in run.error_message
