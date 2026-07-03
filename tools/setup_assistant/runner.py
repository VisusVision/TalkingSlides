from __future__ import annotations

import locale
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Mapping, Sequence

from .reports.sanitize import sanitize_argv, sanitize_text


def _decode_output(value: bytes | None) -> str:
    if not value:
        return ""
    encodings = ["utf-8-sig"]
    preferred = locale.getpreferredencoding(False)
    if preferred.lower() not in {"utf-8", "utf-8-sig"}:
        encodings.append(preferred)
    if os.name == "nt":
        encodings.extend(["mbcs", "cp850", "cp437"])
    for encoding in encodings:
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return value.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout_seconds: float = 30.0
    env: Mapping[str, str] = field(default_factory=dict)
    sanitize_output: bool = True

    @classmethod
    def create(
        cls,
        argv: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        timeout_seconds: float = 30.0,
        env: Mapping[str, str] | None = None,
        sanitize_output: bool = True,
    ) -> "CommandSpec":
        return cls(
            tuple(os.fspath(item) for item in argv),
            Path(cwd) if cwd is not None else None,
            timeout_seconds,
            dict(env or {}),
            sanitize_output,
        )


@dataclass
class CommandResult:
    argv: tuple[str, ...]
    cwd: str | None
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled and not self.error

    @property
    def display_command(self) -> str:
        argv = sanitize_argv(self.argv)
        if os.name == "nt":
            return subprocess.list2cmdline(list(argv))
        return shlex.join(argv)

    def to_dict(self) -> dict[str, object]:
        return {
            "argv": list(sanitize_argv(self.argv)),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "error": self.error,
        }


class CommandRunner:
    """Run argv-only commands without a shell and preserve native process semantics."""

    def run(self, spec: CommandSpec, cancel_event: Event | None = None) -> CommandResult:
        started = time.monotonic()
        cwd = spec.cwd.resolve() if spec.cwd else None
        if cwd is not None and not cwd.is_dir():
            return self._error_result(spec, started, f"Working directory does not exist: {cwd}")
        if not spec.argv:
            return self._error_result(spec, started, "No executable was provided.")

        process_env = os.environ.copy()
        process_env.update(spec.env)
        process_env.setdefault("PYTHONIOENCODING", "utf-8")
        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            process = subprocess.Popen(
                list(spec.argv),
                cwd=os.fspath(cwd) if cwd else None,
                env=process_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return self._error_result(spec, started, f"Could not start {spec.argv[0]}: {exc}")

        timed_out = False
        cancelled = False
        stdout_bytes = b""
        stderr_bytes = b""
        deadline = started + max(spec.timeout_seconds, 0.01)
        while True:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                process.terminate()
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                process.terminate()
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if timed_out or cancelled:
                    try:
                        stdout_bytes, stderr_bytes = process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        stdout_bytes, stderr_bytes = process.communicate()
                    break

        stdout = _decode_output(stdout_bytes)
        stderr = _decode_output(stderr_bytes)
        if spec.sanitize_output:
            stdout = sanitize_text(stdout)
            stderr = sanitize_text(stderr)
        return CommandResult(
            argv=spec.argv,
            cwd=os.fspath(cwd) if cwd else None,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=round((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            cancelled=cancelled,
            error="Command timed out." if timed_out else ("Command cancelled." if cancelled else ""),
        )

    @staticmethod
    def _error_result(spec: CommandSpec, started: float, message: str) -> CommandResult:
        return CommandResult(
            argv=spec.argv,
            cwd=os.fspath(spec.cwd) if spec.cwd else None,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=round((time.monotonic() - started) * 1000),
            error=sanitize_text(message),
        )
