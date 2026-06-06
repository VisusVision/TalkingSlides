"""
Publication policy helpers shared between core.views and ai_agents.

Key rules
---------
* Moderation only BLOCKS publishing when the lesson has been explicitly
  rejected or flagged as needing revision.  Unscanned / pending / failed
  moderation does NOT prevent the owner from publishing — the owner sees a
  clear status badge instead.
* Staff / publisher / owner can always see their own lessons in Studio
  regardless of moderation state.
* The public catalog only shows lessons that are published, render-ready,
  AND moderation-approved (or admin-approved).
"""

# Moderation statuses that ACTIVELY BLOCK publication.
# Any status not in this set is allowed to publish (including not_scanned,
# pending, failed — which are informational, not enforcement gates).
BLOCKED_MODERATION_STATUSES = frozenset({
    "admin_rejected",
    "revision_required",
    "needs_admin_review",
    "failed",
    "not_scanned",
    "not_scanned_required",
    "pending",
    "processing",
    "running",
})

# Moderation statuses accepted for the *public catalog* filter.
# The catalog applies a stricter rule: only positively-approved lessons appear.
APPROVED_MODERATION_STATUSES = frozenset({"approved", "admin_approved"})
PROCESSING_MODERATION_STATUSES = frozenset({"pending", "processing", "running"})
UNSCANNED_REQUIRED_MODERATION_STATUSES = frozenset({"not_scanned", "not_scanned_required"})
MANUAL_PUBLICATION_BLOCK_STATUSES = frozenset({"blocked", "rejected", "request_changes", "needs_review"})
MANUAL_STICKY_RESCAN_BLOCK_STATUSES = frozenset({"blocked", "rejected", "needs_review"})

# The render status that means a video is ready to publish.
PUBLISHABLE_PROJECT_STATUS = "ready"
VISUAL_MODERATION_DEFAULT_PHASE = "visual_asset_scan"
VISUAL_BLOCKING_DECISIONS = frozenset({"block", "revision_required", "needs_admin_review"})
VISUAL_BLOCKING_SEVERITIES = frozenset({"high", "critical"})
VIDEO_FRAME_AUDIT_DEFAULT_PHASE = "video_frame_audit"
VIDEO_FRAME_AUDIT_BLOCKING_DECISIONS = frozenset({"block", "revision_required", "needs_admin_review"})
VIDEO_FRAME_AUDIT_BLOCKING_SEVERITIES = frozenset({"high", "critical"})
SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def project_can_publish(project) -> bool:
    """
    Return True when an owner/staff/publisher is allowed to publish this lesson.

    Rules:
    - render must be done (status == "ready")
    - moderation must NOT be in BLOCKED_MODERATION_STATUSES
      (not_scanned / pending / failed / approved / needs_admin_review → all allowed)
    """
    render_ready = str(getattr(project, "status", "") or "") == PUBLISHABLE_PROJECT_STATUS
    moderation = str(getattr(project, "moderation_status", "") or "")
    if not render_ready:
        return False
    if moderation not in APPROVED_MODERATION_STATUSES:
        return False
    if manual_moderation_blocks_publish(project):
        return False
    if project_has_unresolved_publication_block(project):
        return False
    if moderation == "admin_approved":
        return True
    return not visual_moderation_blocks_publish(project) and not video_frame_audit_blocks_publish(project)


def manual_moderation_blocks_publish(project) -> bool:
    manual_status = str(getattr(project, "manual_moderation_status", "") or "")
    return bool(
        getattr(project, "moderation_blocked_until_review", False)
        or manual_status in MANUAL_PUBLICATION_BLOCK_STATUSES
    )


def publisher_changed_after_manual_decision(project) -> bool:
    manual_at = getattr(project, "manual_moderation_at", None)
    changed_at = getattr(project, "latest_publisher_change_at", None)
    return bool(manual_at and changed_at and changed_at > manual_at)


def manual_moderation_prevents_auto_override(project) -> bool:
    manual_status = str(getattr(project, "manual_moderation_status", "") or "")
    if manual_status in MANUAL_STICKY_RESCAN_BLOCK_STATUSES and manual_moderation_blocks_publish(project):
        return True
    if manual_status == "request_changes" and manual_moderation_blocks_publish(project):
        return not publisher_changed_after_manual_decision(project)
    return False


def project_has_unresolved_publication_block(project) -> bool:
    if str(getattr(project, "moderation_status", "") or "") == "admin_approved":
        return False
    project_id = getattr(project, "pk", None)
    if not project_id:
        return False
    from ai_agents.models import PublicationBlockEvent

    return PublicationBlockEvent.objects.filter(project_id=project_id, resolved=False).exists()


def project_has_unresolved_moderation_block(project) -> bool:
    moderation = str(getattr(project, "moderation_status", "") or "").strip()
    if moderation in {"revision_required", "needs_admin_review", "admin_rejected", "failed"}:
        return True
    if manual_moderation_blocks_publish(project):
        return True
    if project_has_unresolved_publication_block(project):
        return True
    if moderation == "admin_approved":
        return False
    return visual_moderation_blocks_publish(project) or video_frame_audit_blocks_publish(project)


