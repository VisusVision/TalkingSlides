from __future__ import annotations

import os
import stat
from pathlib import Path

from tools.setup_assistant.checks import CheckEngine
from tools.setup_assistant.clone import CloneManager, CloneRequest, cleanup_incomplete_clone_destination
from tools.setup_assistant.models import CheckStatus
from tools.setup_assistant.repository import (
    RECENT_REPOSITORY_LIMIT,
    RepositorySettings,
    RepositoryState,
    forget_repository,
    initial_repository_context,
    load_repository_settings,
    save_repository_preference,
    save_repository_settings,
    select_system_only,
    validate_repository,
)
from tools.setup_assistant.runner import CommandResult


def make_repository(path: Path, *, modern: bool = True, legacy: bool = False, embedded_assistant: bool = True) -> Path:
    for directory in ("infra", "scripts", "services/api", "services/frontend"):
        (path / directory).mkdir(parents=True, exist_ok=True)
    if embedded_assistant:
        (path / "tools" / "setup_assistant").mkdir(parents=True, exist_ok=True)
    (path / "infra" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (path / "README.md").write_text("# TalkingSlides\n", encoding="utf-8")
    if modern:
        (path / "scripts" / "windows-runtime.ps1").write_text("# runtime\n", encoding="utf-8")
    if legacy:
        (path / "scripts" / "windows-dev-start.ps1").write_text("# legacy start\n", encoding="utf-8")
        (path / "scripts" / "windows-dev-setup.ps1").write_text("# legacy setup\n", encoding="utf-8")
    if embedded_assistant:
        (path / "tools" / "setup_assistant" / "__init__.py").write_text("", encoding="utf-8")
    return path


def make_public_main_style_repository(path: Path) -> Path:
    return make_repository(path, modern=False, legacy=True, embedded_assistant=False)


class CloneRunner:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.specs = []

    def run(self, spec, cancel_event=None, output_callback=None):
        self.specs.append(spec)
        destination = Path(spec.argv[-1])
        destination.mkdir(parents=True, exist_ok=True)
        if output_callback:
            output_callback("stderr", "Cloning objects\n")
        if self.mode == "success":
            make_repository(destination)
            return CommandResult(spec.argv, str(spec.cwd), 0, "done\n", "progress\n", 1)
        if self.mode == "limited":
            make_public_main_style_repository(destination)
            return CommandResult(spec.argv, str(spec.cwd), 0, "done\n", "progress\n", 1)
        if self.mode == "cancel":
            return CommandResult(spec.argv, str(spec.cwd), None, "", "", 1, cancelled=True, error="Command cancelled.")
        if self.mode == "timeout":
            return CommandResult(spec.argv, str(spec.cwd), None, "", "", 1, timed_out=True, error="Command timed out.")
        if self.mode == "invalid":
            (destination / "README.md").write_text("not TalkingSlides\n", encoding="utf-8")
            return CommandResult(spec.argv, str(spec.cwd), 0, "done\n", "", 1)
        return CommandResult(spec.argv, str(spec.cwd), 7, "", "clone failed\n", 1)


def test_lookalike_repository_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    validation = validate_repository(tmp_path)
    assert not validation.valid
    assert "services/api" in validation.missing_markers
    assert "services/frontend" in validation.missing_markers
    assert "scripts" in validation.missing_markers


def test_public_main_style_repository_is_valid_with_capability_warnings(tmp_path: Path) -> None:
    repository = make_public_main_style_repository(tmp_path / "TalkingSlides")
    validation = validate_repository(repository)
    assert validation.valid
    assert validation.missing_identity_markers == ()
    assert "scripts/windows-runtime.ps1" in validation.missing_capabilities
    assert "tools/setup_assistant/__init__.py" in validation.missing_capabilities
    assert validation.capabilities.legacy_windows_runtime
    assert not validation.capabilities.modern_windows_runtime
    assert validation.compatibility_level in {"legacy", "limited"}
    assert validation.warnings


def test_repository_paths_with_spaces_and_unicode_are_valid(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "Talking Slides Türkçe")
    assert validate_repository(repository).valid
    assert validate_repository(repository).path == repository.resolve()


def test_recent_repositories_are_bounded_and_forgettable(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    repositories = [make_repository(tmp_path / f"repo-{index}") for index in range(7)]
    for repository in repositories:
        save_repository_preference(repository, settings_path)
    settings = load_repository_settings(settings_path)
    assert len(settings.recent_repositories) == RECENT_REPOSITORY_LIMIT
    assert settings.recent_repositories[0] == repositories[-1].resolve()
    forget_repository(repositories[-1], settings_path)
    assert load_repository_settings(settings_path).repository is None


def test_saved_repository_disappearance_returns_to_onboarding(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        "tools.setup_assistant.repository.load_repository_settings",
        lambda path=None: RepositorySettings(missing, (missing,), False),
    )
    monkeypatch.setattr("tools.setup_assistant.repository._repository_candidates", lambda: [missing])
    context = initial_repository_context()
    assert context.state is RepositoryState.NONE_SELECTED


def test_system_only_setting_round_trip(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    save_repository_settings(RepositorySettings(), settings_path)
    select_system_only(settings_path)
    assert load_repository_settings(settings_path).system_only is True


def test_system_only_diagnostics_do_not_fail_for_repository(monkeypatch) -> None:
    monkeypatch.setattr("tools.setup_assistant.checks.engine.platform_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(CheckEngine, "_docker_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(CheckEngine, "_ollama_checks", lambda *args, **kwargs: [])
    run = CheckEngine().run(system_only=True)
    result = next(item for item in run.results if item.check_id == "repository.discovery")
    assert result.status is CheckStatus.SKIPPED
    assert run.mode == "system-only"
    repository_gated = [
        item
        for item in run.results
        if item.check_id in {
            "config.repository_required",
            "profile.repository_required",
            "git.repository_required",
        }
    ]
    assert len(repository_gated) == 3
    assert all(item.summary == "Repository required" for item in repository_gated)


def test_clone_missing_git_is_guidance_only(tmp_path: Path) -> None:
    request = CloneRequest(tmp_path / "repo")
    result = CloneManager(CloneRunner(), git_executable="").execute(request, confirmed=True)
    assert not result.executed
    assert "Git is unavailable" in result.error
    assert not request.destination.exists()


def test_successful_clone_uses_argument_array_and_validates(tmp_path: Path) -> None:
    runner = CloneRunner()
    destination = tmp_path / "path with spaces" / "Türkçe repo"
    destination.parent.mkdir()
    request = CloneRequest(destination, ref="main")
    progress = []
    result = CloneManager(runner, git_executable="git").execute(
        request,
        confirmed=True,
        progress=lambda channel, text: progress.append((channel, text)),
    )
    assert result.ok
    assert runner.specs[0].argv[-1] == os.fspath(destination.resolve())
    assert runner.specs[0].argv[1:7] == (
        "clone",
        "--progress",
        "--branch",
        "main",
        "--single-branch",
        request.repository_url,
    )
    assert progress


def test_empty_clone_ref_uses_repository_default_branch(tmp_path: Path) -> None:
    runner = CloneRunner()
    destination = tmp_path / "default-branch"
    request = CloneRequest(destination, ref="")
    result = CloneManager(runner, git_executable="git").execute(request, confirmed=True)
    assert result.ok
    argv = runner.specs[0].argv
    assert argv[:2] == ("git", "clone")
    assert "--branch" not in argv
    assert "--single-branch" not in argv
    assert argv[-2:] == (request.repository_url, os.fspath(destination.resolve()))


def test_successful_limited_clone_is_preserved_and_ok(tmp_path: Path) -> None:
    destination = tmp_path / "limited"
    result = CloneManager(CloneRunner("limited"), git_executable="git").execute(
        CloneRequest(destination, ref=""),
        confirmed=True,
    )
    assert result.ok
    assert result.validation and result.validation.valid
    assert result.outcome == "cloned_with_warnings"
    assert not result.cleaned_incomplete_destination
    assert destination.exists()
    assert "scripts/windows-runtime.ps1" in result.validation.missing_capabilities


def test_successful_invalid_clone_is_preserved_for_inspection(tmp_path: Path) -> None:
    destination = tmp_path / "invalid-clone"
    result = CloneManager(CloneRunner("invalid"), git_executable="git").execute(
        CloneRequest(destination),
        confirmed=True,
    )
    assert not result.ok
    assert result.outcome == "cloned_but_not_talkingslides"
    assert not result.cleaned_incomplete_destination
    assert destination.exists()
    assert "preserved for inspection" in result.error


def test_failed_and_cancelled_clones_clean_only_new_destination(tmp_path: Path) -> None:
    for mode in ("failure", "cancel"):
        destination = tmp_path / mode
        result = CloneManager(CloneRunner(mode), git_executable="git").execute(
            CloneRequest(destination),
            confirmed=True,
        )
        assert not result.ok
        assert result.cleaned_incomplete_destination
        assert not destination.exists()

    timeout_destination = tmp_path / "timeout"
    result = CloneManager(CloneRunner("timeout"), git_executable="git").execute(
        CloneRequest(timeout_destination),
        confirmed=True,
    )
    assert not result.ok
    assert result.cleaned_incomplete_destination
    assert not timeout_destination.exists()

    existing = tmp_path / "existing-empty"
    existing.mkdir()
    result = CloneManager(CloneRunner("failure"), git_executable="git").execute(
        CloneRequest(existing),
        confirmed=True,
    )
    assert not result.ok
    assert not result.cleaned_incomplete_destination
    assert existing.is_dir()


def test_non_empty_destination_and_unsafe_ref_are_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")
    manager = CloneManager(CloneRunner(), git_executable="git")
    assert "not empty" in manager.preflight(CloneRequest(destination))
    assert "cannot start" in manager.preflight(CloneRequest(tmp_path / "new", ref="--upload-pack=bad"))


def test_post_clone_validation_failure_is_reported_and_cleaned(tmp_path: Path) -> None:
    destination = tmp_path / "invalid-clone"
    result = CloneManager(CloneRunner("invalid"), git_executable="git").execute(
        CloneRequest(destination),
        confirmed=True,
    )
    assert not result.ok
    assert "preserved for inspection" in result.error
    assert not result.cleaned_incomplete_destination
    assert destination.exists()


def test_cleanup_retries_permission_error_then_succeeds(tmp_path: Path) -> None:
    destination = tmp_path / "partial"
    destination.mkdir()
    attempts = {"count": 0}

    def flaky_remover(path, onerror=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError(5, "Access is denied", os.fspath(path))
        destination.rmdir()

    result = cleanup_incomplete_clone_destination(
        destination,
        destination_existed_before=False,
        remover=flaky_remover,
        sleep=lambda _seconds: None,
    )
    assert result.removed
    assert attempts["count"] == 2


def test_cleanup_reports_persistent_permission_error(tmp_path: Path) -> None:
    destination = tmp_path / "partial"
    destination.mkdir()

    def locked_remover(path, onerror=None):
        raise PermissionError(5, "Access is denied", os.fspath(path))

    result = cleanup_incomplete_clone_destination(
        destination,
        destination_existed_before=False,
        remover=locked_remover,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    assert not result.removed
    assert destination.exists()
    assert "Access is denied" in result.error


def test_cleanup_read_only_file(tmp_path: Path) -> None:
    destination = tmp_path / "partial"
    destination.mkdir()
    locked = destination / "readonly.txt"
    locked.write_text("locked", encoding="utf-8")
    locked.chmod(stat.S_IREAD)
    result = cleanup_incomplete_clone_destination(
        destination,
        destination_existed_before=False,
        sleep=lambda _seconds: None,
    )
    assert result.removed
    assert not destination.exists()


def test_cleanup_protects_preexisting_destination(tmp_path: Path) -> None:
    destination = tmp_path / "preexisting"
    destination.mkdir()
    result = cleanup_incomplete_clone_destination(destination, destination_existed_before=True)
    assert not result.removed
    assert destination.exists()
    assert "pre-existing" in result.error


def test_no_misspelled_setup_assistant_token_in_user_facing_sources() -> None:
    repository = Path(__file__).resolve().parents[2]
    suffixes = {".py", ".md", ".yml", ".yaml", ".ps1", ".txt"}
    typo = "setup_" + "assitant"
    offenders = []
    for path in repository.rglob("*"):
        if ".git" in path.parts or path.suffix.lower() not in suffixes or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if typo in text.lower():
            offenders.append(path.relative_to(repository).as_posix())
    assert offenders == []
