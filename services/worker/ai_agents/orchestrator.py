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

        Project.objects.filter(pk=project.id).update(
            moderation_status="pending",
            moderation_summary={
                "moderation_status": "pending",
                "message": "Moderation scan is running.",
                "run_id": run.id,
            },
            last_moderation_run_id=run.id,
        )
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
                Project.objects.filter(pk=project.id).update(
                    moderation_status=project_status,
                    moderation_summary=summary,
                    last_moderation_run_id=run.id,
                )
                if project_status == "revision_required":
                    self._create_block_event(project, run, result.findings)
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
            Project.objects.filter(pk=project.id).update(
                moderation_status="failed",
                moderation_summary=failure_summary,
                last_moderation_run_id=run.id,
            )
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
        PublicationBlockEvent.objects.create(
            project=project,
            run=run,
            blocked_by="text_moderation_local_rules",
            reason_category=highest.category,
            highest_severity=highest.severity,
            message_to_user=highest.user_message or _summary_message("block"),
            message_to_admin=highest.admin_message or "Blocked by local text moderation.",
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
