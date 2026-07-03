from __future__ import annotations

import platform
from pathlib import Path

import pytest

from tools.setup_assistant.actions import SafeActionExecutor
from tools.setup_assistant.models import Profile
from tools.setup_assistant.runner import CommandResult
from tools.setup_assistant.runtime import RuntimeManager


class FakeRunner:
    def __init__(self) -> None:
        self.specs = []

    def run(self, spec, cancel_event=None):
        self.specs.append(spec)
        return CommandResult(spec.argv, str(spec.cwd), 0, "ok\n", "progress\n", 1)


def test_create_env_requires_confirmation(talking_slides_repo: Path) -> None:
    result = SafeActionExecutor().execute("config.create_env", talking_slides_repo, confirmed=False)
    assert not result.executed
    assert not (talking_slides_repo / "infra" / ".env").exists()


def test_create_env_does_not_overwrite(talking_slides_repo: Path) -> None:
    target = talking_slides_repo / "infra" / ".env"
    target.write_text("KEEP=1\n", encoding="utf-8")
    result = SafeActionExecutor().execute("config.create_env", talking_slides_repo, confirmed=True)
    assert not result.changed
    assert target.read_text(encoding="utf-8") == "KEEP=1\n"


def test_create_storage_is_narrow_and_idempotent(talking_slides_repo: Path) -> None:
    executor = SafeActionExecutor()
    assert executor.execute("config.create_storage", talking_slides_repo, confirmed=True).changed
    assert not executor.execute("config.create_storage", talking_slides_repo, confirmed=True).changed


def test_runtime_start_requires_confirmation(talking_slides_repo: Path) -> None:
    result = RuntimeManager(talking_slides_repo, FakeRunner()).execute("start", Profile.CORE)
    assert not result.executed
    assert "confirmation" in result.error.lower()


def test_avatar_start_requires_queue_risk_acknowledgement(talking_slides_repo: Path) -> None:
    result = RuntimeManager(talking_slides_repo, FakeRunner()).execute(
        "start", Profile.AVATAR, confirmed=True
    )
    assert not result.executed
    assert "queued avatar" in result.error


def test_runtime_status_is_read_only_and_executes_without_confirmation(talking_slides_repo: Path) -> None:
    runner = FakeRunner()
    result = RuntimeManager(talking_slides_repo, runner).execute("status", Profile.TTS)
    assert result.ok
    assert runner.specs


def test_runtime_windows_command_uses_existing_wrapper(monkeypatch, talking_slides_repo: Path) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    preview = RuntimeManager(talking_slides_repo, FakeRunner()).preview("stop", Profile.CORE)
    assert "windows-runtime.ps1" in preview
    assert "-Stop" in preview


def test_runtime_linux_stop_uses_compose_stop(monkeypatch, talking_slides_repo: Path) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    preview = RuntimeManager(talking_slides_repo, FakeRunner()).preview("stop", Profile.CORE)
    assert "compose" in preview
    assert "stop" in preview
    assert "down" not in preview
