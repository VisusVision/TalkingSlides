from __future__ import annotations

from pathlib import Path

from tools.setup_assistant.runner import CommandResult
from tools.setup_assistant.services import (
    SERVICE_REGISTRY,
    ServiceController,
    ServiceStatus,
    available_service_groups,
    discover_compose_services,
    registry_errors,
)
from tools.setup_assistant.status import STATUS_PRESENTATIONS, status_presentation, status_text


class MappingRunner:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.specs = []

    def run(self, spec, cancel_event=None, output_callback=None):
        self.specs.append(spec)
        return CommandResult(spec.argv, str(spec.cwd), 0, self.stdout, "", 1)


def test_every_service_status_has_accessible_presentation() -> None:
    assert set(STATUS_PRESENTATIONS) == set(ServiceStatus)
    for status in ServiceStatus:
        presentation = status_presentation(status)
        assert presentation.icon
        assert presentation.label
        assert presentation.description
        assert presentation.light_color.startswith("#")
        assert presentation.dark_color.startswith("#")
        assert presentation.label in status_text(status)
        assert presentation.icon in status_text(status)


def test_status_roles_support_light_and_dark_appearance() -> None:
    for status in ServiceStatus:
        light = status_presentation(status, dark=False)
        dark = status_presentation(status, dark=True)
        assert light.light_color
        assert dark.light_color == light.dark_color


def test_declarative_registry_is_valid_and_unique() -> None:
    assert not registry_errors()
    assert len({item.service_id for item in SERVICE_REGISTRY}) == len(SERVICE_REGISTRY)
    assert {item.service_type.value for item in SERVICE_REGISTRY} == {
        "docker_compose",
        "host_application",
        "external_http",
        "configuration_only",
    }


def test_real_compose_services_and_profiles_are_discovered() -> None:
    repository = Path(__file__).resolve().parents[2]
    services = discover_compose_services(repository)
    assert set(services) == {
        "api",
        "tts_service",
        "worker",
        "worker-avatar",
        "redis",
        "postgres",
        "minio",
        "libretranslate",
        "frontend",
    }
    assert services["worker-avatar"].profiles == ("avatar",)
    assert services["libretranslate"].profiles == ("translation",)
    assert services["frontend"].ports == (3000,)


def test_only_existing_runtime_groups_are_exposed() -> None:
    repository = Path(__file__).resolve().parents[2]
    groups = available_service_groups(repository)
    assert {item.group_id for item in groups} == {"core", "tts", "translation", "avatar", "full"}


def test_repository_gating_is_explicit() -> None:
    snapshot = ServiceController(None, MappingRunner()).inspect("api")
    assert snapshot.status is ServiceStatus.BLOCKED
    assert snapshot.explanation == "Repository required"


def test_start_stop_restart_commands_are_nondestructive(talking_slides_repo: Path) -> None:
    controller = ServiceController(talking_slides_repo, MappingRunner())
    start = controller.command_specs("api", "start")[0].argv
    assert start[-6:] == ("up", "-d", "--no-build", "--pull", "never", "api")
    assert "down" not in start
    assert "-v" not in start
    restart = controller.command_specs("api", "restart")
    assert len(restart) == 2
    assert "stop" in restart[0].argv
    assert "--no-build" in restart[1].argv
    assert controller.command_specs("api", "stop")[0].argv[-2:] == ("stop", "api")


def test_start_never_implicitly_builds_or_pulls(talking_slides_repo: Path) -> None:
    argv = ServiceController(talking_slides_repo, MappingRunner()).command_specs("tts_service", "start")[0].argv
    assert "--no-build" in argv
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[-1] == "tts_service"
    assert "build" not in argv


def test_operation_conflict_prevention(talking_slides_repo: Path) -> None:
    controller = ServiceController(talking_slides_repo, MappingRunner())
    controller._active.add("api")
    result = controller.execute("api", "start", confirmed=True)
    assert not result.executed
    assert "conflicting" in result.error


def test_compose_health_state_transitions(talking_slides_repo: Path) -> None:
    cases = (
        ('{"State":"running","Health":"healthy"}', ServiceStatus.HEALTHY),
        ('{"State":"running","Health":"starting"}', ServiceStatus.STARTING),
        ('{"State":"running","Health":"unhealthy"}', ServiceStatus.DEGRADED),
        ('{"State":"exited"}', ServiceStatus.STOPPED),
    )
    for output, expected in cases:
        snapshot = ServiceController(talking_slides_repo, MappingRunner(output)).inspect("api")
        assert snapshot.status is expected


def test_bounded_logs_and_secret_redaction(talking_slides_repo: Path) -> None:
    output = "\n".join([f"line-{index}" for index in range(600)] + ["API_TOKEN=do-not-show"])
    result = ServiceController(talking_slides_repo, MappingRunner(output)).logs("api", tail=9999)
    assert result.command_results
    text = result.command_results[0].stdout
    assert len(text.splitlines()) <= 500
    assert "do-not-show" not in text
    spec = ServiceController(talking_slides_repo, MappingRunner()).command_specs("api", "logs", tail=9999)[0]
    assert spec.argv[spec.argv.index("--tail") + 1] == "500"


def test_group_commands_use_supported_runtime_script_on_windows(
    monkeypatch,
    talking_slides_repo: Path,
) -> None:
    compose = talking_slides_repo / "infra" / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  postgres:\n    image: postgres\n"
        "  redis:\n    image: redis\n"
        "  minio:\n    image: minio\n"
        "  api:\n    image: api\n"
        "  frontend:\n    image: frontend\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.setup_assistant.services.platform.system", lambda: "Windows")
    spec = ServiceController(talking_slides_repo, MappingRunner()).group_specs("core", "start")[0]
    assert "windows-runtime.ps1" in " ".join(spec.argv)
    assert "-Profile" in spec.argv
    assert "core" in spec.argv
