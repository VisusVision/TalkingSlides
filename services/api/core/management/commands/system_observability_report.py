from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.storage_retention import bytes_to_human
from core.system_observability import build_system_observability_report


class Command(BaseCommand):
    help = "Report read-only system, render, storage, and recovery observability metrics."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--pretty", action="store_true", help="Emit human-readable text. This is the default.")
        parser.add_argument("--storage-root", default=None, help="Override STORAGE_ROOT for this report.")
        parser.add_argument("--older-than-days", type=int, default=30, help="Age threshold for storage retention candidates.")
        parser.add_argument("--recovery-max-age-hours", type=float, default=2.0, help="Age threshold for stale recovery candidates.")

    def handle(self, *args, **options):
        report = build_system_observability_report(
            storage_root=options.get("storage_root"),
            retention_older_than_days=options.get("older_than_days") or 30,
            recovery_max_age_hours=options.get("recovery_max_age_hours") or 2.0,
        )
        if options.get("json"):
            self.stdout.write(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
            return
        self._write_pretty(report)

    def _write_pretty(self, report: dict):
        self.stdout.write("System observability report")
        self.stdout.write(f"generated_at: {report['generated_at']}")
        self.stdout.write(f"mode: {report['mode']}")
        self.stdout.write("")
        self._write_section("Render", report["render"])
        self._write_section("Follow-up intents", report["follow_up_intents"])
        self._write_section("Storage", report["storage"], human_bytes=True)
        self._write_section("Recovery", report["recovery"])
        warnings = report.get("warnings") or []
        self.stdout.write("")
        self.stdout.write("Warnings")
        if warnings:
            for warning in warnings:
                self.stdout.write(f"- {warning}")
        else:
            self.stdout.write("- none")
        self.stdout.write("")
        self.stdout.write("No application behavior was changed. This command is read-only/report-only.")

    def _write_section(self, title: str, section: dict, *, human_bytes: bool = False):
        self.stdout.write(title)
        self.stdout.write(f"available: {section.get('available')}")
        for name, value in sorted((section.get("metrics") or {}).items()):
            display = bytes_to_human(value) if human_bytes and name.endswith("bytes") else value
            self.stdout.write(f"- {name}: {display}")
        for warning in section.get("warnings") or []:
            self.stdout.write(f"- warning: {warning}")
        self.stdout.write("")