def enforce_unpublished_for_unresolved_moderation(project, *, save: bool = True) -> bool:
    """Fail closed: unresolved moderation blockers must never remain public."""
    if not bool(getattr(project, "is_published", False)):
        return False
    if not project_has_unresolved_moderation_block(project):
        return False
    project.is_published = False
    if save:
        project.save(update_fields=["is_published", "updated_at"])
    return True


def visual_moderation_blocks_publish(project) -> bool:
    return visual_publication_block_payload(project)["blocked"]


def visual_publication_block_payload(project) -> dict:
    latest_run = _latest_visual_asset_run(project)
    if latest_run is None and not _visual_publish_gate_enabled():
        return {
            "blocked": False,
            "reason": "visual_publish_gate_disabled",
            "visual_moderation_status": "disabled",
            "finding_count": 0,
        }

    if latest_run is None:
        return {
            "blocked": False,
            "reason": "visual_scan_not_found",
            "visual_moderation_status": "not_scanned",
            "finding_count": 0,
        }

    serious_findings = _serious_visual_findings(latest_run)
    finding_count = serious_findings.count()
    final_decision = str(getattr(latest_run, "final_decision", "") or "")
    final_decision_blocks = final_decision in VISUAL_BLOCKING_DECISIONS
    blocked = bool(finding_count or final_decision_blocks)
    return {
        "blocked": blocked,
        "reason": "visual_moderation_rejected" if blocked else "visual_moderation_allowed",
        "visual_moderation_status": final_decision or "unknown",
        "finding_count": finding_count,
        "latest_visual_run_id": latest_run.id,
        "phase": str(getattr(latest_run, "phase", "") or ""),
    }


def video_frame_audit_blocks_publish(project) -> bool:
    return video_frame_audit_publication_block_payload(project)["blocked"]


def video_frame_audit_publication_block_payload(project) -> dict:
    latest_run = _latest_video_frame_audit_run(project)
    if latest_run is None and not _video_frame_audit_publish_gate_enabled():
        return {
            "blocked": False,
            "reason": "video_frame_audit_publish_gate_disabled",
            "video_frame_audit_status": "disabled",
            "finding_count": 0,
        }

    if latest_run is None:
        return {
            "blocked": False,
            "reason": "video_frame_audit_not_found",
            "video_frame_audit_status": "not_scanned",
            "finding_count": 0,
        }

    serious_findings = list(_serious_video_frame_audit_findings(latest_run))
    finding_count = len(serious_findings)
    final_decision = str(getattr(latest_run, "final_decision", "") or "")
    final_decision_blocks = final_decision in VIDEO_FRAME_AUDIT_BLOCKING_DECISIONS
    blocked = bool(finding_count or final_decision_blocks)
    highest = _highest_severity_finding(serious_findings)
    return {
        "blocked": blocked,
        "reason": "video_frame_audit_rejected" if blocked else "video_frame_audit_allowed",
        "video_frame_audit_status": final_decision or "unknown",
        "finding_count": finding_count,
        "latest_run_id": latest_run.id,
        "latest_video_frame_run_id": latest_run.id,
        "phase": str(getattr(latest_run, "phase", "") or ""),
        "highest_category": str(getattr(highest, "category", "") or "") if highest else "",
        "highest_severity": str(getattr(highest, "severity", "") or "") if highest else "",
    }


def moderation_is_approved_for_catalog(project) -> bool:
    """
    Stricter check used only for the PUBLIC catalog.

    Returns True only when the moderation status is positively approved.
    Used to filter what anonymous/student users see — not what owners see.
    """
    moderation = str(getattr(project, "moderation_status", "") or "")
    return moderation in APPROVED_MODERATION_STATUSES


