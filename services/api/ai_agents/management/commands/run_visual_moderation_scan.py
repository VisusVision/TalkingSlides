from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ai_agents.models import AgentFinding, AgentRun
from core.models import Project


def _ensure_services_on_path() -> None:
    services_root = Path(__file__).resolve().parents[4]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))


class Command(BaseCommand):
    help = "Run a local cover/slide image moderation scan for terminal smoke testing."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int, required=True, help="Project id to scan.")
        parser.add_argument("--cover-path", default="", help="Optional cover image path to scan.")
        parser.add_argument("--slide-path", action="append", default=[], help="Slide image path to scan. Repeatable.")
        parser.add_argument(
            "--slide-order",
            action="append",
            type=int,
            default=[],
            help="Slide order for the matching --slide-path. Repeatable.",
        )
        parser.add_argument("--sync", action="store_true", help="Run synchronously. This command is local-only.")
        parser.add_argument("--persist", action="store_true", help="Persist AgentRun and AgentFinding rows.")

    def handle(self, *args, **options):
        project = Project.objects.filter(pk=int(options["project_id"])).first()
        if project is None:
            raise CommandError(f"Project {options['project_id']} not found.")

        _ensure_services_on_path()
        from worker.ai_agents.policy_engine import PolicyEngine
        from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from worker.ai_agents.visual_moderation import VisualModerationAgent

        provider = LocalImageRulesProvider()
        agent = VisualModerationAgent(provider=provider)
        results = []

        cover_path = str(options.get("cover_path") or "").strip()
        slide_paths = [str(path or "").strip() for path in options.get("slide_path") or []]
        slide_orders = list(options.get("slide_order") or [])

        if cover_path:
            results.append(agent.scan_cover_image(project, image_path=cover_path))

        if slide_paths:
            for index, path in enumerate(slide_paths):
                slide_order = slide_orders[index] if index < len(slide_orders) else index
                results.append(
                    agent.scan_slide_image(
                        project_id=project.id,
                        image_path=path,
                        slide_order=slide_order,
                        ui_anchor=f"manual-slide-{slide_order}-image",
                    )
                )

        if not results:
            results.append(agent.scan_project_visual_assets(project))

        final_decision = PolicyEngine().combine_results(results)
        run = (
            self._persist_results(project=project, results=results, final_decision=final_decision)
            if options["persist"]
            else None
        )

        self.stdout.write(f"Project: {project.id} - {project.title}")
        self.stdout.write(f"Mode: {'sync' if options.get('sync') else 'local-report'}")
        self.stdout.write(f"Persisted AgentRun id: {run.id if run else ''}")
        self.stdout.write(f"Overall decision: {final_decision}")
        for result in results:
            self._print_result(result)

    def _print_result(self, result) -> None:
        metadata = result.metadata or {}
        location = metadata.get("location") or {}
        self.stdout.write(f"Result provider: {result.provider}")
        self.stdout.write(f"Result modality: {result.modality}")
        self.stdout.write(f"Decision: {result.decision}")
        self.stdout.write(f"Image dimensions: {_format_dimensions(metadata)}")
        self.stdout.write(f"Image format: {metadata.get('format', '')}")
        self.stdout.write(f"Location: {location}")
        if not result.findings:
            self.stdout.write("Finding: none")
            return
        for finding in result.findings:
            self.stdout.write(
                "Finding: "
                f"category={finding.category} severity={finding.severity} decision={finding.decision} "
                f"message={finding.user_message}"
            )

    def _persist_results(self, *, project: Project, results: list, final_decision: str) -> AgentRun:
        run = AgentRun.objects.create(
            project=project,
            purpose="moderation",
            phase="visual_manual_scan",
            status="done",
            final_decision=final_decision,
            summary={
                "moderation_status": "report_only",
                "final_decision": final_decision,
                "finding_count": sum(len(result.findings) for result in results),
            },
            completed_at=timezone.now(),
        )
        rows = []
        for result in results:
            for finding in result.findings:
                location = finding.location.model_dump(exclude_none=True)
                rows.append(
                    AgentFinding(
                        run=run,
                        agent_slug=result.agent_slug,
                        agent_version=result.agent_version,
                        content_type="image",
                        object_type=str(location.get("asset_type") or ""),
                        object_id=_object_id(location),
                        location=location,
                        category=finding.category,
                        severity=finding.severity,
                        confidence=finding.confidence,
                        decision=finding.decision,
                        user_message=finding.user_message,
                        admin_message=finding.admin_message,
                        evidence_excerpt=finding.evidence_excerpt,
                        provider=result.provider,
                        provider_raw={
                            key: value
                            for key, value in result.metadata.items()
                            if key in {"width", "height", "format", "mode", "file_size_bytes", "missing"}
                        },
                    )
                )
        if rows:
            AgentFinding.objects.bulk_create(rows)
        return run


def _format_dimensions(metadata: dict) -> str:
    width = metadata.get("width")
    height = metadata.get("height")
    if width is None or height is None:
        return ""
    return f"{width}x{height}"


def _object_id(location: dict) -> str:
    if location.get("slide_order") is not None:
        return str(location["slide_order"])
    return str(location.get("project_id") or "")
