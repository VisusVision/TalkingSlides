
from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from ai_agents.models import (
    AdminReviewRequest,
    AgentFinding,
    AgentRun,
    ModerationAuditEvent,
    ModerationReport,
    PublicationBlockEvent,
)
from ai_agents.policies import (
    APPROVED_MODERATION_STATUSES,
    manual_moderation_blocks_publish,
    project_can_publish,
    publication_block_payload,
)


SEVERITY_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

REVIEWABLE_MODERATION_STATUSES = frozenset(
    {
        "not_scanned",
        "revision_required",
        "needs_admin_review",
        "admin_rejected",
        "failed",
    }
)


class RescanModerationSerializer(serializers.Serializer):
    phase = serializers.CharField(required=False, allow_blank=True, max_length=50, default="manual_rescan")

    def validate_phase(self, value: str) -> str:
        cleaned = str(value or "manual_rescan").strip() or "manual_rescan"
        if len(cleaned) > 50:
            raise serializers.ValidationError("phase must be 50 characters or less.")
        return cleaned


class RequestAdminReviewSerializer(serializers.Serializer):
    message = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")


class ModerationReportCreateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=[choice[0] for choice in ModerationReport.CATEGORY_CHOICES])
    message = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")

    def validate_message(self, value: str) -> str:
        return str(value or "").strip()


class AdminReviewDecisionSerializer(serializers.Serializer):
    admin_response = serializers.CharField(required=False, allow_blank=True, max_length=4000, default="")


class AdminProjectModerationActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=["approve", "block", "needs_review", "request_changes", "add_note", "rescan"]
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=4000, default="")
    note = serializers.CharField(required=False, allow_blank=True, max_length=4000, default="")
    phase = serializers.CharField(required=False, allow_blank=True, max_length=50, default="manual_admin_rescan")
    unpublish = serializers.BooleanField(required=False, default=True)

    def validate_phase(self, value: str) -> str:
        cleaned = str(value or "manual_admin_rescan").strip() or "manual_admin_rescan"
        if len(cleaned) > 50:
            raise serializers.ValidationError("phase must be 50 characters or less.")
        return cleaned


class AdminModerationReportActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["dismiss", "resolve", "reopen"])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=4000, default="")


class AdminReviewRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminReviewRequest
        fields = ["id", "project", "run", "status", "publisher_message", "created_at"]
        read_only_fields = fields


def moderation_summary_payload(project, *, include_admin_fields: bool = False) -> dict[str, Any]:
    summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
    raw_status = str(project.moderation_status or "")
    can_publish = project_can_publish(project)
    publish_block = publication_block_payload(project) if not can_publish else {}
    run = _latest_run(project)
    findings = _moderation_findings_for_project(project, base_run=run)
    effective_status = _effective_moderation_status(project, findings, publish_block=publish_block)
    message = _effective_moderation_message(
        project,
        findings,
        summary=summary,
        effective_status=effective_status,
        can_publish=can_publish,
        publish_block=publish_block,
    )
    has_open_review = project.admin_review_requests.filter(status="open").exists()
    manual_status = str(getattr(project, "manual_moderation_status", "") or "").strip()
    manually_reviewable = manual_status in {"blocked", "rejected", "request_changes", "needs_review"} or bool(
        getattr(project, "moderation_blocked_until_review", False)
    )
    can_request_admin_review = (
        (str(project.moderation_status or "") in REVIEWABLE_MODERATION_STATUSES or manually_reviewable)
        and not has_open_review
    )
    visual_issues = [
        *_visual_issue_payloads(
            project,
            findings,
            include_admin_fields=include_admin_fields,
        ),
        *_pending_visual_issue_payloads(project, summary, findings),
    ]

    payload = {
        "project_id": project.id,
        "moderation_status": effective_status,
        "raw_moderation_status": raw_status,
        "can_publish": can_publish,
        "manual_moderation_status": str(getattr(project, "manual_moderation_status", "") or ""),
        "manual_moderation_reason": str(getattr(project, "manual_moderation_reason", "") or ""),
        "manual_moderation_at": getattr(project, "manual_moderation_at", None),
        "moderation_blocked_until_review": bool(getattr(project, "moderation_blocked_until_review", False)),
        "publish_blocked_by_moderation": _publish_blocked_by_moderation(can_publish, publish_block),
        "publish_block": publish_block if publish_block else None,
        "publish_block_reason": str(publish_block.get("reason") or "") if publish_block else "",
        "latest_publisher_change_at": getattr(project, "latest_publisher_change_at", None),
        "latest_review_requested_at": getattr(project, "latest_review_requested_at", None),
        "can_request_admin_review": can_request_admin_review,
        "message": message,
        "admin_review": latest_admin_review_payload(project),
        "editor_text_changed": summary.get("editor_text_changed") if isinstance(summary.get("editor_text_changed"), dict) else None,
        "visual_asset_scan": summary.get("visual_asset_scan") if isinstance(summary.get("visual_asset_scan"), dict) else None,
        "visual_issues": visual_issues,
        "latest_run_id": run.id if run else project.last_moderation_run_id,
        "findings": [
            finding_payload(finding, include_admin_fields=include_admin_fields, project=project)
            for finding in findings
        ],
    }
    if include_admin_fields:
        payload["admin_audit_events"] = [
            moderation_audit_event_payload(event)
            for event in project.moderation_audit_events.select_related("actor").order_by("-created_at", "-id")[:10]
        ]
    else:
        public_note = (
            str(getattr(project, "manual_moderation_reason", "") or "").strip()
            or summary.get("publisher_admin_note")
            or summary.get("admin_response")
            or ""
        )
        if public_note:
            payload["admin_note"] = str(public_note)
    return payload


def latest_admin_review_payload(project) -> dict[str, Any] | None:
    review = project.admin_review_requests.select_related("reviewed_by").order_by("-created_at", "-id").first()
    if review is None:
        return None
    return {
        "id": review.id,
        "status": review.status,
        "publisher_message": review.publisher_message,
        "admin_response": review.admin_response,
        "reviewed_at": review.reviewed_at,
        "updated_at": review.reviewed_at or review.created_at,
        "reviewed_by_username": _username(review.reviewed_by),
    }


def moderation_report_payload(report: ModerationReport, *, deduped: bool = False) -> dict[str, Any]:
    project = report.project
    return {
        "id": report.id,
        "source_type": "report",
        "source": "user_report",
        "allowed_actions": _allowed_actions_for_project_item(project, source_type="report", status=report.status),
        "project_id": report.project_id,
        "project_title": getattr(project, "title", "") or "",
        "publisher_id": report.publisher_id,
        "publisher_username": _username(report.publisher),
        "reporter_id": report.reporter_id,
        "reporter_username": _username(report.reporter),
        "category": report.category,
        "category_label": _choice_label(ModerationReport.CATEGORY_CHOICES, report.category),
        "message": report.message,
        "status": report.status,
        "moderation_status": getattr(project, "moderation_status", "") or "",
        "manual_moderation_status": str(getattr(project, "manual_moderation_status", "") or ""),
        "manual_moderation_reason": str(getattr(project, "manual_moderation_reason", "") or ""),
        "latest_message": report.message,
        "reason_category": report.category,
        "reason_label": _choice_label(ModerationReport.CATEGORY_CHOICES, report.category),
        "item_time": report.reviewed_at or report.created_at,
        "created_at": report.created_at,
        "reviewed_at": report.reviewed_at,
        "reviewed_by_id": report.reviewed_by_id,
        "reviewed_by_username": _username(report.reviewed_by),
        "admin_review_request_id": report.admin_review_request_id,
        "deduped": deduped,
    }


