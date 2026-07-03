from __future__ import annotations

from pathlib import Path

from tools.setup_assistant import ollama
from tools.setup_assistant.configuration import action_required_items, inspect_configuration
from tools.setup_assistant.ollama import OllamaManager
from tools.setup_assistant.runner import CommandResult
from tools.setup_assistant.status import ServiceStatus


class Runner:
    def run(self, spec, cancel_event=None, output_callback=None):
        return CommandResult(spec.argv, str(spec.cwd) if spec.cwd else None, 0, "ollama version 1.0\n", "", 1)


class Response:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload.encode("utf-8")


def manager(monkeypatch, payload: str, executable: Path | None = None) -> OllamaManager:
    value = OllamaManager(Runner(), urlopen=lambda *args, **kwargs: Response(payload))
    monkeypatch.setattr(value, "locate_executable", lambda: executable)
    monkeypatch.setattr(value, "_port_open", lambda: False)
    monkeypatch.setattr(value, "_management_hint", lambda: "")
    return value


def test_ollama_not_installed_is_optional(monkeypatch) -> None:
    value = manager(monkeypatch, "not-json", None).inspect()
    assert value.status is ServiceStatus.NOT_CONFIGURED
    assert not value.installed
    assert not value.required


def test_ollama_installed_but_stopped(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "ollama"
    executable.touch()
    value = manager(monkeypatch, "not-json", executable).inspect()
    assert value.status is ServiceStatus.STOPPED
    assert value.installed
    assert not value.running


def test_ollama_healthy_and_no_models(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "ollama"
    executable.touch()
    healthy = manager(monkeypatch, '{"models":[{"name":"qwen2.5:3b"}]}', executable).inspect()
    assert healthy.status is ServiceStatus.HEALTHY
    empty = manager(monkeypatch, '{"models":[]}', executable).inspect()
    assert empty.status is ServiceStatus.DEGRADED
    assert "no models" in empty.summary


def test_ollama_required_model_missing(monkeypatch, talking_slides_repo: Path, tmp_path: Path) -> None:
    (talking_slides_repo / "infra" / ".env").write_text(
        "ENABLE_LOCAL_OLLAMA=true\nOLLAMA_LESSON_INTELLIGENCE_MODEL=qwen2.5:7b\n",
        encoding="utf-8",
    )
    executable = tmp_path / "ollama"
    executable.touch()
    value = manager(monkeypatch, '{"models":[{"name":"qwen2.5:3b"}]}', executable).inspect(talking_slides_repo)
    assert value.status is ServiceStatus.DEGRADED
    assert value.required
    assert value.missing_models == ("qwen2.5:7b",)


def test_ollama_unhealthy_and_port_conflict(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "ollama"
    executable.touch()
    value = manager(monkeypatch, '{"unexpected":[]}', executable)
    monkeypatch.setattr(value, "_port_open", lambda: True)
    state = value.inspect()
    assert state.status is ServiceStatus.FAILED
    assert "Port conflict" in state.summary


def test_user_owned_ollama_cannot_be_stopped(monkeypatch) -> None:
    monkeypatch.setattr(ollama, "_owned_process", None)
    result = OllamaManager(Runner()).stop(confirmed=True)
    assert not result.executed
    assert "does not own" in result.error


def test_assistant_owned_ollama_can_be_stopped(monkeypatch) -> None:
    class Process:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    process = Process()
    monkeypatch.setattr(ollama, "_owned_process", process)
    result = OllamaManager(Runner()).stop(confirmed=True)
    assert result.executed
    assert result.command_result and result.command_result.ok
    assert ollama._owned_process is None


def test_configuration_reports_presence_without_values(talking_slides_repo: Path) -> None:
    secret = "super-secret-value"
    (talking_slides_repo / "infra" / ".env").write_text(
        f"SECRET_KEY={secret}\nOPENAI_API_KEY={secret}\nREDIS_URL=redis://redis:6379/0\n",
        encoding="utf-8",
    )
    statuses = inspect_configuration(talking_slides_repo)
    secret_status = next(item for item in statuses if item.variable == "SECRET_KEY")
    optional_status = next(item for item in statuses if item.variable == "OPENAI_API_KEY")
    missing_status = next(item for item in statuses if item.variable == "MEDIA_TOKEN_SECRET")
    assert secret_status.present and secret_status.valid and secret_status.secret
    assert optional_status.present and not optional_status.required
    assert not missing_status.present and missing_status.required
    assert secret not in repr(statuses)
    assert secret not in repr([item.to_dict() for item in statuses])


def test_action_required_classifies_missing_env(talking_slides_repo: Path) -> None:
    items = action_required_items(talking_slides_repo)
    item = next(value for value in items if value.title == "Environment file missing")
    assert item.automatic_action == "config.create_env"


def test_enabled_feature_makes_provider_key_required(talking_slides_repo: Path) -> None:
    (talking_slides_repo / "infra" / ".env").write_text(
        "TTS_LLM_SUGGESTIONS_ENABLED=true\nTTS_LLM_PROVIDER=openai\n",
        encoding="utf-8",
    )
    status = next(
        item for item in inspect_configuration(talking_slides_repo) if item.variable == "OPENAI_API_KEY"
    )
    assert status.required
    assert not status.present


def test_env_creation_refuses_overwrite(talking_slides_repo: Path) -> None:
    from tools.setup_assistant.actions import SafeActionExecutor

    target = talking_slides_repo / "infra" / ".env"
    target.write_text("KEEP=this\n", encoding="utf-8")
    result = SafeActionExecutor().execute("config.create_env", talking_slides_repo, confirmed=True)
    assert not result.changed
    assert target.read_text(encoding="utf-8") == "KEEP=this\n"
