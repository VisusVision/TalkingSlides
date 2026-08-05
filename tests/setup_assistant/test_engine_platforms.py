from __future__ import annotations

from pathlib import Path

import pytest

from tools.setup_assistant.checks import CheckEngine
from tools.setup_assistant.models import CheckStatus, Profile
from tools.setup_assistant.platforms import linux, windows
from tools.setup_assistant.runner import CommandResult


def result_for(spec, exit_code=0, stdout="ok\n", stderr="", error=""):
    return CommandResult(spec.argv, str(spec.cwd) if spec.cwd else None, exit_code, stdout, stderr, 1, error=error)


class MappingRunner:
    def __init__(self, failures: tuple[str, ...] = ()) -> None:
        self.failures = failures
        self.specs = []

    def run(self, spec, cancel_event=None):
        self.specs.append(spec)
        command = " ".join(spec.argv)
        if any(marker in command for marker in self.failures):
            return result_for(spec, 1, "", "unavailable\n")
        return result_for(spec)


def by_id(results, check_id):
    return next(result for result in results if result.check_id == check_id)


def test_core_profile_has_no_avatar_requirement(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner()).run(repository=talking_slides_repo, profile=Profile.CORE)
    assert by_id(run.results, "profile.core").status is CheckStatus.PASS
    assert not any(result.check_id.endswith("avatar_models") for result in run.results)


def test_tts_profile_reports_cache_without_avatar_requirement(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner()).run(repository=talking_slides_repo, profile=Profile.TTS)
    assert by_id(run.results, "profile.tts_cache").status is CheckStatus.WARNING
    assert not any(result.check_id.endswith("avatar_models") for result in run.results)


def test_avatar_quick_check_skips_expensive_model_inventory(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner()).run(repository=talking_slides_repo, profile=Profile.AVATAR)
    result = by_id(run.results, "profile.avatar_models")
    assert result.status is CheckStatus.SKIPPED
    assert result.expensive


def test_avatar_full_check_reports_missing_models_without_download(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner()).run(
        repository=talking_slides_repo, profile=Profile.AVATAR, full=True
    )
    result = by_id(run.results, "profile.avatar_models")
    assert result.status is CheckStatus.WARNING
    assert result.diagnostic_data["missing_count"] == 4


def test_docker_unavailable_and_compose_missing_are_failures(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner(("docker --version", "docker compose version"))).run(
        repository=talking_slides_repo
    )
    assert by_id(run.results, "docker.command").status is CheckStatus.FAILURE
    assert by_id(run.results, "docker.compose").status is CheckStatus.FAILURE


def test_docker_daemon_unavailable_is_failure(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner(("docker version --format",))).run(repository=talking_slides_repo)
    assert by_id(run.results, "docker.daemon").status is CheckStatus.FAILURE


def test_internet_is_skipped_unless_explicit(talking_slides_repo: Path) -> None:
    run = CheckEngine(MappingRunner()).run(repository=talking_slides_repo)
    assert by_id(run.results, "internet.connectivity").status is CheckStatus.SKIPPED


def test_invalid_repository_is_clear_failure(tmp_path: Path) -> None:
    run = CheckEngine(MappingRunner()).run(repository=tmp_path)
    result = by_id(run.results, "repository.discovery")
    assert result.status is CheckStatus.FAILURE
    assert "Missing identity markers" in result.technical_details
    assert "README.md" in result.technical_details


def test_windows_wsl_absent_and_nvidia_absent(monkeypatch, talking_slides_repo: Path, tmp_path: Path) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    results = windows.windows_checks(
        talking_slides_repo,
        MappingRunner(("wsl.exe", "nvidia-smi")),
        Profile.AVATAR,
        True,
    )
    assert by_id(results, "windows.wsl").status is CheckStatus.WARNING
    assert by_id(results, "windows.docker_desktop").status is CheckStatus.WARNING
    assert by_id(results, "windows.nvidia_smi").status is CheckStatus.WARNING


def test_windows_wsl_and_nvidia_present(talking_slides_repo: Path) -> None:
    results = windows.windows_checks(talking_slides_repo, MappingRunner(), Profile.AVATAR, True)
    assert by_id(results, "windows.wsl").status is CheckStatus.PASS
    assert by_id(results, "windows.nvidia_smi").status is CheckStatus.PASS


def test_linux_docker_socket_permission_denied(monkeypatch, talking_slides_repo: Path, tmp_path: Path) -> None:
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    monkeypatch.setenv("DOCKER_HOST", f"unix://{socket_path}")
    original_access = linux.os.access
    monkeypatch.setattr(
        linux.os,
        "access",
        lambda path, mode: False if Path(path) == socket_path else original_access(path, mode),
    )
    results = linux.linux_checks(talking_slides_repo, MappingRunner(), Profile.CORE, True)
    assert by_id(results, "linux.docker_socket").status is CheckStatus.FAILURE


def test_linux_systemd_unavailable_and_rootless_hint(monkeypatch, talking_slides_repo: Path, tmp_path: Path) -> None:
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path / 'rootless.sock'}")
    monkeypatch.setattr(linux.shutil, "which", lambda _name: None)
    results = linux.linux_checks(talking_slides_repo, MappingRunner(), Profile.CORE, True)
    assert by_id(results, "linux.systemd").status is CheckStatus.SKIPPED
    assert by_id(results, "linux.docker_socket").diagnostic_data["rootless_hint"] is True


def test_linux_nvidia_toolkit_absent(monkeypatch, talking_slides_repo: Path) -> None:
    results = linux.linux_checks(
        talking_slides_repo,
        MappingRunner(("nvidia-smi", "nvidia-container-cli")),
        Profile.AVATAR,
        True,
    )
    assert by_id(results, "linux.nvidia_smi").status is CheckStatus.WARNING
    assert by_id(results, "linux.nvidia_container_toolkit").status is CheckStatus.WARNING


def test_linux_x11_and_wayland_detection(monkeypatch, talking_slides_repo: Path) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    results = linux.linux_checks(talking_slides_repo, MappingRunner(), Profile.CORE, True)
    assert by_id(results, "linux.display").diagnostic_data["display"] == "wayland"


def test_linux_repository_execute_permission_failure(monkeypatch, talking_slides_repo: Path) -> None:
    original_access = linux.os.access
    monkeypatch.setattr(
        linux.os,
        "access",
        lambda path, mode: False if Path(path) == talking_slides_repo else original_access(path, mode),
    )
    results = linux.linux_checks(talking_slides_repo, MappingRunner(), Profile.CORE, True)
    assert by_id(results, "linux.repository_permissions").status is CheckStatus.FAILURE
