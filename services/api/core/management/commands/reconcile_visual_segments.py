"""Explicit bounded replay of private pending dispatch intents; no new source."""
import json
from django.core.management.base import BaseCommand, CommandError
from core.visual_design.schemas import DesignError
from core.visual_segment_dispatch import reconcile_pending


class Command(BaseCommand):
    help = "Reconcile private segment dispatch (disabled until verified deployment and producer trust)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--run-id")
        parser.add_argument("--retry-claimed", action="store_true")

    def handle(self, *args, **options):
        try:
            result = reconcile_pending(limit=options["limit"], run_id=options["run_id"], retry_claimed=options["retry_claimed"])
        except DesignError as exc:
            raise CommandError(exc.code) from exc
        self.stdout.write(json.dumps(result))
