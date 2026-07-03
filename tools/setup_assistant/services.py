from __future__ import annotations

import json
import os
import platform
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable

from .reports.sanitize import sanitize_text
from .repository import validate_repository
from .runner import CommandResult, CommandRunner, CommandSpec
from .status import ServiceStatus


class ServiceType(str, Enum):
    COMPOSE = "docker_compose"
    HOST = "host_application"
    EXTERNAL_HTTP = "external_http"
    CONFIGURATION = "configuration_only"


@dataclass(frozen=True)
class ServiceDefinition:
    service_id: str
    display_name: str
    category: str
    service_type: ServiceType
    optional: bool = False
    repository_required: bool = True
    compose_service: str = ""
    compose_profile: str = ""
    health_url: str = ""
    public_url: str = ""
    expected_ports: tuple[int, ...] = ()
    configuration_requirements: tuple[str, ...] = ()
    supported_actions: tuple[str, ...] = ()
    manual_guidance: str = ""
    documentation_reference: str = ""


@dataclass(frozen=True)
class ComposeService:
    name: str
    profiles: tuple[str, ...] = ()
    ports: tuple[int, ...] = ()
    has_image: bool = False
    has_build: bool = False


@dataclass(frozen=True)
class ServiceGroup:
    group_id: str
    display_name: str
    profile: str
    service_ids: tuple[str, ...]
    requirements: str
    resource_usage: str
    optional: bool = False


@dataclass
class ServiceSnapshot:
    definition: ServiceDefinition
    status: ServiceStatus
    explanation: str
    last_checked: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: str = ""
    exit_code: int | None = None
    assistant_owned: bool = False


@dataclass
class ServiceOperationResult:
    service_id: str
    action: str
    executed: bool
    command_results: tuple[CommandResult, ...] = ()
    error: str = ""
    outcome: str = ""

    @property
    def ok(self) -> bool:
        return self.executed and bool(self.command_results) and all(item.ok for item in self.command_results)


