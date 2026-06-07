"""Report-only render recovery and reconciliation helpers."""

from __future__ import annotations

import hashlib
import json
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
    current_status: str | None = None
    updated_at: str | None = None
    celery_task_id: str | None = None
    metadata: dict[str, Any] | None = None

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
            "apply_eligible": remediation_plan["apply_eligible"],
            "apply_blockers": remediation_plan["apply_blockers"],
            "finding_priority": remediation_plan["finding_priority"],
            "precondition_token": remediation_plan["precondition_token"],
            "metadata_hash": remediation_plan["metadata_hash"],
            "proposed_conditional_update": remediation_plan["proposed_conditional_update"],
            "required_confirm_token": remediation_plan["required_confirm_token"],
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
        findings = [finding.as_dict() for finding in self.findings]
        return {
            "dry_run": self.dry_run,
            "max_age_hours": self.max_age_hours,
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "warnings": list(self.warnings),
            "findings": findings,
            "object_summaries": _object_summaries_from_findings(findings),
            "manual_command_summary": _manual_command_summary_from_findings(findings),
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
        findings=sorted(findings, key=_finding_report_sort_key),
        warnings=warnings,
    )


def _load_models():
    from core.models import Job, RenderFollowUpIntent

    return Job, RenderFollowUpIntent


def _finding_report_sort_key(finding: RenderRecoveryFinding) -> tuple[int, str, str, str]:
    payload = finding.as_dict()
    return (
        -finding.age_seconds,
        str(payload.get("finding_priority") or ""),
        finding.category,
        str(finding.object_id),
    )


def _age_seconds(now, changed_at) -> int:
    if changed_at is None:
        return 0
    return max(0, int((now - changed_at).total_seconds()))


def _object_age(now, obj) -> int:
    changed_at = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
    return _age_seconds(now, changed_at)


def _isoformat(value) -> str | None:
    return value.isoformat() if value else None


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


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

    plan.update(_apply_precondition_contract(finding, plan))
    return plan


def _apply_precondition_contract(finding: RenderRecoveryFinding, plan: dict[str, Any]) -> dict[str, Any]:
    candidate_action = str(plan.get("candidate_action") or finding.category)
    metadata_hash = _metadata_hash(finding.metadata)
    precondition_token = _precondition_token(
        object_type=finding.object_type,
        object_id=finding.object_id,
        status=finding.current_status,
        updated_at=finding.updated_at,
        celery_task_id=finding.celery_task_id,
        finding_kind=candidate_action,
        metadata_hash=metadata_hash,
    )
    object_kind = "job" if finding.object_type == "Job" else "intent"
    return {
        "apply_eligible": False,
        "apply_blockers": [
            "apply mode is intentionally not implemented; this report is evidence-only",
            "operator must inspect API, worker, and Celery evidence outside this command before any future state change",
            "future apply would require transaction-time compare-and-swap preconditions and a reviewed rollback path",
        ],
        "finding_priority": finding_priority(
            {
                "candidate_action": candidate_action,
                "detail": finding.detail,
                "category": finding.category,
                "object_id": finding.object_id,
            }
        ),
        "precondition_token": precondition_token,
        "metadata_hash": metadata_hash,
        "proposed_conditional_update": _proposed_conditional_update(finding),
        "required_confirm_token": f"future-apply:{object_kind}:{finding.object_id}:{candidate_action}:{precondition_token}",
    }


def finding_priority(finding: dict[str, Any]) -> str:
    candidate_action = str(finding.get("candidate_action") or "")
    detail = str(finding.get("detail") or "")
    if candidate_action == "inspect_pending_video_export_without_task_id" or "pending_without_task_id" in detail:
        return "01_pending_without_task_id_dispatch_window"
    if candidate_action == "inspect_video_export_missing_task_id":
        return "02_video_export_missing_task_id"
    if candidate_action == "inspect_active_job_progress_terminal_mismatch":
        return "03_impossible_active_progress"
    if candidate_action == "inspect_stale_active_video_export":
        return "04_stale_active_video_export"
    if candidate_action in {"inspect_stale_followup_intent", "inspect_claimed_followup_missing_task_id"}:
        return "05_stale_followup_intent"
    if candidate_action == "inspect_orphan_followup_intent_reference":
        return "06_orphan_followup_reference"
    return "99_operator_inspect_recovery_candidate"