def admin_review_list_payload(review: AdminReviewRequest) -> dict[str, Any]:
    project = review.project
    findings = _sort_findings_by_severity(list(_findings_for_review(review)))
    highest = _highest_finding(findings)
    run = review.run or _latest_run(project)
    return {
        "id": review.id,
        "source_type": "review_request",
        "source": "publisher_recheck" if review.requested_by_id == project.user_id else "admin_review",
        "allowed_actions": _allowed_actions_for_project_item(project, source_type="review_request", status=review.status),
        "project_id": project.id,
        "project_title": project.title,
        "requested_by_id": review.requested_by_id,
        "requested_by_username": _username(review.requested_by),
        "publisher_username": _username(project.user),
        "publisher_id": project.user_id,
        "status": review.status,
        "moderation_status": project.moderation_status,
        "manual_moderation_status": str(getattr(project, "manual_moderation_status", "") or ""),
        "manual_moderation_reason": str(getattr(project, "manual_moderation_reason", "") or ""),
        "moderation_blocked_until_review": bool(getattr(project, "moderation_blocked_until_review", False)),
        "is_published": bool(getattr(project, "is_published", False)),
        "publisher_message": review.publisher_message,
        "admin_response": review.admin_response,
        "latest_message": review.admin_response or review.publisher_message,
        "reason_category": highest.category if highest else "",
        "reason_label": highest.category.replace("_", " ").title() if highest else "",
        "item_time": review.reviewed_at or review.created_at,
        "created_at": review.created_at,
        "requested_at": review.created_at,
        "reviewed_at": review.reviewed_at,
        "updated_at": review.reviewed_at or review.created_at,
        "reviewed_by_id": review.reviewed_by_id,
        "reviewed_by_username": _username(review.reviewed_by),
        "latest_run_id": run.id if run else review.run_id,
        "latest_final_decision": str(getattr(run, "final_decision", "") or ""),
        "finding_count": len(findings),
        "categories_summary": _count_values(findings, "category"),
        "severities_summary": _count_values(findings, "severity"),
        "latest_findings_summary": [
            finding_summary_payload(finding, project=project, include_admin_fields=True)
            for finding in findings[:5]
        ],
        "highest_severity": highest.severity if highest else "",
        "highest_category": highest.category if highest else "",
        "primary_reason_title": _finding_reason_title(highest) if highest else "",
        "primary_reason_message": _finding_admin_message(highest) if highest else "",
        "primary_asset_label": _primary_visual_asset_label(highest),
        "visual_issues": _visual_issue_payloads(project, findings, include_admin_fields=True, summary=True),
        "finding_badges": _finding_badges(findings),
        "has_scannable_assets": _project_has_scannable_assets(project),
    }


def admin_review_detail_payload(review: AdminReviewRequest) -> dict[str, Any]:
    project = review.project
    findings = _sort_findings_by_severity(list(_findings_for_review(review)))
    first_timestamp = _first_timestamp_seconds(findings)
    payload = admin_review_list_payload(review)
    payload.update(
        {
            "requested_by_id": review.requested_by_id,
            "reviewed_by_id": review.reviewed_by_id,
            "reviewed_by_username": _username(review.reviewed_by),
            "run_id": review.run_id,
            "project_moderation": moderation_summary_payload(project, include_admin_fields=True),
            "findings": [
                finding_payload(finding, include_admin_fields=True, project=project)
                for finding in findings
            ],
            "open_project_studio_hint": f"/studio?lesson={project.id}&review=1",
            "open_review_hint": f"/watch?lesson={project.id}&review=1",
            "open_watch_timestamp_hint": (
                f"/watch?lesson={project.id}&review=1&t={first_timestamp:g}"
                if first_timestamp is not None
                else f"/watch?lesson={project.id}&review=1"
            ),
        }
    )
    return payload


def admin_project_queue_payload(project, *, queue: str = "") -> dict[str, Any]:
    run = _latest_run(project)
    findings = _sort_findings_by_severity(_moderation_findings_for_project(project, base_run=run))
    highest = _highest_finding(findings)
    latest_review = project.admin_review_requests.select_related("requested_by", "reviewed_by").order_by("-created_at", "-id").first()
    latest_block = (
        PublicationBlockEvent.objects.filter(project=project, resolved=False)
        .order_by("-created_at", "-id")
        .first()
    )
    updated_at = (
        getattr(latest_review, "reviewed_at", None)
        or getattr(latest_review, "created_at", None)
        or getattr(latest_block, "created_at", None)
        or project.updated_at
    )
    return {
        "id": f"project-{project.id}",
        "source_type": "project",
        "source": _project_item_source(project, queue=queue, latest_review=latest_review, latest_block=latest_block),
        "allowed_actions": _allowed_actions_for_project_item(project, source_type="project", status=getattr(latest_review, "status", "") or "", queue=queue),
        "queue": queue,
        "project_id": project.id,
        "project_title": project.title,
        "requested_by_id": getattr(latest_review, "requested_by_id", None),
        "requested_by_username": _username(getattr(latest_review, "requested_by", None)),
        "publisher_id": project.user_id,
        "publisher_username": _username(project.user),
        "status": getattr(latest_review, "status", "") or "",
        "moderation_status": project.moderation_status,
        "manual_moderation_status": str(getattr(project, "manual_moderation_status", "") or ""),
        "manual_moderation_reason": str(getattr(project, "manual_moderation_reason", "") or ""),
        "moderation_blocked_until_review": bool(getattr(project, "moderation_blocked_until_review", False)),
        "is_published": bool(getattr(project, "is_published", False)),
        "publisher_message": getattr(latest_review, "publisher_message", "") or "",
        "admin_response": getattr(latest_review, "admin_response", "") or str(getattr(project, "manual_moderation_reason", "") or ""),
        "latest_message": (
            getattr(latest_review, "admin_response", "")
            or getattr(latest_review, "publisher_message", "")
            or str(getattr(project, "manual_moderation_reason", "") or "")
            or str(getattr(latest_block, "message_to_user", "") or "")
        ),
        "reason_category": highest.category if highest else str(getattr(latest_block, "reason_category", "") or ""),
        "reason_label": (highest.category.replace("_", " ").title() if highest else str(getattr(latest_block, "reason_category", "") or "").replace("_", " ").title()),
        "item_time": updated_at,
        "created_at": getattr(latest_review, "created_at", None) or getattr(latest_block, "created_at", None) or project.created_at,
        "requested_at": getattr(latest_review, "created_at", None) or getattr(latest_block, "created_at", None) or project.created_at,
        "reviewed_at": getattr(latest_review, "reviewed_at", None),
        "updated_at": updated_at,
        "reviewed_by_id": getattr(latest_review, "reviewed_by_id", None),
        "reviewed_by_username": _username(getattr(latest_review, "reviewed_by", None)),
        "latest_run_id": run.id if run else project.last_moderation_run_id,
        "latest_final_decision": str(getattr(run, "final_decision", "") or ""),
        "finding_count": len(findings),
        "categories_summary": _count_values(findings, "category"),
        "severities_summary": _count_values(findings, "severity"),
        "latest_findings_summary": [
            finding_summary_payload(finding, project=project, include_admin_fields=True)
            for finding in findings[:5]
        ],
        "highest_severity": highest.severity if highest else str(getattr(latest_block, "highest_severity", "") or ""),
        "highest_category": highest.category if highest else str(getattr(latest_block, "reason_category", "") or ""),
        "primary_reason_title": _finding_reason_title(highest) if highest else "",
        "primary_reason_message": _finding_admin_message(highest) if highest else "",
        "primary_asset_label": _primary_visual_asset_label(highest),
        "visual_issues": _visual_issue_payloads(project, findings, include_admin_fields=True, summary=True),
        "finding_badges": _finding_badges(findings),
        "has_scannable_assets": _project_has_scannable_assets(project),
        "publication_block_message": str(getattr(latest_block, "message_to_user", "") or ""),
    }


