from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .reports.sanitize import sanitize_text
from .runner import CommandResult, CommandRunner, CommandSpec
from .status import ServiceStatus

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_MODEL_KEYS = (
    "OLLAMA_PRONUNCIATION_MODEL",
    "OLLAMA_LESSON_INTELLIGENCE_MODEL",
    "OLLAMA_ANALYTICS_INTELLIGENCE_MODEL",
    "OLLAMA_TRANSLATION_MODEL",
)
_owned_process: subprocess.Popen | None = None
_owned_output: deque[str] = deque(maxlen=500)
_owned_lock = threading.Lock()


@dataclass(frozen=True)
class OllamaState:
    status: ServiceStatus
    summary: str
    installed: bool
    running: bool
    required: bool
    executable: Path | None = None
    version: str = ""
    models: tuple[str, ...] = ()
    required_models: tuple[str, ...] = ()
    missing_models: tuple[str, ...] = ()
    assistant_owned: bool = False
    details: str = ""


@dataclass
class OllamaActionResult:
    action: str
    executed: bool
    summary: str
    error: str = ""
    command_result: CommandResult | None = None


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def configured_ollama_models(repository: Path | None) -> tuple[str, ...]:
    if not repository:
        return ()
    values = _read_env_file(repository / "infra" / ".env")
    models = [values.get(key, "").strip() for key in OLLAMA_MODEL_KEYS]
    return tuple(dict.fromkeys(model for model in models if model))


def ollama_required(repository: Path | None) -> bool:
    if not repository:
        return False
    values = _read_env_file(repository / "infra" / ".env")
    truthy = {"1", "true", "yes", "on"}
    if values.get("ENABLE_LOCAL_OLLAMA", "").lower() in truthy:
        return True
    if (
        values.get("TTS_LLM_SUGGESTIONS_ENABLED", "").lower() in truthy
        and values.get("TTS_LLM_PROVIDER", "").lower() == "ollama"
    ):
        return True
    chains = (
        values.get("LESSON_INTELLIGENCE_PROVIDER_CHAIN", ""),
        values.get("ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN", ""),
        values.get("SUBTITLE_TRANSLATION_PROVIDER_CHAIN", ""),
    )
    return any("ollama" in {part.strip().lower() for part in chain.split(",")} for chain in chains)


