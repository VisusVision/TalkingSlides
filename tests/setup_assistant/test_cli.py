from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.setup_assistant import cli
from tools.setup_assistant.models import CheckResult, CheckRun, CheckStatus, Profile, Severity


def fake_run(profile: Profile = Profile.CORE) -> CheckRun:
    return CheckRun(
        profile,
        "quick",
        "TestOS",
        [
            CheckResult(
                "test.ok",
                "Test",
                "Test",
                CheckStatus.PASS,
                Severity.INFO,
                "Passed.",
            )
        ],
        "2026-01-01T00:00:00+00:00",
        1,
    )


class FakeEngine:
    calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return fake_run(kwargs["profile"])


def test_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "TalkingSlides Setup Assistant" in capsys.readouterr().out


def test_json_output_and_profile_selection(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "CheckEngine", FakeEngine)
    assert cli.main(["check", "--profile", "tts", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"] == "tts"
    assert FakeEngine.calls[-1]["profile"] is Profile.TTS


def test_report_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "CheckEngine", FakeEngine)
    assert cli.main(["report", "--format", "markdown"]) == 0
    assert "# TalkingSlides Setup Assistant" in capsys.readouterr().out


def test_report_creation(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(cli, "CheckEngine", FakeEngine)
    target = tmp_path / "report.json"
    assert cli.main(["report", "--output", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "pass"


def test_invalid_command_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["not-a-command"])
    assert exc.value.code == 2


def test_runtime_invalid_repository_is_safe_failure(tmp_path: Path, capsys) -> None:
    assert cli.main(["runtime", "status", "--repository", str(tmp_path)]) == 2
    assert "valid TalkingSlides repository" in capsys.readouterr().err


def test_redirected_output_has_no_ansi(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "CheckEngine", FakeEngine)
    cli.main(["check"])
    assert "\x1b[" not in capsys.readouterr().out


def test_safe_action_preview_does_not_change_repository(talking_slides_repo: Path, capsys) -> None:
    assert cli.main(
        ["action", "config.create_env", "--repository", str(talking_slides_repo)]
    ) == 0
    assert "Preview only" in capsys.readouterr().out
    assert not (talking_slides_repo / "infra" / ".env").exists()
