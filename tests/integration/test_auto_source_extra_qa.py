# pyright: reportMissingImports=false
import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.models import Project, UserProfile
from django.contrib.auth.models import User
from worker import tasks as worker_tasks


@pytest.mark.django_db
class TestAutoSourceExtraQA:
    def test_settings_bool_parsing_variants(self, monkeypatch):
        # Test the helper that handles env and settings
        monkeypatch.setenv("TEST_FLAG_1", "1")
        monkeypatch.setenv("TEST_FLAG_TRUE", "true")
        monkeypatch.setenv("TEST_FLAG_OFF", "off")
        monkeypatch.setenv("TEST_FLAG_FALSE", "False")

        assert worker_tasks._settings_bool("TEST_FLAG_1") is True
        assert worker_tasks._settings_bool("TEST_FLAG_TRUE") is True
        assert worker_tasks._settings_bool("TEST_FLAG_OFF") is False
        assert worker_tasks._settings_bool("TEST_FLAG_FALSE") is False
        assert worker_tasks._settings_bool("NON_EXISTENT_FLAG", default=True) is True

    def test_auto_moderation_is_strictly_source_scan_phase(self, monkeypatch):
        # Capture the phase passed to the orchestrator
        captured_phase = None
        from worker.ai_agents.orchestrator import ModerationOrchestrator

        def mock_run(self_obj, *args, **kwargs):
            nonlocal captured_phase
            captured_phase = kwargs.get("phase")
            return {"status": "done", "moderation_status": "approved"}

        monkeypatch.setattr(ModerationOrchestrator, "run", mock_run)
        monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "SOURCE_MODERATION_PHASE", "source_scan", raising=False)

        user = User.objects.create_user(username="phase_test_user")
        UserProfile.objects.create(user=user, role="teacher")
        project = Project.objects.create(title="Phase Test", user=user)

        worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

        assert captured_phase == "source_scan"

    def test_auto_source_moderation_catches_unexpected_orchestrator_failure(self, monkeypatch):
        from worker.ai_agents.orchestrator import ModerationOrchestrator

        def failing_run(self_obj, *args, **kwargs):
            raise RuntimeError("orchestrator exploded")

        monkeypatch.setattr(ModerationOrchestrator, "run", failing_run)
        monkeypatch.setattr(settings, "SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)

        user = User.objects.create_user(username="auto_source_failure_user")
        UserProfile.objects.create(user=user, role="teacher")
        project = Project.objects.create(title="Failure Test", user=user)

        result = worker_tasks._run_auto_source_moderation_after_transcript_sync(project.id)

        assert result["enabled"] is True
        assert result["status"] == "failed"
        assert result["project_id"] == project.id
        assert result["moderation_status"] == "failed"
        assert result["block_render"] is False
        assert "orchestrator exploded" in result["error_message"]
