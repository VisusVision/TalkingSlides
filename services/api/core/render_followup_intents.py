"""Helpers for recording and claiming render follow-up intents."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django.utils import timezone
from django.db import IntegrityError, transaction

from core.models import Job, Project, RENDER_FOLLOWUP_ACTIVE_STATUSES, RenderFollowUpIntent


STRUCTURAL_REASONS = {
    "structural_action",
    "transcript_structural_action",
    "transcript_split",
    "transcript_merge",
    "transcript_reorder",
    "transcript_delete",
    "transcript_restore",
}


def merge_render_followup_intent(
    *,
    project: Project,
    mode: str = RenderFollowUpIntent.MODE_TARGETED,
    page_keys: Iterable[str] | None = None,
    reason: str = "",
    requested_by=None,
    metadata: Mapping[str, Any] | None = None,
) -> RenderFollowUpIntent:
    """Create or merge the active follow-up intent for a project.

    This is intentionally a foundation helper only: it records durable intent
    and never dispatches render work or mutates API/worker behavior.
    """

    for attempt in range(2):
        try:
            return _merge_render_followup_intent_once(
                project=project,
                mode=mode,
                page_keys=page_keys,
                reason=reason,
                requested_by=requested_by,
                metadata=metadata,
            )
        except IntegrityError:
            if attempt:
                raise

    raise RuntimeError("unreachable render follow-up intent retry state")


def _merge_render_followup_intent_once(
    *,
    project: Project,
    mode: str,
    page_keys: Iterable[str] | None,
    reason: str,
    requested_by,
    metadata: Mapping[str, Any] | None,
) -> RenderFollowUpIntent:
    normalized_reason = str(reason or "").strip()
    incoming_mode = _normalize_mode(mode, normalized_reason)
    incoming_page_keys = [] if incoming_mode == RenderFollowUpIntent.MODE_FULL else _normalize_page_keys(page_keys)
    incoming_metadata = dict(metadata or {})

    with transaction.atomic():
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        intent = (
            RenderFollowUpIntent.objects.select_for_update()
            .filter(project=locked_project, status__in=RENDER_FOLLOWUP_ACTIVE_STATUSES)
            .first()
        )

        if intent is None:
            return RenderFollowUpIntent.objects.create(
                project=locked_project,
                mode=incoming_mode,
                page_keys=incoming_page_keys,
                status=RenderFollowUpIntent.STATUS_PENDING,
                reason=normalized_reason,
                requested_by=requested_by,
                metadata=incoming_metadata,
            )

        changed_fields = {"updated_at"}
        if intent.mode != RenderFollowUpIntent.MODE_FULL:
            if incoming_mode == RenderFollowUpIntent.MODE_FULL:
                intent.mode = RenderFollowUpIntent.MODE_FULL
                intent.page_keys = []
                changed_fields.update({"mode", "page_keys"})
            else:
                merged_page_keys = _union_page_keys(intent.page_keys, incoming_page_keys)
                if merged_page_keys != intent.page_keys:
                    intent.page_keys = merged_page_keys
                    changed_fields.add("page_keys")

        if normalized_reason and normalized_reason != intent.reason:
            intent.reason = normalized_reason
            changed_fields.add("reason")
        if requested_by is not None and requested_by_id(requested_by) != intent.requested_by_id:
            intent.requested_by = requested_by
            changed_fields.add("requested_by")

        merged_metadata = _safe_merge_metadata(intent.metadata, incoming_metadata)
        if merged_metadata != intent.metadata:
            intent.metadata = merged_metadata
            changed_fields.add("metadata")

        intent.save(update_fields=sorted(changed_fields))
        return intent


def claim_render_followup_intent_for_completed_job(
    *,
    project_id: int | str,
    completed_job_id: int | str | None,
) -> tuple[RenderFollowUpIntent, Job] | None:
    """Claim a pending intent and reserve the follow-up render job.

    Dispatch is intentionally left to the caller and should happen only after
    this transaction commits.
    """

    if not completed_job_id:
        return None

    completed_id = int(completed_job_id)
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=int(project_id))
        active_job_exists = (
            Job.objects.select_for_update()
            .filter(project=project, job_type="video_export", status__in=("pending", "running"))
            .exclude(pk=completed_id)
            .exists()
        )
        if active_job_exists:
            return None

        intent = (
            RenderFollowUpIntent.objects.select_for_update()
            .filter(project=project, status=RenderFollowUpIntent.STATUS_PENDING)
            .order_by("-created_at", "-id")
            .first()
        )
        if intent is None:
            return None

        metadata = dict(intent.metadata or {})
        active_job_id = metadata.get("active_job_id")
        if str(active_job_id or "") != str(completed_id):
            metadata.update(
                {
                    "cancelled_reason": "active_job_id_mismatch",
                    "completed_job_id": completed_id,
                    "cancelled_at": timezone.now().isoformat(),
                }
            )
            intent.status = RenderFollowUpIntent.STATUS_CANCELLED
            intent.metadata = metadata
            intent.save(update_fields=["status", "metadata", "updated_at"])
            return None

        job = Job.objects.create(project=project, job_type="video_export", status="pending")
        metadata.update(
            {
                "claimed_at": timezone.now().isoformat(),
                "completed_job_id": completed_id,
                "dispatched_job_id": job.id,
            }
        )
        intent.status = RenderFollowUpIntent.STATUS_CLAIMED
        intent.claimed_at = timezone.now()
        intent.metadata = metadata
        intent.save(update_fields=["status", "claimed_at", "metadata", "updated_at"])
        return intent, job


def mark_render_followup_intent_dispatched(
    *,
    intent_id: int | str,
    job_id: int | str,
    celery_task_id: str,
) -> None:
    now = timezone.now()
    with transaction.atomic():
        intent = RenderFollowUpIntent.objects.select_for_update().filter(pk=int(intent_id)).first()
        if intent is None:
            return
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "dispatched_job_id": int(job_id),
                "celery_task_id": str(celery_task_id or ""),
                "dispatched_at": now.isoformat(),
                "cleared_at": now.isoformat(),
            }
        )
        intent.status = RenderFollowUpIntent.STATUS_CLEARED
        intent.metadata = metadata
        intent.save(update_fields=["status", "metadata", "updated_at"])


def mark_render_followup_intent_dispatch_failed(
    *,
    intent_id: int | str,
    job_id: int | str | None,
    error_message: str,
) -> None:
    now = timezone.now()
    with transaction.atomic():
        intent = RenderFollowUpIntent.objects.select_for_update().filter(pk=int(intent_id)).first()
        if intent is None:
            return
        metadata = dict(intent.metadata or {})
        metadata.update(
            {
                "dispatch_failed_at": now.isoformat(),
                "dispatch_error": str(error_message or "dispatch_failed")[:1000],
            }
        )
        if job_id:
            metadata["dispatched_job_id"] = int(job_id)
        intent.status = RenderFollowUpIntent.STATUS_CANCELLED
        intent.metadata = metadata
        intent.save(update_fields=["status", "metadata", "updated_at"])


def _normalize_mode(mode: str, reason: str) -> str:
    if reason in STRUCTURAL_REASONS:
        return RenderFollowUpIntent.MODE_FULL
    if mode == RenderFollowUpIntent.MODE_FULL:
        return RenderFollowUpIntent.MODE_FULL
    return RenderFollowUpIntent.MODE_TARGETED


def _normalize_page_keys(page_keys: Iterable[str] | None) -> list[str]:
    return _union_page_keys([], page_keys or [])


def _union_page_keys(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for raw_key in [*existing, *incoming]:
        key = str(raw_key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _safe_merge_metadata(existing: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    conflicts = list(merged.get("merge_conflicts") or [])
    for key, value in incoming.items():
        if key not in merged:
            merged[key] = value
        elif merged[key] != value:
            conflicts.append({"key": key, "incoming": value})
    if conflicts:
        merged["merge_conflicts"] = conflicts
    return merged


def requested_by_id(user) -> int | None:
    return getattr(user, "pk", None)