def _allowed_actions_for_project_item(project, *, source_type: str, status: str = "", queue: str = "") -> list[str]:
    actions = ["view_review"]
    if project is None:
        return actions
    normalized_status = str(status or "").strip().lower()
    moderation_status = str(getattr(project, "moderation_status", "") or "").strip().lower()
    manual_status = str(getattr(project, "manual_moderation_status", "") or "").strip().lower()
    processing = moderation_status in {"pending", "processing", "running"}
    terminal_or_history = normalized_status in {"approved", "rejected", "closed", "reviewed", "resolved", "dismissed"}
    actions.extend(["approve", "reject_block", "request_changes"])
    if source_type == "report" and normalized_status == "open":
        actions.append("dismiss_report")
    if terminal_or_history or moderation_status == "admin_rejected" or manual_status in {"blocked", "rejected"}:
        actions.append("reopen_unreject")
    if _project_has_scannable_assets(project) and not processing:
        actions.append("rescan")
    if queue == "auto_rejected" or (manual_status == "" and moderation_status in {"revision_required", "needs_admin_review"}):
        actions.append("keep_blocked")
    return list(dict.fromkeys(actions))


def _project_has_scannable_assets(project) -> bool:
    if project is None:
        return False
    if getattr(project, "cover_image_processed", "") or getattr(project, "cover_image_original", ""):
        return True
    try:
        if project.transcript_pages.filter(active=True).exists():
            return True
    except Exception:
        pass
    try:
        if project.slides.exists():
            return True
    except Exception:
        pass
    return True


def _finding_badges(findings: list[AgentFinding]) -> list[str]:
    badges: list[str] = []
    for finding in findings:
        category = str(getattr(finding, "category", "") or "").lower()
        if _is_visual_finding(finding):
            badges.append("visual" if category != "provider_unavailable" else "provider_unavailable")
        if _is_text_finding(finding):
            badges.append("text_ocr")
        if category:
            badges.append(category)
    return list(dict.fromkeys(badges))


def _project_item_source(project, *, queue: str, latest_review, latest_block) -> str:
    if latest_review and getattr(latest_review, "status", "") == "open":
        return "publisher_recheck"
    manual_status = str(getattr(project, "manual_moderation_status", "") or "")
    if manual_status:
        return "admin_action"
    if latest_block:
        return str(getattr(latest_block, "blocked_by", "") or "automatic_scan")
    if queue == "auto_rejected":
        return "automatic_scan"
    return "moderation"


def moderation_audit_event_payload(event: ModerationAuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "action": event.action,
        "reason": event.reason,
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "actor_id": event.actor_id,
        "actor_username": _username(event.actor),
        "created_at": event.created_at,
        "metadata": event.metadata if isinstance(event.metadata, dict) else {},
    }


def finding_summary_payload(
    finding: AgentFinding,
    *,
    project=None,
    include_admin_fields: bool = False,
) -> dict[str, Any]:
    location = finding.location if isinstance(finding.location, dict) else {}
    payload: dict[str, Any] = {
        "id": finding.id,
        "finding_id": finding.id,
        "category": finding.category,
        "severity": finding.severity,
        "decision": finding.decision,
        "user_message": finding.user_message,
        "content_type": finding.content_type,
        "object_type": finding.object_type,
        "object_id": finding.object_id,
        "provider": finding.provider,
        "location_label": location_label(location),
        "ui_anchor": str(location.get("ui_anchor") or ""),
    }
    payload.update(_visual_issue_fields(finding, location, project=project, include_admin_fields=include_admin_fields))
    for key in ("timestamp_seconds", "timestamp_label", "slide_order", "slide_index", "slide_number", "page_key", "transcript_page_id"):
        if key in location and location[key] not in (None, ""):
            payload[key] = location[key]
    return payload


def finding_payload(finding: AgentFinding, *, include_admin_fields: bool = False, project=None) -> dict[str, Any]:
    location = finding.location if isinstance(finding.location, dict) else {}
    payload: dict[str, Any] = {
        "id": finding.id,
        "finding_id": finding.id,
        "category": finding.category,
        "severity": finding.severity,
        "decision": finding.decision,
        "user_message": finding.user_message,
        "location_label": location_label(location),
        "ui_anchor": str(location.get("ui_anchor") or ""),
        "content_type": finding.content_type,
        "object_type": finding.object_type,
        "object_id": finding.object_id,
    }
    payload.update(_visual_issue_fields(finding, location, project=project, include_admin_fields=include_admin_fields))
    for key in ("timestamp_seconds", "timestamp_label", "slide_order", "slide_index", "slide_number", "page_key", "transcript_page_id"):
        if key in location and location[key] not in (None, ""):
            payload[key] = location[key]

    if include_admin_fields:
        payload.update(
            {
                "confidence": finding.confidence,
                "admin_message": finding.admin_message,
                "evidence_excerpt": finding.evidence_excerpt,
                "provider": finding.provider,
                "agent_slug": finding.agent_slug,
                "agent_version": finding.agent_version,
                "location": location,
            }
        )
    return payload


def location_label(location: dict[str, Any]) -> str:
    timestamp_label = str(location.get("timestamp_label") or "")
    if timestamp_label:
        return timestamp_label

    page_key = str(location.get("page_key") or "")
    field_name = str(location.get("field_name") or "").replace("_", " ").strip()
    slide_order = location.get("slide_order")

    if page_key and field_name:
        return f"{page_key} {field_name}"
    if page_key:
        return page_key
    if slide_order not in (None, ""):
        try:
            return f"Slide {int(slide_order) + 1}" + (f" {field_name}" if field_name else "")
        except (TypeError, ValueError):
            return f"Slide {slide_order}" + (f" {field_name}" if field_name else "")
    if field_name:
        return field_name.title()
    return "Project"


VISUAL_ASSET_LABELS = {
    "cover": "Lesson cover",
    "custom_background": "Custom background image",
    "slide_image": "Slide image",
    "draft_visual_asset": "Draft visual asset",
    "video_frame": "Video frame",
    "profile_image": "Profile image",
    "channel_logo": "Channel logo",
    "channel_banner": "Channel banner",
    "unknown": "Lesson visual",
}

VISUAL_ASSET_KIND_ALIASES = {
    "cover": "cover",
    "lesson_cover": "cover",
    "custom_background": "custom_background",
    "background": "custom_background",
    "slide_image": "slide_image",
    "slide": "slide_image",
    "draft_visual_asset": "draft_visual_asset",
    "video_frame": "video_frame",
    "frame": "video_frame",
    "avatar_image": "profile_image",
    "profile_image": "profile_image",
    "profile_avatar": "profile_image",
    "profile_logo": "channel_logo",
    "channel_logo": "channel_logo",
    "profile_banner": "channel_banner",
    "channel_banner": "channel_banner",
}

VISUAL_CONTENT_TYPES = frozenset({"image", "video_frame"})
VISUAL_CATEGORIES = frozenset(
    {
        "sexual",
        "violence",
        "graphic_content",
        "self_harm",
        "provider_unavailable",
    }
)
VISUAL_BLOCK_DECISIONS = frozenset({"block", "needs_admin_review", "revision_required", "rejected"})
TEXT_CONTENT_TYPES = frozenset({"text", "ocr", "transcript", "subtitle", "language"})
TEXT_CATEGORIES = frozenset(
    {
        "abusive_language",
        "copyright_text",
        "dangerous_instruction",
        "hate_or_harassment",
        "inappropriate_language",
        "language",
        "profanity",
        "self_harm_instruction",
        "sexual_text",
        "text_moderation",
        "violence_text",
    }
)
STORAGE_RELATIVE_ROOTS = frozenset({"uploads", "profiles", "avatars", "moderation"})

