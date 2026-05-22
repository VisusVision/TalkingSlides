
from __future__ import annotations

from typing import Any

from rest_framework import serializers

from ai_agents.models import (
    AdminReviewRequest,
    AgentFinding,
    AgentRun,
    ModerationAuditEvent,
    ModerationReport,
)
from ai_agents.policies import project_can_publish


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


class AdminReviewRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminReviewRequest
        fields = ["id", "project", "run", "status", "publisher_message", "created_at"]
        read_only_fields = fields


def moderation_summary_payload(project, *, include_admin_fields: bool = False) -> dict[str, Any]:
    run = _latest_run(project)
    findings = list(_findings_for_run(run))
    summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
    message = str(summary.get("message") or _default_message(project.moderation_status))
    has_open_review = project.admin_review_requests.filter(status="open").exists()
    can_request_admin_review = (
        str(project.moderation_status or "") in REVIEWABLE_MODERATION_STATUSES
        and not has_open_review
    )

    payload = {
        "project_id": project.id,
        "moderation_status": project.moderation_status,
        "can_publish": project_can_publish(project),
        "can_request_admin_review": can_request_admin_review,
        "message": message,
        "admin_review": latest_admin_review_payload(project),
        "editor_text_changed": summary.get("editor_text_changed") if isinstance(summary.get("editor_text_changed"), dict) else None,
        "visual_asset_scan": summary.get("visual_asset_scan") if isinstance(summary.get("visual_asset_scan"), dict) else None,
        "latest_run_id": run.id if run else project.last_moderation_run_id,
        "findings": [
            finding_payload(finding, include_admin_fields=include_admin_fields)
            for finding in findings
        ],
    }
    if include_admin_fields:
        payload["admin_audit_events"] = [
            moderation_audit_event_payload(event)
            for event in project.moderation_audit_events.select_related("actor").order_by("-created_at", "-id")[:10]
        ]
    else:
        public_note = summary.get("publisher_admin_note") or summary.get("admin_response") or ""
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
        "project_id": project.id,
        "project_title": project.title,
        "requested_by_id": review.requested_by_id,
        "requested_by_username": _username(review.requested_by),
        "publisher_username": _username(review.requested_by),
        "status": review.status,
        "moderation_status": project.moderation_status,
        "publisher_message": review.publisher_message,
        "admin_response": review.admin_response,
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
            finding_summary_payload(finding)
            for finding in findings[:5]
        ],
        "highest_severity": highest.severity if highest else "",
        "highest_category": highest.category if highest else "",
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
                finding_payload(finding, include_admin_fields=True)
                for finding in findings
            ],
            "open_project_studio_hint": "",
            "open_review_hint": f"/watch?lesson={project.id}&review=1",
            "open_watch_timestamp_hint": (
                f"/watch?lesson={project.id}&review=1&t={first_timestamp:g}"
                if first_timestamp is not None
                else f"/watch?lesson={project.id}&review=1"
            ),
        }
    )
    return payload


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


def finding_summary_payload(finding: AgentFinding) -> dict[str, Any]:
    location = finding.location if isinstance(finding.location, dict) else {}
    payload: dict[str, Any] = {
        "category": finding.category,
        "severity": finding.severity,
        "decision": finding.decision,
        "user_message": finding.user_message,
        "location_label": location_label(location),
        "ui_anchor": str(location.get("ui_anchor") or ""),
    }
    for key in ("timestamp_seconds", "timestamp_label", "slide_order", "page_key"):
        if key in location and location[key] not in (None, ""):
            payload[key] = location[key]
    return payload


def finding_payload(finding: AgentFinding, *, include_admin_fields: bool = False) -> dict[str, Any]:
    location = finding.location if isinstance(finding.location, dict) else {}
    payload: dict[str, Any] = {
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
    for key in ("timestamp_seconds", "timestamp_label", "slide_order", "page_key"):
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
    return run.findings.all().order_by("-created_at", "-id")


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
