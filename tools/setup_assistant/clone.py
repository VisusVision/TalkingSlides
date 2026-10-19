from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .repository import RepositoryValidation, canonicalize_path, validate_repository
from .runner import CommandResult, CommandRunner, CommandSpec

DEFAULT_REPOSITORY_URL = os.environ.get(
    "TALKINGSLIDES_CLONE_URL",
    "https://github.com/VisusVision/TalkingSlides.git",
)
DEFAULT_REPOSITORY_REF = os.environ.get("TALKINGSLIDES_CLONE_REF", "")


@dataclass(frozen=True)
class CloneRequest:
    destination: Path
    repository_url: str = DEFAULT_REPOSITORY_URL
    ref: str = DEFAULT_REPOSITORY_REF


@dataclass
class CloneResult:
    request: CloneRequest
    executed: bool
    validation: RepositoryValidation | None = None
    command_result: CommandResult | None = None
    error: str = ""
    cleaned_incomplete_destination: bool = False
    cleanup_error: str = ""
    existing_checkout: bool = False
    outcome: str = ""
    checked_out_branch: str = ""
    head_commit: str = ""
    origin_url: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.validation and self.validation.valid and not self.error)


@dataclass(frozen=True)
class CleanupResult:
    removed: bool
    error: str = ""


def sanitized_repository_url(repository_url: str) -> str:
    parsed = urlsplit(repository_url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return repository_url


def _clear_readonly_and_retry(function, path, _exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)
    except OSError:
        raise


def _clear_readonly_and_retry_onexc(function, path, _exc) -> None:
    _clear_readonly_and_retry(function, path, None)


def _permission_denied(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5


def cleanup_incomplete_clone_destination(
    destination: Path,
    *,
    destination_existed_before: bool,
    remover: Callable[..., None] = shutil.rmtree,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 5,
    initial_delay_seconds: float = 0.1,
) -> CleanupResult:
    if destination_existed_before:
        return CleanupResult(False, "Cleanup skipped because the destination was pre-existing.")
    try:
        canonical = canonicalize_path(destination)
    except (OSError, ValueError) as exc:
        return CleanupResult(False, f"Cleanup skipped because the destination path is unsafe: {exc}")
    if canonical != destination.resolve(strict=False):
        return CleanupResult(False, "Cleanup skipped because the clone destination changed.")
    if not canonical.exists():
        return CleanupResult(False, "")
    validation = validate_repository(canonical)
    if validation.valid:
        return CleanupResult(False, "Cleanup skipped because the destination is a valid TalkingSlides repository.")

    last_error = ""
    delay = initial_delay_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            if remover is shutil.rmtree:
                remover(canonical, onexc=_clear_readonly_and_retry_onexc)
            else:
                remover(canonical, onerror=_clear_readonly_and_retry)
            return CleanupResult(True, "")
        except OSError as exc:
            last_error = str(exc)
            if attempt >= max_attempts or not _permission_denied(exc):
                break
            sleep(delay)
            delay = min(delay * 2, 2.0)
    return CleanupResult(
        False,
        (
            f"Incomplete destination cleanup failed: {last_error}. "
            f"The folder remains at {canonical}. Close applications using it and remove it manually later."
        ),
    )


def _git_metadata(git_executable: str | None, repository: Path) -> tuple[str, str, str]:
    if not git_executable or not (repository / ".git").exists():
        return "", "", ""

    def run_git(*args: str) -> str:
        try:
            result = subprocess.run(
                (git_executable, "-C", os.fspath(repository), *args),
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    branch = run_git("branch", "--show-current")
    head = run_git("rev-parse", "HEAD")
    origin = sanitized_repository_url(run_git("remote", "get-url", "origin"))
    return branch, head, origin


class CloneManager:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        git_executable: str | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.git_executable = git_executable if git_executable is not None else shutil.which("git")

    def preflight(self, request: CloneRequest) -> str:
        if not self.git_executable:
            return (
                "Git is unavailable. Install Git for Windows from https://git-scm.com/download/win "
                "or use your operating system package manager, then reopen the assistant."
            )
        if not request.repository_url.strip():
            return "Repository URL is required."
        parsed = urlsplit(request.repository_url)
        if parsed.username or parsed.password:
            return "Repository URLs containing credentials are not accepted."
        if request.ref.strip().startswith("-"):
            return "Branch or ref cannot start with '-'."
        try:
            destination = canonicalize_path(request.destination)
        except (OSError, ValueError) as exc:
            return str(exc)
        parent = destination.parent
        if not parent.is_dir():
            return f"Destination parent does not exist: {parent}"
        if not os.access(parent, os.W_OK | os.X_OK):
            return f"Destination parent is not writable: {parent}"
        if destination.exists():
            if not destination.is_dir():
                return "Destination exists and is not a directory."
            validation = validate_repository(destination)
            if validation.valid:
                return ""
            try:
                non_empty = next(destination.iterdir(), None) is not None
            except OSError as exc:
                return f"Destination cannot be inspected: {exc}"
            if non_empty:
                return (
                    "Destination is not empty and is not a TalkingSlides checkout. "
                    "Choose another folder, open the folder, or retry using a different empty destination."
                )
        return ""

    def command(self, request: CloneRequest) -> tuple[str, ...]:
        if not self.git_executable:
            raise FileNotFoundError("Git is unavailable.")
        destination = canonicalize_path(request.destination)
        argv = [self.git_executable, "clone", "--progress"]
        ref = request.ref.strip()
        if ref:
            argv.extend(("--branch", ref, "--single-branch"))
        argv.extend((request.repository_url, os.fspath(destination)))
        return tuple(argv)

    def execute(
        self,
        request: CloneRequest,
        *,
        confirmed: bool,
        cancel_event: Event | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> CloneResult:
        error = self.preflight(request)
        if error:
            return CloneResult(request, False, error=error)
        destination = canonicalize_path(request.destination)
        existing_validation = validate_repository(destination) if destination.exists() else None
        if existing_validation and existing_validation.valid:
            branch, head, origin = _git_metadata(self.git_executable, existing_validation.path)
            return CloneResult(
                request,
                False,
                validation=existing_validation,
                existing_checkout=True,
                outcome=(
                    "existing_compatible_checkout"
                    if existing_validation.compatibility_level == "modern"
                    else "existing_limited_checkout"
                ),
                checked_out_branch=branch,
                head_commit=head,
                origin_url=origin,
            )
        if not confirmed:
            return CloneResult(request, False, error="Explicit confirmation is required.")
        destination_existed = destination.exists()
        spec = CommandSpec.create(
            self.command(request),
            cwd=destination.parent,
            timeout_seconds=1800,
        )
        try:
            result = self.runner.run(spec, cancel_event, progress)
        except TypeError:
            result = self.runner.run(spec, cancel_event)
            if progress:
                if result.stdout:
                    progress("stdout", result.stdout)
                if result.stderr:
                    progress("stderr", result.stderr)
        validation = validate_repository(destination) if destination.exists() else None
        branch, head, origin = _git_metadata(self.git_executable, destination)
        if result.ok:
            if validation and validation.valid:
                outcome = "cloned_and_compatible" if validation.compatibility_level == "modern" else "cloned_with_warnings"
                return CloneResult(
                    request,
                    True,
                    validation=validation,
                    command_result=result,
                    outcome=outcome,
                    checked_out_branch=branch,
                    head_commit=head,
                    origin_url=origin,
                )
            return CloneResult(
                request,
                True,
                validation=validation,
                command_result=result,
                error=(
                    "Clone completed, but the downloaded repository does not match the expected "
                    "TalkingSlides project structure. The folder was preserved for inspection."
                ),
                outcome="cloned_but_not_talkingslides",
                checked_out_branch=branch,
                head_commit=head,
                origin_url=origin,
            )

        cleanup = CleanupResult(False, "")
        if destination.exists():
            cleanup = cleanup_incomplete_clone_destination(
                destination,
                destination_existed_before=destination_existed,
            )
        if result.cancelled:
            error = "Clone cancelled."
            outcome = "clone_cancelled"
        elif result.timed_out:
            error = "Clone timed out."
            outcome = "clone_timed_out"
        else:
            error = result.error or result.stderr.strip() or f"Git exited with code {result.exit_code}."
            outcome = "clone_failed"
        if cleanup.error:
            error += f" {cleanup.error}"
        return CloneResult(
            request,
            True,
            validation=validation,
            command_result=result,
            error=error,
            cleaned_incomplete_destination=cleanup.removed,
            cleanup_error=cleanup.error,
            outcome=outcome,
            checked_out_branch=branch,
            head_commit=head,
            origin_url=origin,
        )