SERVICE_REGISTRY: tuple[ServiceDefinition, ...] = (
    ServiceDefinition(
        "postgres",
        "PostgreSQL",
        "Data",
        ServiceType.COMPOSE,
        compose_service="postgres",
        expected_ports=(5432,),
        configuration_requirements=("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"),
        supported_actions=("start", "stop", "restart", "logs", "pull"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "redis",
        "Redis",
        "Data",
        ServiceType.COMPOSE,
        compose_service="redis",
        expected_ports=(6379,),
        supported_actions=("start", "stop", "restart", "logs", "pull"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "minio",
        "MinIO",
        "Data",
        ServiceType.COMPOSE,
        compose_service="minio",
        public_url="http://localhost:9001",
        expected_ports=(9000, 9001),
        configuration_requirements=("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"),
        supported_actions=("start", "stop", "restart", "logs", "pull"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "api",
        "TalkingSlides API",
        "Core",
        ServiceType.COMPOSE,
        compose_service="api",
        health_url="http://127.0.0.1:8000/api/v1/ready/",
        public_url="http://localhost:8000",
        expected_ports=(8000,),
        configuration_requirements=("SECRET_KEY", "MEDIA_TOKEN_SECRET"),
        supported_actions=("start", "stop", "restart", "logs", "build"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "frontend",
        "TalkingSlides Web",
        "Core",
        ServiceType.COMPOSE,
        compose_service="frontend",
        public_url="http://localhost:3000",
        expected_ports=(3000,),
        supported_actions=("start", "stop", "restart", "logs", "pull"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "tts_service",
        "Text-to-Speech",
        "Media / TTS",
        ServiceType.COMPOSE,
        compose_service="tts_service",
        health_url="http://127.0.0.1:8001/ready",
        public_url="http://localhost:8001",
        expected_ports=(8001,),
        supported_actions=("start", "stop", "restart", "logs", "build"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "worker",
        "Render Worker",
        "Media / TTS",
        ServiceType.COMPOSE,
        compose_service="worker",
        supported_actions=("start", "stop", "restart", "logs", "build"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "worker-avatar",
        "Avatar GPU Worker",
        "Optional integrations",
        ServiceType.COMPOSE,
        optional=True,
        compose_service="worker-avatar",
        compose_profile="avatar",
        supported_actions=("start", "stop", "restart", "logs", "build"),
        manual_guidance="Requires NVIDIA GPU, Docker GPU support, and provisioned avatar models.",
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "libretranslate",
        "LibreTranslate",
        "Optional integrations",
        ServiceType.COMPOSE,
        optional=True,
        compose_service="libretranslate",
        compose_profile="translation",
        health_url="http://127.0.0.1:5000/languages",
        public_url="http://localhost:5000",
        expected_ports=(5000,),
        supported_actions=("start", "stop", "restart", "logs", "pull"),
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md",
    ),
    ServiceDefinition(
        "ollama",
        "Ollama",
        "Optional integrations",
        ServiceType.HOST,
        optional=True,
        repository_required=False,
        health_url="http://127.0.0.1:11434/api/tags",
        public_url="http://localhost:11434",
        expected_ports=(11434,),
        supported_actions=("start", "stop", "logs"),
        manual_guidance="Install Ollama from https://ollama.com/download. Model downloads always require a separate explicit action.",
        documentation_reference="docs/FULL_STACK_LOCAL_RUNTIME.md#intelligence--ollama",
    ),
    ServiceDefinition(
        "translation-api",
        "External Translation API",
        "Optional integrations",
        ServiceType.EXTERNAL_HTTP,
        optional=True,
        configuration_requirements=(
            "SUBTITLE_TRANSLATION_API_PROVIDER",
            "SUBTITLE_TRANSLATION_API_BASE_URL",
            "SUBTITLE_TRANSLATION_API_KEY",
            "SUBTITLE_TRANSLATION_API_MODEL",
        ),
        manual_guidance="Configure all translation API variables only when an external provider is intentionally enabled.",
        documentation_reference="docs/ENVIRONMENT_VARIABLES.md",
    ),
    ServiceDefinition(
        "google-oauth",
        "Google OAuth",
        "Configuration",
        ServiceType.CONFIGURATION,
        optional=True,
        configuration_requirements=("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
        manual_guidance="Create OAuth credentials manually and place them only in infra/.env.",
        documentation_reference="docs/ENVIRONMENT_VARIABLES.md",
    ),
)

GROUP_REGISTRY: tuple[ServiceGroup, ...] = (
    ServiceGroup(
        "core",
        "Core",
        "core",
        ("postgres", "redis", "minio", "api", "frontend"),
        "Docker Desktop or Docker Engine with Compose v2",
        "Moderate CPU and memory; no GPU required.",
    ),
    ServiceGroup(
        "tts",
        "Media / TTS",
        "tts",
        ("postgres", "redis", "minio", "api", "tts_service", "worker", "frontend"),
        "Core requirements plus provisioned TTS image/model cache",
        "Higher memory and CPU use.",
    ),
    ServiceGroup(
        "translation",
        "Optional translation",
        "translation",
        ("postgres", "redis", "minio", "api", "frontend", "libretranslate"),
        "Core requirements and network access for an explicit image pull if unavailable",
        "Additional memory; no GPU required.",
        optional=True,
    ),
    ServiceGroup(
        "avatar",
        "Avatar",
        "avatar",
        ("postgres", "redis", "minio", "api", "tts_service", "worker", "worker-avatar", "frontend"),
        "NVIDIA GPU, Docker GPU runtime, avatar-capable image, and local model bundle",
        "High GPU, disk, and memory use. Can consume queued avatar work.",
        optional=True,
    ),
    ServiceGroup(
        "full",
        "Full supported environment",
        "full",
        (
            "postgres",
            "redis",
            "minio",
            "api",
            "tts_service",
            "worker",
            "worker-avatar",
            "libretranslate",
            "frontend",
        ),
        "All Core, TTS, translation, and avatar requirements",
        "Highest resource usage; optional heavy services are included.",
        optional=True,
    ),
)


def _service_blocks(compose_file: Path) -> dict[str, list[str]]:
    lines = compose_file.read_text(encoding="utf-8").splitlines()
    blocks: dict[str, list[str]] = {}
    in_services = False
    current = ""
    for line in lines:
        if line.rstrip() == "services:":
            in_services = True
            current = ""
            continue
        if not in_services:
            continue
        if line and not line.startswith((" ", "\t", "#")):
            break
        match = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):(?:\s*(?:#.*)?)$", line)
        if match:
            current = match.group(1)
            blocks[current] = []
            continue
        if current:
            blocks[current].append(line)
    return blocks


def discover_compose_services(repository: Path) -> dict[str, ComposeService]:
    validation = validate_repository(repository)
    if not validation.valid:
        raise ValueError("Repository required")
    compose_file = validation.path / "infra" / "docker-compose.yml"
    discovered: dict[str, ComposeService] = {}
    for name, lines in _service_blocks(compose_file).items():
        profiles: list[str] = []
        ports: list[int] = []
        in_profiles = False
        in_ports = False
        has_image = False
        has_build = False
        for line in lines:
            stripped = line.strip()
            indentation = len(line) - len(line.lstrip(" "))
            if indentation == 4:
                in_profiles = stripped == "profiles:"
                in_ports = stripped == "ports:"
                has_image = has_image or stripped.startswith("image:")
                has_build = has_build or stripped.startswith("build:")
                continue
            if indentation >= 6 and stripped.startswith("-"):
                value = stripped[1:].strip().strip("\"'")
                if in_profiles and value:
                    profiles.append(value)
                if in_ports:
                    port_match = re.match(r"(?:[^:]+:)?(\d+):(\d+)", value)
                    if port_match:
                        ports.append(int(port_match.group(1)))
        discovered[name] = ComposeService(
            name,
            tuple(dict.fromkeys(profiles)),
            tuple(dict.fromkeys(ports)),
            has_image,
            has_build,
        )
    return discovered


def available_service_registry(repository: Path | None) -> tuple[ServiceDefinition, ...]:
    if repository is None:
        return SERVICE_REGISTRY
    try:
        compose_services = discover_compose_services(repository)
    except (OSError, ValueError):
        return SERVICE_REGISTRY
    return tuple(
        item
        for item in SERVICE_REGISTRY
        if item.service_type is not ServiceType.COMPOSE or item.compose_service in compose_services
    )


def available_service_groups(repository: Path) -> tuple[ServiceGroup, ...]:
    validation = validate_repository(repository)
    if not validation.valid:
        return ()
    script = (validation.path / "scripts" / "windows-runtime.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$ValidProfiles\s*=\s*@\((?P<profiles>[^)]+)\)", script)
    if not match:
        return ()
    profiles = set(re.findall(r'"([^"]+)"', match.group("profiles")))
    compose_services = discover_compose_services(validation.path)
    return tuple(
        group
        for group in GROUP_REGISTRY
        if group.profile in profiles
        and all(service_id in compose_services for service_id in group.service_ids)
    )


class ServiceController:
    def __init__(self, repository: Path | None, runner: CommandRunner | None = None) -> None:
        validation = validate_repository(repository) if repository else None
        self.repository = validation.path if validation and validation.valid else None
        self.runner = runner or CommandRunner()
        self._active: set[str] = set()
        self._active_lock = threading.Lock()

    @staticmethod
    def definition(service_id: str) -> ServiceDefinition:
        for definition in SERVICE_REGISTRY:
            if definition.service_id == service_id:
                return definition
        raise KeyError(service_id)

    def _compose_base(self, definition: ServiceDefinition) -> list[str]:
        if not self.repository:
            raise ValueError("Repository required")
        argv = [
            "docker",
            "compose",
            "-f",
            os.fspath(self.repository / "infra" / "docker-compose.yml"),
            "--project-directory",
            os.fspath(self.repository),
        ]
        if definition.compose_profile:
            argv.extend(("--profile", definition.compose_profile))
        return argv

    def command_specs(self, service_id: str, action: str, *, tail: int = 200) -> tuple[CommandSpec, ...]:
        definition = self.definition(service_id)
        if definition.repository_required and not self.repository:
            raise ValueError("Repository required")
        if action not in definition.supported_actions and action != "status":
            raise ValueError(f"{action.title()} is not supported for {definition.display_name}.")
        if definition.service_type is not ServiceType.COMPOSE:
            raise ValueError(f"{definition.display_name} uses a host adapter.")
        base = self._compose_base(definition)
        service = definition.compose_service
        commands: list[list[str]]
        if action == "start":
            commands = [[*base, "up", "-d", "--no-build", "--pull", "never", service]]
        elif action == "stop":
            commands = [[*base, "stop", service]]
        elif action == "restart":
            commands = [
                [*base, "stop", service],
                [*base, "up", "-d", "--no-build", "--pull", "never", service],
            ]
        elif action == "status":
            commands = [[*base, "ps", "--format", "json", service]]
        elif action == "logs":
            bounded_tail = min(max(int(tail), 1), 500)
            commands = [[*base, "logs", "--no-color", "--timestamps", "--tail", str(bounded_tail), service]]
        elif action == "pull":
            commands = [[*base, "pull", service]]
        elif action == "build":
            commands = [[*base, "build", service]]
        else:
            raise ValueError(f"Unsupported service action: {action}")
        timeout = 900 if action in {"pull", "build"} else (120 if action in {"start", "stop", "restart"} else 30)
        return tuple(CommandSpec.create(argv, cwd=self.repository, timeout_seconds=timeout) for argv in commands)

    def preview(self, service_id: str, action: str) -> tuple[str, ...]:
        return tuple(
            CommandResult(spec.argv, os.fspath(spec.cwd) if spec.cwd else None, None, "", "", 0).display_command
            for spec in self.command_specs(service_id, action)
        )

    def _begin(self, service_id: str) -> bool:
        with self._active_lock:
            if service_id in self._active:
                return False
            self._active.add(service_id)
            return True

    def _end(self, service_id: str) -> None:
        with self._active_lock:
            self._active.discard(service_id)

    def operation_active(self, service_id: str) -> bool:
        with self._active_lock:
            return service_id in self._active

    def execute(self, service_id: str, action: str, *, confirmed: bool) -> ServiceOperationResult:
        definition = self.definition(service_id)
        if action in {"start", "stop", "restart", "pull", "build"} and not confirmed:
            return ServiceOperationResult(service_id, action, False, error="Explicit confirmation is required.")
        if definition.service_type is ServiceType.HOST:
            return self._execute_host(definition, action, confirmed=confirmed)
        if not self._begin(service_id):
            return ServiceOperationResult(service_id, action, False, error="A conflicting operation is already running.")
        results: list[CommandResult] = []
        try:
            for spec in self.command_specs(service_id, action):
                result = self.runner.run(spec)
                results.append(result)
                if not result.ok:
                    break
        except (OSError, ValueError) as exc:
            return ServiceOperationResult(service_id, action, False, error=str(exc))
        finally:
            self._end(service_id)
        combined = "\n".join(item.stderr or item.stdout or item.error for item in results)
        outcome = self._operation_outcome(action, combined, all(item.ok for item in results))
        return ServiceOperationResult(
            service_id,
            action,
            True,
            tuple(results),
            "" if all(item.ok for item in results) else sanitize_text(combined).strip(),
            outcome,
        )

    @staticmethod
    def _operation_outcome(action: str, output: str, ok: bool) -> str:
        lowered = output.lower()
        if ok:
            return f"{action.title()} completed."
        if "no such image" in lowered or "pull access denied" in lowered:
            return "Image unavailable"
        if "requires build" in lowered or ("no image" in lowered and "build" in lowered):
            return "Build required"
        return f"{action.title()} failed."

    def _execute_host(
        self,
        definition: ServiceDefinition,
        action: str,
        *,
        confirmed: bool,
    ) -> ServiceOperationResult:
        if definition.service_id != "ollama":
            return ServiceOperationResult(definition.service_id, action, False, error="No safe host adapter is available.")
        from .ollama import OllamaManager

        manager = OllamaManager(self.runner)
        if action == "start":
            result = manager.start(confirmed=confirmed)
        elif action == "stop":
            result = manager.stop(confirmed=confirmed)
        else:
            return ServiceOperationResult(definition.service_id, action, False, error="Use the Ollama details view.")
        command_results = (result.command_result,) if result.command_result else ()
        return ServiceOperationResult(
            definition.service_id,
            action,
            result.executed,
            command_results,
            result.error,
            result.summary,
        )

    def inspect(self, service_id: str) -> ServiceSnapshot:
        definition = self.definition(service_id)
        if definition.service_type is ServiceType.HOST and service_id == "ollama":
            from .ollama import OllamaManager

            state = OllamaManager(self.runner).inspect(self.repository)
            return ServiceSnapshot(
                definition,
                state.status,
                state.summary,
                details=state.details,
                assistant_owned=state.assistant_owned,
            )
        if definition.service_type in {ServiceType.EXTERNAL_HTTP, ServiceType.CONFIGURATION}:
            if not self.repository:
                return ServiceSnapshot(definition, ServiceStatus.BLOCKED, "Repository required")
            present = self._configured_variable_names()
            missing = tuple(
                variable for variable in definition.configuration_requirements if variable not in present
            )
            if missing:
                return ServiceSnapshot(
                    definition,
                    ServiceStatus.NOT_CONFIGURED,
                    f"{len(missing)} optional configuration variable(s) are missing.",
                    details="Missing variable names: " + ", ".join(missing),
                )
            summary = (
                "Configuration is present; external endpoint calls remain feature-triggered."
                if definition.service_type is ServiceType.EXTERNAL_HTTP
                else "Required configuration variable names are present."
            )
            return ServiceSnapshot(definition, ServiceStatus.HEALTHY, summary)
        if definition.repository_required and not self.repository:
            return ServiceSnapshot(definition, ServiceStatus.BLOCKED, "Repository required")
        try:
            result = self.runner.run(self.command_specs(service_id, "status")[0])
        except (OSError, ValueError) as exc:
            return ServiceSnapshot(definition, ServiceStatus.BLOCKED, str(exc))
        if not result.ok:
            return ServiceSnapshot(
                definition,
                ServiceStatus.FAILED,
                "Docker Compose status failed.",
                details=sanitize_text(result.stderr or result.error),
                exit_code=result.exit_code,
            )
        records = self._parse_compose_ps(result.stdout)
        if not records:
            status = ServiceStatus.OPTIONAL if definition.optional else ServiceStatus.STOPPED
            explanation = "Optional service is not running." if definition.optional else "Service is stopped."
            return ServiceSnapshot(definition, status, explanation, exit_code=result.exit_code)
        record = records[0]
        state = str(record.get("State") or record.get("state") or "").lower()
        health = str(record.get("Health") or record.get("health") or "").lower()
        if state == "running" and health == "healthy":
            status = ServiceStatus.HEALTHY
            explanation = "Running and healthy."
        elif state == "running" and health == "starting":
            status = ServiceStatus.STARTING
            explanation = "Running; health check is still starting."
        elif state == "running" and health == "unhealthy":
            status = ServiceStatus.DEGRADED
            explanation = "Running but unhealthy."
        elif state == "running":
            status = ServiceStatus.RUNNING
            explanation = "Running."
        elif state in {"restarting", "created"}:
            status = ServiceStatus.DEGRADED
            explanation = f"Compose state: {state}."
        else:
            status = ServiceStatus.STOPPED
            explanation = f"Compose state: {state or 'stopped'}."
        return ServiceSnapshot(
            definition,
            status,
            explanation,
            details=json.dumps(record, sort_keys=True),
            exit_code=result.exit_code,
        )

    def _configured_variable_names(self) -> set[str]:
        if not self.repository:
            return set()
        env_file = self.repository / "infra" / ".env"
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        names: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if value.strip():
                names.add(name.strip())
        return names

    @staticmethod
    def _parse_compose_ps(output: str) -> list[dict[str, object]]:
        text = output.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, dict):
                return [payload]
        except ValueError:
            pass
        records: list[dict[str, object]] = []
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                records.append(item)
        return records

    def inspect_all(self) -> tuple[ServiceSnapshot, ...]:
        definitions = available_service_registry(self.repository)
        return tuple(self.inspect(item.service_id) for item in definitions)

    def logs(self, service_id: str, *, tail: int = 200) -> ServiceOperationResult:
        definition = self.definition(service_id)
        if definition.service_type is ServiceType.HOST and service_id == "ollama":
            from .ollama import OllamaManager

            text = OllamaManager.owned_logs() or "No assistant-owned Ollama startup output is available."
            result = CommandResult(("ollama", "serve"), None, 0, sanitize_text(text), "", 0)
            return ServiceOperationResult(service_id, "logs", True, (result,), outcome="Logs loaded.")
        result = self.execute(service_id, "logs", confirmed=True)
        if not result.command_results:
            return result
        command = result.command_results[-1]
        lines = sanitize_text(command.stdout + command.stderr).splitlines()[-500:]
        command.stdout = "\n".join(lines)
        command.stderr = ""
        return result

    def follow_logs_spec(self, service_id: str, *, tail: int = 50) -> CommandSpec:
        definition = self.definition(service_id)
        if definition.service_type is not ServiceType.COMPOSE:
            raise ValueError("Live follow is only available for Docker Compose services.")
        bounded_tail = min(max(int(tail), 1), 500)
        argv = [
            *self._compose_base(definition),
            "logs",
            "--no-color",
            "--timestamps",
            "--tail",
            str(bounded_tail),
            "--follow",
            definition.compose_service,
        ]
        return CommandSpec.create(argv, cwd=self.repository, timeout_seconds=86400)

    def group_specs(self, group_id: str, action: str) -> tuple[CommandSpec, ...]:
        if not self.repository:
            raise ValueError("Repository required")
        groups = {group.group_id: group for group in available_service_groups(self.repository)}
        if group_id not in groups:
            raise ValueError(f"Unsupported service group: {group_id}")
        if action not in {"start", "stop"}:
            raise ValueError(f"Unsupported group action: {action}")
        group = groups[group_id]
        if platform.system() == "Windows":
            argv = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                os.fspath(self.repository / "scripts" / "windows-runtime.ps1"),
                "-Profile",
                group.profile,
            ]
            if action == "stop":
                argv.append("-Stop")
            return (CommandSpec.create(argv, cwd=self.repository, timeout_seconds=180),)
        profiles: list[str] = []
        if "worker-avatar" in group.service_ids:
            profiles.extend(("--profile", "avatar"))
        if "libretranslate" in group.service_ids:
            profiles.extend(("--profile", "translation"))
        base = [
            "docker",
            "compose",
            "-f",
            os.fspath(self.repository / "infra" / "docker-compose.yml"),
            "--project-directory",
            os.fspath(self.repository),
            *profiles,
        ]
        if action == "start":
            argv = [*base, "up", "-d", "--no-build", "--pull", "never", *group.service_ids]
        else:
            argv = [*base, "stop", *group.service_ids]
        return (CommandSpec.create(argv, cwd=self.repository, timeout_seconds=180),)

    def execute_group(self, group_id: str, action: str, *, confirmed: bool) -> ServiceOperationResult:
        operation_id = f"group:{group_id}"
        if not confirmed:
            return ServiceOperationResult(operation_id, action, False, error="Explicit confirmation is required.")
        if not self._begin(operation_id):
            return ServiceOperationResult(operation_id, action, False, error="A conflicting group operation is already running.")
        try:
            results = tuple(self.runner.run(spec) for spec in self.group_specs(group_id, action))
        except (OSError, ValueError) as exc:
            return ServiceOperationResult(operation_id, action, False, error=str(exc))
        finally:
            self._end(operation_id)
        error = "\n".join(item.stderr or item.error for item in results if not item.ok)
        return ServiceOperationResult(
            operation_id,
            action,
            True,
            results,
            sanitize_text(error),
            self._operation_outcome(action, error, all(item.ok for item in results)),
        )


def registry_errors(registry: Iterable[ServiceDefinition] = SERVICE_REGISTRY) -> tuple[str, ...]:
    errors: list[str] = []
    seen: set[str] = set()
    for item in registry:
        if not item.service_id or item.service_id in seen:
            errors.append(f"Duplicate or empty service ID: {item.service_id}")
        seen.add(item.service_id)
        if item.service_type is ServiceType.COMPOSE and not item.compose_service:
            errors.append(f"Compose service missing for {item.service_id}")
        if not item.display_name or not item.category:
            errors.append(f"Display metadata missing for {item.service_id}")
        if "stop" in item.supported_actions and item.service_type is ServiceType.HOST and item.service_id != "ollama":
            errors.append(f"Unsafe host stop action declared for {item.service_id}")
    return tuple(errors)
