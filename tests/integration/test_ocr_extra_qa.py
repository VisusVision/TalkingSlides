# pyright: reportMissingImports=false
import os
import sys
from pathlib import Path
from io import StringIO

import django
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from ai_agents.models import AgentRun
from core.models import Project, UserProfile
from django.contrib.auth.models import User

@pytest.fixture
def test_user():
    user = User.objects.create_user(username="ocr_qa_user", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user

@pytest.fixture
def test_project(test_user):
    return Project.objects.create(user=test_user, title="OCR QA Project", status="ready")

@pytest.mark.django_db
class TestOCRExtraQA:

    def test_command_preserves_slide_order_zero(self):
        out = StringIO()
        call_command("run_ocr_bridge", image_path="any.jpg", slide_order=0, stdout=out)
        output = out.getvalue()
        assert "Slide order: 0" in output
        assert "Provider: noop_ocr" in output

    def test_command_handles_nonexistent_file(self):
        out = StringIO()
        # Noop provider should not crash, just mark asset_missing in metadata
        call_command("run_ocr_bridge", image_path="/non/existent/path.jpg", stdout=out)
        output = out.getvalue()
        assert "Success: True" in output
        assert "'asset_missing': True" in output

    def test_command_moderate_text_with_invalid_project_id(self):
        with pytest.raises(CommandError) as exc:
            call_command("run_ocr_bridge", image_path="any.jpg", project_id=99999, moderate_text=True)
        assert "not found" in str(exc.value).lower()

    def test_ocr_bridge_handles_none_path(self):
        from worker.ai_agents.ocr_bridge import OCRBridge
        bridge = OCRBridge()
        result = bridge.extract(image_path=None)
        assert result.text == ""
        assert result.success is True
        assert result.metadata["asset_missing"] is True

    def test_ocr_command_report_mode_no_persistence(self, test_project):
        initial_run_count = AgentRun.objects.count()
        call_command("run_ocr_bridge", image_path="any.jpg", project_id=test_project.id)
        assert AgentRun.objects.count() == initial_run_count

    def test_ocr_command_moderate_text_with_empty_text_safe(self, test_project):
        out = StringIO()
        # Noop returns empty text, so moderate_text should skip
        call_command("run_ocr_bridge", image_path="any.jpg", project_id=test_project.id, moderate_text=True, stdout=out)
        output = out.getvalue()
        assert "Moderation decision: skipped_empty_text" in output
