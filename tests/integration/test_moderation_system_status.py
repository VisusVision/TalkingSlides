# pyright: reportMissingImports=false

import json
import os
import sys
from io import StringIO
from pathlib import Path

import django
import pytest
from django.core.management import call_command

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from ai_agents.management.commands import moderation_system_status as status_command  # noqa: E402
from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun  # noqa: E402


@pytest.mark.django_db
def test_status_command_runs_without_creating_moderation_rows():
    before = _moderation_counts()
    out = StringIO()

    call_command("moderation_system_status", stdout=out)

    assert "Moderation database" in out.getvalue()
    assert _moderation_counts() == before


@pytest.mark.django_db
def test_human_output_includes_key_sections():
    out = StringIO()

    call_command("moderation_system_status", stdout=out)

    output = out.getvalue()
    assert "Moderation database" in output
    assert "Text providers" in output
    assert "Visual/OCR/video providers" in output
    assert "Runtime imports" in output
    assert "Docker guidance" in output
    assert "Available commands" in output
    assert "Recommended smoke commands" in output


@pytest.mark.django_db
def test_json_output_is_valid_json():
    out = StringIO()

    call_command("moderation_system_status", json=True, stdout=out)

    payload = json.loads(out.getvalue())
    assert payload["database"]["ai_agents_app_installed"] is True
    assert "text_providers" in payload
    assert "runtime" in payload


@pytest.mark.django_db
def test_json_includes_ollama_enabled_info():
    out = StringIO()

    call_command("moderation_system_status", json=True, stdout=out)

    payload = json.loads(out.getvalue())
    assert "ollama_enabled" in payload["text_providers"]
    assert "ollama_base_url" in payload["text_providers"]
    assert "ollama_text_model" in payload["text_providers"]
    assert "ollama_timeout_seconds" in payload["text_providers"]


@pytest.mark.django_db
def test_json_includes_ffmpeg_availability_field():
    out = StringIO()

    call_command("moderation_system_status", json=True, stdout=out)

    payload = json.loads(out.getvalue())
    assert "ffmpeg_available" in payload["runtime"]
    assert isinstance(payload["runtime"]["ffmpeg_available"], bool)


@pytest.mark.django_db
def test_check_imports_does_not_crash():
    out = StringIO()

    call_command("moderation_system_status", check_imports=True, stdout=out)

    assert "Runtime imports" in out.getvalue()


@pytest.mark.django_db
def test_worker_ai_agents_import_failure_is_reported_gracefully(monkeypatch):
    original_import_status = status_command._import_status

    def fake_import_status(module_name: str):
        if module_name == "worker.ai_agents":
            return {"available": False, "error": "ImportError: synthetic worker import failure"}
        return original_import_status(module_name)

    monkeypatch.setattr(status_command, "_import_status", fake_import_status)
    out = StringIO()

    call_command("moderation_system_status", check_imports=True, stdout=out)

    output = out.getvalue()
    assert "worker.ai_agents importable: no" in output
    assert "Run sync moderation scan commands from the worker container in Docker." in output
    assert "synthetic worker import failure" in output


@pytest.mark.django_db
def test_status_lists_known_moderation_commands():
    out = StringIO()

    call_command("moderation_system_status", json=True, stdout=out)

    payload = json.loads(out.getvalue())
    for command_name in status_command.KNOWN_MODERATION_COMMANDS:
        assert command_name in payload["commands"]
        assert payload["commands"][command_name]["discoverable"] is True
        assert payload["commands"][command_name]["importable"] is True


def _moderation_counts() -> tuple[int, int, int]:
    return (
        AgentRun.objects.count(),
        AgentFinding.objects.count(),
        AdminReviewRequest.objects.count(),
    )