PROVIDER_UNAVAILABLE_TITLE = "Visual safety scan unavailable"
PROVIDER_UNAVAILABLE_ADMIN_MESSAGE = (
    "The semantic visual safety provider did not return a completed result. "
    "This visual cannot be automatically approved and requires manual admin review before publishing."
)
PROVIDER_UNAVAILABLE_PUBLISHER_MESSAGE = (
    "We could not complete the visual safety scan for one or more images/video frames. "
    "An admin must review it before the lesson can be published."
)


def _effective_moderation_status(
    project,
    findings: list[AgentFinding],
    *,
    publish_block: dict[str, Any],
) -> str:
    raw_status = str(getattr(project, "moderation_status", "") or "")
    if raw_status not in APPROVED_MODERATION_STATUSES:
        return raw_status

    reason = str((publish_block or {}).get("reason") or "")
    if not _publish_blocked_by_moderation(False, publish_block):
        return raw_status

    manual_status = str(getattr(project, "manual_moderation_status", "") or "").strip().lower()
    if reason == "manual_moderation_block":
        if manual_status == "request_changes":
            return "revision_required"
        if manual_status in {"blocked", "rejected"}:
            return "admin_rejected"
        return "needs_admin_review"

    if reason in {"visual_moderation_rejected", "video_frame_audit_rejected"}:
        visual_findings = [finding for finding in findings if _is_visual_finding(finding)]
        if any(str(getattr(finding, "decision", "") or "").strip().lower() == "block" for finding in visual_findings):
            return "revision_required"
        return "needs_admin_review"

    if reason in {"publication_block", "moderation_rejected", "moderation_required", "moderation_processing"}:
        return "needs_admin_review"
    return raw_status


def _effective_moderation_message(
    project,
    findings: list[AgentFinding],
    *,
    summary: dict[str, Any],
    effective_status: str,
    can_publish: bool,
    publish_block: dict[str, Any],
) -> str:
    raw_status = str(getattr(project, "moderation_status", "") or "")
    if not can_publish and raw_status in APPROVED_MODERATION_STATUSES and effective_status != raw_status:
        visual_finding = next((finding for finding in findings if _is_visual_finding(finding)), None)
        if visual_finding is not None:
            return _finding_publisher_message(visual_finding)
        detail = str((publish_block or {}).get("detail") or "").strip()
        if detail:
            return detail
        return _default_message(effective_status)

    message = summary.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return _default_message(effective_status)


def _publish_blocked_by_moderation(can_publish: bool, publish_block: dict[str, Any] | None) -> bool:
    if can_publish or not publish_block:
        return False
    reason = str(publish_block.get("reason") or "").strip()
    return bool(reason and reason not in {"render_not_ready", "draft_render_required"})


def _visual_issue_fields(
    finding: AgentFinding,
    location: dict[str, Any],
    *,
    project=None,
    include_admin_fields: bool = False,
) -> dict[str, Any]:
    is_visual = _is_visual_finding(finding)
    issue_type = _finding_issue_type(finding, location)
    source_kind = _finding_source_kind(finding, location)
    source_label = _finding_source_label(source_kind, location, finding)
    canonical_decision = _finding_decision(finding)
    moderation_state = _finding_moderation_state(finding)
    payload: dict[str, Any] = {
        "issue_id": getattr(finding, "id", None),
        "issue_type": issue_type,
        "moderation_state": moderation_state,
        "decision": canonical_decision,
        "source_kind": source_kind,
        "source_label": source_label,
        "reason_title": _finding_reason_title(finding),
        "reason_message": _finding_publisher_message(finding),
        "publisher_reason_message": _finding_publisher_message(finding),
        "admin_reason_message": _finding_admin_message(finding),
        "technical_reason": _finding_technical_reason(finding),
    }
    if is_visual:
        asset_kind = _finding_asset_kind(location, finding)
        asset_label = _finding_asset_label(location, finding)
        preview_url = _finding_preview_url(finding, project, location, asset_kind=asset_kind)
        timestamp_seconds = _safe_float(location.get("timestamp_seconds"))
        slide_index = _finding_slide_index(location)
        transcript_page_id = _safe_int(location.get("transcript_page_id"))
        payload["asset_kind"] = asset_kind
        payload["asset_label"] = asset_label
        payload["finding_id"] = getattr(finding, "id", None)
        payload["id"] = getattr(finding, "id", None)
        if preview_url:
            payload["preview_url"] = preview_url
            payload["asset_url"] = preview_url
        else:
            payload["preview_unavailable_reason"] = _preview_unavailable_reason(location, asset_kind=asset_kind)
        if slide_index is not None:
            payload["slide_index"] = slide_index
            payload["slide_number"] = slide_index + 1
        if transcript_page_id is not None:
            payload["transcript_page_id"] = transcript_page_id
        if timestamp_seconds is not None:
            payload["timestamp_seconds"] = timestamp_seconds
    slide_id = _finding_slide_id(location, finding)
    if slide_id not in (None, ""):
        payload["slide_id"] = slide_id
    if source_kind == "transcript_text":
        transcript_segment_id = _safe_int(location.get("transcript_segment_id")) or _safe_int(location.get("transcript_page_id"))
        if transcript_segment_id is not None:
            payload["transcript_segment_id"] = transcript_segment_id
        text_start = _safe_int(location.get("start_char"))
        text_end = _safe_int(location.get("end_char"))
        if text_start is not None:
            payload["text_start"] = text_start
        if text_end is not None:
            payload["text_end"] = text_end
        if text_start is not None or text_end is not None:
            payload["text_range"] = {"start": text_start, "end": text_end}
    source_asset_id = _finding_source_asset_id(location, finding)
    if source_asset_id not in (None, ""):
        payload["source_asset_id"] = source_asset_id
    project_id = location.get("project_id") or getattr(project, "id", None)
    if project_id not in (None, ""):
        payload["project_id"] = project_id
    user_id = _location_user_id(location)
    if user_id is not None:
        payload["owner_id"] = user_id
        payload["user_id"] = user_id
        payload["profile_id"] = user_id
    return payload


def _is_visual_finding(finding: AgentFinding | None) -> bool:
    if finding is None:
        return False
    if _is_text_finding(finding):
        return False
    location = finding.location if isinstance(finding.location, dict) else {}
    explicit_asset = _explicit_visual_asset_kind(location, finding)
    if explicit_asset != "unknown":
        return True
    content_type = str(getattr(finding, "content_type", "") or "").strip().lower()
    if content_type in VISUAL_CONTENT_TYPES:
        return True
    category = str(getattr(finding, "category", "") or "").strip().lower()
    provider = str(getattr(finding, "provider", "") or "").strip().lower()
    return category in VISUAL_CATEGORIES or "visual" in provider


def _is_text_finding(finding: AgentFinding | None) -> bool:
    if finding is None:
        return False
    content_type = str(getattr(finding, "content_type", "") or "").strip().lower()
    if content_type in TEXT_CONTENT_TYPES:
        return True
    category = str(getattr(finding, "category", "") or "").strip().lower()
    if category in TEXT_CATEGORIES:
        return True
    provider = str(getattr(finding, "provider", "") or "").strip().lower()
    object_type = str(getattr(finding, "object_type", "") or "").strip().lower()
    location = finding.location if isinstance(finding.location, dict) else {}
    asset_type = str(location.get("asset_type") or "").strip().lower()
    return bool("ocr" in provider or "text" in provider or "ocr" in object_type or asset_type == "ocr_text")


def _explicit_visual_asset_kind(location: dict[str, Any], finding: AgentFinding | None = None) -> str:
    raw = str(location.get("asset_type") or getattr(finding, "object_type", "") or "").strip().lower()
    return VISUAL_ASSET_KIND_ALIASES.get(raw, "unknown")


