from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from .policy_engine import PolicyEngine, SEVERITY_RANK
from .schemas import AgentFindingSchema, AgentResultSchema, Decision
from .text_moderation import TextModerationAgent

logger = logging.getLogger(__name__)


class ModerationOrchestrator:
    def __init__(
        self,
        text_agent: TextModerationAgent | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.text_agent = text_agent or TextModerationAgent(policy_engine=self.policy_engine)

    def run(
        self,
        project_id: int,
        triggered_by_user_id: int | None = None,
        phase: str = "source_scan",
    ) -> dict[str, Any]:
        from django.contrib.auth.models import User
        from ai_agents.models import AgentRun
        from ai_agents.policies import manual_moderation_prevents_auto_override
        from core.models import Project

        project = Project.objects.select_related("user").get(pk=int(project_id))
        triggered_by = None
        if triggered_by_user_id is not None:
            triggered_by = User.objects.filter(pk=int(triggered_by_user_id)).first()

        run = AgentRun.objects.create(
            project=project,
            triggered_by=triggered_by,
            purpose="moderation",
            phase=str(phase or "source_scan"),
            status="running",
        )

        with transaction.atomic():
            locked = Project.objects.select_for_update().get(pk=project.id)
            if manual_moderation_prevents_auto_override(locked):
                summary = {
                    "moderation_status": locked.moderation_status,
                    "message": "Moderation scan skipped because a manual admin decision is active.",
                    "run_id": run.id,
                    "manual_moderation_status": locked.manual_moderation_status,
                }
                run.status = "done"
                run.final_decision = "manual_block_active"
                run.summary = summary
                run.completed_at = timezone.now()
                run.save(update_fields=["status", "final_decision", "summary", "completed_at"])
                return {
                    "status": "skipped_manual_block",
                    "project_id": project.id,
                    "run_id": run.id,
                    "moderation_status": locked.moderation_status,
                }
            pending_summary = dict(locked.moderation_summary or {})
            pending_summary.update(
                {
                    "moderation_status": "pending",
                    "message": "Moderation scan is running.",
                    "run_id": run.id,
                }
            )
            locked.moderation_status = "pending"
            locked.moderation_summary = pending_summary
            locked.last_moderation_run_id = run.id
            locked.save(update_fields=["moderation_status", "moderation_summary", "last_moderation_run_id", "updated_at"])
        project.refresh_from_db()

        try:
            result = self.text_agent.scan_project(project)
            final_decision = self.policy_engine.combine_results([result])
            project_status = self.policy_engine.project_status_for_decision(final_decision)
            persisted_count = self._persist_findings(run, result)
            summary = self._frontend_safe_summary(
                run_id=run.id,
                moderation_status=project_status,
                final_decision=final_decision,
                result=result,
            )
            with transaction.atomic():
                locked = Project.objects.select_for_update().get(pk=project.id)
                if manual_moderation_prevents_auto_override(locked):
                    skipped_summary = dict(summary)
                    skipped_summary.update(
                        {
                            "moderation_status": locked.moderation_status,
                            "message": "Automatic moderation did not change this lesson because a manual admin decision is active.",
                            "manual_moderation_status": locked.manual_moderation_status,
                        }
                    )
                    run.status = "done"
                    run.final_decision = final_decision
                    run.input_hash = str(result.metadata.get("input_hash") or "")
                    run.summary = skipped_summary
                    run.completed_at = timezone.now()
                    run.save(update_fields=["status", "final_decision", "input_hash", "summary", "completed_at"])
                    return {
                        "status": "skipped_manual_block",
                        "project_id": project.id,
                        "run_id": run.id,
                        "final_decision": final_decision,
                        "moderation_status": locked.moderation_status,
                        "finding_count": persisted_count,
                    }
                locked.moderation_status = project_status
                locked.moderation_summary = summary
                locked.last_moderation_run_id = run.id
                update_fields = ["moderation_status", "moderation_summary", "last_moderation_run_id", "updated_at"]
                if project_status in {"approved", "admin_approved"}:
                    locked.manual_moderation_status = ""
                    locked.manual_moderation_reason = ""
                    locked.manual_moderation_by = None
                    locked.manual_moderation_at = None
                    locked.moderation_blocked_until_review = False
                    update_fields.extend(
                        [
                            "manual_moderation_status",
                            "manual_moderation_reason",
                            "manual_moderation_by",
                            "manual_moderation_at",
                            "moderation_blocked_until_review",
                        ]
                    )
                    self._resolve_publication_blocks(locked)
                locked.save(update_fields=update_fields)
                if project_status == "revision_required":
                    self._create_block_event(locked, run, result.findings)
                if project_status in {"revision_required", "needs_admin_review"}:
                    self._ensure_auto_review_request(locked, run, project_status)
                run.status = "done"
                run.final_decision = final_decision
                run.input_hash = str(result.metadata.get("input_hash") or "")
                run.summary = summary
                run.completed_at = timezone.now()
                run.save(update_fields=["status", "final_decision", "input_hash", "summary", "completed_at"])

            return {
                "status": "done",
                "project_id": project.id,
                "run_id": run.id,
                "final_decision": final_decision,
                "moderation_status": project_status,
                "finding_count": persisted_count,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("Project moderation failed project=%s run=%s", project.id, run.id)
            error_text = _concise_error(exc)
            failure_summary = {
                "moderation_status": "failed",
                "message": "Moderation scan failed. Please try again or contact support.",
                "run_id": run.id,
            }
            with transaction.atomic():
                locked = Project.objects.select_for_update().get(pk=project.id)
                if not manual_moderation_prevents_auto_override(locked):
                    summary = dict(locked.moderation_summary or {})
                    summary.update(failure_summary)
                    locked.moderation_status = "failed"
                    locked.moderation_summary = summary
                    locked.last_moderation_run_id = run.id
                    locked.save(update_fields=["moderation_status", "moderation_summary", "last_moderation_run_id", "updated_at"])
            run.status = "failed"
            run.final_decision = "needs_admin_review"
            run.error_message = error_text
            run.summary = failure_summary
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "final_decision", "error_message", "summary", "completed_at"])
            return {
                "status": "failed",
                "project_id": project.id,
                "run_id": run.id,
                "moderation_status": "failed",
                "error_message": error_text,
            }

    def scan_project_visual_assets(self, project) -> AgentResultSchema:
        from .visual_moderation import VisualModerationAgent

        return VisualModerationAgent(policy_engine=self.policy_engine).scan_project_visual_assets(project)

    def scan_cover_image(self, project) -> AgentResultSchema:
        from .visual_moderation import VisualModerationAgent

        return VisualModerationAgent(policy_engine=self.policy_engine).scan_cover_image(project)

    def scan_slide_images(self, project) -> AgentResultSchema:
        from .visual_moderation import VisualModerationAgent

        return VisualModerationAgent(policy_engine=self.policy_engine).scan_slide_images(project)

    def scan_video_frames(self, project, frames=None) -> AgentResultSchema:
        from .video_frame_moderation import VideoFrameModerationAgent

        return VideoFrameModerationAgent(policy_engine=self.policy_engine).scan_video_frames(project, frames=frames)

    def _persist_findings(self, run, result: AgentResultSchema) -> int:
        from ai_agents.models import AgentFinding

        rows = []
        for finding in result.findings:
            location = finding.location.model_dump(exclude_none=True)
            transcript_page_id = location.get("transcript_page_id")
            rows.append(
                AgentFinding(
                    run=run,
                    agent_slug=result.agent_slug,
                    agent_version=result.agent_version,
                    content_type="text",
                    object_type="transcript_page" if transcript_page_id else "project",
                    object_id=str(transcript_page_id or location.get("project_id") or ""),
                    location=location,
                    category=finding.category,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    decision=finding.decision,
                    user_message=finding.user_message,
                    admin_message=finding.admin_message,
                    evidence_excerpt=finding.evidence_excerpt[:220],
                    provider=result.provider,
                    provider_raw={},
                )
            )
        if rows:
            AgentFinding.objects.bulk_create(rows)
        return len(rows)

    def _frontend_safe_summary(
        self,
        *,
        run_id: int,
        moderation_status: str,
        final_decision: Decision,
        result: AgentResultSchema,
    ) -> dict[str, Any]:
        findings = result.findings
        highest = self.policy_engine.highest_severity(findings) if findings else "low"
        return {
            "moderation_status": moderation_status,
            "final_decision": final_decision,
            "message": _summary_message(final_decision),
            "run_id": run_id,
            "finding_count": len(findings),
            "highest_severity": highest,
            "categories": sorted({finding.category for finding in findings}),
            "findings": [
                _safe_finding_payload(finding)
                for finding in sorted(
                    findings,
                    key=lambda item: (SEVERITY_RANK[item.severity], item.confidence),
                    reverse=True,
                )[:20]
            ],
        }

    def _create_block_event(self, project, run, findings: list[AgentFindingSchema]) -> None:
        from ai_agents.models import PublicationBlockEvent

        highest = self.policy_engine.highest_priority_finding(findings)
        if highest is None:
            return
        if PublicationBlockEvent.objects.filter(
            project=project,
            resolved=False,
            reason_category=highest.category,
        ).exists():
            return
        PublicationBlockEvent.objects.create(
            project=project,
            run=run,
            blocked_by="text_moderation_local_rules",
            reason_category=highest.category,
            highest_severity=highest.severity,
            message_to_user=highest.user_message or _summary_message("block"),
            message_to_admin=highest.admin_message or "Blocked by local text moderation.",
        )

    def _resolve_publication_blocks(self, project) -> None:
        from ai_agents.models import PublicationBlockEvent

        PublicationBlockEvent.objects.filter(project=project, resolved=False).update(
            resolved=True,
            resolved_at=timezone.now(),
        )

    def _ensure_auto_review_request(self, project, run, project_status: str) -> None:
        from ai_agents.models import AdminReviewRequest

        if AdminReviewRequest.objects.filter(project=project, status="open").exists():
            return
        AdminReviewRequest.objects.create(
            project=project,
            run=run,
            requested_by=None,
            publisher_message=_summary_message("needs_admin_review" if project_status == "needs_admin_review" else "block"),
            status="open",
        )


