from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

try:
    from celery.exceptions import SoftTimeLimitExceeded
except Exception:  # pragma: no cover - celery is present in worker runtime.
    class SoftTimeLimitExceeded(Exception):  # type: ignore[no-redef]
        pass

logger = logging.getLogger(__name__)

CANONICAL_ENGINE = "liveportrait+musetalk"
MUSETALK_ONLY_ENGINE = "musetalk"
SUPPORTED_ENGINES = (CANONICAL_ENGINE, MUSETALK_ONLY_ENGINE)


@dataclass
class EngineResult:
    success: bool
    engine: str
    output_path: str
    error: str = ""
    command: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def normalize_avatar_engine(value: str | None) -> str:
    requested = str(value or "").strip().lower()
    if requested in {"", CANONICAL_ENGINE}:
        return CANONICAL_ENGINE
    if requested == MUSETALK_ONLY_ENGINE:
        if musetalk_only_fast_mode_enabled() and str(os.environ.get("AVATAR_ENGINE", "")).strip().lower() == MUSETALK_ONLY_ENGINE:
            return MUSETALK_ONLY_ENGINE
        return CANONICAL_ENGINE
    logger.warning("Unknown avatar engine=%s; using canonical engine=%s", requested, CANONICAL_ENGINE)
    return CANONICAL_ENGINE


