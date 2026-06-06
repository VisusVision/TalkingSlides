# pyright: reportMissingImports=false
import os
import sys
import json
from io import StringIO
from pathlib import Path

import django
import pytest
from django.core.management import call_command
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

from ai_agents.models import AgentRun

@pytest.mark.django_db
class TestModerationStatusExtraQA:

    def test_json_output_is_valid_and_contains_expected_keys(self):
        out = StringIO()
        call_command("moderation_system_status", json=True, stdout=out)
        data = json.loads(out.getvalue())
        
        expected_keys = {
            "database",
            "runtime",
            "text_providers",
            "visual_ocr_video_providers",
            "docker_guidance",
            "commands",
            "recommended_smoke_commands"
        }
        assert expected_keys.issubset(data.keys())
        assert "ollama_enabled" in data["text_providers"]
        assert "ffmpeg_available" in data["runtime"]

    def test_command_does_not_create_db_rows(self):
        initial_count = AgentRun.objects.count()
        call_command("moderation_system_status")
        assert AgentRun.objects.count() == initial_count

    def test_output_avoids_sensitive_settings(self):
        out = StringIO()
        call_command("moderation_system_status", json=True, stdout=out)
        output = out.getvalue()
        
        # Verify SECRET_KEY is not leaked
        assert settings.SECRET_KEY not in output
        
        # Verify DATABASE_URL or sensitive db strings are not leaked
        # Usually it shows db engine or names, which is okay for diagnostics,
        # but we check for common sensitive patterns.
        assert "password" not in output.lower()

    def test_lists_all_expected_moderation_commands(self):
        out = StringIO()
        call_command("moderation_system_status", stdout=out)
        output = out.getvalue()
        
        commands = [
            "run_moderation_scan",
            "create_moderation_review_request",
            "create_moderation_smoke_project",
            "run_visual_moderation_scan",
            "run_ocr_bridge",
            "sample_video_frames",
            "moderation_system_status",
        ]
        for cmd in commands:
            assert cmd in output
