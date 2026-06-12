from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.storage_reference_inventory import build_storage_reference_inventory


class Command(BaseCommand):
    help = "Emit a report-only inventory of DB and sidecar storage references."

    def add_arguments(self, parser):
        parser.add_argument("--storage-root", default=None, help="Override STORAGE_ROOT for this inventory.")
        parser.add_argument("--project-id", default=None, help="Limit inventory to a single project ID.")
        parser.add_argument("--include-missing", action="store_true", help="Include missing references. This is the default.")
        parser.add_argument("--json", action="store_true", help="Emit JSON output.")
        parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    def handle(self, *args, **options):
        report = build_storage_reference_inventory(
            storage_root=options.get("storage_root"),
            project_id=options.get("project_id"),
            include_missing=True,
        )

        if options.get("json") or options.get("pretty"):
            indent = 2 if options.get("pretty") else None
            self.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=indent))
            return

        summary = report["summary"]
        self.stdout.write("Storage reference inventory")
        self.stdout.write(f"mode: {report['mode']}")
        self.stdout.write(f"storage_root: {report['storage_root']}")
        if report.get("project_id"):
            self.stdout.write(f"project_id: {report['project_id']}")
        self.stdout.write(f"total_references: {summary['total_references']}")
        self.stdout.write(f"existing_references: {summary['existing_references']}")
        self.stdout.write(f"missing_references: {summary['missing_references']}")
        self.stdout.write("No files were modified or deleted.")