def _finding_asset_kind(location: dict[str, Any], finding: AgentFinding | None = None) -> str:
    explicit = _explicit_visual_asset_kind(location, finding)
    if explicit != "unknown":
        return explicit
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            location.get("ui_anchor"),
            location.get("field_name"),
            location.get("page_key"),
            getattr(finding, "object_id", "") if finding is not None else "",
            getattr(finding, "content_type", "") if finding is not None else "",
        )
    )
    if "cover" in haystack:
        return "cover"
    if "custom_background" in haystack or "background" in haystack:
        return "custom_background"
    if "avatar" in haystack or "profile" in haystack:
        return "profile_image"
    if "logo" in haystack:
        return "channel_logo"
    if "banner" in haystack:
        return "channel_banner"
    if "video" in haystack or "frame" in haystack:
        return "video_frame"
    if (
        "slide" in haystack
        or location.get("slide_order") not in (None, "")
        or location.get("slide_index") not in (None, "")
        or location.get("slide_number") not in (None, "")
    ):
        return "slide_image"
    return "unknown"


def _finding_asset_label(location: dict[str, Any], finding: AgentFinding | None = None) -> str:
    asset_kind = _finding_asset_kind(location, finding)
    if asset_kind == "slide_image":
        slide_index = _finding_slide_index(location)
        if slide_index is not None:
            return f"Slide {slide_index + 1} image"
    if asset_kind == "video_frame":
        timestamp = str(location.get("timestamp_label") or "").strip()
        if not timestamp:
            seconds = _safe_float(location.get("timestamp_seconds"))
            timestamp = _format_timestamp(seconds) if seconds is not None else ""
        return f"Video frame at {timestamp}" if timestamp else "Video frame"
    return VISUAL_ASSET_LABELS.get(asset_kind, "Lesson visual")


def _primary_visual_asset_label(finding: AgentFinding | None) -> str:
    if not _is_visual_finding(finding):
        return ""
    location = finding.location if isinstance(finding.location, dict) else {}
    return _finding_asset_label(location, finding)


def _finding_reason_title(finding: AgentFinding | None) -> str:
    if finding is None:
        return ""
    category = str(getattr(finding, "category", "") or "").strip().lower()
    decision = _finding_decision(finding)
    if category == "provider_unavailable":
        return PROVIDER_UNAVAILABLE_TITLE
    if _is_visual_finding(finding) and decision in {"block", "blocked", "rejected", "revision_required"}:
        return "Unsafe visual detected"
    if _is_visual_finding(finding) and decision == "needs_admin_review":
        return "Visual needs admin review"
    return str(getattr(finding, "category", "") or "Moderation finding").replace("_", " ").title()


def _finding_admin_message(finding: AgentFinding | None) -> str:
    if finding is None:
        return ""
    if str(getattr(finding, "category", "") or "").strip().lower() == "provider_unavailable":
        return PROVIDER_UNAVAILABLE_ADMIN_MESSAGE
    message = str(getattr(finding, "admin_message", "") or "").strip()
    if message:
        return message
    if _is_visual_finding(finding):
        return "Review this visual manually. Approve only if it is safe."
    return str(getattr(finding, "user_message", "") or "").strip() or "This content needs staff attention."


def _finding_publisher_message(finding: AgentFinding | None) -> str:
    if finding is None:
        return ""
    if str(getattr(finding, "category", "") or "").strip().lower() == "provider_unavailable":
        return PROVIDER_UNAVAILABLE_PUBLISHER_MESSAGE
    message = str(getattr(finding, "user_message", "") or "").strip()
    if message:
        return message
    if _is_visual_finding(finding):
        return "This visual needs admin review before the lesson can be published."
    return "This content needs moderation attention."


def _finding_technical_reason(finding: AgentFinding | None) -> str:
    if finding is None:
        return ""
    evidence = str(getattr(finding, "evidence_excerpt", "") or "").strip()
    if evidence:
        return evidence
    return str(getattr(finding, "category", "") or "").strip()


def _finding_decision(finding: AgentFinding | None) -> str:
    decision = str(getattr(finding, "decision", "") or "").strip().lower()
    if decision in {"pending", "pending_scan", "processing", "running"}:
        return "pending"
    if decision in {"block", "blocked", "revision_required"}:
        return "block"
    if decision in {"reject", "rejected", "admin_rejected"}:
        return "reject"
    if decision == "allow":
        return "allow"
    if decision in {"approve", "approved", "admin_approved"}:
        return "approve"
    if decision == "needs_admin_review":
        return decision
    return "needs_admin_review"


def _finding_moderation_state(finding: AgentFinding | None) -> str:
    decision = _finding_decision(finding)
    if decision == "pending":
        return "pending_scan"
    if decision == "allow":
        return "scan_passed"
    if decision in {"approve", "approved"}:
        return "approved"
    if decision == "block":
        return "blocked"
    if decision == "reject":
        return "rejected"
    if decision == "needs_admin_review":
        return "needs_admin_review"
    return "needs_admin_review"


def _finding_issue_type(finding: AgentFinding | None, location: dict[str, Any]) -> str:
    source_kind = _finding_source_kind(finding, location)
    if source_kind in {"profile_image", "channel_logo", "channel_banner"}:
        return "profile_asset"
    if source_kind == "video_frame":
        return "video"
    if _is_text_finding(finding):
        return "text"
    if _is_visual_finding(finding):
        return "visual"
    return "unknown"


def _finding_source_kind(finding: AgentFinding | None, location: dict[str, Any]) -> str:
    if _is_text_finding(finding):
        return "transcript_text"
    asset_kind = _finding_asset_kind(location, finding)
    return {
        "cover": "lesson_cover",
        "custom_background": "scene_background",
        "slide_image": "slide_image",
        "draft_visual_asset": "slide_image",
        "video_frame": "video_frame",
        "profile_image": "profile_image",
        "channel_logo": "channel_logo",
        "channel_banner": "channel_banner",
    }.get(asset_kind, "unknown")


def _finding_source_label(source_kind: str, location: dict[str, Any], finding: AgentFinding | None) -> str:
    if source_kind == "lesson_cover":
        return "Lesson cover"
    if source_kind == "scene_background":
        return "Scene background"
    if source_kind == "slide_image":
        slide_index = _finding_slide_index(location)
        if slide_index is not None:
            return f"Slide {slide_index + 1} image"
        return "Slide image"
    if source_kind == "transcript_text":
        slide_index = _finding_slide_index(location)
        if slide_index is not None:
            return f"Slide {slide_index + 1} transcript text"
        return "Transcript text"
    if source_kind == "video_frame":
        return _finding_asset_label(location, finding)
    if source_kind == "profile_image":
        return "Profile image"
    if source_kind == "channel_logo":
        return "Channel logo"
    if source_kind == "channel_banner":
        return "Channel banner"
    return "Unknown source"


def _finding_source_asset_id(location: dict[str, Any], finding: AgentFinding | None) -> str | None:
    for key in ("source_asset_id", "asset_id", "visual_asset_id", "profile_asset_id"):
        value = location.get(key)
        if value not in (None, ""):
            return str(value)
    source_kind = _finding_source_kind(finding, location)
    object_id = str(getattr(finding, "object_id", "") or "").strip()
    if source_kind in {"profile_image", "channel_logo", "channel_banner"} and object_id:
        return object_id
    return None


def _finding_slide_id(location: dict[str, Any], finding: AgentFinding | None) -> int | str | None:
    for key in ("slide_id", "transcript_page_id"):
        value = _safe_int(location.get(key))
        if value is not None:
            return value
    source_kind = _finding_source_kind(finding, location)
    object_id = str(getattr(finding, "object_id", "") or "").strip()
    if source_kind == "slide_image" and object_id and object_id.isdigit():
        return int(object_id)
    return None


