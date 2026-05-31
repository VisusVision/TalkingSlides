from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.storage_retention import build_storage_report, bytes_to_human


class Command(BaseCommand):
    help = "Report storage retention candidates, orphan media, and capacity by category."

    def add_arguments(self, parser):
        parser.add_argument("--storage-root", default=None, help="Override STORAGE_ROOT for this check.")
        parser.add_argument("--older-than-days", type=int, default=30, help="Age threshold for retention candidates.")
        parser.add_argument("--dry-run", action="store_true", help="Report only. This is also the default behavior.")
        parser.add_argument("--json", action="store_true", help="Emit the full report as JSON.")

    def handle(self, *args, **options):
        report = build_storage_report(
            storage_root=options.get("storage_root"),
            older_than_days=options.get("older_than_days") or 30,
        )

        if options.get("json"):
            self.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
            return

        capacity = report["capacity"]
        self.stdout.write("Storage retention check")
        self.stdout.write(f"root: {report['storage_root']}")
        self.stdout.write(f"mode: dry-run/report-only")
        self.stdout.write(f"older_than_days: {report['older_than_days']}")
        if not report.get("db_available"):
            self.stdout.write("database: unavailable; orphan and DB reference checks skipped")
        for warning in report.get("warnings") or []:
            self.stdout.write(f"warning: {warning}")
        self.stdout.write(f"total: {bytes_to_human(capacity['total_bytes'])}")
        self.stdout.write(f"referenced_existing: {bytes_to_human(capacity['referenced_existing_bytes'])}")
        self.stdout.write(f"orphan_estimate: {bytes_to_human(capacity['orphan_estimate_bytes'])}")
        self.stdout.write("")
        self.stdout.write("Capacity by category:")
        for name, payload in sorted(capacity["categories"].items()):
            self.stdout.write(f"- {name}: {bytes_to_human(payload['bytes'])} in {payload['files']} files")

        retention_candidates = report["retention_candidates"]
        orphan_candidates = report["orphan_candidates"]
        self.stdout.write("")
        self.stdout.write(
            f"Retention candidates: {len(retention_candidates)} files, "
            f"{bytes_to_human(sum(item['size_bytes'] for item in retention_candidates))}"
        )
        for item in retention_candidates[:25]:
            self.stdout.write(
                f"- [{item['category']}] {item['rel_path']} "
                f"({bytes_to_human(item['size_bytes'])}, {item['reason']})"
            )
        if len(retention_candidates) > 25:
            self.stdout.write(f"- ... {len(retention_candidates) - 25} more")

        self.stdout.write("")
        self.stdout.write(
            f"Orphan candidates: {len(orphan_candidates)} paths, "
            f"{bytes_to_human(sum(item['size_bytes'] for item in orphan_candidates))}"
        )
        for item in orphan_candidates[:25]:
            self.stdout.write(
                f"- [{item['category']}] {item['rel_path']} "
                f"({bytes_to_human(item['size_bytes'])}, {item['reason']})"
            )
        if len(orphan_candidates) > 25:
            self.stdout.write(f"- ... {len(orphan_candidates) - 25} more")

        self.stdout.write("")
        self.stdout.write("No files were deleted. Orphan paths are report-only.")
