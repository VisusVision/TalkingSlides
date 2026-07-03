from __future__ import annotations

import os
import shutil
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
DEFAULT_REPOSITORY_REF = os.environ.get("TALKINGSLIDES_CLONE_REF", "main")


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
    existing_checkout: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.validation and self.validation.valid and not self.error)


def sanitized_repository_url(repository_url: str) -> str:
    parsed = urlsplit(repository_url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return repository_url


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
        if not request.ref.strip() or request.ref.startswith("-"):
            return "A safe branch or ref is required."
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
                return "Destination is not empty and is not a compatible TalkingSlides checkout."
        return ""

    def command(self, request: CloneRequest) -> tuple[str, ...]:
        if not self.git_executable:
            raise FileNotFoundError("Git is unavailable.")
        destination = canonicalize_path(request.destination)
        return (
            self.git_executable,
            "clone",
            "--progress",
            "--branch",
            request.ref,
            "--single-branch",
            request.repository_url,
            os.fspath(destination),
        )

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
            return CloneResult(
                request,
                False,
                validation=existing_validation,
                existing_checkout=True,
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
        if result.ok and validation and validation.valid:
            return CloneResult(request, True, validation=validation, command_result=result)

        cleaned = False
        cleanup_error = ""
        if not destination_existed and destination.exists():
            try:
                if canonicalize_path(request.destination) != destination:
                    raise OSError("Clone destination changed during cleanup.")
                shutil.rmtree(destination)
                cleaned = True
            except OSError as exc:
                cleanup_error = f" Incomplete destination cleanup failed: {exc}"
        if result.cancelled:
            error = "Clone cancelled."
        elif result.timed_out:
            error = "Clone timed out."
        elif result.ok:
            missing = ", ".join(validation.missing_markers) if validation else "repository checkout"
            error = f"Clone completed but validation failed. Missing: {missing}"
        else:
            error = result.error or result.stderr.strip() or f"Git exited with code {result.exit_code}."
        error += cleanup_error
        return CloneResult(
            request,
            True,
            validation=validation,
            command_result=result,
            error=error,
            cleaned_incomplete_destination=cleaned,
        )
