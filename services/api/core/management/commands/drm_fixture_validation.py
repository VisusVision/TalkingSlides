from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from core.drm_fixture_validation import build_drm_fixture_validation_report


class Command(BaseCommand):
    help = "Emit a report-only staging validation report for an externally packaged DRM fixture."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", required=True, help="Project ID whose playback sidecar should be validated.")
        parser.add_argument("--storage-root", default=None, help="Override STORAGE_ROOT for this validation.")
        parser.add_argument("--json", action="store_true", help="Emit JSON output.")
        parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    def handle(self, *args, **options):
        project_id = options.get("project_id")
        if project_id in (None, ""):
            raise CommandError("--project-id is required")

        report = build_drm_fixture_validation_report(
            project_id=project_id,
            storage_root=options.get("storage_root"),
        )

        if options.get("json") or options.get("pretty"):
            indent = 2 if options.get("pretty") else None
            self.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=indent))
            return

        self.stdout.write("DRM fixture staging validation")
        self.stdout.write(f"mode: {report['mode']}")
        self.stdout.write(f"project_id: {report['project_id']}")
        self.stdout.write(f"storage_root: {report['storage_root']}")
        self.stdout.write(f"ready_for_staging_fixture_attempt: {report['summary']['ready_for_staging_fixture_attempt']}")
        self.stdout.write(f"blockers: {', '.join(report['blockers']) if report['blockers'] else 'none'}")
        self.stdout.write(f"warnings: {', '.join(report['warnings']) if report['warnings'] else 'none'}")
        self.stdout.write("No database rows, sidecars, manifests, or remote services were modified or contacted.")