class OllamaManager:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        urlopen: Callable[..., object] | None = None,
        process_factory: Callable[..., subprocess.Popen] | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.urlopen = urlopen or urllib.request.urlopen
        self.process_factory = process_factory or subprocess.Popen

    @staticmethod
    def locate_executable() -> Path | None:
        found = shutil.which("ollama")
        if found:
            return Path(found).resolve()
        if os.name == "nt":
            candidates = (
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
            )
            for candidate in candidates:
                if candidate.is_file():
                    return candidate.resolve()
        return None

    def _version(self, executable: Path | None) -> str:
        if not executable:
            return ""
        result = self.runner.run(CommandSpec.create((executable, "--version"), timeout_seconds=8))
        return sanitize_text((result.stdout or result.stderr).strip().splitlines()[0]) if result.ok else ""

    def _probe(self) -> tuple[bool, tuple[str, ...], str]:
        try:
            with self.urlopen(OLLAMA_TAGS_URL, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError) as exc:
            return False, (), sanitize_text(str(exc))
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return False, (), "Port 11434 responded, but the response is not an Ollama model list."
        models: list[str] = []
        for item in payload["models"]:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if isinstance(name, str) and name:
                    models.append(name)
        return True, tuple(dict.fromkeys(models)), ""

    @staticmethod
    def _port_open() -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", 11434)) == 0

    @staticmethod
    def _is_owned() -> bool:
        with _owned_lock:
            return _owned_process is not None and _owned_process.poll() is None

    def inspect(self, repository: Path | None = None) -> OllamaState:
        executable = self.locate_executable()
        required = ollama_required(repository)
        required_models = configured_ollama_models(repository)
        healthy, models, detail = self._probe()
        owned = self._is_owned()
        version = self._version(executable)
        manager_hint = self._management_hint()
        if healthy:
            missing = tuple(model for model in required_models if model not in models)
            if not models:
                return OllamaState(
                    ServiceStatus.DEGRADED,
                    "Ollama is running but no models are installed.",
                    bool(executable),
                    True,
                    required,
                    executable,
                    version,
                    models,
                    required_models,
                    required_models,
                    owned,
                    " ".join(
                        part
                        for part in ("Model downloads are never started automatically.", manager_hint)
                        if part
                    ),
                )
            if missing:
                return OllamaState(
                    ServiceStatus.DEGRADED,
                    f"{len(missing)} configured Ollama model(s) are missing.",
                    bool(executable),
                    True,
                    required,
                    executable,
                    version,
                    models,
                    required_models,
                    missing,
                    owned,
                    " ".join(
                        part
                        for part in (
                            "Use an explicit `ollama pull <model>` after reviewing download size and network impact.",
                            manager_hint,
                        )
                        if part
                    ),
                )
            return OllamaState(
                ServiceStatus.HEALTHY,
                "Ollama is running and healthy.",
                bool(executable),
                True,
                required,
                executable,
                version,
                models,
                required_models,
                (),
                owned,
                manager_hint,
            )
        port_open = self._port_open()
        if port_open:
            return OllamaState(
                ServiceStatus.FAILED,
                "Port conflict or unhealthy Ollama endpoint on port 11434.",
                bool(executable),
                True,
                required,
                executable,
                version,
                required_models=required_models,
                assistant_owned=owned,
                details=" ".join(part for part in (detail, manager_hint) if part),
            )
        if not executable:
            status = ServiceStatus.BLOCKED if required else ServiceStatus.NOT_CONFIGURED
            summary = "Ollama is required but is not installed." if required else "Ollama is not installed; it is optional."
            return OllamaState(
                status,
                summary,
                False,
                False,
                required,
                required_models=required_models,
                details=" ".join(
                    part
                    for part in (
                        "Install manually from https://ollama.com/download. The assistant does not install Ollama.",
                        manager_hint,
                    )
                    if part
                ),
            )
        return OllamaState(
            ServiceStatus.STOPPED,
            "Ollama is installed but stopped.",
            True,
            False,
            required,
            executable,
            version,
            required_models=required_models,
            assistant_owned=owned,
            details=" ".join(part for part in (detail, manager_hint) if part),
        )

    def _management_hint(self) -> str:
        if os.name != "nt" and shutil.which("systemctl"):
            result = self.runner.run(
                CommandSpec.create(("systemctl", "is-active", "ollama"), timeout_seconds=5)
            )
            if result.ok and result.stdout.strip() == "active":
                return "Detected systemd-managed Ollama; use systemctl manually for lifecycle changes."
        if shutil.which("docker"):
            result = self.runner.run(
                CommandSpec.create(
                    ("docker", "ps", "--filter", "name=ollama", "--format", "{{.Names}}"),
                    timeout_seconds=5,
                )
            )
            if result.ok and result.stdout.strip():
                return "Detected a Docker-managed Ollama container; use its owning Compose project manually."
        return ""

    @staticmethod
    def _capture_output(stream) -> None:
        while True:
            line = stream.readline()
            if not line:
                return
            if isinstance(line, bytes):
                text = line.decode("utf-8", errors="replace")
            else:
                text = str(line)
            _owned_output.append(sanitize_text(text).rstrip())

    def start(self, *, confirmed: bool, timeout_seconds: float = 20) -> OllamaActionResult:
        if not confirmed:
            return OllamaActionResult("start", False, "", "Explicit confirmation is required.")
        current = self.inspect()
        if current.running and current.status is not ServiceStatus.FAILED:
            return OllamaActionResult("start", False, "Ollama is already running.")
        executable = current.executable or self.locate_executable()
        if not executable:
            return OllamaActionResult(
                "start",
                False,
                "",
                "Ollama is not installed. Install it manually from https://ollama.com/download.",
            )
        global _owned_process
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "creationflags": creationflags,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            process = self.process_factory([os.fspath(executable), "serve"], **kwargs)
        except (OSError, PermissionError) as exc:
            return OllamaActionResult("start", False, "", sanitize_text(str(exc)))
        with _owned_lock:
            _owned_process = process
            _owned_output.clear()
        for stream in (process.stdout, process.stderr):
            threading.Thread(target=self._capture_output, args=(stream,), daemon=True).start()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return OllamaActionResult(
                    "start",
                    True,
                    "Ollama exited during startup.",
                    "\n".join(_owned_output),
                    CommandResult((os.fspath(executable), "serve"), None, process.returncode, "", "\n".join(_owned_output), 0),
                )
            healthy, _models, _detail = self._probe()
            if healthy:
                return OllamaActionResult(
                    "start",
                    True,
                    "Ollama started and passed its health check.",
                    command_result=CommandResult((os.fspath(executable), "serve"), None, 0, "\n".join(_owned_output), "", 0),
                )
            time.sleep(0.25)
        return OllamaActionResult(
            "start",
            True,
            "Ollama is still starting.",
            "Health check timed out; the assistant-owned process was left running.",
            CommandResult((os.fspath(executable), "serve"), None, None, "\n".join(_owned_output), "", 0, timed_out=True),
        )

    def stop(self, *, confirmed: bool) -> OllamaActionResult:
        if not confirmed:
            return OllamaActionResult("stop", False, "", "Explicit confirmation is required.")
        global _owned_process
        with _owned_lock:
            process = _owned_process
        if process is None or process.poll() is not None:
            return OllamaActionResult(
                "stop",
                False,
                "",
                "Stop is unavailable because the assistant does not own the running Ollama process.",
            )
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        with _owned_lock:
            _owned_process = None
        return OllamaActionResult(
            "stop",
            True,
            "Stopped the Ollama process started by this assistant.",
            command_result=CommandResult(("ollama", "serve"), None, 0, "\n".join(_owned_output), "", 0),
        )

    @staticmethod
    def owned_logs() -> str:
        return "\n".join(_owned_output)
