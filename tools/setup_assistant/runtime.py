from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

from .models import Profile
from .runner import CommandResult, CommandRunner, CommandSpec

PROFILE_SERVICES = {
    Profile.CORE: ("postgres", "redis", "minio", "api", "frontend"),
    Profile.TTS: ("postgres", "redis", "minio", "api", "tts_service", "worker", "frontend"),
    Profile.AVATAR: ("postgres", "redis", "minio", "api", "tts_service", "worker", "worker-avatar", "frontend"),
}


@dataclass
class RuntimeActionResult:
    action: str
    profile: Profile
    executed: bool
    preview: str
    result: CommandResult | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.executed and self.result is not None and self.result.ok and not self.error


class RuntimeManager:
    def __init__(self, repository: Path, runner: CommandRunner | None = None) -> None:
        self.repository = repository.resolve()
        self.runner = runner or CommandRunner()

    def _command(self, action: str, profile: Profile, no_frontend: bool) -> tuple[str, ...]:
        if platform.system() == "Windows":
            script = self.repository / "scripts" / "windows-runtime.ps1"
            argv = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                os.fspath(script),
                "-Profile",
                profile.value,
            ]
            if action == "status":
                argv.append("-Status")
            elif action == "stop":
                argv.append("-Stop")
            elif action == "health":
                argv.append("-HealthOnly")
            if no_frontend:
                argv.append("-NoFrontend")
            return tuple(argv)

        compose_file = self.repository / "infra" / "docker-compose.yml"
        argv = ["docker", "compose", "-f", os.fspath(compose_file)]
        if profile is Profile.AVATAR:
            argv.extend(("--profile", "avatar"))
        services = [service for service in PROFILE_SERVICES[profile] if not (no_frontend and service == "frontend")]
        if action == "start":
            argv.extend(("up", "-d", "--no-build", "--pull", "never", *services))
        elif action == "stop":
            argv.extend(("stop", *services))
        elif action == "status":
            argv.extend(("ps", "--format", "json", *services))
        elif action == "health":
            argv.extend(("ps", "--format", "json", *services))
        else:
            raise ValueError(f"Unsupported runtime action: {action}")
        return tuple(argv)

    def preview(self, action: str, profile: Profile, no_frontend: bool = False) -> str:
        result = CommandResult(self._command(action, profile, no_frontend), os.fspath(self.repository), None, "", "", 0)
        warning = ""
        if action == "start" and profile is Profile.AVATAR:
            warning = " Avatar start can consume queued avatar work."
        return f"{result.display_command}{warning}"

    def execute(
        self,
        action: str,
        profile: Profile,
        *,
        no_frontend: bool = False,
        confirmed: bool = False,
        allow_avatar_queue_risk: bool = False,
    ) -> RuntimeActionResult:
        preview = self.preview(action, profile, no_frontend)
        mutating = action in {"start", "stop"}
        if mutating and not confirmed:
            return RuntimeActionResult(action, profile, False, preview, error="Explicit confirmation is required.")
        if action == "start" and profile is Profile.AVATAR and not allow_avatar_queue_risk:
            return RuntimeActionResult(
                action,
                profile,
                False,
                preview,
                error="Avatar start requires explicit acknowledgement that queued avatar work may be consumed.",
            )
        command = self._command(action, profile, no_frontend)
        timeout = 120 if mutating else 20
        result = self.runner.run(CommandSpec.create(command, cwd=self.repository, timeout_seconds=timeout))
        return RuntimeActionResult(action, profile, True, preview, result=result)
