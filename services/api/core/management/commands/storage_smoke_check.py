from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.storage_health import StorageHealthError, run_filesystem_storage_smoke


class Command(BaseCommand):
    help = "Verify filesystem storage by writing, reading, and deleting a probe file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--storage-root",
            default=None,
            help="Override STORAGE_ROOT for this check.",
        )

    def handle(self, *args, **options):
        try:
            result = run_filesystem_storage_smoke(options.get("storage_root"))
        except StorageHealthError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Storage smoke check passed: "
                f"backend={result['backend']} root={result['storage_root']}"
            )
        )