def _truthy_env(name: str, default: str = "0") -> bool:
    raw = str(os.environ.get(name, default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def musetalk_only_fast_mode_enabled() -> bool:
    return _truthy_env("AVATAR_ALLOW_MUSETALK_ONLY_FAST_MODE", "0")


def _required_env_vars() -> list[str]:
    selected = normalize_avatar_engine(os.environ.get("AVATAR_ENGINE"))
    if selected == MUSETALK_ONLY_ENGINE:
        return ["AVATAR_MUSETALK_CMD"]
    return ["AVATAR_LIVEPORTRAIT_CMD", "AVATAR_MUSETALK_CMD"]


def get_avatar_engine_configuration_report() -> dict[str, object]:
    selected = normalize_avatar_engine(os.environ.get("AVATAR_ENGINE"))
    missing = [name for name in _required_env_vars() if not str(os.environ.get(name, "")).strip()]
    configured = {selected: _required_env_vars()} if not missing else {}
    missing_map = {selected: missing} if missing else {}
    active_chain = [MUSETALK_ONLY_ENGINE] if selected == MUSETALK_ONLY_ENGINE else [CANONICAL_ENGINE]
    return {
        "selected_engine": selected,
        "configured": configured,
        "missing": missing_map,
        "real_engine_count": int(bool(configured)),
        "active_chain": active_chain,
        "required_env_by_engine": {
            CANONICAL_ENGINE: ["AVATAR_LIVEPORTRAIT_CMD", "AVATAR_MUSETALK_CMD"],
            MUSETALK_ONLY_ENGINE: ["AVATAR_MUSETALK_CMD"],
        },
        "musetalk_only_fast_mode_enabled": musetalk_only_fast_mode_enabled(),
    }


def _timeout_seconds(env_name: str, default_seconds: float) -> float:
    raw = str(os.environ.get(env_name, "")).strip()
    if not raw:
        return float(default_seconds)
    try:
        value = float(raw)
    except Exception:
        return float(default_seconds)
    return value if value > 0 else float(default_seconds)


def _process_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    stage_name: str,
    reason: str,
    grace_seconds: float = 5.0,
) -> dict[str, Any]:
    pid = int(getattr(process, "pid", 0) or 0)
    payload: dict[str, Any] = {
        "pid": pid,
        "pgid": 0,
        "reason": str(reason or ""),
        "terminated": False,
        "killed": False,
        "error": "",
    }
    if pid <= 0 or process.poll() is not None:
        return payload

    try:
        if os.name == "nt":
            process.terminate()
            payload["terminated"] = True
        else:
            pgid = os.getpgid(pid)
            payload["pgid"] = int(pgid)
            os.killpg(pgid, signal.SIGTERM)
            payload["terminated"] = True
        logger.warning(
            "Avatar adapter terminate process group stage=%s reason=%s pid=%s pgid=%s",
            stage_name,
            reason,
            pid,
            payload.get("pgid") or "",
        )
        process.wait(timeout=max(float(grace_seconds), 0.1))
        return payload
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                pgid = int(payload.get("pgid") or os.getpgid(pid))
                payload["pgid"] = int(pgid)
                os.killpg(pgid, signal.SIGKILL)
            payload["killed"] = True
            logger.warning(
                "Avatar adapter kill process group stage=%s reason=%s pid=%s pgid=%s",
                stage_name,
                reason,
                pid,
                payload.get("pgid") or "",
            )
            process.wait(timeout=2.0)
        except Exception as exc:
            payload["error"] = str(exc)
        return payload
    except ProcessLookupError:
        return payload
    except Exception as exc:
        payload["error"] = str(exc)
        try:
            process.kill()
            payload["killed"] = True
        except Exception:
            pass
        return payload


def _run_command(
    *,
    stage_name: str,
    command: str,
    env_overrides: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    logger.info(
        "Avatar adapter start stage=%s timeout_seconds=%s command=%s",
        stage_name,
        round(float(timeout_seconds or 0.0), 3) if timeout_seconds else 0.0,
        command,
    )
    env = os.environ.copy()
    if env_overrides:
        env.update({str(key): str(value) for key, value in env_overrides.items()})

    shell_command = ["bash", "-lc", command] if os.name != "nt" else ["powershell", "-Command", command]
    started_at = time.monotonic()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            shell_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            **_process_group_kwargs(),
        )
        stdout, stderr = process.communicate(
            timeout=(float(timeout_seconds) if timeout_seconds and float(timeout_seconds) > 0 else None)
        )
    except SoftTimeLimitExceeded:
        if process is not None:
            _terminate_process_group(process, stage_name=stage_name, reason="soft_time_limit")
        raise
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started_at
        cleanup = (
            _terminate_process_group(process, stage_name=stage_name, reason="timeout")
            if process is not None
            else {}
        )
        try:
            partial_stdout, partial_stderr = process.communicate(timeout=2.0) if process is not None else ("", "")
        except Exception:
            partial_stdout, partial_stderr = "", ""
        partial_stdout = str(partial_stdout or "")[-2000:]
        partial_stderr = str(partial_stderr or "")[-2000:]
        logger.warning(
            "Avatar adapter timeout stage=%s elapsed_seconds=%s timeout_seconds=%s cleanup=%s\nstdout_tail=%s\nstderr_tail=%s",
            stage_name,
            round(float(elapsed), 3),
            round(float(timeout_seconds or 0.0), 3),
            cleanup,
            partial_stdout or "(empty)",
            partial_stderr or "(empty)",
        )
        return False, f"{stage_name}_timeout", {
            "command": command,
            "return_code": None,
            "stderr": "timeout",
            "stdout_tail": partial_stdout,
            "stderr_tail": partial_stderr,
            "timeout_seconds": round(float(timeout_seconds or 0.0), 3),
            "elapsed_seconds": round(float(elapsed), 3),
            "stage_name": stage_name,
            "process_cleanup": cleanup,
        }
    except Exception as exc:
        elapsed = time.monotonic() - started_at
        if process is not None and process.poll() is None:
            _terminate_process_group(process, stage_name=stage_name, reason="exception")
        logger.warning(
            "Avatar adapter failure stage=%s elapsed_seconds=%s error=%s",
            stage_name,
            round(float(elapsed), 3),
            str(exc),
        )
        return False, str(exc), {
            "command": command,
            "return_code": None,
            "stderr": str(exc),
            "timeout_seconds": round(float(timeout_seconds or 0.0), 3),
            "elapsed_seconds": round(float(elapsed), 3),
            "stage_name": stage_name,
        }

    return_code = int(process.returncode if process is not None and process.returncode is not None else 0)
    stderr_summary = str(stderr or stdout or "").strip()
    elapsed = time.monotonic() - started_at
    if return_code != 0:
        logger.warning(
            "Avatar adapter nonzero exit stage=%s elapsed_seconds=%s return_code=%s stderr=%s",
            stage_name,
            round(float(elapsed), 3),
            return_code,
            stderr_summary[:280],
        )
        return False, stderr_summary or f"{stage_name}_command_failed", {
            "command": command,
            "return_code": return_code,
            "stderr": stderr_summary,
            "timeout_seconds": round(float(timeout_seconds or 0.0), 3),
            "elapsed_seconds": round(float(elapsed), 3),
            "stage_name": stage_name,
        }
    logger.info(
        "Avatar adapter finished stage=%s elapsed_seconds=%s return_code=%s",
        stage_name,
        round(float(elapsed), 3),
        return_code,
    )
    return True, "", {
        "command": command,
        "return_code": return_code,
        "stderr": stderr_summary,
        "timeout_seconds": round(float(timeout_seconds or 0.0), 3),
        "elapsed_seconds": round(float(elapsed), 3),
        "stage_name": stage_name,
    }


def _template_command(template: str, replacements: dict[str, str]) -> str:
    command = str(template or "").strip()
    for key, value in replacements.items():
        command = command.replace("{" + str(key) + "}", str(value))
    return command


def run_liveportrait(
    *,
    input_path: str,
    output_path: str,
    audio_path: str,
    source_video: str = "",
    fps: float = 0.0,
    target_frame_count: int = 0,
    env_overrides: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> EngineResult:
    template = str(os.environ.get("AVATAR_LIVEPORTRAIT_CMD", "")).strip()
    if not template:
        return EngineResult(False, "liveportrait", output_path, "AVATAR_LIVEPORTRAIT_CMD is not configured")
    command = _template_command(
        template,
        {
            "input_path": input_path,
            "source_image": input_path,
            "source_video": str(source_video or ""),
            "audio_path": audio_path,
            "output_path": output_path,
            "fps": str(int(fps)) if fps > 0.0 else "0",
            "target_frame_count": str(int(target_frame_count)) if target_frame_count > 0 else "0",
        },
    )
    ok, error, details = _run_command(
        stage_name="liveportrait",
        command=command,
        env_overrides=env_overrides,
        timeout_seconds=(
            float(timeout_seconds)
            if timeout_seconds is not None and float(timeout_seconds) > 0.0
            else _timeout_seconds("AVATAR_STAGE_TIMEOUT_LIVEPORTRAIT_SECONDS", 360.0)
        ),
    )
    return EngineResult(ok, "liveportrait", output_path, error, command, details)


# MuseTalk needs at least this many MiB free in VRAM to avoid soft CPU fallback.
_MUSETALK_MIN_GPU_MIB = int(os.environ.get("AVATAR_MUSETALK_MIN_GPU_MIB", "1800"))


def _check_gpu_headroom() -> str | None:
    """Return a best-effort low-headroom warning string, else None.

    This check must never be treated as a hard gate: low-VRAM devices should
    continue with slower, smaller-batch execution.
    """
    try:
        import subprocess as _sp
        result = _sp.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parsed_rows = []
            for line in result.stdout.strip().splitlines():
                parts = line.split(",")
                if len(parts) == 2:
                    free_mib = int(parts[0].strip())
                    total_mib = int(parts[1].strip())
                    parsed_rows.append((free_mib, total_mib))
            if parsed_rows:
                free_mib, total_mib = sorted(parsed_rows, key=lambda row: row[0], reverse=True)[0]
                if free_mib < _MUSETALK_MIN_GPU_MIB:
                    return (
                        f"low_gpu_headroom:free_mib={free_mib} total_mib={total_mib} "
                        f"required_mib={_MUSETALK_MIN_GPU_MIB}"
                    )
                logger.info(
                    "MuseTalk GPU headroom ok free_mib=%s total_mib=%s required_mib=%s",
                    free_mib, total_mib, _MUSETALK_MIN_GPU_MIB,
                )
    except Exception as exc:
        logger.debug("GPU headroom check skipped reason=%s", exc)
    return None


# ---------------------------------------------------------------------------
# MuseTalk persistent service helpers
# ---------------------------------------------------------------------------

def _musetalk_route_mode(*, stage_name: str) -> str:
    """Return the legacy MuseTalk routing hint for diagnostics.

    Runtime routing is service-first when AVATAR_MUSETALK_SERVICE_ENABLED=1.
    AVATAR_MUSETALK_ROUTE and AVATAR_PREVIEW_FORCE_ISOLATED_MUSETALK are kept
    only as compatibility hints in logs so stale route config cannot silently
    bypass the persistent service.
    """
    raw_mode = str(os.environ.get("AVATAR_MUSETALK_ROUTE", "service")).strip().lower()
    alias_map = {
        "": "service",
        "subprocess": "subprocess",
        "standalone": "subprocess",
        "isolated": "subprocess",
        "runner": "subprocess",
        "entrypoint": "subprocess",
        "service": "service",
        "persistent_service": "service",
        "persistent-service": "service",
    }
    mode = alias_map.get(raw_mode)
    if mode is None:
        logger.warning(
            "Unknown AVATAR_MUSETALK_ROUTE=%s; treating as persistent service hint",
            raw_mode,
        )
        mode = "service"

    if mode == "subprocess" and _musetalk_service_enabled():
        logger.info(
            "MuseTalk legacy route hint ignored because persistent service is enabled "
            "stage=%s env_AVATAR_MUSETALK_ROUTE=%s env_AVATAR_PREVIEW_FORCE_ISOLATED_MUSETALK=%s",
            stage_name,
            os.environ.get("AVATAR_MUSETALK_ROUTE", "(unset)"),
            os.environ.get("AVATAR_PREVIEW_FORCE_ISOLATED_MUSETALK", "(unset)"),
        )

    return mode


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _musetalk_service_enabled() -> bool:
    return _env_flag("AVATAR_MUSETALK_SERVICE_ENABLED", "1")


def _musetalk_standalone_fallback_enabled() -> bool:
    return _env_flag("AVATAR_MUSETALK_STANDALONE_FALLBACK", "0")


def _musetalk_service_url() -> str | None:
    """Return the service base URL if the persistent service is enabled, else None."""
    if not _musetalk_service_enabled():
        return None
    port = int(os.environ.get("AVATAR_MUSETALK_SERVICE_PORT", "17860"))
    return f"http://127.0.0.1:{port}"


def _musetalk_service_health(url: str | None) -> dict[str, Any]:
    """Return the parsed MuseTalk service health payload."""
    if not url:
        return {
            "status": "disabled",
            "ready_for_inference": False,
            "models_loaded": False,
            "process_alive": False,
        }
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as resp:
            body = json.loads(resp.read())
            if isinstance(body, dict):
                body.setdefault("http_status", int(resp.status))
                body.setdefault("process_alive", True)
                body.setdefault("ready_for_inference", body.get("status") == "ready")
                body.setdefault("models_loaded", body.get("status") == "ready")
                return body
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
            if isinstance(body, dict):
                body.setdefault("http_status", int(exc.code))
                body.setdefault("process_alive", True)
                body.setdefault("ready_for_inference", False)
                return body
        except Exception:
            pass
        return {
            "status": f"http_{exc.code}",
            "http_status": int(exc.code),
            "process_alive": True,
            "ready_for_inference": False,
            "models_loaded": False,
        }
    except Exception as exc:
        return {
            "status": "unreachable",
            "error": str(exc),
            "process_alive": False,
            "ready_for_inference": False,
            "models_loaded": False,
        }
    return {
        "status": "invalid_health_payload",
        "process_alive": True,
        "ready_for_inference": False,
        "models_loaded": False,
    }


def _musetalk_service_healthy(url: str) -> bool:
    """Return True only when /health reports ready (models loaded)."""
    health = _musetalk_service_health(url)
    return bool(health.get("ready_for_inference") or health.get("status") == "ready")


def _musetalk_debug_sidecar_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".musetalk_debug.json")


def _musetalk_run_marker_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".musetalk_run.json")


