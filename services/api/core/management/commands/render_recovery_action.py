"""Manual render recovery actions for operators."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from core.render_recovery import DEFAULT_MAX_AGE_HOURS
from core.render_recovery_actions import ACTION_IGNORE, ACTION_INSPECT, ACTION_RESOLVE, run_render_recovery_action


class Command(BaseCommand):
    help = "Run an explicit operator-approved render recovery action."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview the action. This is the default without --confirm.")
        parser.add_argument("--confirm", action="store_true", help="Required to execute resolve/ignore annotations.")
        parser.add_argument("--action", required=True, choices=[ACTION_INSPECT, ACTION_RESOLVE, ACTION_IGNORE])
        parser.add_argument("--type", required=True, choices=["job", "intent"])
        parser.add_argument("--id", required=True, type=int)
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument(
            "--max-age-hours",
            type=float,
            default=DEFAULT_MAX_AGE_HOURS,
            help=f"Age threshold used to match current recovery candidates. Default: {DEFAULT_MAX_AGE_HOURS}.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        confirmed = bool(options.get("confirm"))
        dry_run = bool(options.get("dry_run")) or not confirmed
        max_age_hours = float(options["max_age_hours"])
        if max_age_hours <= 0:
            raise CommandError("--max-age-hours must be greater than 0.")
        if action in {ACTION_RESOLVE, ACTION_IGNORE} and dry_run:
            self.stderr.write("No execution performed and no audit record written; pass --confirm without --dry-run.")

        try:
            result = run_render_recovery_action(
                action=action,
                object_type=options["type"],
                object_id=options["id"],
                dry_run=dry_run,
                confirmed=confirmed,
                max_age_hours=max_age_hours,
            )
        except (LookupError, ValueError, DatabaseError) as exc:
            raise CommandError(str(exc)) from exc

        payload = result.as_dict()
        if options.get("json"):
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        self.stdout.write(f"Render recovery action: {payload['action']}")
        self.stdout.write(f"Object: {payload['object_type']}#{payload['object_id']}")
        self.stdout.write(f"Dry-run: {payload['dry_run']}")
        self.stdout.write(f"Executed: {payload['executed']}")
        self.stdout.write(f"Current candidate: {payload['current_candidate']}")
        self.stdout.write(f"Matched findings: {payload['matched_findings_count']}")
        self.stdout.write("Target")
        self.stdout.write(json.dumps(payload["target"], indent=2, sort_keys=True))
        self.stdout.write("Before state")
        self.stdout.write(json.dumps(payload["before_state"], indent=2, sort_keys=True))
        self.stdout.write("Related IDs")
        self.stdout.write(json.dumps(payload["related_ids"], indent=2, sort_keys=True))
        self.stdout.write("Remediation plan")
        if payload["remediation_plan"]:
            self.stdout.write(json.dumps(payload["remediation_plan"], indent=2, sort_keys=True))
        else:
            self.stdout.write("No current remediation plan found for this object.")
        self.stdout.write("Object state")
        self.stdout.write(json.dumps(payload["object_state"], indent=2, sort_keys=True))
        self.stdout.write("Recommendation")
        if payload["recommendation"]:
            self.stdout.write(json.dumps(payload["recommendation"], indent=2, sort_keys=True))
        else:
            self.stdout.write("No current recovery recommendation found for this object.")
        self.stdout.write("Audit record")
        self.stdout.write(json.dumps(payload["audit_record"], indent=2, sort_keys=True))