def _finding_slide_index(location: dict[str, Any]) -> int | None:
    for key in ("slide_order", "slide_index"):
        value = _safe_int(location.get(key))
        if value is not None:
            return value
    slide_number = _safe_int(location.get("slide_number"))
    if slide_number is not None and slide_number > 0:
        return slide_number - 1
    return None


def _finding_preview_url(
    finding: AgentFinding,
    project,
    location: dict[str, Any],
    *,
    asset_kind: str,
) -> str:
    project_id = location.get("project_id") or getattr(project, "id", None)
    finding_id = getattr(finding, "id", None)
    if not project_id or not finding_id:
        return ""
    if asset_kind == "cover":
        if str(location.get("image_path") or "").strip() or getattr(project, "cover_image_processed", "") or getattr(project, "cover_image_original", ""):
            return f"/api/v1/projects/{project_id}/moderation-preview/{finding_id}/"
        return ""
    if asset_kind == "custom_background":
        if str(location.get("image_path") or location.get("frame_path") or "").strip():
            return f"/api/v1/projects/{project_id}/moderation-preview/{finding_id}/"
        page = _finding_preview_page(project, location)
        scene = getattr(page, "editor_document", {}) if page is not None else {}
        if isinstance(scene, dict):
            scene = scene.get("scene") if isinstance(scene.get("scene"), dict) else {}
        if isinstance(scene, dict) and str(scene.get("custom_background_path") or "").strip():
            return f"/api/v1/projects/{project_id}/moderation-preview/{finding_id}/"
        return ""
    if str(location.get("image_path") or location.get("frame_path") or "").strip():
        return f"/api/v1/projects/{project_id}/moderation-preview/{finding_id}/"
    return ""


def _finding_preview_page(project, location: dict[str, Any]):
    if project is None:
        return None
    transcript_page_id = _safe_int(location.get("transcript_page_id"))
    if transcript_page_id is not None:
        page = project.transcript_pages.filter(pk=transcript_page_id, is_active=True).first()
        if page is not None:
            return page
    page_key = str(location.get("page_key") or "").strip()
    if page_key:
        page = project.transcript_pages.filter(page_key=page_key, is_active=True).first()
        if page is not None:
            return page
    slide_index = _finding_slide_index(location)
    if slide_index is not None:
        return (
            project.transcript_pages
            .filter(is_active=True)
            .filter(Q(source_slide_index=slide_index) | Q(order=slide_index))
            .order_by("order", "id")
            .first()
        )
    return None


def _preview_unavailable_reason(location: dict[str, Any], *, asset_kind: str) -> str:
    if asset_kind == "cover":
        return "cover_image_unavailable"
    if asset_kind == "custom_background":
        return "background_image_unavailable"
    if asset_kind in {"slide_image", "draft_visual_asset"}:
        return "slide_image_unavailable"
    if asset_kind == "video_frame":
        return "video_frame_unavailable"
    return "preview_unavailable"


def _path_has_parent_reference(normalized_path: str) -> bool:
    return any(part == ".." for part in normalized_path.split("/"))


def _looks_like_windows_absolute_path(normalized_path: str) -> bool:
    return (
        len(normalized_path) >= 3
        and normalized_path[0].isalpha()
        and normalized_path[1] == ":"
        and normalized_path[2] == "/"
    ) or normalized_path.startswith("//")


def _known_storage_relative_suffix(normalized_path: str) -> str:
    parts = [part for part in normalized_path.split("/") if part and part != "."]
    for index, part in enumerate(parts):
        root = part.lower()
        if root in STORAGE_RELATIVE_ROOTS:
            suffix_parts = [root, *parts[index + 1 :]]
            suffix = "/".join(suffix_parts)
            if suffix and not _path_has_parent_reference(suffix):
                return suffix
    return ""


def _safe_storage_relative_path(raw_path: str) -> str:
    raw = str(raw_path or "").strip()
    if not raw:
        return ""
    slash_path = raw.replace("\\", "/")
    normalized = slash_path.lstrip("/")
    if not normalized or _path_has_parent_reference(normalized):
        return ""
    storage_root = Path(str(getattr(settings, "STORAGE_ROOT", "storage_local") or "storage_local")).resolve()
    root_name = storage_root.name
    normalized_lower = normalized.lower()
    root_prefix = f"{root_name}/".lower()
    if normalized_lower == root_name.lower():
        return ""
    if normalized_lower.startswith(root_prefix):
        normalized = normalized[len(root_name) + 1 :]
        return _known_storage_relative_suffix(normalized) or normalized

    absolute_hint = slash_path.startswith("/") or _looks_like_windows_absolute_path(slash_path)
    candidate = Path(raw)
    try:
        if candidate.is_absolute():
            resolved = candidate.resolve()
            return str(resolved.relative_to(storage_root)).replace("\\", "/")
    except Exception:
        if absolute_hint:
            return _known_storage_relative_suffix(normalized)
        return normalized
    if absolute_hint:
        return _known_storage_relative_suffix(normalized)
    return normalized


def _location_user_id(location: dict[str, Any]) -> int | None:
    for key in ("owner_id", "user_id", "profile_id"):
        value = _safe_int(location.get(key))
        if value is not None:
            return value
    ui_anchor = str(location.get("ui_anchor") or "")
    if ui_anchor.startswith("user-"):
        parts = ui_anchor.split("-", 2)
        if len(parts) >= 2:
            return _safe_int(parts[1])
    return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minute:02d}:{second:02d}"
    return f"{minute:02d}:{second:02d}"


def _latest_run(project) -> AgentRun | None:
    if project.last_moderation_run_id:
        run = AgentRun.objects.filter(pk=project.last_moderation_run_id, project=project).first()
        if run is not None:
            return run
    return AgentRun.objects.filter(project=project, purpose="moderation").order_by("-created_at", "-id").first()


def _findings_for_run(run: AgentRun | None):
    if run is None:
        return AgentFinding.objects.none()
    return run.findings.all().order_by("-created_at", "-id")


def _findings_for_review(review: AdminReviewRequest):
    run = review.run or _latest_run(review.project)
    if run is None:
        return AgentFinding.objects.none()
    if str(getattr(review, "status", "") or "") == "open":
        return _moderation_findings_for_project(review.project, base_run=run)
    return run.findings.all().order_by("-created_at", "-id")


def _moderation_findings_for_project(project, *, base_run: AgentRun | None = None) -> list[AgentFinding]:
    if project is None:
        return []
    runs: list[AgentRun] = []
    if base_run is not None:
        runs.append(base_run)
    for phase in (_visual_asset_phase(), _video_frame_audit_phase()):
        run = _latest_completed_run_for_phase(project, phase)
        if run is not None:
            runs.append(run)

    findings: list[AgentFinding] = []
    seen_runs: set[int] = set()
    for run in runs:
        run_id = int(getattr(run, "id", 0) or 0)
        if not run_id or run_id in seen_runs:
            continue
        seen_runs.add(run_id)
        run_findings = list(_findings_for_run(run))
        if run is base_run:
            findings.extend(run_findings)
            continue
        findings.extend(finding for finding in run_findings if _is_unresolved_finding(finding, project=project))
    return [
        finding
        for finding in _dedupe_findings(findings)
        if _finding_matches_current_project_asset(project, finding)
    ]