def publication_block_payload(project) -> dict:
    """
    Structured error response when publish is actively blocked.
    Includes a human-readable reason the frontend can surface.
    """
    moderation = str(getattr(project, "moderation_status", "") or "")
    render_status = str(getattr(project, "status", "") or "")

    if render_status != PUBLISHABLE_PROJECT_STATUS:
        return {
            "detail": "This lesson cannot be published until rendering is complete.",
            "reason": "render_not_ready",
            "moderation_status": moderation,
            "render_status": render_status,
        }

    if manual_moderation_blocks_publish(project):
        note = str(getattr(project, "manual_moderation_reason", "") or "").strip()
        return {
            "detail": note or "Publish blocked by moderation. Update the lesson and request review.",
            "reason": "manual_moderation_block",
            "moderation_status": moderation,
            "manual_moderation_status": str(getattr(project, "manual_moderation_status", "") or ""),
            "render_status": render_status,
        }

    if moderation in PROCESSING_MODERATION_STATUSES:
        return {
            "detail": "Moderation in progress. Publishing is temporarily blocked.",
            "reason": "moderation_processing",
            "moderation_status": moderation,
            "render_status": render_status,
        }

    if moderation in UNSCANNED_REQUIRED_MODERATION_STATUSES:
        return {
            "detail": "This lesson must pass moderation before publishing.",
            "reason": "moderation_required",
            "moderation_status": moderation,
            "render_status": render_status,
        }

    if moderation not in APPROVED_MODERATION_STATUSES:
        return {
            "detail": (
                "This lesson cannot be published because moderation has not approved it. "
                "Please revise the content or request an admin review."
            ),
            "reason": "moderation_rejected",
            "moderation_status": moderation,
            "render_status": render_status,
        }

    if project_has_unresolved_publication_block(project):
        latest_block = _latest_unresolved_publication_block(project)
        return {
            "detail": str(getattr(latest_block, "message_to_user", "") or "").strip()
            or "This lesson cannot be published until moderation blockers are cleared.",
            "reason": "publication_block",
            "moderation_status": moderation,
            "render_status": render_status,
        }

    visual_block = visual_publication_block_payload(project)
    if visual_block["blocked"]:
        return {
            "detail": "This lesson cannot be published until visual moderation findings are resolved.",
            "reason": "visual_moderation_rejected",
            "moderation_status": moderation,
            "render_status": render_status,
            "visual_moderation_status": visual_block["visual_moderation_status"],
            "finding_count": visual_block["finding_count"],
            "latest_visual_run_id": visual_block.get("latest_visual_run_id"),
        }

    video_frame_block = video_frame_audit_publication_block_payload(project)
    if video_frame_block["blocked"]:
        return {
            "detail": "This lesson cannot be published until video frame audit findings are resolved.",
            "message": "This lesson cannot be published until video frame audit findings are resolved.",
            "reason": "video_frame_audit_rejected",
            "moderation_status": moderation,
            "render_status": render_status,
            "video_frame_audit_status": video_frame_block["video_frame_audit_status"],
            "finding_count": video_frame_block["finding_count"],
            "latest_run_id": video_frame_block.get("latest_run_id"),
            "highest_category": video_frame_block.get("highest_category", ""),
            "highest_severity": video_frame_block.get("highest_severity", ""),
        }

    # Fallback (should not normally occur if project_can_publish is checked first)
    return {
        "detail": "This lesson cannot be published.",
        "reason": "unknown",
        "moderation_status": moderation,
        "render_status": render_status,
    }


def _latest_unresolved_publication_block(project):
    project_id = getattr(project, "pk", None)
    if not project_id:
        return None
    from ai_agents.models import PublicationBlockEvent

    return PublicationBlockEvent.objects.filter(project_id=project_id, resolved=False).order_by("-created_at", "-id").first()


def _visual_publish_gate_enabled() -> bool:
    from django.conf import settings

    return bool(getattr(settings, "VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION", False))


def _video_frame_audit_publish_gate_enabled() -> bool:
    from django.conf import settings

    return bool(getattr(settings, "VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION", False))


def _visual_asset_phase() -> str:
    from django.conf import settings

    configured_phase = str(
        getattr(settings, "VISUAL_MODERATION_PHASE", VISUAL_MODERATION_DEFAULT_PHASE) or ""
    ).strip()
    return configured_phase or VISUAL_MODERATION_DEFAULT_PHASE


def _video_frame_audit_phase() -> str:
    from django.conf import settings

    configured_phase = str(
        getattr(settings, "VIDEO_FRAME_AUDIT_PHASE", VIDEO_FRAME_AUDIT_DEFAULT_PHASE) or ""
    ).strip()
    return configured_phase or VIDEO_FRAME_AUDIT_DEFAULT_PHASE


def _latest_visual_asset_run(project):
    from ai_agents.models import AgentRun

    return (
        AgentRun.objects.filter(
            project_id=getattr(project, "id", None),
            phase=_visual_asset_phase(),
            status__in=["done", "completed"],
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _latest_video_frame_audit_run(project):
    from ai_agents.models import AgentRun

    return (
        AgentRun.objects.filter(
            project_id=getattr(project, "id", None),
            phase=_video_frame_audit_phase(),
            status__in=["done", "completed"],
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _serious_visual_findings(run):
    from ai_agents.models import AgentFinding
    from django.db.models import Q

    return AgentFinding.objects.filter(run=run).filter(
        Q(decision__in=VISUAL_BLOCKING_DECISIONS) | Q(severity__in=VISUAL_BLOCKING_SEVERITIES)
    )


def _serious_video_frame_audit_findings(run):
    from ai_agents.models import AgentFinding
    from django.db.models import Q

    return AgentFinding.objects.filter(run=run).filter(
        Q(decision__in=VIDEO_FRAME_AUDIT_BLOCKING_DECISIONS)
        | Q(severity__in=VIDEO_FRAME_AUDIT_BLOCKING_SEVERITIES)
    )


def _highest_severity_finding(findings):
    return max(
        findings,
        key=lambda finding: SEVERITY_RANK.get(str(getattr(finding, "severity", "") or "").lower(), 0),
        default=None,
    )
