"""Report-only render recovery reconciliation command."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from core.render_recovery import DEFAULT_MAX_AGE_HOURS, build_render_recovery_report


class Command(BaseCommand):
    help = "Inspect stuck or orphaned render workflows without modifying data."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Required safety flag. The command is report-only.")
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument(
            "--max-age-hours",
            type=float,
            default=DEFAULT_MAX_AGE_HOURS,
            help=f"Age threshold for stuck active records. Default: {DEFAULT_MAX_AGE_HOURS}.",
        )

    def handle(self, *args, **options):
        if not options.get("dry_run"):
            raise CommandError("render_recovery_check is report-only; pass --dry-run to confirm no mutations.")
        max_age_hours = float(options["max_age_hours"])
        if max_age_hours <= 0:
            raise CommandError("--max-age-hours must be greater than 0.")

        report = build_render_recovery_report(dry_run=True, max_age_hours=max_age_hours)
        payload = report.as_dict()
        if options.get("json"):
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        summary = payload["summary"]
        self.stdout.write("Render recovery reconciliation (dry-run)")
        self.stdout.write(f"Generated at: {payload['generated_at']}")
        self.stdout.write(f"Max age threshold: {payload['max_age_hours']}h")
        self.stdout.write("")
        self.stdout.write("Summary")
        self.stdout.write(f"  Stuck render jobs: {summary['stuck_render_count']}")
        self.stdout.write(f"  Stuck follow-up intents: {summary['stuck_intent_count']}")
        self.stdout.write(f"  Orphan recovery candidates: {summary['orphan_candidate_count']}")
        self.stdout.write(f"  Oldest stuck age: {summary['oldest_stuck_age_hours']}h")
        if payload["warnings"]:
            self.stdout.write("")
            self.stdout.write("Warnings")
            for warning in payload["warnings"]:
                self.stdout.write(f"  - {warning}")
        if not payload["findings"]:
            self.stdout.write("")
            self.stdout.write("No stuck render workflows or orphan candidates found.")
            return

        self.stdout.write("")
        self.stdout.write("Findings")
        for finding in payload["findings"]:
            self.stdout.write(
                "  - "
                f"{finding['category']} {finding['object_type']}#{finding['object_id']} "
                f"project={finding['project_id']} age={finding['age_hours']}h"
            )
            self.stdout.write(f"    detail: {finding['detail']}")
            self.stdout.write(f"    recommended action: {finding['recommended_action']}")