def _latest_completed_run_for_phase(project, phase: str) -> AgentRun | None:
    if project is None or not phase:
        return None
    return (
        AgentRun.objects.filter(
            project=project,
            phase=phase,
            status__in=["done", "completed"],
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _visual_asset_phase() -> str:
    return str(getattr(settings, "VISUAL_MODERATION_PHASE", "visual_asset_scan") or "visual_asset_scan").strip() or "visual_asset_scan"


def _video_frame_audit_phase() -> str:
    return str(getattr(settings, "VIDEO_FRAME_AUDIT_PHASE", "video_frame_audit") or "video_frame_audit").strip() or "video_frame_audit"


def _dedupe_findings(findings: list[AgentFinding]) -> list[AgentFinding]:
    deduped: list[AgentFinding] = []
    seen: set[int] = set()
    for finding in findings:
        finding_id = int(getattr(finding, "id", 0) or 0)
        if finding_id and finding_id in seen:
            continue
        if finding_id:
            seen.add(finding_id)
        deduped.append(finding)
    return deduped


def _finding_matches_current_project_asset(project, finding: AgentFinding | None) -> bool:
    if project is None or finding is None or not _is_visual_finding(finding):
        return True
    location = finding.location if isinstance(finding.location, dict) else {}
    source_kind = _finding_source_kind(finding, location)
    if source_kind not in {"lesson_cover", "scene_background"}:
        return True
    finding_path = _safe_storage_relative_path(
        str(location.get("image_path") or location.get("asset_path") or location.get("frame_path") or "")
    )
    if not finding_path:
        return True
    current_paths = _current_project_asset_paths(project, source_kind=source_kind, location=location)
    if not current_paths:
        return True
    return finding_path in current_paths


def _current_project_asset_paths(project, *, source_kind: str, location: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    if source_kind == "lesson_cover":
        for raw_path in (
            getattr(project, "cover_image_processed", ""),
            getattr(project, "cover_image_original", ""),
        ):
            _add_current_asset_path(paths, raw_path)
        draft_data = getattr(project, "draft_data", None)
        if isinstance(draft_data, dict):
            project_fields = draft_data.get("project")
            if isinstance(project_fields, dict):
                for raw_path in (
                    project_fields.get("cover_image_processed"),
                    project_fields.get("cover_image_original"),
                ):
                    _add_current_asset_path(paths, raw_path)
        return paths

    if source_kind == "scene_background":
        for scene in _current_scene_dicts_for_location(project, location):
            _add_current_asset_path(paths, scene.get("custom_background_path"))
        return paths

    return paths


def _add_current_asset_path(paths: set[str], raw_path: Any) -> None:
    normalized = _safe_storage_relative_path(str(raw_path or ""))
    if normalized:
        paths.add(normalized)


def _current_scene_dicts_for_location(project, location: dict[str, Any]) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []
    pages = list(_project_pages_for_location(project, location))
    for page in pages:
        scene = _scene_dict_from_page_payload(getattr(page, "editor_document", None))
        if scene:
            scenes.append(scene)

    draft_data = getattr(project, "draft_data", None)
    draft_pages = draft_data.get("transcript_pages") if isinstance(draft_data, dict) else None
    if isinstance(draft_pages, list):
        for draft_page in draft_pages:
            if not isinstance(draft_page, dict) or not _draft_page_matches_location(draft_page, location):
                continue
            scene = _scene_dict_from_page_payload(draft_page.get("editor_document"))
            if scene:
                scenes.append(scene)
    return scenes


def _project_pages_for_location(project, location: dict[str, Any]):
    transcript_rel = getattr(project, "transcript_pages", None)
    if transcript_rel is None:
        return []
    queryset = transcript_rel.filter(is_active=True)
    transcript_page_id = _safe_int(location.get("transcript_page_id") or location.get("slide_id"))
    if transcript_page_id is not None:
        return queryset.filter(id=transcript_page_id)
    page_key = str(location.get("page_key") or "").strip()
    if page_key:
        return queryset.filter(page_key=page_key)
    slide_order = _finding_slide_index(location)
    if slide_order is not None:
        return queryset.filter(order=slide_order)
    return []


def _draft_page_matches_location(draft_page: dict[str, Any], location: dict[str, Any]) -> bool:
    transcript_page_id = _safe_int(location.get("transcript_page_id") or location.get("slide_id"))
    if transcript_page_id is not None and _safe_int(draft_page.get("id")) == transcript_page_id:
        return True
    page_key = str(location.get("page_key") or "").strip()
    if page_key and page_key == str(draft_page.get("page_key") or "").strip():
        return True
    slide_order = _finding_slide_index(location)
    if slide_order is not None and _safe_int(draft_page.get("order")) == slide_order:
        return True
    return transcript_page_id is None and not page_key and slide_order is None


def _scene_dict_from_page_payload(editor_document: Any) -> dict[str, Any]:
    if not isinstance(editor_document, dict):
        return {}
    scene = editor_document.get("scene")
    return scene if isinstance(scene, dict) else {}


def _visual_issue_payloads(
    project,
    findings: list[AgentFinding],
    *,
    include_admin_fields: bool = False,
    summary: bool = False,
) -> list[dict[str, Any]]:
    if _project_manual_approval_resolves_issues(project):
        return []
    payload_fn = finding_summary_payload if summary else finding_payload
    return [
        payload_fn(finding, include_admin_fields=include_admin_fields, project=project)
        for finding in findings
        if (
            _is_visual_finding(finding)
            and _is_unresolved_finding(finding, project=project)
            and _finding_matches_current_project_asset(project, finding)
        )
    ]


def _pending_visual_issue_payloads(project, summary: dict[str, Any], findings: list[AgentFinding]) -> list[dict[str, Any]]:
    if _project_manual_approval_resolves_issues(project):
        return []
    scan = summary.get("visual_asset_scan") if isinstance(summary.get("visual_asset_scan"), dict) else {}
    if not isinstance(scan, dict) or not scan:
        return []
    status = str(scan.get("status") or "").strip().lower()
    stale = bool(scan.get("stale") or scan.get("needs_rescan"))
    if status not in {"pending", "pending_scan", "processing", "running", "needs_rescan"} and not stale:
        return []
    source_kind = _source_kind_from_asset_type(str(scan.get("asset_type") or "unknown"))
    if source_kind == "unknown":
        return []
    scan_asset_path = _safe_storage_relative_path(str(scan.get("asset_path") or scan.get("image_path") or ""))
    if scan_asset_path:
        current_paths = _current_project_asset_paths(project, source_kind=source_kind, location=scan)
        if current_paths and scan_asset_path not in current_paths:
            return []
    if any(
        _is_unresolved_finding(finding, project=project)
        and _is_visual_finding(finding)
        and _finding_source_kind(finding, finding.location if isinstance(finding.location, dict) else {}) == source_kind
        for finding in findings
    ):
        return []
    project_id = getattr(project, "id", None)
    source_label = _source_label_from_kind(source_kind, slide_index=_safe_int(scan.get("slide_order")))
    payload: dict[str, Any] = {
        "id": f"pending-{project_id}-{source_kind}",
        "issue_id": f"pending-{project_id}-{source_kind}",
        "issue_type": "video" if source_kind == "video_frame" else "visual",
        "moderation_state": "pending_scan",
        "decision": "pending",
        "source_kind": source_kind,
        "source_label": source_label,
        "asset_kind": _asset_kind_from_source_kind(source_kind),
        "asset_label": source_label,
        "project_id": project_id,
        "reason_title": "Visual scan pending",
        "reason_message": "Visual scan pending.",
        "publisher_reason_message": "Visual scan pending.",
        "admin_reason_message": "Visual scan pending.",
        "technical_reason": str(scan.get("reason") or status or "pending_scan"),
    }
    slide_index = _safe_int(scan.get("slide_order"))
    if slide_index is not None and source_kind in {"scene_background", "slide_image"}:
        payload["slide_index"] = slide_index
        payload["slide_number"] = slide_index + 1
    transcript_page_id = _safe_int(scan.get("transcript_page_id"))
    if transcript_page_id is not None:
        payload["slide_id"] = transcript_page_id
        payload["transcript_page_id"] = transcript_page_id
    page_key = str(scan.get("page_key") or "").strip()
    if page_key:
        payload["page_key"] = page_key
    return [payload]


def _source_kind_from_asset_type(asset_type: str) -> str:
    normalized = VISUAL_ASSET_KIND_ALIASES.get(str(asset_type or "").strip().lower(), "unknown")
    return {
        "cover": "lesson_cover",
        "custom_background": "scene_background",
        "slide_image": "slide_image",
        "draft_visual_asset": "slide_image",
        "video_frame": "video_frame",
        "profile_image": "profile_image",
        "channel_logo": "channel_logo",
        "channel_banner": "channel_banner",
    }.get(normalized, "unknown")


def _asset_kind_from_source_kind(source_kind: str) -> str:
    return {
        "lesson_cover": "cover",
        "scene_background": "custom_background",
        "slide_image": "slide_image",
        "video_frame": "video_frame",
        "profile_image": "profile_image",
        "channel_logo": "channel_logo",
        "channel_banner": "channel_banner",
    }.get(str(source_kind or ""), "unknown")


def _source_label_from_kind(source_kind: str, *, slide_index: int | None = None) -> str:
    if source_kind == "lesson_cover":
        return "Lesson cover"
    if source_kind == "scene_background":
        return "Scene background"
    if source_kind == "slide_image":
        return f"Slide {slide_index + 1} image" if slide_index is not None else "Slide image"
    if source_kind == "video_frame":
        return "Video frame"
    if source_kind == "profile_image":
        return "Profile image"
    if source_kind == "channel_logo":
        return "Channel logo"
    if source_kind == "channel_banner":
        return "Channel banner"
    return "Unknown source"


def _is_unresolved_finding(finding: AgentFinding | None, *, project=None) -> bool:
    if finding is None:
        return False
    if _manual_approval_resolves_finding(project, finding):
        return False
    return _finding_moderation_state(finding) not in {"scan_passed", "approved"}


def _manual_approval_resolves_finding(project, finding: AgentFinding | None) -> bool:
    if project is None or finding is None:
        return False
    moderation_status = str(getattr(project, "moderation_status", "") or "").strip().lower()
    manual_status = str(getattr(project, "manual_moderation_status", "") or "").strip().lower()
    if moderation_status != "admin_approved" and manual_status != "approved":
        return False
    if _project_manual_approval_resolves_issues(project):
        return True

    changed_sources = _visual_sources_changed_after_manual_approval(project)
    if not changed_sources:
        return False

    location = finding.location if isinstance(finding.location, dict) else {}
    source_kind = _finding_source_kind(finding, location)
    matching_sources = [source for source in changed_sources if source.get("source_kind") == source_kind]
    if not matching_sources:
        return True

    finding_path = _safe_storage_relative_path(
        str(location.get("image_path") or location.get("asset_path") or location.get("frame_path") or "")
    )
    if not finding_path:
        return False
    return not any(
        not source.get("asset_path") or source.get("asset_path") == finding_path
        for source in matching_sources
    )


def _project_manual_approval_resolves_issues(project) -> bool:
    if project is None:
        return False
    moderation_status = str(getattr(project, "moderation_status", "") or "").strip().lower()
    manual_status = str(getattr(project, "manual_moderation_status", "") or "").strip().lower()
    if moderation_status != "admin_approved" and manual_status != "approved":
        return False
    return not _project_has_change_after_manual_approval(project)


def _project_has_change_after_manual_approval(project) -> bool:
    manual_at = _coerce_datetime(getattr(project, "manual_moderation_at", None))
    candidate_times: list[datetime] = []

    latest_change = _coerce_datetime(getattr(project, "latest_publisher_change_at", None))
    if latest_change is not None:
        candidate_times.append(latest_change)

    summary = getattr(project, "moderation_summary", None)
    if isinstance(summary, dict):
        visual_scan = summary.get("visual_asset_scan")
        if isinstance(visual_scan, dict) and (
            visual_scan.get("stale")
            or visual_scan.get("needs_rescan")
            or str(visual_scan.get("status") or "").strip().lower() in {"pending", "pending_scan", "processing", "running", "needs_rescan"}
        ):
            changed_at = _coerce_datetime(visual_scan.get("changed_at"))
            if changed_at is not None:
                candidate_times.append(changed_at)

    draft_data = getattr(project, "draft_data", None)
    metadata = draft_data.get("metadata") if isinstance(draft_data, dict) else None
    if isinstance(metadata, dict) and metadata.get("dirty"):
        for key in ("moderation_failed_at", "updated_at", "created_at"):
            changed_at = _coerce_datetime(metadata.get(key))
            if changed_at is not None:
                candidate_times.append(changed_at)

    if not candidate_times:
        return False
    if manual_at is None:
        return True
    return any(changed_at > manual_at for changed_at in candidate_times)


def _visual_sources_changed_after_manual_approval(project) -> list[dict[str, str]]:
    manual_at = _coerce_datetime(getattr(project, "manual_moderation_at", None))
    summary = getattr(project, "moderation_summary", None)
    scan = summary.get("visual_asset_scan") if isinstance(summary, dict) else None
    if not isinstance(scan, dict):
        return []
    if not (
        scan.get("stale")
        or scan.get("needs_rescan")
        or str(scan.get("status") or "").strip().lower() in {"pending", "pending_scan", "processing", "running", "needs_rescan"}
    ):
        return []
    changed_at = _coerce_datetime(scan.get("changed_at"))
    if changed_at is None or (manual_at is not None and changed_at <= manual_at):
        return []
    source_kind = _source_kind_from_asset_type(str(scan.get("asset_type") or "unknown"))
    if source_kind == "unknown":
        return []
    return [
        {
            "source_kind": source_kind,
            "asset_path": _safe_storage_relative_path(str(scan.get("asset_path") or scan.get("image_path") or "")),
        }
    ]


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, str):
        candidate = parse_datetime(value)
        if candidate is None:
            return None
    else:
        return None
    if timezone.is_naive(candidate):
        return candidate.replace(tzinfo=datetime_timezone.utc)
    return candidate


def _highest_finding(findings: list[AgentFinding]) -> AgentFinding | None:
    if not findings:
        return None
    return max(findings, key=lambda finding: SEVERITY_RANK.get(str(finding.severity or "").lower(), 0))


def _sort_findings_by_severity(findings: list[AgentFinding]) -> list[AgentFinding]:
    return sorted(
        findings,
        key=lambda finding: (
            SEVERITY_RANK.get(str(finding.severity or "").lower(), 0),
            finding.created_at,
            finding.id,
        ),
        reverse=True,
    )


def _count_values(findings: list[AgentFinding], attr_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        key = str(getattr(finding, attr_name, "") or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _first_timestamp_seconds(findings: list[AgentFinding]) -> float | None:
    for finding in findings:
        location = finding.location if isinstance(finding.location, dict) else {}
        value = location.get("timestamp_seconds")
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _username(user) -> str:
    if not user:
        return ""
    return str(getattr(user, "username", "") or "")


def _choice_label(choices, value: str) -> str:
    lookup = {key: label for key, label in choices}
    return str(lookup.get(value, value) or "")


def _default_message(moderation_status: str) -> str:
    if moderation_status in {"approved", "admin_approved"}:
        return "Moderation approved this lesson."
    if moderation_status == "pending":
        return "Moderation scan is running."
    if moderation_status == "revision_required":
        return "This lesson cannot be published yet. Please revise the highlighted content and scan again."
    if moderation_status == "needs_admin_review":
        return "This lesson needs admin review before publishing."
    if moderation_status == "failed":
        return "Moderation scan failed. Please try again or contact support."
    return "This lesson has not been scanned yet."
