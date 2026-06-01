from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.storage_metrics_snapshot import storage_metrics_snapshot_path, write_storage_metrics_snapshot
from core.storage_retention import bytes_to_human


class Command(BaseCommand):
    help = "Generate the cached storage metrics snapshot used by Prometheus scrapes."

    def add_arguments(self, parser):
        parser.add_argument("--storage-root", default=None, help="Override STORAGE_ROOT for this snapshot.")
        parser.add_argument("--older-than-days", type=int, default=30, help="Age threshold for retention candidates.")
        parser.add_argument("--json", action="store_true", help="Emit the snapshot as JSON.")

    def handle(self, *args, **options):
        storage_root = options.get("storage_root")
        snapshot = write_storage_metrics_snapshot(
            storage_root=storage_root,
            older_than_days=options.get("older_than_days") or 30,
        )
        path = storage_metrics_snapshot_path(storage_root)

        if options.get("json"):
            payload = dict(snapshot)
            payload["snapshot_path"] = str(path)
            self.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
            return

        self.stdout.write("Storage metrics snapshot")
        self.stdout.write(f"snapshot_path: {path}")
        self.stdout.write(f"generated_at: {snapshot['generated_at']}")
        self.stdout.write(f"total_storage_bytes: {bytes_to_human(snapshot['total_storage_bytes'])}")
        self.stdout.write(f"retention_candidate_count: {snapshot['retention_candidate_count']}")
        self.stdout.write(f"orphan_candidate_count: {snapshot['orphan_candidate_count']}")
        self.stdout.write(f"reclaimable_bytes_estimate: {bytes_to_human(snapshot['reclaimable_bytes_estimate'])}")
