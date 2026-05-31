import io
import json
import os
import sys
from datetime import timedelta
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
from django.core.management import call_command  # noqa: E402
from django.core.management.base import CommandError  # noqa: E402
from django.utils import timezone  # noqa: E402

from core.models import Job, Project, RenderFollowUpIntent, UserProfile  # noqa: E402
from core import render_recovery  # noqa: E402
from core.render_recovery import build_render_recovery_report  # noqa: E402


pytestmark = pytest.mark.django_db


def _make_project(username: str) -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title=f"Render recovery {username}", user=user, status="processing")


def _age_job(job: Job, *, hours: int = 4) -> Job:
    old = timezone.now() - timedelta(hours=hours)
    Job.objects.filter(pk=job.pk).update(created_at=old, updated_at=old)
    job.refresh_from_db()
    return job


def _age_intent(intent: RenderFollowUpIntent, *, hours: int = 4) -> RenderFollowUpIntent:
    old = timezone.now() - timedelta(hours=hours)
    RenderFollowUpIntent.objects.filter(pk=intent.pk).update(created_at=old, updated_at=old)
    intent.refresh_from_db()
    return intent


def test_detects_stuck_render_jobs():
    project = _make_project("stuck_render")
    stale_pending = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending"))
    fresh_running = Job.objects.create(project=project, job_type="video_export", status="running")

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    stuck_ids = {finding.object_id for finding in report.findings if finding.category == "stuck_render_job"}
    assert stale_pending.id in stuck_ids
    assert fresh_running.id not in stuck_ids
    assert report.stuck_render_count >= 1


def test_detects_stuck_followup_intents():
    project = _make_project("stuck_intent")
    active_job = Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-active")
    intent = _age_intent(
        RenderFollowUpIntent.objects.create(
            project=project,
            status=RenderFollowUpIntent.STATUS_CLAIMED,
            metadata={"active_job_id": active_job.id, "dispatched_job_id": active_job.id},
            claimed_at=timezone.now() - timedelta(hours=4),
        )
    )

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    matches = [finding for finding in report.findings if finding.object_type == "RenderFollowUpIntent" and finding.object_id == intent.id]
    assert any(finding.category == "stuck_followup_intent" for finding in matches)
    assert any("Celery task id" in finding.detail for finding in matches)


def test_detects_orphan_render_job_missing_task_id():
    project = _make_project("orphan_job")
    orphan = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending", celery_task_id=""))

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    assert any(
        finding.category == "orphan_recovery_candidate"
        and finding.object_type == "Job"
        and finding.object_id == orphan.id
        for finding in report.findings
    )


def test_detects_orphan_intent_disconnected_from_active_render_flow():
    project = _make_project("orphan_intent")
    completed_job = Job.objects.create(project=project, job_type="video_export", status="done", celery_task_id="task-done")
    intent = _age_intent(
        RenderFollowUpIntent.objects.create(
            project=project,
            status=RenderFollowUpIntent.STATUS_PENDING,
            metadata={"active_job_id": completed_job.id},
        )
    )

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    assert any(
        finding.category == "orphan_recovery_candidate"
        and finding.object_type == "RenderFollowUpIntent"
        and finding.object_id == intent.id
        and "not an active render job" in finding.detail
        for finding in report.findings
    )


def test_report_generation_summary_counts():
    project = _make_project("summary")
    _age_job(Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-running"))

    payload = build_render_recovery_report(dry_run=True, max_age_hours=2).as_dict()

    assert payload["dry_run"] is True
    assert payload["summary"]["stuck_render_count"] >= 1
    assert "oldest_stuck_age_hours" in payload["summary"]
    assert payload["findings"]


def test_render_recovery_check_command_text_output():
    project = _make_project("command_text")
    _age_job(Job.objects.create(project=project, job_type="video_export", status="pending"))

    stdout = io.StringIO()
    call_command("render_recovery_check", "--dry-run", "--max-age-hours", "2", stdout=stdout)
    output = stdout.getvalue()

    assert "Render recovery reconciliation (dry-run)" in output
    assert "Stuck render jobs:" in output
    assert "Orphan recovery candidates:" in output
    assert "recommended action:" in output


def test_render_recovery_check_command_json_output():
    project = _make_project("command_json")
    _age_job(Job.objects.create(project=project, job_type="video_export", status="running"))

    stdout = io.StringIO()
    call_command("render_recovery_check", "--dry-run", "--json", "--max-age-hours", "2", stdout=stdout)
    payload = json.loads(stdout.getvalue())

    assert payload["dry_run"] is True
    assert payload["summary"]["total_findings"] >= 1
    assert payload["findings"][0]["category"] in {"stuck_render_job", "orphan_recovery_candidate"}


def test_render_recovery_check_requires_dry_run():
    with pytest.raises(CommandError):
        call_command("render_recovery_check")


def test_recovery_report_graceful_when_models_unavailable(monkeypatch):
    def raise_import_error():
        raise ImportError("optional dependency unavailable")

    monkeypatch.setattr(render_recovery, "_load_models", raise_import_error)

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    assert report.findings == []
    assert report.warnings
    assert "optional dependency unavailable" in report.warnings[0]


def test_recovery_report_graceful_when_query_fails(monkeypatch):
    def raise_query_error(*_args, **_kwargs):
        raise RuntimeError("database schema unavailable")

    monkeypatch.setattr(render_recovery, "_detect_stuck_render_jobs", raise_query_error)

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    assert report.findings == []
    assert report.warnings
    assert "database schema unavailable" in report.warnings[0]
