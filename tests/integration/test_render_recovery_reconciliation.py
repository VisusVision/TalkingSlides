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
from django.test import override_settings  # noqa: E402
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


def _assert_remediation_plan_fields(finding: dict) -> None:
    assert finding["candidate_action"]
    assert finding["action_mode"] == "report_only"
    assert finding["risk_level"] in {"low", "medium", "high"}
    assert isinstance(finding["requires_operator_checks"], list)
    assert finding["requires_operator_checks"]
    assert finding["mutation_if_applied"]
    assert finding["dedupe_impact"]
    assert finding["suggested_manual_command"]


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


def test_pending_video_export_without_task_id_reports_dispatch_window_detail():
    project = _make_project("pending_no_task_detail")
    orphan = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending", celery_task_id=""))

    before = {
        "status": orphan.status,
        "progress": orphan.progress,
        "celery_task_id": orphan.celery_task_id,
        "error_message": orphan.error_message,
    }
    report = build_render_recovery_report(dry_run=True, max_age_hours=2)

    matches = [
        finding
        for finding in report.findings
        if finding.category == "orphan_recovery_candidate"
        and finding.object_type == "Job"
        and finding.object_id == orphan.id
    ]
    assert matches
    finding = matches[0]
    assert "pending_without_task_id" in finding.detail
    assert "dispatch_window_candidate" in finding.detail
    assert "API dispatch crash window" in finding.detail
    assert "no recorded Celery task id" in finding.recommended_action
    payload = finding.as_dict()
    _assert_remediation_plan_fields(payload)
    assert payload["candidate_action"] == "inspect_pending_video_export_without_task_id"
    assert payload["action_mode"] == "report_only"
    assert payload["risk_level"] == "high"
    assert payload["dedupe_impact"] == "would_unblock_render_dedupe_if_failed_or_cancelled"
    assert "no task was enqueued" in payload["mutation_if_applied"]

    orphan.refresh_from_db()
    assert {
        "status": orphan.status,
        "progress": orphan.progress,
        "celery_task_id": orphan.celery_task_id,
        "error_message": orphan.error_message,
    } == before


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
    _assert_remediation_plan_fields(payload["findings"][0])


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
    assert "candidate action:" in output
    assert "action mode: report_only" in output


def test_render_recovery_check_command_json_output():
    project = _make_project("command_json")
    _age_job(Job.objects.create(project=project, job_type="video_export", status="running"))

    stdout = io.StringIO()
    call_command("render_recovery_check", "--dry-run", "--json", "--max-age-hours", "2", stdout=stdout)
    payload = json.loads(stdout.getvalue())

    assert payload["dry_run"] is True
    assert payload["summary"]["total_findings"] >= 1
    assert payload["findings"][0]["category"] in {"stuck_render_job", "orphan_recovery_candidate"}
    _assert_remediation_plan_fields(payload["findings"][0])


def test_stale_followup_intent_reports_plan_without_mutating_state():
    project = _make_project("stale_intent_plan")
    active_job = Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-active")
    intent = _age_intent(
        RenderFollowUpIntent.objects.create(
            project=project,
            status=RenderFollowUpIntent.STATUS_PENDING,
            metadata={"active_job_id": active_job.id, "reason": "transcript_edit"},
        )
    )
    before = {
        "status": intent.status,
        "metadata": dict(intent.metadata or {}),
        "claimed_at": intent.claimed_at,
    }

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)
    matches = [
        finding.as_dict()
        for finding in report.findings
        if finding.category == "stuck_followup_intent"
        and finding.object_type == "RenderFollowUpIntent"
        and finding.object_id == intent.id
    ]

    assert matches
    _assert_remediation_plan_fields(matches[0])
    assert matches[0]["candidate_action"] == "inspect_stale_followup_intent"
    assert matches[0]["action_mode"] == "report_only"
    assert matches[0]["dedupe_impact"] == "would_unblock_followup_intent_uniqueness_if_cancelled"
    intent.refresh_from_db()
    assert {
        "status": intent.status,
        "metadata": dict(intent.metadata or {}),
        "claimed_at": intent.claimed_at,
    } == before


def test_orphan_followup_intent_reference_reports_plan_without_mutating_state():
    project = _make_project("orphan_intent_plan")
    completed_job = Job.objects.create(project=project, job_type="video_export", status="done", celery_task_id="task-done")
    intent = _age_intent(
        RenderFollowUpIntent.objects.create(
            project=project,
            status=RenderFollowUpIntent.STATUS_PENDING,
            metadata={"active_job_id": completed_job.id},
        )
    )
    before = {"status": intent.status, "metadata": dict(intent.metadata or {})}

    payload = build_render_recovery_report(dry_run=True, max_age_hours=2).as_dict()
    matches = [
        finding
        for finding in payload["findings"]
        if finding["category"] == "orphan_recovery_candidate"
        and finding["object_type"] == "RenderFollowUpIntent"
        and finding["object_id"] == intent.id
    ]

    assert matches
    _assert_remediation_plan_fields(matches[0])
    assert matches[0]["candidate_action"] == "inspect_orphan_followup_intent_reference"
    assert matches[0]["action_mode"] == "report_only"
    intent.refresh_from_db()
    assert {"status": intent.status, "metadata": dict(intent.metadata or {})} == before


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


