from __future__ import annotations

import os
import platform
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

from ..models import CheckResult, CheckRun, CheckStatus, Profile, SafeAction, Severity
from ..ollama import OllamaManager
from ..platforms import platform_checks
from ..repository import (
    REPOSITORY_MARKERS,
    RepositoryValidation,
    discover_repository,
    display_marker_list,
    repository_capability_summary,
)
from ..runner import CommandResult, CommandRunner, CommandSpec
from ..status import ServiceStatus

ProgressCallback = Callable[[str, int, int], None]

PORTS = {
    3000: "Frontend",
    8000: "API",
    8001: "TTS",
    5432: "PostgreSQL",
    6379: "Redis",
    9000: "MinIO API",
    9001: "MinIO console",
}
def _command_detail(result: CommandResult) -> str:
    return (result.stderr.strip() or result.stdout.strip() or result.error).splitlines()[0] if (
        result.stderr.strip() or result.stdout.strip() or result.error
    ) else ""


class CheckEngine:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def run(
        self,
        *,
        profile: Profile = Profile.CORE,
        full: bool = False,
        internet: bool = False,
        repository: str | os.PathLike[str] | None = None,
        system_only: bool = False,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> CheckRun:
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        validation = None if system_only else discover_repository(repository)
        repo = validation.path if validation and validation.valid else None
        steps: list[tuple[str, Callable[[], list[CheckResult]]]] = [
            ("System requirements", lambda: platform_checks(repo, self.runner, profile, full)),
            ("Repository", lambda: self._repository_checks(validation, system_only)),
            ("Configuration", lambda: self._configuration_checks(repo, profile)),
            ("Docker", lambda: self._docker_checks(repo, profile, full, cancel_event)),
            ("Ports", lambda: self._port_checks(full)),
            ("Profile assets", lambda: self._profile_checks(repo, profile, full)),
            ("Ollama", lambda: self._ollama_checks(repo)),
            ("Runtime health", lambda: self._health_checks(profile) if full else self._skipped_health()),
            ("Git summary", lambda: self._git_checks(repo)),
            ("Internet", self._internet_check if internet else self._skipped_internet),
        ]
        results: list[CheckResult] = []
        for index, (title, callback) in enumerate(steps, start=1):
            if cancel_event and cancel_event.is_set():
                results.append(
                    CheckResult(
                        "run.cancelled",
                        "Check cancelled",
                        "System Diagnostics",
                        CheckStatus.SKIPPED,
                        Severity.INFO,
                        "The remaining checks were cancelled.",
                    )
                )
                break
            if progress:
                progress(title, index, len(steps))
            results.extend(callback())
        return CheckRun(
            profile=profile,
            mode="system-only" if system_only else ("full" if full else "quick"),
            platform=platform.system(),
            results=results,
            started_at=started_at,
            duration_ms=round((time.monotonic() - started) * 1000),
            repository=os.fspath(repo) if repo else None,
        )

    @staticmethod
    def _repository_checks(
        validation: RepositoryValidation | None,
        system_only: bool = False,
    ) -> list[CheckResult]:
        if system_only:
            return [
                CheckResult(
                    "repository.discovery",
                    "TalkingSlides repository",
                    "Installation & Configuration",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Repository required for project checks and actions; system checks remain available.",
                )
            ]
        if validation is None:
            return [
                CheckResult(
                    "repository.discovery",
                    "TalkingSlides repository",
                    "Installation & Configuration",
                    CheckStatus.FAILURE,
                    Severity.CRITICAL,
                    "No TalkingSlides repository was found.",
                    remediation="Select the repository folder containing infra, services, and scripts.",
                )
            ]
        if not validation.valid:
            return [
                CheckResult(
                    "repository.discovery",
                    "TalkingSlides repository",
                    "Installation & Configuration",
                    CheckStatus.FAILURE,
                    Severity.CRITICAL,
                    "The selected folder is not a valid TalkingSlides repository.",
                    technical_details=f"Missing identity markers: {', '.join(display_marker_list(validation.missing_identity_markers))}",
                    remediation="Choose the repository root rather than a parent or child directory.",
                    diagnostic_data={"missing_identity_markers": list(validation.missing_identity_markers)},
                )
            ]
        writable = os.access(validation.path, os.R_OK | os.W_OK | os.X_OK)
        summary = (
            "Compatible TalkingSlides repository detected."
            if validation.compatibility_level == "modern"
            else "TalkingSlides repository detected, but some modern runtime controls are unavailable."
        )
        results = [
            CheckResult(
                "repository.discovery",
                "TalkingSlides repository",
                "Installation & Configuration",
                CheckStatus.PASS if validation.compatibility_level == "modern" else CheckStatus.WARNING,
                Severity.INFO if validation.compatibility_level == "modern" else Severity.MEDIUM,
                summary,
                technical_details="\n".join(repository_capability_summary(validation)),
                remediation=" ".join(validation.warnings),
                diagnostic_data={
                    "identity_marker_count": len(REPOSITORY_MARKERS),
                    "compatibility_level": validation.compatibility_level,
                    "missing_capabilities": list(validation.missing_capabilities),
                },
            ),
            CheckResult(
                "repository.permissions",
                "Repository permissions",
                "Installation & Configuration",
                CheckStatus.PASS if writable else CheckStatus.FAILURE,
                Severity.HIGH if not writable else Severity.INFO,
                "Repository is readable and writable." if writable else "Repository permissions are insufficient.",
                remediation="Review local permissions for the selected repository." if not writable else "",
                diagnostic_data={"read_write_traverse": writable},
            ),
            CheckResult(
                "repository.path",
                "Repository path compatibility",
                "Installation & Configuration",
                CheckStatus.WARNING if any(character in os.fspath(validation.path) for character in "\n\r\t") else CheckStatus.PASS,
                Severity.HIGH
                if any(character in os.fspath(validation.path) for character in "\n\r\t")
                else Severity.INFO,
                "Repository path is compatible with argv-based command execution.",
                remediation=(
                    "Remove control characters from the repository path."
                    if any(character in os.fspath(validation.path) for character in "\n\r\t")
                    else ""
                ),
                diagnostic_data={"contains_spaces": " " in os.fspath(validation.path), "contains_unicode": not os.fspath(validation.path).isascii()},
            ),
        ]
        return results

    def _ollama_checks(self, repository: Path | None) -> list[CheckResult]:
        state = OllamaManager(self.runner).inspect(repository)
        if state.status in {ServiceStatus.HEALTHY, ServiceStatus.RUNNING}:
            check_status = CheckStatus.PASS
            severity = Severity.INFO
        elif state.status in {ServiceStatus.OPTIONAL, ServiceStatus.NOT_CONFIGURED} and not state.required:
            check_status = CheckStatus.SKIPPED
            severity = Severity.INFO
        elif state.status is ServiceStatus.BLOCKED and state.required:
            check_status = CheckStatus.FAILURE
            severity = Severity.HIGH
        else:
            check_status = CheckStatus.WARNING
            severity = Severity.MEDIUM
        return [
            CheckResult(
                "ollama.host",
                "Host-side Ollama",
                "Optional integrations",
                check_status,
                severity,
                state.summary,
                technical_details=state.details,
                remediation=(
                    "Install or start Ollama manually; model downloads require separate confirmation."
                    if check_status in {CheckStatus.FAILURE, CheckStatus.WARNING}
                    else ""
                ),
                documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md#intelligence--ollama",
                diagnostic_data={
                    "installed": state.installed,
                    "running": state.running,
                    "required": state.required,
                    "model_count": len(state.models),
                    "missing_model_count": len(state.missing_models),
                    "assistant_owned": state.assistant_owned,
                },
            )
        ]

    @staticmethod
    def _configuration_checks(repository: Path | None, profile: Profile) -> list[CheckResult]:
        if not repository:
            return [
                CheckResult(
                    "config.repository_required",
                    "Repository configuration",
                    "Installation & Configuration",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Repository required",
                )
            ]
        env_file = repository / "infra" / ".env"
        env_example = repository / "infra" / ".env.example"
        results = [
            CheckResult(
                "config.env_template",
                "Environment template",
                "Installation & Configuration",
                CheckStatus.PASS if env_example.is_file() else CheckStatus.FAILURE,
                Severity.HIGH if not env_example.is_file() else Severity.INFO,
                "infra/.env.example exists." if env_example.is_file() else "infra/.env.example is missing.",
            )
        ]
        action = SafeAction(
            "config.create_env",
            "Create local environment file",
            "Copy infra/.env.example to infra/.env only if infra/.env does not exist.",
            command=(
                "talkingslides-setup-cli.exe" if platform.system() == "Windows" else "talkingslides-setup",
                "action",
                "config.create_env",
                "--repository",
                os.fspath(repository),
            ),
        )
        results.append(
            CheckResult(
                "config.env_file",
                "Local environment file",
                "Installation & Configuration",
                CheckStatus.PASS if env_file.is_file() else CheckStatus.WARNING,
                Severity.HIGH if not env_file.is_file() else Severity.INFO,
                "infra/.env exists; values were not read into the report." if env_file.is_file() else "infra/.env is missing.",
                remediation="Create it from the template without overwriting an existing file." if not env_file.is_file() else "",
                safe_action=action if env_example.is_file() and not env_file.exists() else None,
                diagnostic_data={"present": env_file.is_file()},
            )
        )
        storage = repository / "storage_local"
        results.append(
            CheckResult(
                "config.storage_directory",
                "Local storage directory",
                "Installation & Configuration",
                CheckStatus.PASS if storage.is_dir() else CheckStatus.WARNING,
                Severity.MEDIUM if not storage.is_dir() else Severity.INFO,
                "storage_local exists." if storage.is_dir() else "storage_local is not present in this checkout.",
                remediation="Create the local directory only when local runtime storage is required." if not storage.is_dir() else "",
                safe_action=SafeAction(
                    "config.create_storage",
                    "Create storage_local",
                    "Create the empty storage_local directory. Existing content is never removed.",
                    command=(
                        "talkingslides-setup-cli.exe"
                        if platform.system() == "Windows"
                        else "talkingslides-setup",
                        "action",
                        "config.create_storage",
                        "--repository",
                        os.fspath(repository),
                    ),
                )
                if not storage.exists()
                else None,
                profile=profile.value,
            )
        )
        return results

    def _docker_checks(
        self,
        repository: Path | None,
        profile: Profile,
        full: bool,
        cancel_event: Event | None,
    ) -> list[CheckResult]:
        version = self.runner.run(CommandSpec.create(("docker", "--version"), timeout_seconds=8), cancel_event)
        compose = self.runner.run(CommandSpec.create(("docker", "compose", "version"), timeout_seconds=8), cancel_event)
        daemon = self.runner.run(
            CommandSpec.create(("docker", "version", "--format", "{{.Server.Version}}"), timeout_seconds=10),
            cancel_event,
        )
        results = [
            self._command_check("docker.command", "Docker", version, "Install Docker manually and reopen the assistant."),
            self._command_check("docker.compose", "Docker Compose plugin", compose, "Install Docker Compose v2."),
            self._command_check("docker.daemon", "Docker daemon", daemon, "Start Docker and wait for the daemon to become ready."),
        ]
        if not repository:
            results.append(
                CheckResult(
                    "docker.compose_config",
                    "Compose configuration",
                    "Docker",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Repository required",
                )
            )
            return results
        if not full:
            results.append(
                CheckResult(
                    "docker.compose_config",
                    "Compose configuration",
                    "Docker",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Compose parsing runs during Full Check.",
                )
            )
            return results
        if profile is Profile.AVATAR:
            runtimes = self.runner.run(
                CommandSpec.create(("docker", "info", "--format", "{{json .Runtimes}}"), timeout_seconds=10),
                cancel_event,
            )
            nvidia_runtime = runtimes.ok and "nvidia" in runtimes.stdout.lower()
            results.append(
                CheckResult(
                    "docker.gpu_runtime",
                    "Docker GPU runtime",
                    "Docker",
                    CheckStatus.PASS if nvidia_runtime else CheckStatus.WARNING,
                    Severity.HIGH if not nvidia_runtime else Severity.INFO,
                    "Docker reports an NVIDIA runtime."
                    if nvidia_runtime
                    else "Docker did not report an NVIDIA runtime; no GPU container was started.",
                    technical_details="" if runtimes.ok else _command_detail(runtimes),
                    remediation="Configure Docker GPU support before using the avatar profile." if not nvidia_runtime else "",
                    duration_ms=runtimes.duration_ms,
                    diagnostic_data={"nvidia_runtime_present": nvidia_runtime, "exit_code": runtimes.exit_code},
                    profile=Profile.AVATAR.value,
                )
            )
        compose_file = repository / "infra" / "docker-compose.yml"
        argv = ["docker", "compose", "-f", os.fspath(compose_file)]
        if profile is Profile.AVATAR:
            argv.extend(("--profile", "avatar"))
        argv.extend(("config", "--quiet"))
        config = self.runner.run(CommandSpec.create(argv, cwd=repository, timeout_seconds=20), cancel_event)
        results.append(
            self._command_check(
                "docker.compose_config",
                "Compose configuration",
                config,
                "Create/validate infra/.env, then re-run the check. No images are built or pulled.",
            )
        )
        return results

    @staticmethod
    def _command_check(check_id: str, title: str, result: CommandResult, remediation: str) -> CheckResult:
        return CheckResult(
            check_id,
            title,
            "Docker",
            CheckStatus.PASS if result.ok else CheckStatus.FAILURE,
            Severity.HIGH if not result.ok else Severity.INFO,
            _command_detail(result) if result.ok else f"{title} is unavailable or not ready.",
            technical_details="" if result.ok else _command_detail(result),
            remediation="" if result.ok else remediation,
            duration_ms=result.duration_ms,
            diagnostic_data={
                "exit_code": result.exit_code,
                "stderr_present": bool(result.stderr),
                "timed_out": result.timed_out,
            },
        )

    @staticmethod
    def _port_checks(full: bool) -> list[CheckResult]:
        if not full:
            return [
                CheckResult(
                    "ports.conflicts",
                    "Required ports",
                    "Requirements",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Port inspection runs during Full Check.",
                )
            ]
        occupied: list[dict[str, object]] = []
        for port, name in PORTS.items():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    occupied.append({"port": port, "service": name})
        return [
            CheckResult(
                "ports.conflicts",
                "Required ports",
                "Requirements",
                CheckStatus.WARNING if occupied else CheckStatus.PASS,
                Severity.MEDIUM if occupied else Severity.INFO,
                f"{len(occupied)} expected port(s) are already accepting connections."
                if occupied
                else "Expected local ports are currently available.",
                remediation="Use Runtime Status to determine whether existing TalkingSlides services own these ports." if occupied else "",
                diagnostic_data={"occupied": occupied},
            )
        ]

    @staticmethod
    def _profile_checks(repository: Path | None, profile: Profile, full: bool) -> list[CheckResult]:
        if not repository:
            return [
                CheckResult(
                    "profile.repository_required",
                    "Profile assets",
                    "Profile",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Repository required",
                    profile=profile.value,
                )
            ]
        if profile is Profile.CORE:
            return [
                CheckResult(
                    "profile.core",
                    "Core profile",
                    "Profile",
                    CheckStatus.PASS,
                    Severity.INFO,
                    "Core checks do not require avatar models or a GPU.",
                    profile=profile.value,
                )
            ]
        if profile is Profile.TTS:
            cache = repository / "storage_local" / "tts_cache"
            return [
                CheckResult(
                    "profile.tts_cache",
                    "TTS model cache",
                    "Profile",
                    CheckStatus.PASS if cache.is_dir() else CheckStatus.WARNING,
                    Severity.MEDIUM if not cache.is_dir() else Severity.INFO,
                    "TTS cache directory exists." if cache.is_dir() else "TTS cache directory was not found; nothing was downloaded.",
                    remediation="Provision the configured TTS model/cache before offline use." if not cache.is_dir() else "",
                    profile=profile.value,
                )
            ]
        model_root = repository / "storage_local" / "models"
        required = (
            "musetalk/musetalk.json",
            "musetalkV15/unet.pth",
            "whisper/config.json",
            "dwpose/dw-ll_ucoco_384.pth",
        )
        if not full:
            return [
                CheckResult(
                    "profile.avatar_models",
                    "Avatar model bundle",
                    "Profile",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Avatar model inventory is an expensive-profile Full Check. No model was read or downloaded.",
                    profile=profile.value,
                    expensive=True,
                )
            ]
        missing = [item for item in required if not (model_root / item).is_file()]
        return [
            CheckResult(
                "profile.avatar_models",
                "Avatar model bundle",
                "Profile",
                CheckStatus.WARNING if missing else CheckStatus.PASS,
                Severity.HIGH if missing else Severity.INFO,
                f"{len(missing)} required avatar model marker(s) are missing." if missing else "Required avatar model markers were found.",
                remediation="Provision models separately; the assistant will not download large models automatically." if missing else "",
                diagnostic_data={"missing_count": len(missing), "missing_relative_paths": missing},
                profile=profile.value,
                expensive=True,
            )
        ]

    @staticmethod
    def _health_checks(profile: Profile) -> list[CheckResult]:
        endpoints = [("API readiness", "http://127.0.0.1:8000/api/v1/ready/")]
        if profile in {Profile.TTS, Profile.AVATAR}:
            endpoints.append(("TTS readiness", "http://127.0.0.1:8001/ready"))
        results: list[CheckResult] = []
        for title, url in endpoints:
            started = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    status_code = response.status
                status = CheckStatus.PASS if 200 <= status_code < 400 else CheckStatus.WARNING
                summary = f"{title} returned HTTP {status_code}."
                details = ""
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                status_code = None
                status = CheckStatus.WARNING
                summary = f"{title} is not reachable."
                details = str(exc)
            results.append(
                CheckResult(
                    f"health.{title.lower().replace(' ', '_')}",
                    title,
                    "Runtime",
                    status,
                    Severity.MEDIUM if status is CheckStatus.WARNING else Severity.INFO,
                    summary,
                    technical_details=details,
                    remediation="Start the selected runtime profile or inspect Runtime Status." if status is CheckStatus.WARNING else "",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    diagnostic_data={"http_status": status_code},
                    profile=profile.value,
                )
            )
        return results

    @staticmethod
    def _skipped_health() -> list[CheckResult]:
        return [
            CheckResult(
                "health.endpoints",
                "Service health endpoints",
                "Runtime",
                CheckStatus.SKIPPED,
                Severity.INFO,
                "Endpoint probes run during Full Check.",
            )
        ]

    def _git_checks(self, repository: Path | None) -> list[CheckResult]:
        if not repository:
            return [
                CheckResult(
                    "git.repository_required",
                    "Git state",
                    "Installation & Configuration",
                    CheckStatus.SKIPPED,
                    Severity.INFO,
                    "Repository required",
                )
            ]
        result = self.runner.run(CommandSpec.create(("git", "status", "--short"), cwd=repository, timeout_seconds=8))
        if not result.ok:
            return [
                CheckResult(
                    "git.summary",
                    "Git state",
                    "Installation & Configuration",
                    CheckStatus.WARNING,
                    Severity.LOW,
                    "Git status could not be read.",
                    technical_details=_command_detail(result),
                )
            ]
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        staged = sum(1 for line in lines if line[:1] not in {" ", "?"})
        unstaged = sum(1 for line in lines if len(line) > 1 and line[1] != " ")
        untracked = sum(1 for line in lines if line.startswith("??"))
        return [
            CheckResult(
                "git.summary",
                "Git state",
                "Installation & Configuration",
                CheckStatus.WARNING if lines else CheckStatus.PASS,
                Severity.LOW,
                f"Working tree has {len(lines)} changed path(s)." if lines else "Working tree is clean.",
                remediation="Review local changes before replacing configuration files or packaging." if lines else "",
                diagnostic_data={"changed_count": len(lines), "staged_count": staged, "unstaged_count": unstaged, "untracked_count": untracked},
                duration_ms=result.duration_ms,
            )
        ]

    @staticmethod
    def _internet_check() -> list[CheckResult]:
        started = time.monotonic()
        try:
            with urllib.request.urlopen("https://www.docker.com/", timeout=3) as response:
                status_code = response.status
            ok = 200 <= status_code < 500
            detail = ""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ok = False
            status_code = None
            detail = str(exc)
        return [
            CheckResult(
                "internet.connectivity",
                "Internet connectivity",
                "Requirements",
                CheckStatus.PASS if ok else CheckStatus.WARNING,
                Severity.LOW,
                "Connectivity check succeeded." if ok else "Connectivity check did not succeed.",
                technical_details=detail,
                remediation="Check proxy/DNS settings if online setup is intended." if not ok else "",
                duration_ms=round((time.monotonic() - started) * 1000),
                diagnostic_data={"http_status": status_code},
            )
        ]

    @staticmethod
    def _skipped_internet() -> list[CheckResult]:
        return [
            CheckResult(
                "internet.connectivity",
                "Internet connectivity",
                "Requirements",
                CheckStatus.SKIPPED,
                Severity.INFO,
                "Internet access was not tested because it was not explicitly selected.",
            )
        ]
