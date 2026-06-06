# pyright: reportMissingImports=false

import os
import sys
from io import StringIO
from pathlib import Path

import django
import pytest
from django.core.management import call_command

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun  # noqa: E402


@pytest.mark.django_db
def test_moderation_smoke_checklist_runs():
    out = StringIO()

    call_command("moderation_smoke_checklist", stdout=out)

    output = out.getvalue()
    assert "Moderation smoke checklist" in output
    assert "Current flag summary" in output
    assert "Recommended commands" in output
    assert "python manage.py moderation_system_status" in output
    assert "python manage.py cleanup_video_frame_audit_files --dry-run" in output


@pytest.mark.django_db
def test_moderation_smoke_checklist_does_not_create_moderation_rows():
    before = _moderation_counts()
    out = StringIO()

    call_command("moderation_smoke_checklist", stdout=out)

    assert _moderation_counts() == before


@pytest.mark.django_db
def test_moderation_smoke_checklist_does_not_print_azure_key(settings):
    settings.AZURE_OCR_KEY = "super-secret-test-key"
    out = StringIO()

    call_command("moderation_smoke_checklist", stdout=out)

    output = out.getvalue()
    assert "super-secret-test-key" not in output
    assert "AZURE_OCR_KEY_CONFIGURED=yes" in output


def _moderation_counts() -> tuple[int, int, int]:
    return (
        AgentRun.objects.count(),
        AgentFinding.objects.count(),
        AdminReviewRequest.objects.count(),
    )
