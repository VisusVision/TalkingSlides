"""Report-only render recovery and reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.utils import timezone


DEFAULT_MAX_AGE_HOURS = 2


@dataclass(frozen=True)
class RenderRecoveryFinding:
    category: str
    object_type: str
    object_id: int | str
    age_seconds: int
    recommended_action: str
    detail: str = ""
    project_id: int | None = None

    @property
    def age_hours(self) -> float:
        return round(self.age_seconds / 3600, 2)

    def as_dict(self) -> dict[str, Any]:
        remediation_plan = _remediation_plan_for_finding(self)
        return {
            "category": self.category,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "project_id": self.project_id,
            "age_seconds": self.age_seconds,
            "age_hours": self.age_hours,
            "recommended_action": self.recommended_action,
            "detail": self.detail,
            "candidate_action": remediation_plan["candidate_action"],
            "action_mode": remediation_plan["action_mode"],
            "risk_level": remediation_plan["risk_level"],
            "requires_operator_checks": remediation_plan["requires_operator_checks"],
            "mutation_if_applied": remediation_plan["mutation_if_applied"],
            "dedupe_impact": remediation_plan["dedupe_impact"],
            "suggested_manual_command": remediation_plan["suggested_manual_command"],
        }


@dataclass(frozen=True)
class RenderRecoveryReport:
    dry_run: bool
    max_age_hours: float
    generated_at: str
    findings: list[RenderRecoveryFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def stuck_render_count(self) -> int:
        return sum(1 for finding in self.findings if finding.category == "stuck_render_job")

    @property
    def stuck_intent_count(self) -> int:
        return sum(1 for finding in self.findings if finding.category == "stuck_followup_intent")

    @property
    def orphan_candidate_count(self) -> int:
        return sum(1 for finding in self.findings if finding.category == "orphan_recovery_candidate")

    @property
    def oldest_stuck_age_seconds(self) -> int:
        stuck_ages = [
            finding.age_seconds
            for finding in self.findings
            if finding.category in {"stuck_render_job", "stuck_followup_intent"}
        ]
        return max(stuck_ages, default=0)

    def summary(self) -> dict[str, Any]:
        return {
            "stuck_render_count": self.stuck_render_count,
            "stuck_intent_count": self.stuck_intent_count,
            "orphan_candidate_count": self.orphan_candidate_count,
            "oldest_stuck_age_seconds": self.oldest_stuck_age_seconds,
            "oldest_stuck_age_hours": round(self.oldest_stuck_age_seconds / 3600, 2),
            "total_findings": len(self.findings),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "max_age_hours": self.max_age_hours,
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "warnings": list(self.warnings),
            "findings": [finding.as_dict() for finding in self.findings],
        }


def build_render_recovery_report(*, dry_run: bool = True, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> RenderRecoveryReport:
    """Inspect render state and return recovery candidates without mutating data."""

    now = timezone.now()
    try:
        Job, RenderFollowUpIntent = _load_models()
    except Exception as exc:  # pragma: no cover - exercised through monkeypatch tests.
        return RenderRecoveryReport(
            dry_run=bool(dry_run),
            max_age_hours=float(max_age_hours),
            generated_at=now.isoformat(),
            warnings=[f"render recovery inspection unavailable: {exc}"],
        )

    cutoff = now - timedelta(hours=float(max_age_hours))
    findings: list[RenderRecoveryFinding] = []
    warnings: list[str] = []
    try:
        findings.extend(_detect_stuck_render_jobs(Job, cutoff=cutoff, now=now))
        findings.extend(_detect_impossible_render_states(Job, now=now))
        findings.extend(_detect_stuck_followup_intents(RenderFollowUpIntent, cutoff=cutoff, now=now))
        findings.extend(_detect_orphan_candidates(Job, RenderFollowUpIntent, cutoff=cutoff, now=now))
    except Exception as exc:
        warnings.append(f"render recovery inspection query failed: {exc}")
    return RenderRecoveryReport(
        dry_run=bool(dry_run),
        max_age_hours=float(max_age_hours),
        generated_at=now.isoformat(),
        findings=sorted(findings, key=lambda finding: (-finding.age_seconds, finding.category, str(finding.object_id))),
        warnings=warnings,
    )


def _load_models():
    from core.models import Job, RenderFollowUpIntent

    return Job, RenderFollowUpIntent


def _age_seconds(now, changed_at) -> int:
    if changed_at is None:
        return 0
    return max(0, int((now - changed_at).total_seconds()))


def _object_age(now, obj) -> int:
    changed_at = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
    return _age_seconds(now, changed_at)


def _remediation_plan_for_finding(finding: RenderRecoveryFinding) -> dict[str, Any]:
    detail = str(finding.detail or "")
    object_kind = "job" if finding.object_type == "Job" else "intent"
    suggested_manual_command = (
        "python manage.py render_recovery_action "
        f"--action inspect --type {object_kind} --id {finding.object_id}"
    )
    plan: dict[str, Any] = {
        "candidate_action": "operator_inspect_recovery_candidate",
        "action_mode": "report_only",
        "risk_level": "medium",
        "requires_operator_checks": [
            "confirm the object is still a current recovery candidate",
            "inspect API, worker, and Celery logs for the object age window",
            "verify no live worker still owns the render before planning any manual state change",
        ],
        "mutation_if_applied": "No mutation is performed by this report. A future explicit operator action would need to reconcile state manually.",
        "dedupe_impact": "none",
        "suggested_manual_command": suggested_manual_command,
    }

    if finding.object_type == "Job":
        plan["requires_operator_checks"] = [
            "inspect the Job row status, progress, celery_task_id, error_message, timestamps, project_id, and job_type",
            "check API enqueue logs and request logs around Job creation",
            "check Celery broker/result state and worker logs for the recorded or suspected task",
            "inspect generated output files without deleting artifacts",
        ]
        plan["dedupe_impact"] = "would_unblock_render_dedupe_if_failed_or_cancelled"

    if finding.category == "stuck_render_job":
        plan["candidate_action"] = "inspect_stale_active_video_export"
        plan["mutation_if_applied"] = (
            "A future explicit operator action might mark the active video_export job failed after external ownership checks."
        )
        if "progress=" in detail and "100" in detail:
            plan["candidate_action"] = "inspect_active_job_progress_terminal_mismatch"
            plan["risk_level"] = "medium"
            plan["requires_operator_checks"] = [
                "confirm the active Job still has progress at or above 100",
                "inspect worker finalize logs for successful completion, failure, or stale-finalize skip",
                "verify playback sidecar and output assets exist before deciding whether the status is mismatched",
                "verify no live worker still owns the render before planning any manual state change",
            ]
        elif "pending" in detail:
            plan["risk_level"] = "high"
            plan["requires_operator_checks"].append("confirm whether a pending job was actually dispatched despite stale updated_at")
        elif "running" in detail:
            plan["risk_level"] = "high"
            plan["requires_operator_checks"].append("confirm the render is not a legitimate long-running job for the source size and hardware")

    if finding.category == "stuck_followup_intent":
        plan.update(
            {
                "candidate_action": "inspect_stale_followup_intent",
                "risk_level": "medium",
                "requires_operator_checks": [
                    "inspect RenderFollowUpIntent status, mode, page_keys, metadata, claimed_at, and timestamps",
                    "inspect metadata.active_job_id, metadata.dispatched_job_id, and metadata.celery_task_id",
                    "check the active and terminal video_export history for the same project",
                    "verify no active render or follow-up dispatch still owns the intent before planning cancellation",
                ],
                "mutation_if_applied": (
                    "A future explicit operator action might cancel the stale follow-up intent after confirming it cannot be drained safely."
                ),
                "dedupe_impact": "would_unblock_followup_intent_uniqueness_if_cancelled",
            }
        )
        if "no recorded Celery task id" in detail:
            plan["candidate_action"] = "inspect_claimed_followup_missing_task_id"
            plan["risk_level"] = "high"

    if finding.category == "orphan_recovery_candidate":
        if finding.object_type == "Job":
            plan["candidate_action"] = "inspect_video_export_missing_task_id"
            plan["risk_level"] = "high"
            plan["requires_operator_checks"] = [
                "confirm celery_task_id is still blank on the Job row",
                "inspect API enqueue logs and request logs for a dispatch crash window",
                "check Celery broker/result state for a task that may have been enqueued without being recorded",
                "verify no live worker still owns the render before planning any manual fail or recreate action",
            ]
            plan["mutation_if_applied"] = (
                "A future explicit operator action might mark the active video_export job failed after confirming no task was enqueued."
            )
            if "pending_without_task_id" in detail:
                plan["candidate_action"] = "inspect_pending_video_export_without_task_id"
        else:
            plan["candidate_action"] = "inspect_orphan_followup_intent_reference"
            plan["risk_level"] = "medium"
            plan["requires_operator_checks"] = [
                "inspect RenderFollowUpIntent metadata.active_job_id and metadata.dispatched_job_id",
                "check whether referenced render jobs completed, failed, were superseded, or never existed",
                "inspect project render history before deciding whether the follow-up intent should be cancelled or recreated",
                "verify no active render or follow-up dispatch still owns the intent before planning cancellation",
            ]
            plan["mutation_if_applied"] = (
                "A future explicit operator action might cancel the orphaned follow-up intent after reference checks."
            )
            plan["dedupe_impact"] = "would_unblock_followup_intent_uniqueness_if_cancelled"

    return plan


def _detect_stuck_render_jobs(Job, *, cutoff, now) -> list[RenderRecoveryFinding]:
    findings: list[RenderRecoveryFinding] = []
    active_jobs = Job.objects.filter(job_type="video_export", status__in=("pending", "running"), updated_at__lte=cutoff)
    for job in active_jobs.order_by("updated_at", "id"):
        status = str(job.status or "")
        if status == "pending":
            action = "Inspect API enqueue logs and Celery broker health; if no task was dispatched, manually fail or recreate the render after review."
            detail = "pending video_export job exceeded the operator age threshold"
        else:
            action = "Inspect worker logs, Celery task state, and output files; manually fail or retry only after confirming no worker still owns it."
            detail = "running video_export job exceeded the operator age threshold"
        findings.append(
            RenderRecoveryFinding(
                category="stuck_render_job",
                object_type="Job",
                object_id=int(job.id),
                project_id=job.project_id,
                age_seconds=_object_age(now, job),
                recommended_action=action,
                detail=detail,
            )
        )
    return findings


def _detect_impossible_render_states(Job, *, now) -> list[RenderRecoveryFinding]:
    findings: list[RenderRecoveryFinding] = []
    impossible_jobs = Job.objects.filter(job_type="video_export", status__in=("pending", "running")).filter(
        progress__gte=100
    )
    for job in impossible_jobs.order_by("updated_at", "id"):
        findings.append(
            RenderRecoveryFinding(
                category="stuck_render_job",
                object_type="Job",
                object_id=int(job.id),
                project_id=job.project_id,
                age_seconds=_object_age(now, job),
                recommended_action="Inspect worker logs and playback assets; reconcile status manually because active jobs should not be at 100% progress.",
                detail=f"video_export job is {job.status} with progress={job.progress}",
            )
        )
    return findings


def _detect_stuck_followup_intents(RenderFollowUpIntent, *, cutoff, now) -> list[RenderRecoveryFinding]:
    findings: list[RenderRecoveryFinding] = []
    active_intents = RenderFollowUpIntent.objects.filter(
        status__in=(
            RenderFollowUpIntent.STATUS_PENDING,
            RenderFollowUpIntent.STATUS_CLAIMED,
            RenderFollowUpIntent.STATUS_DISPATCHED,
        ),
        updated_at__lte=cutoff,
    )
    for intent in active_intents.order_by("updated_at", "id"):
        metadata = dict(intent.metadata or {})
        status = str(intent.status or "")
        if status == RenderFollowUpIntent.STATUS_PENDING:
            action = "Inspect the active base render referenced by metadata.active_job_id; clear or re-request only after the base render state is reconciled."
            detail = "pending follow-up intent exceeded the operator age threshold"
        elif status == RenderFollowUpIntent.STATUS_CLAIMED:
            action = "Inspect metadata.dispatched_job_id and Celery dispatch logs; this may be a post-commit dispatch crash window."
            detail = "claimed follow-up intent exceeded the operator age threshold"
        else:
            action = "Inspect metadata.celery_task_id and the associated render job; dispatched intents should normally be terminally cleared."
            detail = "dispatched follow-up intent exceeded the operator age threshold"
        if status == RenderFollowUpIntent.STATUS_CLAIMED and not metadata.get("celery_task_id"):
            detail = "claimed follow-up intent has no recorded Celery task id"
        findings.append(
            RenderRecoveryFinding(
                category="stuck_followup_intent",
                object_type="RenderFollowUpIntent",
                object_id=int(intent.id),
                project_id=intent.project_id,
                age_seconds=_object_age(now, intent),
                recommended_action=action,
                detail=detail,
            )
        )
    return findings


def _detect_orphan_candidates(Job, RenderFollowUpIntent, *, cutoff, now) -> list[RenderRecoveryFinding]:
    findings: list[RenderRecoveryFinding] = []
    missing_task_jobs = Job.objects.filter(
        job_type="video_export",
        status__in=("pending", "running"),
        celery_task_id="",
        updated_at__lte=cutoff,
    )
    for job in missing_task_jobs.order_by("updated_at", "id"):
        status = str(job.status or "")
        if status == "pending":
            action = (
                "Inspect API enqueue logs, request logs, and Celery broker health; "
                "this pending job has no recorded Celery task id and may be an API dispatch crash window. "
                "Do not retry, fail, or recreate until confirming no task was enqueued."
            )
            detail = (
                "pending_without_task_id dispatch_window_candidate: pending video_export job has no celery_task_id; "
                "likely API dispatch crash window after Job creation before Celery enqueue or task-id persistence"
            )
        else:
            action = "Verify no matching Celery task exists; this is a DB commit vs task dispatch recovery candidate."
            detail = f"{status} video_export job has no celery_task_id"
        findings.append(
            RenderRecoveryFinding(
                category="orphan_recovery_candidate",
                object_type="Job",
                object_id=int(job.id),
                project_id=job.project_id,
                age_seconds=_object_age(now, job),
                recommended_action=action,
                detail=detail,
            )
        )

    active_statuses = (
        RenderFollowUpIntent.STATUS_PENDING,
        RenderFollowUpIntent.STATUS_CLAIMED,
        RenderFollowUpIntent.STATUS_DISPATCHED,
    )
    for intent in RenderFollowUpIntent.objects.filter(status__in=active_statuses, updated_at__lte=cutoff).order_by("updated_at", "id"):
        metadata = dict(intent.metadata or {})
        active_job_id = metadata.get("active_job_id")
        dispatched_job_id = metadata.get("dispatched_job_id")
        if active_job_id and not Job.objects.filter(pk=active_job_id, job_type="video_export", status__in=("pending", "running")).exists():
            findings.append(
                RenderRecoveryFinding(
                    category="orphan_recovery_candidate",
                    object_type="RenderFollowUpIntent",
                    object_id=int(intent.id),
                    project_id=intent.project_id,
                    age_seconds=_object_age(now, intent),
                    recommended_action="Review whether the referenced base render completed, failed, or was superseded before manually cancelling or recreating the follow-up.",
                    detail=f"metadata.active_job_id={active_job_id} is not an active render job",
                )
            )
        if dispatched_job_id and not Job.objects.filter(pk=dispatched_job_id, job_type="video_export").exists():
            findings.append(
                RenderRecoveryFinding(
                    category="orphan_recovery_candidate",
                    object_type="RenderFollowUpIntent",
                    object_id=int(intent.id),
                    project_id=intent.project_id,
                    age_seconds=_object_age(now, intent),
                    recommended_action="Inspect audit history and worker logs; the intent points at a missing reserved follow-up render job.",
                    detail=f"metadata.dispatched_job_id={dispatched_job_id} does not exist",
                )
            )
    return findings