def _sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_int_value(value: Any, default_value: int) -> int:
    try:
        return int(str(value).strip() or str(default_value))
    except Exception:
        return int(default_value)


def _read_float_value(value: Any, default_value: float) -> float:
    try:
        return float(str(value).strip() or str(default_value))
    except Exception:
        return float(default_value)


def _read_bool_value(value: Any, default_value: bool = False) -> bool:
    if value is None:
        return bool(default_value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _service_env_value(env_overrides: dict[str, str], name: str, default_value: Any) -> Any:
    return env_overrides.get(name, os.environ.get(name, default_value))


def _probe_duration_seconds(path: str) -> float:
    if not path or not Path(path).exists():
        return 0.0
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode != 0:
            return 0.0
        return max(float((proc.stdout or "0").strip() or "0"), 0.0)
    except Exception:
        return 0.0


def _probe_frame_count(path: str) -> int:
    if not path or not Path(path).exists():
        return 0
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,nb_frames",
                "-of",
                "json",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode != 0:
            return 0
        payload = json.loads(proc.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
    except Exception:
        return 0


def _musetalk_chunk_count_for_service(
    *,
    source_video: str,
    audio_path: str,
    chunk_max_seconds: float,
) -> tuple[int, float]:
    duration_seconds = max(_probe_duration_seconds(source_video), _probe_duration_seconds(audio_path))
    if chunk_max_seconds <= 0.0 or duration_seconds <= 0.0:
        return 1, duration_seconds
    return max(int((duration_seconds + chunk_max_seconds - 1e-6) // chunk_max_seconds), 1), duration_seconds


def _musetalk_chunk_ranges(*, duration_seconds: float, chunk_max_seconds: float) -> list[tuple[float, float]]:
    total = max(float(duration_seconds), 0.0)
    chunk_max = max(float(chunk_max_seconds), 0.0)
    if total <= 0.0:
        return []
    if chunk_max <= 0.0 or total <= chunk_max:
        return [(0.0, total)]
    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < total - 1e-6:
        duration = min(chunk_max, total - cursor)
        ranges.append((round(cursor, 6), round(duration, 6)))
        cursor += duration
    return ranges


def _run_media_command(command: list[str], *, stage_name: str, timeout_seconds: float, expected_output: Path | None = None) -> None:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=max(float(timeout_seconds), 1.0),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{stage_name}_failed:{str(proc.stderr or proc.stdout or '').strip()[-500:]}")
    if expected_output is not None and (not expected_output.exists() or expected_output.stat().st_size <= 0):
        raise RuntimeError(f"{stage_name}_missing_output:{expected_output}")


def _prepare_service_chunk_media(
    *,
    source_video: str,
    audio_path: str,
    work_dir: Path,
    chunk_index: int,
    start_seconds: float,
    duration_seconds: float,
) -> tuple[str, str]:
    chunk_audio = work_dir / f"chunk_{int(chunk_index):04d}.wav"
    _run_media_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{float(start_seconds):.6f}",
            "-t",
            f"{float(duration_seconds):.6f}",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(chunk_audio),
        ],
        stage_name="musetalk_service_chunk_audio_prepare",
        timeout_seconds=max(float(duration_seconds) * 3.0, 120.0),
        expected_output=chunk_audio,
    )

    if not source_video:
        return "", str(chunk_audio)

    chunk_video = work_dir / f"chunk_{int(chunk_index):04d}.mp4"
    _run_media_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{float(start_seconds):.6f}",
            "-t",
            f"{float(duration_seconds):.6f}",
            "-i",
            str(source_video),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            str(chunk_video),
        ],
        stage_name="musetalk_service_chunk_video_prepare",
        timeout_seconds=max(float(duration_seconds) * 3.0, 120.0),
        expected_output=chunk_video,
    )
    return str(chunk_video), str(chunk_audio)


def _concat_service_chunk_outputs(*, chunk_outputs: list[Path], output_path: Path, work_dir: Path) -> None:
    if not chunk_outputs:
        raise RuntimeError("musetalk_service_chunk_concat_no_inputs")
    concat_file = work_dir / "concat_inputs.txt"
    def _concat_path(path: Path) -> str:
        return str(path).replace("'", "'\\''")

    concat_file.write_text(
        "".join(f"file '{_concat_path(path)}'\n" for path in chunk_outputs),
        encoding="utf-8",
    )
    _run_media_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        stage_name="musetalk_service_chunk_concat",
        timeout_seconds=max(len(chunk_outputs) * 120.0, 180.0),
        expected_output=output_path,
    )


def _build_musetalk_service_params(
    *,
    env_overrides: dict[str, str],
    stage_name: str,
    stage_budget_timeout_seconds: float,
    http_timeout_seconds: float,
) -> dict[str, Any]:
    version = str(
        env_overrides.get(
            "MUSETALK_VERSION",
            os.environ.get("MUSETALK_VERSION", os.environ.get("AVATAR_MUSETALK_VERSION", "v15")),
        )
    ).strip() or "v15"
    return {
        "bbox_shift": _read_int_value(_service_env_value(env_overrides, "MUSETALK_BBOX_SHIFT", "0"), 0),
        "extra_margin": _read_int_value(_service_env_value(env_overrides, "MUSETALK_EXTRA_MARGIN", "10"), 10),
        "fps": _read_int_value(_service_env_value(env_overrides, "MUSETALK_FPS", "25"), 25),
        "audio_padding_length_left": _read_int_value(_service_env_value(env_overrides, "MUSETALK_AUDIO_PADDING_LEFT", "2"), 2),
        "audio_padding_length_right": _read_int_value(_service_env_value(env_overrides, "MUSETALK_AUDIO_PADDING_RIGHT", "2"), 2),
        "batch_size": max(_read_int_value(_service_env_value(env_overrides, "MUSETALK_BATCH_SIZE", "8"), 8), 1),
        "parsing_mode": str(_service_env_value(env_overrides, "MUSETALK_PARSING_MODE", "jaw")),
        "left_cheek_width": _read_int_value(_service_env_value(env_overrides, "MUSETALK_LEFT_CHEEK_WIDTH", "90"), 90),
        "right_cheek_width": _read_int_value(_service_env_value(env_overrides, "MUSETALK_RIGHT_CHEEK_WIDTH", "90"), 90),
        "use_float16": _read_bool_value(_service_env_value(env_overrides, "MUSETALK_USE_FLOAT16", "1"), True),
        "version": version,
        "target_frame_count": max(_read_int_value(_service_env_value(env_overrides, "MUSETALK_TARGET_FRAME_COUNT", "0"), 0), 0),
        "target_duration_seconds": max(_read_float_value(_service_env_value(env_overrides, "MUSETALK_TARGET_DURATION_SECONDS", "0"), 0.0), 0.0),
        "preview_fast_mode": _read_bool_value(_service_env_value(env_overrides, "MUSETALK_PREVIEW_FAST_MODE", "0"), False),
        "preview_max_width": max(_read_int_value(_service_env_value(env_overrides, "MUSETALK_PREVIEW_MAX_WIDTH", "512"), 512), 1),
        "chunk_max_seconds": max(_read_float_value(_service_env_value(env_overrides, "MUSETALK_CHUNK_MAX_SECONDS", "0"), 0.0), 0.0),
        "chunk_timeout_seconds": max(_read_float_value(_service_env_value(env_overrides, "MUSETALK_CHUNK_TIMEOUT_SECONDS", "0"), 0.0), 0.0),
        "idle_timeout_seconds": max(_read_float_value(_service_env_value(env_overrides, "MUSETALK_IDLE_TIMEOUT_SECONDS", "0"), 0.0), 0.0),
        "total_timeout_seconds": max(_read_float_value(_service_env_value(env_overrides, "MUSETALK_TOTAL_TIMEOUT_SECONDS", stage_budget_timeout_seconds), stage_budget_timeout_seconds), 0.0),
        "stage_budget_timeout_seconds": float(stage_budget_timeout_seconds),
        "http_timeout_seconds": float(http_timeout_seconds),
        "stage_name": str(stage_name),
    }