def _summary_message(decision: Decision) -> str:
    if decision == "allow":
        return "Moderation approved this lesson."
    if decision == "warn":
        return "Moderation approved this lesson with warnings."
    if decision == "needs_admin_review":
        return "This lesson needs admin review before publishing."
    return "This lesson cannot be published yet. Please revise the highlighted content and scan again."


def _safe_finding_payload(finding: AgentFindingSchema) -> dict[str, Any]:
    location = finding.location.model_dump(exclude_none=True)
    safe_location = {
        key: location[key]
        for key in ("project_id", "transcript_page_id", "page_key", "slide_order", "field_name", "ui_anchor")
        if key in location
    }
    return {
        "category": finding.category,
        "severity": finding.severity,
        "decision": finding.decision,
        "user_message": finding.user_message,
        "location": safe_location,
        "location_label": _location_label(safe_location),
        "ui_anchor": safe_location.get("ui_anchor", ""),
    }


def _location_label(location: dict[str, Any]) -> str:
    field_name = str(location.get("field_name") or "").replace("_", " ")
    page_key = str(location.get("page_key") or "")
    slide_order = location.get("slide_order")
    if page_key:
        return f"{page_key} {field_name}".strip()
    if slide_order is not None:
        return f"Slide {int(slide_order) + 1} {field_name}".strip()
    if field_name:
        return field_name.title()
    return "Project"


def _concise_error(exc: Exception, limit: int = 500) -> str:
    text = str(exc or exc.__class__.__name__).strip() or exc.__class__.__name__
    return text[:limit]