def finding_priority_sort_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    return (
        finding_priority(finding),
        str(finding.get("category") or ""),
        str(finding.get("object_id") or ""),
    )


def _object_summaries_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for finding in findings:
        key = (finding.get("object_type"), finding.get("object_id"))
        grouped.setdefault(key, []).append(finding)

    summaries: list[dict[str, Any]] = []
    for grouped_findings in grouped.values():
        primary = sorted(grouped_findings, key=finding_priority_sort_key)[0]
        summaries.append(
            {
                "object_type": primary.get("object_type"),
                "object_id": primary.get("object_id"),
                "finding_count": len(grouped_findings),
                "primary_finding": primary.get("candidate_action"),
                "highest_risk_level": _highest_risk_level(grouped_findings),
                "candidate_actions": _sorted_unique(finding.get("candidate_action") for finding in grouped_findings),
                "apply_eligible": all(bool(finding.get("apply_eligible")) for finding in grouped_findings),
                "apply_blockers": _sorted_unique(
                    blocker
                    for finding in grouped_findings
                    for blocker in (finding.get("apply_blockers") or [])
                ),
                "precondition_tokens": _sorted_unique(finding.get("precondition_token") for finding in grouped_findings),
            }
        )
    return sorted(summaries, key=_object_summary_sort_key)


def _manual_command_summary_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        command = str(finding.get("suggested_manual_command") or "")
        if command:
            grouped.setdefault(command, []).append(finding)

    summaries: list[dict[str, Any]] = []
    for command, grouped_findings in grouped.items():
        affected_objects = {
            (finding.get("object_type"), finding.get("object_id"))
            for finding in grouped_findings
        }
        summaries.append(
            {
                "command": command,
                "object_count": len(affected_objects),
                "candidate_actions": _sorted_unique(finding.get("candidate_action") for finding in grouped_findings),
                "highest_risk_level": _highest_risk_level(grouped_findings),
                "requires_operator_checks": any(
                    bool(finding.get("requires_operator_checks")) for finding in grouped_findings
                ),
                "apply_eligible": all(bool(finding.get("apply_eligible")) for finding in grouped_findings),
                "apply_blockers": _sorted_unique(
                    blocker
                    for finding in grouped_findings
                    for blocker in (finding.get("apply_blockers") or [])
                ),
            }
        )
    return sorted(summaries, key=lambda summary: str(summary.get("command") or ""))


def _highest_risk_level(findings: list[dict[str, Any]]) -> str:
    risk_order = {"low": 1, "medium": 2, "high": 3}
    return max(
        (str(finding.get("risk_level") or "low").lower() for finding in findings),
        key=lambda risk_level: risk_order.get(risk_level, 0),
        default="low",
    )


def _sorted_unique(values) -> list[Any]:
    return sorted({value for value in values if value not in (None, "")}, key=str)


def _object_summary_sort_key(summary: dict[str, Any]) -> tuple[str, str]:
    return (
        str(summary.get("object_type") or ""),
        str(summary.get("object_id") or ""),
    )