def _prepare_musetalk_service_current_run(
    *,
    source_image: str,
    source_video: str,
    audio_path: str,
    output_path: str,
    run_id: str,
    started_epoch: float,
) -> dict[str, str]:
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for stale_path in [out_path, _musetalk_debug_sidecar_path(out_path)]:
        if stale_path.exists():
            logger.warning(
                "late_musetalk_output_detected reason=stale_before_service_run path=%s mtime=%s run_id=%s",
                str(stale_path),
                round(float(stale_path.stat().st_mtime), 6),
                run_id,
            )
            stale_path.unlink(missing_ok=True)

    source_image_path = Path(source_image) if source_image else None
    source_video_path = Path(source_video) if source_video else None
    audio = Path(audio_path)
    selected_source_path = (
        source_video_path
        if source_video_path is not None and source_video_path.exists()
        else source_image_path
    )
    if selected_source_path is None or not selected_source_path.exists():
        raise FileNotFoundError(f"musetalk_service_source_missing source_image={source_image} source_video={source_video}")
    if not audio.exists():
        raise FileNotFoundError(f"musetalk_service_audio_missing audio_path={audio_path}")

    marker = {
        "run_id": str(run_id),
        "started_epoch": f"{float(started_epoch):.6f}",
        "source_image_path": str(source_image_path or ""),
        "source_video_path": str(source_video_path or ""),
        "source_path": str(selected_source_path),
        "audio_path": str(audio),
        "source_image_sha256": (
            _sha256_file(source_image_path)
            if source_image_path is not None and source_image_path.exists()
            else ""
        ),
        "source_video_sha256": (
            _sha256_file(source_video_path)
            if source_video_path is not None and source_video_path.exists()
            else ""
        ),
        "source_sha256": _sha256_file(selected_source_path),
        "audio_sha256": _sha256_file(audio),
        "output_path": str(out_path),
    }
    _musetalk_run_marker_path(out_path).write_text(
        json.dumps(marker, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return marker


def _validate_musetalk_service_output(
    *,
    output_path: str,
    run_id: str,
    started_epoch: float,
    expected_marker: dict[str, str],
) -> tuple[bool, str]:
    out_path = Path(output_path)
    if not out_path.exists() or out_path.stat().st_size <= 0:
        return False, "missing_output"
    if float(out_path.stat().st_mtime) < float(started_epoch) - 0.5:
        logger.warning(
            "late_musetalk_output_detected reason=older_than_current_service_run path=%s mtime=%s started_epoch=%s run_id=%s",
            str(out_path),
            round(float(out_path.stat().st_mtime), 6),
            round(float(started_epoch), 6),
            run_id,
        )
        return False, "late_musetalk_output_detected:older_than_current_run"

    sidecar = _musetalk_debug_sidecar_path(out_path)
    if not sidecar.exists():
        return False, "late_musetalk_output_detected:missing_debug_sidecar"
    try:
        debug_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return False, "late_musetalk_output_detected:invalid_debug_sidecar"

    sidecar_run_id = str(debug_payload.get("musetalk_run_id") or "").strip()
    if sidecar_run_id != str(run_id):
        logger.warning(
            "late_musetalk_output_detected reason=run_id_mismatch path=%s sidecar_run_id=%s expected_run_id=%s",
            str(out_path),
            sidecar_run_id,
            run_id,
        )
        return False, "late_musetalk_output_detected:run_id_mismatch"

    expected_source_image = str(expected_marker.get("source_image_sha256") or "")
    expected_source_video = str(expected_marker.get("source_video_sha256") or "")
    expected_source = str(expected_marker.get("source_sha256") or "")
    expected_audio = str(expected_marker.get("audio_sha256") or "")
    sidecar_source_image = str(debug_payload.get("input_reference_image_sha256") or "")
    sidecar_source_video = str(debug_payload.get("input_reference_video_sha256") or "")
    sidecar_audio = str(debug_payload.get("input_audio_sha256") or "")

    if expected_source_video:
        if sidecar_source_video != expected_source_video:
            return False, "late_musetalk_output_detected:source_hash_mismatch"
    elif expected_source_image:
        if sidecar_source_image != expected_source_image:
            return False, "late_musetalk_output_detected:source_hash_mismatch"
    elif expected_source and expected_source not in {sidecar_source_image, sidecar_source_video}:
        return False, "late_musetalk_output_detected:source_hash_mismatch"

    if sidecar_audio != expected_audio:
        return False, "late_musetalk_output_detected:audio_hash_mismatch"
    return True, ""


def _run_via_musetalk_service(
    url: str,
    *,
    source_image: str,
    source_video: str,
    audio_path: str,
    output_path: str,
    params: dict,
    timeout_seconds: float,
    stage_budget_timeout_seconds: float,
    stage_name: str,
    run_id: str,
    route_reason: str,
    service_health: dict[str, Any],
) -> EngineResult:
    """Forward an inference request to the persistent MuseTalk service."""
    started_epoch = time.time()
    route_label = route_reason if route_reason in {"service_one_shot", "service_chunked"} else "service"
    try:
        run_marker = _prepare_musetalk_service_current_run(
            source_image=source_image,
            source_video=source_video,
            audio_path=audio_path,
            output_path=output_path,
            run_id=run_id,
            started_epoch=started_epoch,
        )
    except Exception as exc:
        logger.error(
            "MuseTalk service current-run preparation failed stage=%s run_id=%s error=%s",
            stage_name,
            run_id,
            exc,
        )
        return EngineResult(False, "musetalk", output_path, str(exc), "", {
            "stage_name": stage_name,
            "route": route_label,
            "route_reason": route_reason,
            "run_id": run_id,
            "service_health": service_health,
        })

    body = json.dumps({
        "source_image": source_image,
        "source_video": source_video,
        "audio_path": audio_path,
        "output_path": output_path,
        "params": params,
        "run": {
            "run_id": run_id,
            "started_epoch": started_epoch,
            "source_image_sha256": run_marker.get("source_image_sha256", ""),
            "source_video_sha256": run_marker.get("source_video_sha256", ""),
            "source_sha256": run_marker.get("source_sha256", ""),
            "audio_sha256": run_marker.get("audio_sha256", ""),
            "output_path": output_path,
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/infer",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    started_at = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            elapsed = time.monotonic() - started_at
            result = json.loads(resp.read())
    except SoftTimeLimitExceeded:
        raise
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - started_at
        try:
            err_body = json.loads(exc.read())
            error_msg = str(err_body.get("error") or "service_http_error")
        except Exception:
            error_msg = f"service_http_{exc.code}"
        logger.error(
            "MuseTalk service request failed route=service reason=%s stage=%s run_id=%s "
            "status=%s elapsed=%.3fs timeout_budget_seconds=%.1f http_timeout_seconds=%.1f error=%s "
            "service_health=%s",
            route_reason,
            stage_name,
            run_id,
            exc.code,
            elapsed,
            stage_budget_timeout_seconds,
            timeout_seconds,
            error_msg,
            service_health,
        )
        return EngineResult(False, "musetalk", output_path, error_msg, "", {
            "elapsed_seconds": round(elapsed, 3),
            "stage_name": stage_name,
            "route": route_label,
            "route_reason": route_reason,
            "run_id": run_id,
            "service_health": service_health,
            "svc_timeout_seconds": float(timeout_seconds),
            "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
        })
    except TimeoutError:
        elapsed = time.monotonic() - started_at
        logger.warning(
            "MuseTalk service request timed out route=service reason=%s stage=%s run_id=%s "
            "elapsed=%.3fs timeout_budget_seconds=%.1f http_timeout_seconds=%.1f service_health=%s",
            route_reason,
            stage_name,
            run_id,
            elapsed,
            stage_budget_timeout_seconds,
            timeout_seconds,
            service_health,
        )
        return EngineResult(False, "musetalk", output_path, f"{stage_name}_timeout", "", {
            "elapsed_seconds": round(elapsed, 3),
            "stage_name": stage_name,
            "stderr": "timeout",
            "route": route_label,
            "route_reason": route_reason,
            "run_id": run_id,
            "service_health": service_health,
            "svc_timeout_seconds": float(timeout_seconds),
            "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
        })
    except Exception as exc:
        elapsed = time.monotonic() - started_at
        logger.error(
            "MuseTalk service request error route=service reason=%s stage=%s run_id=%s "
            "elapsed=%.3fs timeout_budget_seconds=%.1f http_timeout_seconds=%.1f error=%s service_health=%s",
            route_reason,
            stage_name,
            run_id,
            elapsed,
            stage_budget_timeout_seconds,
            timeout_seconds,
            exc,
            service_health,
        )
        return EngineResult(False, "musetalk", output_path, str(exc), "", {
            "elapsed_seconds": round(elapsed, 3),
            "stage_name": stage_name,
            "route": route_label,
            "route_reason": route_reason,
            "run_id": run_id,
            "service_health": service_health,
            "svc_timeout_seconds": float(timeout_seconds),
            "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
        })

    if result.get("success"):
        response_output = str(result.get("output_path") or output_path)
        if Path(response_output) != Path(output_path):
            logger.warning(
                "MuseTalk service returned unexpected output path stage=%s run_id=%s expected=%s actual=%s",
                stage_name,
                run_id,
                output_path,
                response_output,
            )
            return EngineResult(False, "musetalk", output_path, "musetalk_service_output_path_mismatch", "", {
                "stage_name": stage_name,
                "route": route_label,
                "route_reason": route_reason,
                "run_id": run_id,
                "service_health": service_health,
                "returned_output_path": response_output,
            })
        valid, validation_error = _validate_musetalk_service_output(
            output_path=output_path,
            run_id=run_id,
            started_epoch=started_epoch,
            expected_marker=run_marker,
        )
        if not valid:
            logger.error(
                "MuseTalk service output rejected route=service reason=%s stage=%s run_id=%s "
                "validation_error=%s output_path=%s service_health=%s",
                route_reason,
                stage_name,
                run_id,
                validation_error,
                output_path,
                service_health,
            )
            return EngineResult(False, "musetalk", output_path, validation_error, "", {
                "stage_name": stage_name,
                "route": route_label,
                "route_reason": route_reason,
                "run_id": run_id,
                "service_health": service_health,
                "svc_timeout_seconds": float(timeout_seconds),
                "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
            })
        stage_timings = dict(result.get("stage_timings") or {})
        model_load_seconds = float(
            result.get(
                "model_load_seconds",
                stage_timings.get("model_load_seconds", result.get("cold_start_seconds", 0.0)),
            )
            or 0.0
        )
        face_landmark_seconds = float(stage_timings.get("face_landmark_extraction_seconds", 0.0) or 0.0)
        inference_loop_seconds = float(stage_timings.get("inference_loop_seconds", 0.0) or 0.0)
        total_elapsed_seconds = float(result.get("elapsed_seconds", 0.0) or 0.0)
        logger.info(
            "MuseTalk run summary route=service reason=%s stage=%s run_id=%s service_health=%s "
            "model_load_seconds=%.3f face_landmark_seconds=%.3f inference_loop_seconds=%.3f "
            "total_elapsed_seconds=%.3f output_path=%s timeout_budget_seconds=%.1f http_timeout_seconds=%.1f",
            route_reason,
            stage_name,
            run_id,
            service_health,
            model_load_seconds,
            face_landmark_seconds,
            inference_loop_seconds,
            total_elapsed_seconds,
            output_path,
            stage_budget_timeout_seconds,
            timeout_seconds,
        )
        return EngineResult(True, "musetalk", output_path, "", "", {
            "elapsed_seconds": total_elapsed_seconds,
            "cold_start_seconds": result.get("cold_start_seconds", 0.0),
            "inference_seconds": result.get("inference_seconds", result.get("elapsed_seconds", 0.0)),
            "stage_name": stage_name,
            "via_service": True,
            "route": route_label,
            "route_reason": route_reason,
            "service_health": service_health,
            "run_id": run_id,
            "source_image_sha256": run_marker.get("source_image_sha256", ""),
            "source_video_sha256": run_marker.get("source_video_sha256", ""),
            "source_sha256": run_marker.get("source_sha256", ""),
            "audio_sha256": run_marker.get("audio_sha256", ""),
            "stage_timings": stage_timings,
            "runtime_settings": result.get("runtime_settings", {}),
            "runtime_info": result.get("runtime_info", {}),
            "per_frame_timings": result.get("per_frame_timings", {}),
            "model_load_seconds": model_load_seconds,
            "face_landmark_seconds": face_landmark_seconds,
            "inference_loop_seconds": inference_loop_seconds,
            "total_elapsed_seconds": total_elapsed_seconds,
            "svc_timeout_seconds": float(timeout_seconds),
            "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
        })
    error_msg = str(result.get("error") or "service_inference_failed")
    logger.error(
        "MuseTalk service inference failed route=service reason=%s stage=%s run_id=%s "
        "error=%s service_health=%s timeout_budget_seconds=%.1f http_timeout_seconds=%.1f",
        route_reason,
        stage_name,
        run_id,
        error_msg,
        service_health,
        stage_budget_timeout_seconds,
        timeout_seconds,
    )
    return EngineResult(False, "musetalk", output_path, error_msg, "", {
        "stage_name": stage_name,
        "route": route_label,
        "route_reason": route_reason,
        "run_id": run_id,
        "service_health": service_health,
        "svc_timeout_seconds": float(timeout_seconds),
        "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
    })


def _run_via_musetalk_service_chunked(
    url: str,
    *,
    source_image: str,
    source_video: str,
    audio_path: str,
    output_path: str,
    params: dict,
    timeout_seconds: float,
    stage_budget_timeout_seconds: float,
    stage_name: str,
    run_id: str,
    route_reason: str,
    service_health: dict[str, Any],
    chunk_count: int,
    duration_seconds: float,
) -> EngineResult:
    """Run long MuseTalk segments through the persistent service one chunk at a time."""
    started_epoch = time.time()
    output = Path(output_path)
    work_dir = output.parent / f"{output.stem}.service_chunks_{run_id.replace('/', '_').replace(':', '_')}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_marker = _prepare_musetalk_service_current_run(
            source_image=source_image,
            source_video=source_video,
            audio_path=audio_path,
            output_path=output_path,
            run_id=run_id,
            started_epoch=started_epoch,
        )
    except Exception as exc:
        return EngineResult(False, "musetalk", output_path, str(exc), "", {
            "stage_name": stage_name,
            "route": "service_chunked",
            "route_reason": route_reason,
            "run_id": run_id,
            "service_health": service_health,
        })

    chunk_max_seconds = float(params.get("chunk_max_seconds") or 0.0)
    chunk_ranges = _musetalk_chunk_ranges(duration_seconds=duration_seconds, chunk_max_seconds=chunk_max_seconds)
    if len(chunk_ranges) != int(chunk_count):
        chunk_count = len(chunk_ranges)
    chunk_outputs: list[Path] = []
    chunk_metadata: list[dict[str, Any]] = []
    total_started = time.monotonic()
    try:
        for chunk_index, (start_seconds, chunk_duration) in enumerate(chunk_ranges):
            chunk_source_video, chunk_audio = _prepare_service_chunk_media(
                source_video=source_video,
                audio_path=audio_path,
                work_dir=work_dir,
                chunk_index=int(chunk_index),
                start_seconds=float(start_seconds),
                duration_seconds=float(chunk_duration),
            )
            chunk_output = work_dir / f"chunk_{int(chunk_index):04d}_out.mp4"
            chunk_run_id = f"{run_id}-chunk-{int(chunk_index):04d}"
            chunk_params = dict(params)
            chunk_params["chunk_max_seconds"] = 0.0
            chunk_params["target_duration_seconds"] = round(float(chunk_duration), 6)
            chunk_timeout = max(
                float(params.get("chunk_timeout_seconds") or 0.0),
                float(timeout_seconds) / max(float(chunk_count), 1.0),
                float(chunk_duration) * 60.0,
                120.0,
            )
            chunk_http_timeout = chunk_timeout + _timeout_seconds("AVATAR_MUSETALK_SERVICE_HTTP_TIMEOUT_MARGIN_SECONDS", 60.0)
            chunk_started = time.monotonic()
            result = _run_via_musetalk_service(
                url,
                source_image=source_image,
                source_video=chunk_source_video,
                audio_path=chunk_audio,
                output_path=str(chunk_output),
                params=chunk_params,
                timeout_seconds=chunk_http_timeout,
                stage_budget_timeout_seconds=chunk_timeout,
                stage_name=stage_name,
                run_id=chunk_run_id,
                route_reason=route_reason,
                service_health=service_health,
            )
            elapsed = time.monotonic() - chunk_started
            frame_count = 0
            output_duration_seconds = 0.0
            if result.success:
                frame_count = _probe_frame_count(str(chunk_output))
                output_duration_seconds = round(_probe_duration_seconds(str(chunk_output)), 4)
            metadata = {
                "index": int(chunk_index),
                "chunk_index": int(chunk_index),
                "start_seconds": round(float(start_seconds), 4),
                "duration_seconds": round(float(chunk_duration), 4),
                "audio_duration_seconds": round(float(chunk_duration), 4),
                "source_video_path": str(chunk_source_video or ""),
                "audio_path": str(chunk_audio),
                "output_path": str(chunk_output),
                "run_id": chunk_run_id,
                "stage_budget_timeout_seconds": round(float(chunk_timeout), 4),
                "http_timeout_seconds": round(float(chunk_http_timeout), 4),
                "service_elapsed_seconds": round(float(result.details.get("elapsed_seconds") or elapsed), 4),
                "elapsed_seconds": round(float(elapsed), 4),
                "service_success": bool(result.success),
                "service_error": str(result.error or ""),
                "frame_count": int(frame_count),
                "output_duration_seconds": float(output_duration_seconds),
            }
            if result.success:
                chunk_outputs.append(chunk_output)
            chunk_metadata.append(metadata)
            logger.info(
                "MuseTalk service chunk_timing run_id=%s chunk_index=%s chunk_count=%s "
                "audio_duration_seconds=%.4f frame_count=%s elapsed_seconds=%.4f success=%s",
                chunk_run_id,
                int(chunk_index),
                int(chunk_count),
                float(metadata["audio_duration_seconds"]),
                int(metadata["frame_count"]),
                float(metadata["elapsed_seconds"]),
                bool(result.success),
            )
            if not result.success:
                return EngineResult(False, "musetalk", output_path, result.error or "musetalk_service_chunk_failed", "", {
                    "elapsed_seconds": round(time.monotonic() - total_started, 4),
                    "stage_name": stage_name,
                    "via_service": True,
                    "route": "service_chunked",
                    "route_reason": route_reason,
                    "service_health": service_health,
                    "run_id": run_id,
                    "chunk_count": int(chunk_count),
                    "chunk_metadata": chunk_metadata,
                    "failed_chunk_index": int(chunk_index),
                    "svc_timeout_seconds": float(timeout_seconds),
                    "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
                })

        _concat_service_chunk_outputs(chunk_outputs=chunk_outputs, output_path=output, work_dir=work_dir)
    except Exception as exc:
        return EngineResult(False, "musetalk", output_path, str(exc), "", {
            "elapsed_seconds": round(time.monotonic() - total_started, 4),
            "stage_name": stage_name,
            "via_service": True,
            "route": "service_chunked",
            "route_reason": route_reason,
            "service_health": service_health,
            "run_id": run_id,
            "chunk_count": int(chunk_count),
            "chunk_metadata": chunk_metadata,
            "svc_timeout_seconds": float(timeout_seconds),
            "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
        })

    total_elapsed = time.monotonic() - total_started
    debug_payload = {
        "musetalk_run_id": run_id,
        "input_reference_image_sha256": run_marker.get("source_image_sha256", ""),
        "input_reference_video_sha256": run_marker.get("source_video_sha256", ""),
        "input_audio_sha256": run_marker.get("audio_sha256", ""),
        "route": "service_chunked",
        "route_reason": route_reason,
        "chunking_used": True,
        "chunk_count": int(chunk_count),
        "chunk_ranges": [
            {"start_seconds": round(float(start), 4), "duration_seconds": round(float(duration), 4)}
            for start, duration in chunk_ranges
        ],
        "chunk_metadata": chunk_metadata,
        "final_stitched_output_path": str(output),
        "elapsed_seconds": round(float(total_elapsed), 4),
        "service_health": service_health,
    }
    _musetalk_debug_sidecar_path(output).write_text(json.dumps(debug_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    valid, validation_error = _validate_musetalk_service_output(
        output_path=output_path,
        run_id=run_id,
        started_epoch=started_epoch,
        expected_marker=run_marker,
    )
    if not valid:
        return EngineResult(False, "musetalk", output_path, validation_error, "", {
            "elapsed_seconds": round(time.monotonic() - total_started, 4),
            "stage_name": stage_name,
            "via_service": True,
            "route": "service_chunked",
            "route_reason": route_reason,
            "service_health": service_health,
            "run_id": run_id,
            "chunk_count": int(chunk_count),
            "chunk_metadata": chunk_metadata,
            "svc_timeout_seconds": float(timeout_seconds),
            "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
        })
    return EngineResult(True, "musetalk", output_path, "", "", {
        "elapsed_seconds": round(float(total_elapsed), 4),
        "cold_start_seconds": 0.0,
        "inference_seconds": round(float(total_elapsed), 4),
        "stage_name": stage_name,
        "via_service": True,
        "route": "service_chunked",
        "route_reason": route_reason,
        "service_health": service_health,
        "run_id": run_id,
        "source_image_sha256": run_marker.get("source_image_sha256", ""),
        "source_video_sha256": run_marker.get("source_video_sha256", ""),
        "source_sha256": run_marker.get("source_sha256", ""),
        "audio_sha256": run_marker.get("audio_sha256", ""),
        "chunk_count": int(chunk_count),
        "chunk_ranges": debug_payload["chunk_ranges"],
        "chunk_metadata": chunk_metadata,
        "final_stitched_output_path": str(output),
        "svc_timeout_seconds": float(timeout_seconds),
        "stage_budget_timeout_seconds": round(float(stage_budget_timeout_seconds), 3),
    })


def run_musetalk(
    *,
    source_image: str,
    source_video: str,
    audio_path: str,
    output_path: str,
    env_overrides: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
    stage_name: str = "musetalk",
) -> EngineResult:
    effective_timeout = float(timeout_seconds) if timeout_seconds is not None else _timeout_seconds(
        "AVATAR_STAGE_TIMEOUT_MUSETALK_SECONDS", 420.0
    )
    effective_env_overrides = dict(env_overrides or {})

    def _read_int(value: Any, default_value: int) -> int:
        try:
            return int(str(value).strip() or str(default_value))
        except Exception:
            return int(default_value)

    if "AVATAR_MUSETALK_RUN_ID" not in effective_env_overrides:
        effective_env_overrides["AVATAR_MUSETALK_RUN_ID"] = (
            f"{stage_name}-{int(time.time() * 1000)}-{os.getpid()}"
        )
    run_id = str(effective_env_overrides["AVATAR_MUSETALK_RUN_ID"])

    route_hint = _musetalk_route_mode(stage_name=stage_name)
    service_enabled = _musetalk_service_enabled()
    fallback_allowed = _musetalk_standalone_fallback_enabled()
    svc_url = _musetalk_service_url()
    service_health = _musetalk_service_health(svc_url) if service_enabled else _musetalk_service_health(None)
    service_ready = bool(service_health.get("ready_for_inference") or service_health.get("status") == "ready")

    route_reason = "service_enabled_health_ready"
    if service_enabled and service_ready and svc_url:
        svc_floor = _timeout_seconds("AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", 420.0)
        svc_stage_budget = max(effective_timeout, svc_floor)
        svc_http_margin = _timeout_seconds("AVATAR_MUSETALK_SERVICE_HTTP_TIMEOUT_MARGIN_SECONDS", 60.0)
        svc_http_timeout = svc_stage_budget + svc_http_margin
        logger.info(
            "MuseTalk route decision route=service reason=%s stage=%s run_id=%s route_hint=%s "
            "service_health=%s preview_budget_seconds=%.1f service_floor_seconds=%.1f "
            "timeout_budget_seconds=%.1f http_timeout_seconds=%.1f",
            route_reason,
            stage_name,
            run_id,
            route_hint,
            service_health,
            effective_timeout,
            svc_floor,
            svc_stage_budget,
            svc_http_timeout,
        )
        infer_params = _build_musetalk_service_params(
            env_overrides=effective_env_overrides,
            stage_name=stage_name,
            stage_budget_timeout_seconds=svc_stage_budget,
            http_timeout_seconds=svc_http_timeout,
        )
        estimated_chunk_count, estimated_duration_seconds = _musetalk_chunk_count_for_service(
            source_video=source_video,
            audio_path=audio_path,
            chunk_max_seconds=float(infer_params.get("chunk_max_seconds") or 0.0),
        )
        infer_params["estimated_chunk_count"] = int(estimated_chunk_count)
        infer_params["estimated_duration_seconds"] = round(float(estimated_duration_seconds), 6)
        if estimated_chunk_count <= 1:
            route_reason = "service_one_shot"
            return _run_via_musetalk_service(
                svc_url,
                source_image=source_image,
                source_video=source_video,
                audio_path=audio_path,
                output_path=output_path,
                params=infer_params,
                timeout_seconds=svc_http_timeout,
                stage_budget_timeout_seconds=svc_stage_budget,
                stage_name=stage_name,
                run_id=run_id,
                route_reason=route_reason,
                service_health=service_health,
            )

        route_reason = "service_chunked"
        logger.info(
            "MuseTalk route decision route=service_chunked reason=%s stage=%s run_id=%s "
            "chunk_count=%s duration_seconds=%.3f chunk_max_seconds=%.3f",
            route_reason,
            stage_name,
            run_id,
            estimated_chunk_count,
            estimated_duration_seconds,
            float(infer_params.get("chunk_max_seconds") or 0.0),
        )
        return _run_via_musetalk_service_chunked(
            svc_url,
            source_image=source_image,
            source_video=source_video,
            audio_path=audio_path,
            output_path=output_path,
            params=infer_params,
            timeout_seconds=svc_http_timeout,
            stage_budget_timeout_seconds=svc_stage_budget,
            stage_name=stage_name,
            run_id=run_id,
            route_reason=route_reason,
            service_health=service_health,
            chunk_count=int(estimated_chunk_count),
            duration_seconds=float(estimated_duration_seconds),
        )

    if route_reason == "service_enabled_health_ready":
        route_reason = "service_enabled_health_unready" if service_enabled else "service_disabled"
    if not fallback_allowed:
        logger.error(
            "MuseTalk route decision route=failed reason=%s stage=%s run_id=%s route_hint=%s "
            "fallback_allowed=%s service_health=%s timeout_budget_seconds=%.1f",
            route_reason,
            stage_name,
            run_id,
            route_hint,
            fallback_allowed,
            service_health,
            effective_timeout,
        )
        return EngineResult(False, "musetalk", output_path, "musetalk_service_unavailable", "", {
            "stage_name": stage_name,
            "route": "service",
            "route_reason": route_reason,
            "route_hint": route_hint,
            "fallback_allowed": False,
            "service_enabled": service_enabled,
            "service_health": service_health,
            "run_id": run_id,
            "stage_budget_timeout_seconds": round(float(effective_timeout), 3),
        })

    logger.warning(
        "MuseTalk route decision route=standalone reason=%s stage=%s run_id=%s route_hint=%s "
        "fallback_allowed=%s service_health=%s timeout_budget_seconds=%.1f",
        route_reason,
        stage_name,
        run_id,
        route_hint,
        fallback_allowed,
        service_health,
        effective_timeout,
    )

    route_mode = "subprocess"
    if route_mode == "service":
        # --- Explicit persistent service route ---
        # GPU headroom check is deliberately skipped here: the service already has models
        # loaded in VRAM, so nvidia-smi "free" will legitimately be below the threshold.
        svc_url = _musetalk_service_url()
        if svc_url and _musetalk_service_healthy(svc_url):
            # Authoritative service timeout: the caller budget is the baseline, floored at
            # AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS (default 420s) because on low-end GPUs
            # DWPose alone takes ~1.8 s/frame × 24 frames = 43 s before UNet even starts.
            # Using floor=max(budget, floor) keeps budget as the single reported source while
            # preventing any accidental small value from shrinking the real HTTP timeout.
            svc_floor = _timeout_seconds("AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", 420.0)
            svc_infer_timeout = max(effective_timeout, svc_floor)
            logger.info(
                "MuseTalk service routing stage=%s "
                "preview_budget_seconds=%.1f service_floor_seconds=%.1f effective_svc_timeout_seconds=%.1f "
                "env_AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS=%s",
                stage_name, effective_timeout, svc_floor, svc_infer_timeout,
                os.environ.get("AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS", "(unset→420)"),
            )
            infer_params = {
                "bbox_shift": _read_int(effective_env_overrides.get("MUSETALK_BBOX_SHIFT", os.environ.get("MUSETALK_BBOX_SHIFT", "0")), 0),
                "extra_margin": _read_int(effective_env_overrides.get("MUSETALK_EXTRA_MARGIN", os.environ.get("MUSETALK_EXTRA_MARGIN", "10")), 10),
                "fps": _read_int(effective_env_overrides.get("MUSETALK_FPS", os.environ.get("MUSETALK_FPS", "25")), 25),
                "audio_padding_length_left": _read_int(effective_env_overrides.get("MUSETALK_AUDIO_PADDING_LEFT", os.environ.get("MUSETALK_AUDIO_PADDING_LEFT", "2")), 2),
                "audio_padding_length_right": _read_int(effective_env_overrides.get("MUSETALK_AUDIO_PADDING_RIGHT", os.environ.get("MUSETALK_AUDIO_PADDING_RIGHT", "2")), 2),
                "batch_size": max(_read_int(effective_env_overrides.get("MUSETALK_BATCH_SIZE", os.environ.get("MUSETALK_BATCH_SIZE", "8")), 8), 1),
                "parsing_mode": str(effective_env_overrides.get("MUSETALK_PARSING_MODE", os.environ.get("MUSETALK_PARSING_MODE", "jaw"))),
            }
            return _run_via_musetalk_service(
                svc_url,
                source_image=source_image,
                source_video=source_video,
                audio_path=audio_path,
                output_path=output_path,
                params=infer_params,
                timeout_seconds=svc_infer_timeout,
                stage_name=stage_name,
            )

        logger.info(
            "MuseTalk service route requested but unavailable (not running or not healthy) — "
            "falling back to isolated subprocess stage=%s service_url=%s",
            stage_name, svc_url or "disabled",
        )
    else:
        logger.info(
            "MuseTalk isolated subprocess routing stage=%s route=%s",
            stage_name,
            route_mode,
        )

    # --- Subprocess fallback ---
    # Check GPU headroom only to adapt, never to reject.
    gpu_error = _check_gpu_headroom()
    if gpu_error is not None:
        low_vram_batch_size = max(_read_int(os.environ.get("AVATAR_LOW_VRAM_MUSETALK_BATCH_SIZE", "2"), 2), 1)
        requested_batch_size = max(_read_int(effective_env_overrides.get("MUSETALK_BATCH_SIZE", os.environ.get("MUSETALK_BATCH_SIZE", "8")), 8), 1)
        adapted_batch_size = min(requested_batch_size, low_vram_batch_size)
        effective_env_overrides["MUSETALK_BATCH_SIZE"] = str(adapted_batch_size)

        timeout_multiplier = _timeout_seconds("AVATAR_LOW_VRAM_MUSETALK_TIMEOUT_MULTIPLIER", 2.0)
        effective_timeout = max(effective_timeout * timeout_multiplier, effective_timeout + 120.0)
        logger.warning(
            "MuseTalk low-VRAM adaptive mode stage=%s warning=%s requested_batch_size=%s adapted_batch_size=%s adapted_timeout_seconds=%.1f",
            stage_name,
            gpu_error,
            requested_batch_size,
            adapted_batch_size,
            effective_timeout,
        )

    template = str(os.environ.get("AVATAR_MUSETALK_CMD", "")).strip()
    if not template:
        return EngineResult(False, "musetalk", output_path, "AVATAR_MUSETALK_CMD is not configured")
    command = _template_command(
        template,
        {
            "input_path": source_video or source_image,
            "source_image": source_image,
            "source_video": source_video,
            "audio_path": audio_path,
            "output_path": output_path,
        },
    )
    if "AVATAR_MUSETALK_RUN_ID" not in effective_env_overrides:
        effective_env_overrides["AVATAR_MUSETALK_RUN_ID"] = (
            f"{stage_name}-{int(time.time() * 1000)}-{os.getpid()}"
        )
    command_timeout = float(effective_timeout)
    if "MUSETALK_TOTAL_TIMEOUT_SECONDS" in effective_env_overrides:
        command_timeout += _timeout_seconds("AVATAR_MUSETALK_PARENT_TIMEOUT_GRACE_SECONDS", 60.0)
    ok, error, details = _run_command(
        stage_name=stage_name,
        command=command,
        env_overrides=effective_env_overrides,
        timeout_seconds=command_timeout,
    )
    if details is not None:
        details["stage_budget_timeout_seconds"] = round(float(effective_timeout), 3)
        details["route"] = "standalone"
        details["route_reason"] = route_reason
        details["route_hint"] = route_hint
        details["fallback_allowed"] = True
        details["service_enabled"] = service_enabled
        details["service_health"] = service_health
        details["run_id"] = run_id
    elapsed_details = details or {}
    logger.info(
        "MuseTalk run summary route=standalone reason=%s stage=%s run_id=%s service_health=%s "
        "model_load_seconds=%s face_landmark_seconds=%s inference_loop_seconds=%s total_elapsed_seconds=%s "
        "output_path=%s timeout_budget_seconds=%.1f command_timeout_seconds=%.1f",
        route_reason,
        stage_name,
        run_id,
        service_health,
        elapsed_details.get("model_load_seconds", ""),
        elapsed_details.get("face_landmark_seconds", ""),
        elapsed_details.get("inference_loop_seconds", ""),
        elapsed_details.get("elapsed_seconds", ""),
        output_path,
        effective_timeout,
        command_timeout,
    )
    return EngineResult(ok, "musetalk", output_path, error, command, details)


def run_restoration(
    *,
    input_video: str,
    output_path: str,
    source_image: str = "",
    audio_path: str = "",
    env_overrides: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> EngineResult:
    template = str(os.environ.get("AVATAR_PREVIEW_RESTORE_CMD", "")).strip()
    if not template:
        return EngineResult(False, "restoration", output_path, "AVATAR_PREVIEW_RESTORE_CMD is not configured")
    command = _template_command(
        template,
        {
            "input_path": input_video,
            "source_image": source_image,
            "source_video": input_video,
            "audio_path": audio_path,
            "output_path": output_path,
            "model": str(os.environ.get("AVATAR_PREVIEW_RESTORATION_MODEL", "codeformer") or "codeformer"),
        },
    )
    ok, error, details = _run_command(
        stage_name="restoration",
        command=command,
        env_overrides=env_overrides,
        timeout_seconds=(
            float(timeout_seconds)
            if timeout_seconds is not None and float(timeout_seconds) > 0.0
            else _timeout_seconds("AVATAR_STAGE_TIMEOUT_RESTORATION_SECONDS", 180.0)
        ),
    )
    return EngineResult(ok, "restoration", output_path, error, command, details)
