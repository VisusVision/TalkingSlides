"""Manual, operator-triggered render recovery actions.

Actions in this module are intentionally audit-only. They do not dispatch
Celery work, requeue jobs, delete records, or mutate render lifecycle state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.forms.models import model_to_dict
from django.utils import timezone

from core.render_recovery import DEFAULT_MAX_AGE_HOURS, build_render_recovery_report, finding_priority_sort_key


logger = logging.getLogger(__name__)

ACTION_INSPECT = "inspect"
ACTION_RESOLVE = "resolve"
ACTION_IGNORE = "ignore"
SUPPORTED_ACTIONS = {ACTION_INSPECT, ACTION_RESOLVE, ACTION_IGNORE}
SUPPORTED_TYPES = {"job", "intent"}


@dataclass(frozen=True)
class RenderRecoveryActionResult:
    action: str
    object_type: str
    object_id: int
    dry_run: bool
    executed: bool
    generated_at: str
    target: dict[str, Any]
    before_state: dict[str, Any]
    related_ids: dict[str, Any]
    remediation_plan: dict[str, Any] | None
    current_candidate: bool
    matched_findings_count: int
    object_state: dict[str, Any]
    recommendation: dict[str, Any] | None
    audit_record: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "dry_run": self.dry_run,
            "executed": self.executed,
            "generated_at": self.generated_at,
            "target": self.target,
            "before_state": self.before_state,
            "related_ids": self.related_ids,
            "remediation_plan": self.remediation_plan,
            "current_candidate": self.current_candidate,
            "matched_findings_count": self.matched_findings_count,
            "object_state": self.object_state,
            "recommendation": self.recommendation,
            "audit_record": self.audit_record,
        }


def run_render_recovery_action(
    *,
    action: str,
    object_type: str,
    object_id: int | str,
    dry_run: bool = True,
    confirmed: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> RenderRecoveryActionResult:
    normalized_action = str(action or "").strip().lower()
    normalized_type = str(object_type or "").strip().lower()
    if normalized_action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported recovery action: {action}")
    if normalized_type not in SUPPORTED_TYPES:
        raise ValueError(f"unsupported recovery object type: {object_type}")

    resolved_id = int(object_id)
    model_object = _get_object(normalized_type, resolved_id)
    object_state = _serialize_model_object(model_object)
    target = _serialize_target(normalized_type, model_object)
    before_state = _serialize_before_state(normalized_type, model_object)
    related_ids = _serialize_related_ids(normalized_type, model_object)
    recommendation = _find_recommendation(
        object_type=normalized_type,
        object_id=resolved_id,
        max_age_hours=max_age_hours,
    )
    if normalized_action in {ACTION_RESOLVE, ACTION_IGNORE} and recommendation is None:
        raise LookupError(f"{normalized_type} {resolved_id} is not a current recovery candidate")

    executed = bool(normalized_action in {ACTION_RESOLVE, ACTION_IGNORE} and confirmed and not dry_run)
    matched_findings = list((recommendation or {}).get("findings") or [])
    remediation_plan = _primary_remediation_plan(matched_findings)
    current_candidate = bool(matched_findings)
    generated_at = timezone.now().isoformat()
    audit_record = {
        "event": "render_recovery_action",
        "generated_at": generated_at,
        "action": normalized_action,
        "object_type": normalized_type,
        "object_id": resolved_id,
        "dry_run": bool(dry_run),
        "confirmed": bool(confirmed),
        "executed": executed,
        "annotation_only": normalized_action in {ACTION_RESOLVE, ACTION_IGNORE},
        "target": target,
        "before_state": before_state,
        "related_ids": related_ids,
        "remediation_plan": remediation_plan,
        "current_candidate": current_candidate,
        "matched_findings_count": len(matched_findings),
        "recommendation": recommendation,
    }
    audit_written = bool(normalized_action == ACTION_INSPECT or executed)
    audit_record["audit_written"] = audit_written
    if audit_written:
        _write_audit_record(audit_record)
        logger.info("render_recovery_action audit=%s", json.dumps(audit_record, sort_keys=True))

    return RenderRecoveryActionResult(
        action=normalized_action,
        object_type=normalized_type,
        object_id=resolved_id,
        dry_run=bool(dry_run),
        executed=executed,
        generated_at=generated_at,
        target=target,
        before_state=before_state,
        related_ids=related_ids,
        remediation_plan=remediation_plan,
        current_candidate=current_candidate,
        matched_findings_count=len(matched_findings),
        object_state=object_state,
        recommendation=recommendation,
        audit_record=audit_record,
    )


def audit_log_path() -> Path:
    configured = getattr(settings, "RENDER_RECOVERY_AUDIT_LOG_PATH", "")
    if configured:
        return Path(configured)
    return Path(settings.STORAGE_ROOT) / "audit" / "render_recovery_actions.jsonl"


def _get_object(object_type: str, object_id: int):
    Job, RenderFollowUpIntent = _load_models()
    model = Job if object_type == "job" else RenderFollowUpIntent
    obj = model.objects.filter(pk=object_id).first()
    if obj is None:
        raise LookupError(f"{object_type} {object_id} was not found")
    return obj


def _load_models():
    from core.models import Job, RenderFollowUpIntent

    return Job, RenderFollowUpIntent


def _serialize_model_object(obj) -> dict[str, Any]:
    payload = model_to_dict(obj)
    payload["id"] = int(obj.pk)
    for field_name in ("created_at", "updated_at", "claimed_at"):
        if hasattr(obj, field_name):
            value = getattr(obj, field_name)
            payload[field_name] = value.isoformat() if value else None
    if hasattr(obj, "project_id"):
        payload["project_id"] = getattr(obj, "project_id")
    if hasattr(obj, "requested_by_id"):
        payload["requested_by_id"] = getattr(obj, "requested_by_id")
    return _json_safe(payload)


def _serialize_target(object_type: str, obj) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "object_id": int(obj.pk),
        "model_object_type": obj.__class__.__name__,
    }


def _serialize_before_state(object_type: str, obj) -> dict[str, Any]:
    if object_type == "job":
        payload = {
            "status": getattr(obj, "status", None),
            "progress": getattr(obj, "progress", None),
            "celery_task_id": getattr(obj, "celery_task_id", None),
            "error_message": getattr(obj, "error_message", None),
            "job_type": getattr(obj, "job_type", None),
            "project_id": getattr(obj, "project_id", None),
        }
    else:
        payload = {
            "status": getattr(obj, "status", None),
            "mode": getattr(obj, "mode", None),
            "metadata": dict(getattr(obj, "metadata", None) or {}),
            "claimed_at": _isoformat(getattr(obj, "claimed_at", None)),
            "project_id": getattr(obj, "project_id", None),
            "requested_by_id": getattr(obj, "requested_by_id", None),
        }
    payload["created_at"] = _isoformat(getattr(obj, "created_at", None))
    payload["updated_at"] = _isoformat(getattr(obj, "updated_at", None))
    payload["age_seconds"] = _age_seconds(obj)
    payload["age_hours"] = round(payload["age_seconds"] / 3600, 2)
    return _json_safe(payload)


def _serialize_related_ids(object_type: str, obj) -> dict[str, Any]:
    payload: dict[str, Any] = {"project_id": getattr(obj, "project_id", None)}
    if object_type == "job":
        payload["job_id"] = int(obj.pk)
        celery_task_id = getattr(obj, "celery_task_id", None)
        if celery_task_id:
            payload["celery_task_id"] = celery_task_id
        return _json_safe(payload)

    metadata = dict(getattr(obj, "metadata", None) or {})
    payload["intent_id"] = int(obj.pk)
    for key in ("active_job_id", "dispatched_job_id", "celery_task_id"):
        value = metadata.get(key)
        if value:
            payload[key] = value
    return _json_safe(payload)


def _primary_remediation_plan(findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not findings:
        return None
    finding = findings[0]
    return {
        "candidate_action": finding.get("candidate_action"),
        "action_mode": finding.get("action_mode"),
        "risk_level": finding.get("risk_level"),
        "requires_operator_checks": finding.get("requires_operator_checks"),
        "mutation_if_applied": finding.get("mutation_if_applied"),
        "dedupe_impact": finding.get("dedupe_impact"),
        "suggested_manual_command": finding.get("suggested_manual_command"),
        "apply_eligible": finding.get("apply_eligible"),
        "apply_blockers": finding.get("apply_blockers"),
        "finding_priority": finding.get("finding_priority"),
        "precondition_token": finding.get("precondition_token"),
        "metadata_hash": finding.get("metadata_hash"),
        "proposed_conditional_update": finding.get("proposed_conditional_update"),
        "required_confirm_token": finding.get("required_confirm_token"),
    }


def _age_seconds(obj) -> int:
    changed_at = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
    if changed_at is None:
        return 0
    return max(0, int((timezone.now() - changed_at).total_seconds()))


def _isoformat(value) -> str | None:
    return value.isoformat() if value else None


def _find_recommendation(*, object_type: str, object_id: int, max_age_hours: float) -> dict[str, Any] | None:
    report = build_render_recovery_report(dry_run=True, max_age_hours=max_age_hours)
    expected_object_type = "Job" if object_type == "job" else "RenderFollowUpIntent"
    matches = [
        finding.as_dict()
        for finding in report.findings
        if finding.object_type == expected_object_type and int(finding.object_id) == int(object_id)
    ]
    if not matches:
        return None
    return {"findings": sorted(matches, key=finding_priority_sort_key)}


def _write_audit_record(record: dict[str, Any]) -> None:
    path = audit_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(record), sort_keys=True) + "\n")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
