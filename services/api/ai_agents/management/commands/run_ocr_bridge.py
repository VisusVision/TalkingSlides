from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Project


def _ensure_services_on_path() -> None:
    services_root = Path(__file__).resolve().parents[4]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))


class Command(BaseCommand):
    help = "Run the manual OCR bridge against a cover or slide image."

    def add_arguments(self, parser):
        parser.add_argument("--image-path", required=True, help="Image path to pass to the OCR bridge.")
        parser.add_argument(
            "--asset-type",
            choices=("cover", "slide_image", "ocr_text"),
            default="ocr_text",
            help="Asset type for OCR location metadata.",
        )
        parser.add_argument("--slide-order", type=int, default=None, help="Optional slide order for slide images.")
        parser.add_argument("--project-id", type=int, default=None, help="Optional project id for location metadata.")
        parser.add_argument(
            "--moderate-text",
            action="store_true",
            help="Run local text moderation against extracted text when OCR returns non-empty text.",
        )

    def handle(self, *args, **options):
        project_id = options.get("project_id")
        if project_id is not None and not Project.objects.filter(pk=int(project_id)).exists():
            raise CommandError(f"Project {project_id} not found.")

        _ensure_services_on_path()
        from worker.ai_agents.ocr_bridge import OCRBridge

        image_path = str(options["image_path"] or "")
        asset_type = str(options.get("asset_type") or "ocr_text")
        slide_order = options.get("slide_order")
        result = OCRBridge().extract(
            image_path=image_path,
            asset_type=asset_type,  # type: ignore[arg-type]
            slide_order=slide_order,
            project_id=project_id,
            ui_anchor=_ui_anchor(asset_type=asset_type, slide_order=slide_order, project_id=project_id),
        )

        self._print_ocr_result(result)
        if options.get("moderate_text"):
            self._moderate_text(result)

    def _print_ocr_result(self, result) -> None:
        text = str(result.text or "")
        self.stdout.write(f"Provider: {result.provider}")
        self.stdout.write(f"Success: {result.success}")
        self.stdout.write(f"Error: {result.error_message}")
        self.stdout.write(f"Image path: {result.image_path}")
        self.stdout.write(f"Asset type: {result.asset_type or ''}")
        self.stdout.write(f"Slide order: {'' if result.slide_order is None else result.slide_order}")
        self.stdout.write(f"Extracted text length: {len(text)}")
        self.stdout.write(f"Preview: {_preview(text)}")
        self.stdout.write(f"Metadata: {result.metadata}")

    def _moderate_text(self, result) -> None:
        text = str(result.text or "").strip()
        if not text:
            self.stdout.write("Moderation decision: skipped_empty_text")
            self.stdout.write("Moderation finding count: 0")
            return

        from worker.ai_agents.policy_engine import PolicyEngine
        from worker.ai_agents.providers.local_rules_provider import LocalRulesProvider

        location = result.location.model_copy(update={"field_name": "ocr_text"})
        findings = LocalRulesProvider().scan_text(text, location)
        decision = PolicyEngine().combine_findings(findings)
        self.stdout.write(f"Moderation decision: {decision}")
        self.stdout.write(f"Moderation finding count: {len(findings)}")
        for finding in findings:
            self.stdout.write(
                "Moderation finding: "
                f"category={finding.category} severity={finding.severity} decision={finding.decision} "
                f"message={finding.user_message}"
            )


def _preview(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text or "").split())
    return normalized[:limit]


def _ui_anchor(*, asset_type: str, slide_order: int | None, project_id: int | None) -> str:
    if asset_type == "slide_image" and slide_order is not None:
        return f"manual-slide-{slide_order}-ocr"
    if project_id is not None:
        return f"project-{project_id}-ocr"
    return "manual-ocr"