def _read_audit_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_render_recovery_action_inspect_outputs_state_and_recommendation(tmp_path):
    audit_path = tmp_path / "recovery-audit.jsonl"
    project = _make_project("action_inspect")
    job = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending"))

    stdout = io.StringIO()
    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        call_command(
            "render_recovery_action",
            "--action",
            "inspect",
            "--type",
            "job",
            "--id",
            str(job.id),
            stdout=stdout,
        )

    output = stdout.getvalue()
    assert "Render recovery action: inspect" in output
    assert f"Object: job#{job.id}" in output
    assert "pending video_export job exceeded" in output
    records = _read_audit_records(audit_path)
    assert records[-1]["action"] == "inspect"
    assert records[-1]["dry_run"] is True
    assert records[-1]["executed"] is False
    assert records[-1]["audit_written"] is True


def test_render_recovery_action_resolve_is_audit_only_with_confirm(tmp_path):
    audit_path = tmp_path / "recovery-audit.jsonl"
    project = _make_project("action_resolve")
    job = _age_job(
        Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-1")
    )

    stdout = io.StringIO()
    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        call_command(
            "render_recovery_action",
            "--action",
            "resolve",
            "--type",
            "job",
            "--id",
            str(job.id),
            "--confirm",
            stdout=stdout,
        )

    job.refresh_from_db()
    assert job.status == "running"
    assert job.celery_task_id == "task-1"
    assert "Executed: True" in stdout.getvalue()
    records = _read_audit_records(audit_path)
    assert records[-1]["action"] == "resolve"
    assert records[-1]["annotation_only"] is True
    assert records[-1]["executed"] is True
    assert records[-1]["audit_written"] is True


def test_render_recovery_action_ignore_is_audit_only_with_confirm(tmp_path):
    audit_path = tmp_path / "recovery-audit.jsonl"
    project = _make_project("action_ignore")
    intent = _age_intent(
        RenderFollowUpIntent.objects.create(
            project=project,
            status=RenderFollowUpIntent.STATUS_PENDING,
            metadata={"active_job_id": 987654},
        )
    )

    stdout = io.StringIO()
    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        call_command(
            "render_recovery_action",
            "--action",
            "ignore",
            "--type",
            "intent",
            "--id",
            str(intent.id),
            "--confirm",
            stdout=stdout,
        )

    intent.refresh_from_db()
    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert intent.metadata == {"active_job_id": 987654}
    records = _read_audit_records(audit_path)
    assert records[-1]["action"] == "ignore"
    assert records[-1]["object_type"] == "intent"
    assert records[-1]["executed"] is True
    assert records[-1]["audit_written"] is True


@pytest.mark.parametrize("action", ["resolve", "ignore"])
def test_render_recovery_action_missing_confirm_stays_dry_run(tmp_path, action):
    audit_path = tmp_path / "recovery-audit.jsonl"
    project = _make_project("action_missing_confirm")
    job = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending"))

    stdout = io.StringIO()
    stderr = io.StringIO()
    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        call_command(
            "render_recovery_action",
            "--action",
            action,
            "--type",
            "job",
            "--id",
            str(job.id),
            stdout=stdout,
            stderr=stderr,
        )

    assert "Dry-run: True" in stdout.getvalue()
    assert "Executed: False" in stdout.getvalue()
    assert "No execution performed and no audit record written" in stderr.getvalue()
    assert _read_audit_records(audit_path) == []


def test_render_recovery_action_invalid_id_raises_command_error(tmp_path):
    audit_path = tmp_path / "recovery-audit.jsonl"
    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        with pytest.raises(CommandError):
            call_command("render_recovery_action", "--action", "inspect", "--type", "job", "--id", "987654")

    assert _read_audit_records(audit_path) == []


def test_render_recovery_action_invalid_action_raises_command_error():
    with pytest.raises(CommandError):
        call_command("render_recovery_action", "--action", "retry", "--type", "job", "--id", "1")


def test_render_recovery_action_json_output(tmp_path):
    audit_path = tmp_path / "recovery-audit.jsonl"
    project = _make_project("action_json")
    job = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending"))

    stdout = io.StringIO()
    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        call_command(
            "render_recovery_action",
            "--action",
            "inspect",
            "--type",
            "job",
            "--id",
            str(job.id),
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert payload["action"] == "inspect"
    assert payload["object_state"]["id"] == job.id
    assert payload["recommendation"]["findings"]
    assert payload["audit_record"]["object_id"] == job.id


def test_render_recovery_action_generates_audit_for_dry_run_and_execute(tmp_path):
    audit_path = tmp_path / "recovery-audit.jsonl"
    project = _make_project("action_audit")
    job = _age_job(Job.objects.create(project=project, job_type="video_export", status="pending"))

    with override_settings(RENDER_RECOVERY_AUDIT_LOG_PATH=str(audit_path)):
        call_command(
            "render_recovery_action",
            "--action",
            "inspect",
            "--type",
            "job",
            "--id",
            str(job.id),
            stdout=io.StringIO(),
        )
        call_command(
            "render_recovery_action",
            "--action",
            "ignore",
            "--type",
            "job",
            "--id",
            str(job.id),
            "--confirm",
            stdout=io.StringIO(),
        )

    records = _read_audit_records(audit_path)
    assert [record["action"] for record in records] == ["inspect", "ignore"]
    assert records[0]["dry_run"] is True
    assert records[0]["audit_written"] is True
    assert records[1]["executed"] is True
    assert records[1]["audit_written"] is True