def _metadata_hash(metadata: dict[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    encoded = json.dumps(_json_safe(metadata), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _precondition_token(
    *,
    object_type: str,
    object_id: int | str,
    status: str | None,
    updated_at: str | None,
    celery_task_id: str | None,
    finding_kind: str,
    metadata_hash: str | None,
) -> str:
    payload = {
        "object_type": object_type,
        "object_id": object_id,
        "status": status,
        "updated_at": updated_at,
        "celery_task_id": celery_task_id or "",
        "finding_kind": finding_kind,
        "metadata_hash": metadata_hash,
    }
    encoded = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _proposed_conditional_update(finding: RenderRecoveryFinding) -> str:
    if finding.object_type == "Job":
        return (
            "WHERE id = {id} AND job_type = 'video_export' AND status = {status} "
            "AND updated_at = {updated_at} AND celery_task_id = {celery_task_id}"
        ).format(
            id=finding.object_id,
            status=_sql_literal(finding.current_status),
            updated_at=_sql_literal(finding.updated_at),
            celery_task_id=_sql_literal(finding.celery_task_id or ""),
        )
    return (
        "WHERE id = {id} AND status = {status} AND updated_at = {updated_at} "
        "AND metadata_hash = {metadata_hash}"
    ).format(
        id=finding.object_id,
        status=_sql_literal(finding.current_status),
        updated_at=_sql_literal(finding.updated_at),
        metadata_hash=_sql_literal(_metadata_hash(finding.metadata)),
    )


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _finding_for_job(job, *, category: str, now, recommended_action: str, detail: str) -> RenderRecoveryFinding:
    return RenderRecoveryFinding(
        category=category,
        object_type="Job",
        object_id=int(job.id),
        project_id=job.project_id,
        age_seconds=_object_age(now, job),
        recommended_action=recommended_action,
        detail=detail,
        current_status=str(job.status or ""),
        updated_at=_isoformat(getattr(job, "updated_at", None)),
        celery_task_id=str(getattr(job, "celery_task_id", "") or ""),
    )


def _finding_for_intent(intent, *, category: str, now, recommended_action: str, detail: str) -> RenderRecoveryFinding:
    metadata = dict(getattr(intent, "metadata", None) or {})
    return RenderRecoveryFinding(
        category=category,
        object_type="RenderFollowUpIntent",
        object_id=int(intent.id),
        project_id=intent.project_id,
        age_seconds=_object_age(now, intent),
        recommended_action=recommended_action,
        detail=detail,
        current_status=str(intent.status or ""),
        updated_at=_isoformat(getattr(intent, "updated_at", None)),
        celery_task_id=str(metadata.get("celery_task_id") or ""),
        metadata=metadata,
    )


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
        findings.append(_finding_for_job(job, category="stuck_render_job", now=now, recommended_action=action, detail=detail))
    return findings


def _detect_impossible_render_states(Job, *, now) -> list[RenderRecoveryFinding]:
    findings: list[RenderRecoveryFinding] = []
    impossible_jobs = Job.objects.filter(job_type="video_export", status__in=("pending", "running")).filter(
        progress__gte=100
    )
    for job in impossible_jobs.order_by("updated_at", "id"):
        findings.append(
            _finding_for_job(
                job,
                category="stuck_render_job",
                now=now,
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
            _finding_for_intent(
                intent,
                category="stuck_followup_intent",
                now=now,
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
        findings.append(_finding_for_job(job, category="orphan_recovery_candidate", now=now, recommended_action=action, detail=detail))

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
                _finding_for_intent(
                    intent,
                    category="orphan_recovery_candidate",
                    now=now,
                    recommended_action="Review whether the referenced base render completed, failed, or was superseded before manually cancelling or recreating the follow-up.",
                    detail=f"metadata.active_job_id={active_job_id} is not an active render job",
                )
            )
        if dispatched_job_id and not Job.objects.filter(pk=dispatched_job_id, job_type="video_export").exists():
            findings.append(
                _finding_for_intent(
                    intent,
                    category="orphan_recovery_candidate",
                    now=now,
                    recommended_action="Inspect audit history and worker logs; the intent points at a missing reserved follow-up render job.",
                    detail=f"metadata.dispatched_job_id={dispatched_job_id} does not exist",
                )
            )
    return findings
