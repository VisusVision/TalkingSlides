from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from tools.setup_assistant.runner import CommandResult, CommandRunner, CommandSpec


def python_command(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


def test_stderr_progress_with_exit_zero_is_success() -> None:
    result = CommandRunner().run(
        CommandSpec.create(python_command("import sys; sys.stderr.write('Docker progress\\n')"))
    )
    assert result.ok
    assert result.exit_code == 0
    assert result.stdout == ""
    assert "Docker progress" in result.stderr


def test_stderr_with_nonzero_exit_is_failure() -> None:
    result = CommandRunner().run(
        CommandSpec.create(python_command("import sys; sys.stderr.write('failed\\n'); raise SystemExit(7)"))
    )
    assert not result.ok
    assert result.exit_code == 7
    assert "failed" in result.stderr


def test_timeout_terminates_process() -> None:
    result = CommandRunner().run(
        CommandSpec.create(python_command("import time; time.sleep(10)"), timeout_seconds=0.1)
    )
    assert result.timed_out
    assert not result.ok


def test_missing_executable_is_structured_error() -> None:
    result = CommandRunner().run(CommandSpec.create(("definitely-not-a-real-command-setup-assistant",)))
    assert result.exit_code is None
    assert "Could not start" in result.error


def test_working_directory_with_spaces(tmp_path: Path) -> None:
    cwd = tmp_path / "path with spaces"
    cwd.mkdir()
    result = CommandRunner().run(
        CommandSpec.create(python_command("import os; print(os.getcwd())"), cwd=cwd)
    )
    assert result.ok
    assert "path with spaces" in result.stdout


def test_unicode_working_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "Türkçe-路径"
    cwd.mkdir()
    result = CommandRunner().run(
        CommandSpec.create(python_command("import os; print(os.getcwd())"), cwd=cwd)
    )
    assert result.ok
    assert "Türkçe" in result.stdout


def test_missing_working_directory_is_structured_error(tmp_path: Path) -> None:
    result = CommandRunner().run(
        CommandSpec.create(python_command("print('never')"), cwd=tmp_path / "missing")
    )
    assert result.exit_code is None
    assert "Working directory does not exist" in result.error


def test_process_cancellation() -> None:
    event = threading.Event()
    timer = threading.Timer(0.1, event.set)
    timer.start()
    try:
        result = CommandRunner().run(
            CommandSpec.create(python_command("import time; time.sleep(10)"), timeout_seconds=20),
            event,
        )
    finally:
        timer.cancel()
    assert result.cancelled
    assert not result.ok


def test_secret_output_is_sanitized() -> None:
    result = CommandRunner().run(
        CommandSpec.create(python_command("print('API_TOKEN=super-secret')"))
    )
    assert "super-secret" not in result.stdout


def test_sensitive_command_arguments_are_sanitized_in_display_and_data() -> None:
    result = CommandResult(
        ("example", "--api-token", "super-secret", "--password=hunter2", "--safe", "visible"),
        None,
        0,
        "",
        "",
        1,
    )
    assert "super-secret" not in result.display_command
    assert "hunter2" not in result.display_command
    assert result.to_dict()["argv"] == [
        "example",
        "--api-token",
        "<redacted>",
        "--password=<redacted>",
        "--safe",
        "visible",
    ]


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell.exe"), reason="PowerShell 5.1 regression")
def test_powershell_51_native_stderr_with_exit_zero_uses_native_exit_code() -> None:
    command = (
        f"& '{sys.executable}' -c \"import sys; sys.stderr.write('docker progress')\"; "
        "exit $LASTEXITCODE"
    )
    result = CommandRunner().run(
        CommandSpec.create(("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command))
    )
    assert result.exit_code == 0
    assert result.ok


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell.exe"), reason="PowerShell 5.1 regression")
def test_powershell_51_native_nonzero_exit_is_failure() -> None:
    command = f"& '{sys.executable}' -c \"raise SystemExit(9)\"; exit $LASTEXITCODE"
    result = CommandRunner().run(
        CommandSpec.create(("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command))
    )
    assert result.exit_code == 9
    assert not result.ok
