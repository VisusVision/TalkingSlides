from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


FLAG_NAMES = [
    "SOURCE_MODERATION_AUTO_ENABLED",
    "SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION",
    "VISUAL_MODERATION_AUTO_ENABLED",
    "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION",
    "VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION",
    "VISUAL_SAFETY_PROVIDER",
    "VISUAL_SAFETY_CLASSIFIER_ENABLED",
    "AZURE_CONTENT_SAFETY_ENABLED",
    "AZURE_CONTENT_SAFETY_ENDPOINT",
    "AZURE_CONTENT_SAFETY_API_VERSION",
    "AZURE_CONTENT_SAFETY_CATEGORIES",
    "AZURE_CONTENT_SAFETY_BLOCK_SEVERITY",
    "AVATAR_IMAGE_MODERATION_AUTO_ENABLED",
    "AVATAR_IMAGE_MODERATION_BLOCK_ON_REJECTION",
    "AVATAR_IMAGE_MODERATION_REQUIRE_APPROVAL",
    "OCR_MODERATION_AUTO_ENABLED",
    "OCR_MODERATION_PROVIDER",
    "OCR_MODERATION_BLOCK_RENDER_ON_REJECTION",
    "AZURE_OCR_ENABLED",
    "AZURE_OCR_ENDPOINT",
    "AZURE_OCR_MODEL",
    "AZURE_OCR_API_VERSION",
    "VIDEO_FRAME_AUDIT_AUTO_ENABLED",
    "VIDEO_FRAME_AUDIT_RUN_OCR",
    "VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION",
    "VIDEO_FRAME_AUDIT_RETAIN_FRAMES",
    "VIDEO_FRAME_AUDIT_FRAME_RETENTION_DAYS",
    "VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS",
]

SMOKE_COMMANDS = [
    "python manage.py moderation_system_status",
    "python manage.py create_moderation_smoke_project --kind clean --user-id <user_id> --scan",
    "python manage.py create_moderation_smoke_project --kind profanity --user-id <user_id> --scan --request-review --review-message \"Smoke unsafe text review\"",
    "python manage.py run_ocr_bridge --image-path <path-to-test-image.png> --asset-type slide_image --slide-order 0 --moderate-text --project-id <project_id>",
    "python manage.py sample_video_frames --video-path <path-to-video.mp4> --output-dir <frames-dir> --max-frames 1",
    "python manage.py cleanup_video_frame_audit_files --dry-run",
]


class Command(BaseCommand):
    help = "Print a read-only moderation smoke checklist and current flag summary."

    def handle(self, *args, **options):
        self.stdout.write("Moderation smoke checklist")
        self.stdout.write("")
        self.stdout.write("Current flag summary")
        for flag_name in FLAG_NAMES:
            self.stdout.write(f"  {flag_name}={_display_value(flag_name)}")
        self.stdout.write(
            f"  AZURE_CONTENT_SAFETY_KEY_CONFIGURED={_yes_no(bool(getattr(settings, 'AZURE_CONTENT_SAFETY_KEY', '')))}"
        )
        self.stdout.write(f"  AZURE_OCR_KEY_CONFIGURED={_yes_no(bool(getattr(settings, 'AZURE_OCR_KEY', '')))}")

        self.stdout.write("")
        self.stdout.write("Recommended commands")
        for command in SMOKE_COMMANDS:
            self.stdout.write(f"  {command}")

        self.stdout.write("")
        self.stdout.write("Docker worker examples")
        self.stdout.write(
            "  docker compose exec worker sh -lc \"cd /app/api && python manage.py moderation_system_status\""
        )
        self.stdout.write(
            "  docker compose exec worker sh -lc \"cd /app/api && python manage.py create_moderation_smoke_project --kind profanity --user-id 1 --scan --request-review --review-message 'Ops smoke test'\""
        )

        self.stdout.write("")
        self.stdout.write("Secret safety")
        self.stdout.write("  Do not commit Azure OCR, Azure Content Safety, or other provider secrets.")
        self.stdout.write("  Rotate any key that has been committed, logged, or shared accidentally.")


def _display_value(flag_name: str) -> str:
    value = getattr(settings, flag_name, "")
    if flag_name in {"AZURE_OCR_ENDPOINT", "AZURE_CONTENT_SAFETY_ENDPOINT"}:
        return str(value or "<not configured>")
    return str(value)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
