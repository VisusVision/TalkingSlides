from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.storage_unreferenced_candidates import build_storage_unreferenced_candidates_report


class Command(BaseCommand):
    help = "Emit report-only storage files not found in the reference inventory."

    def add_arguments(self, parser):
        parser.add_argument("--storage-root", default=None, help="Override STORAGE_ROOT for this report.")
        parser.add_argument("--project-id", default=None, help="Limit filesystem scan and inventory to project-scoped paths.")
        parser.add_argument("--older-than-days", type=int, default=None, help="Only report candidates older than this many days.")
        parser.add_argument("--json", action="store_true", help="Emit JSON output.")
        parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    def handle(self, *args, **options):
        report = build_storage_unreferenced_candidates_report(
            storage_root=options.get("storage_root"),
            project_id=options.get("project_id"),
            older_than_days=options.get("older_than_days"),
        )

        if options.get("json") or options.get("pretty"):
            indent = 2 if options.get("pretty") else None
            self.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=indent))
            return

        summary = report["summary"]
        self.stdout.write("Storage unreferenced candidates")
        self.stdout.write(f"mode: {report['mode']}")
        self.stdout.write(f"storage_root: {report['storage_root']}")
        if report.get("project_id"):
            self.stdout.write(f"project_id: {report['project_id']}")
        self.stdout.write(f"total_files_scanned: {summary['total_files_scanned']}")
        self.stdout.write(f"total_referenced_paths: {summary['total_referenced_paths']}")
        self.stdout.write(f"total_candidates: {summary['total_candidates']}")
        self.stdout.write(f"total_candidate_bytes: {summary['total_candidate_bytes']}")
        self.stdout.write("No files were modified or deleted.")
