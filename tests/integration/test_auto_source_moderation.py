# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings

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

from ai_agents.models import AgentFinding, AgentRun  # noqa: E402
from ai_agents.policies import project_can_publish  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Auto moderation lesson", description: str = "") -> Project:
    return Project.objects.create(
        title=title,
        description=description,
        user=_make_teacher(username),
        status="processing",
    )


def _sync_one_page(project: Project, text: str) -> None:
    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "slide_num": 1,
                "page_key": "slide-1",
                "notes_text": text,
                "original_text": text,
                "narration_text": text,
            }
        ],
    )


@pytest.mark.django_db
def test_auto_source_moderation_disabled_does_not_trigger_moderation(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", False, raising=False)
    project = _make_project("auto_source_disabled_teacher")
    _sync_one_page(project, "This clean transcript should not be scanned while disabled.")

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    project.refresh_from_db()
    assert result["status"] == "skipped_disabled"
    assert project.moderation_status == "not_scanned"
    assert AgentRun.objects.filter(project=project).count() == 0
    assert TranscriptPage.objects.filter(project=project, page_key="slide-1").exists()


@pytest.mark.django_db
def test_auto_source_moderation_enabled_runs_after_transcript_creation(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "SOURCE_MODERATION_PHASE", "auto_source_test", raising=False)
    project = _make_project("auto_source_runs_teacher", title="Clean source lesson")
    _sync_one_page(project, "Photosynthesis converts sunlight into stored energy.")

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    project.refresh_from_db()
    run = AgentRun.objects.get(project=project)
    assert result["status"] == "done"
    assert result["phase"] == "auto_source_test"
    assert result["moderation_status"] == "approved"
    assert project.moderation_status == "approved"
    assert run.phase == "auto_source_test"
    assert TranscriptPage.objects.filter(project=project, original_text__icontains="Photosynthesis").exists()


@pytest.mark.django_db
def test_clean_extracted_text_becomes_approved(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    project = _make_project("auto_source_clean_teacher", title="Biology overview")
    _sync_one_page(project, "Cells have membranes, cytoplasm, and organelles.")

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    project.refresh_from_db()
    assert result["moderation_status"] == "approved"
    assert project.moderation_status == "approved"
    assert result["block_render"] is False


@pytest.mark.django_db
def test_unsafe_extracted_text_becomes_revision_required(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    project = _make_project("auto_source_unsafe_teacher")
    _sync_one_page(project, "I will kill you if you continue.")

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    project.refresh_from_db()
    assert result["moderation_status"] == "revision_required"
    assert project.moderation_status == "revision_required"
    assert AgentFinding.objects.filter(run__project=project, category="violence").exists()


@pytest.mark.django_db
def test_blocked_source_moderation_can_prevent_downstream_render(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION", True, raising=False)
    project = _make_project("auto_source_block_render_teacher")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    _sync_one_page(project, "I will kill you tomorrow.")

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)
    worker_tasks._mark_project_source_moderation_blocked(project.id, result)

    project.refresh_from_db()
    job.refresh_from_db()
    assert result["block_render"] is True
    assert project.status == "draft"
    assert project.is_published is False
    assert job.status == "failed"
    assert "Source moderation requires revisions" in job.error_message


@pytest.mark.django_db
def test_block_render_flag_false_preserves_render_continuation(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION", False, raising=False)
    project = _make_project("auto_source_no_block_teacher")
    _sync_one_page(project, "This lesson contains shit that should be revised.")

    result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    assert result["moderation_status"] == "revision_required"
    assert result["block_render"] is False


@pytest.mark.django_db
def test_auto_source_moderation_skips_unchanged_approved_content(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    project = _make_project("auto_source_idempotent_teacher")
    _sync_one_page(project, "A calm lesson about the water cycle.")

    first = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)
    second = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

    assert first["moderation_status"] == "approved"
    assert second["status"] == "skipped_unchanged_approved"
    assert AgentRun.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_manual_run_moderation_scan_still_works(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", False, raising=False)
    project = _make_project("auto_source_manual_teacher")
    _sync_one_page(project, "This manual moderation lesson is clean.")

    result = worker_tasks.run_project_moderation.run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert result["status"] == "done"
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_publish_gate_still_blocks_unapproved_content():
    project = _make_project("auto_source_publish_gate_teacher")
    project.status = "ready"
    project.moderation_status = "revision_required"
    project.save(update_fields=["status", "moderation_status", "updated_at"])

    assert project_can_publish(project) is False
