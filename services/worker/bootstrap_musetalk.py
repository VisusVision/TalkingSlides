from __future__ import annotations

import logging
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("worker.bootstrap")
EXPECTED_RUNTIME_VERSIONS = {
    "python": "3.10",
    "torch": "2.0.1",
    "torchvision": "0.15.2",
    "torchaudio": "2.0.2",
    "mmcv": "2.0.1",
    "mmpose": "1.1.0",
}
_MUSETALK_WARMUP_PROCESS_PATTERNS = (
    "musetalk_entrypoint.py",
    "/scripts/inference.py",
    "\\scripts\\inference.py",
)


@dataclass(frozen=True)
class _WarmupCommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool
    pid: int
    pgid: int
    cleanup: dict[str, object]


@dataclass(frozen=True)
class _WarmupProcessMatch:
    pid: int
    pgid: int
    cmdline: str
    marked: bool


@dataclass(frozen=True)
class _WarmupOrphanCheckResult:
    warmup_id: str
    matched_count: int
    warmup_orphan_count: int
    unscoped_match_count: int
    killed_count: int
    remaining_count: int
    details: list[dict[str, object]]


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    )


def _bool_text(value: bool) -> str:
    return "yes" if value else "no"


def _resolve_model_root(raw_model_root: Path) -> Path:
    nested = raw_model_root / "models"
    if nested.exists():
        return nested
    return raw_model_root


def _extract_dwpose_refs(preprocessing_py: Path) -> list[str]:
    refs: list[str] = []
    if not preprocessing_py.exists():
        return refs
    text = preprocessing_py.read_text(encoding="utf-8", errors="ignore")
    for rel in re.findall(r"\./musetalk/utils/dwpose/([^\"']+)", text):
        rel_clean = str(rel).strip()
        if rel_clean and rel_clean not in refs:
            refs.append(rel_clean)
    return refs


def _ensure_dwpose_checkpoint(
    *,
    target_dwpose_dir: Path,
    model_root: Path,
    checkpoint_name: str,
) -> Path:
    target = target_dwpose_dir / checkpoint_name
    if target.exists():
        return target

    candidates = [
        model_root / "dwpose" / checkpoint_name,
        model_root / checkpoint_name,
        Path("/app/storage_local/models/dwpose") / checkpoint_name,
    ]
    source = next((p for p in candidates if p.exists()), None)
    if source is None:
        return target

    target_dwpose_dir.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source)
        LOGGER.info("Bootstrap: linked dwpose checkpoint source=%s target=%s", source, target)
    except Exception:
        shutil.copy2(source, target)
        LOGGER.info("Bootstrap: copied dwpose checkpoint source=%s target=%s", source, target)
    return target


def _sync_liveportrait_weights(*, model_root: Path, runtime_home: Path) -> list[str]:
    errors: list[str] = []
    dst_root = runtime_home / "pretrained_weights" / "liveportrait"

    required_rel_paths = [
        Path("base_models/appearance_feature_extractor.pth"),
        Path("base_models/motion_extractor.pth"),
        Path("base_models/spade_generator.pth"),
        Path("base_models/warping_module.pth"),
        Path("retargeting_models/stitching_retargeting_module.pth"),
        Path("landmark.onnx"),
    ]

    source_candidates = [model_root / "liveportrait", model_root]
    src_root = next((p for p in source_candidates if (p / "base_models").exists()), None)
    if src_root is None:
        return [f"liveportrait source weights directory missing candidates={source_candidates}"]

    for rel in required_rel_paths:
        src = src_root / rel
        dst = dst_root / rel
        if not src.exists():
            errors.append(f"missing liveportrait source file={src}")
            continue
        if src.resolve() == dst.resolve():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except Exception as exc:
            errors.append(f"failed to copy liveportrait weight source={src} target={dst} error={exc}")

    src_insightface = model_root / "insightface"
    dst_insightface = runtime_home / "pretrained_weights" / "insightface"
    if src_insightface.exists():
        try:
            if src_insightface.resolve() == dst_insightface.resolve():
                return errors
        except Exception:
            pass
        try:
            shutil.copytree(src_insightface, dst_insightface, dirs_exist_ok=True)
        except Exception as exc:
            errors.append(
                "failed to sync insightface model pack "
                f"source={src_insightface} target={dst_insightface} error={exc}"
            )

    return errors


def _validate_liveportrait_runtime_weights(*, runtime_home: Path) -> list[str]:
    missing: list[str] = []
    runtime_root = runtime_home / "pretrained_weights" / "liveportrait"
    required_rel_paths = [
        Path("base_models/appearance_feature_extractor.pth"),
        Path("base_models/motion_extractor.pth"),
        Path("base_models/spade_generator.pth"),
        Path("base_models/warping_module.pth"),
        Path("retargeting_models/stitching_retargeting_module.pth"),
        Path("landmark.onnx"),
    ]
    for rel in required_rel_paths:
        path = runtime_root / rel
        if not path.exists():
            missing.append(str(path))
    return missing


def _version_matches(actual: str, expected: str) -> bool:
    actual_clean = str(actual or "").strip()
    expected_clean = str(expected or "").strip()
    return actual_clean == expected_clean or actual_clean.startswith(expected_clean + "+")


def _command_head_callable(command: str) -> bool:
    raw = str(command or "").strip()
    if not raw:
        return False
    try:
        parts = shlex.split(raw)
    except Exception:
        parts = raw.split()
    if not parts:
        return False
    head = str(parts[0]).strip()
    if head in {"if", "for", "while", "case", "bash", "sh", "python", "python3"}:
        return True
    head_path = Path(head)
    if head_path.is_absolute() and head_path.exists():
        return True
    return shutil.which(head) is not None


def _command_tokens(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except Exception:
        return raw.split()


def _validate_restoration_command(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        return ["restoration is enabled but AVATAR_PREVIEW_RESTORE_CMD is empty"]

    errors: list[str] = []
    for required_placeholder in ("{input_path}", "{output_path}"):
        if required_placeholder not in raw:
            errors.append(
                "AVATAR_PREVIEW_RESTORE_CMD is missing required placeholder "
                f"{required_placeholder}"
            )

    tokens = _command_tokens(raw)
    if not tokens:
        errors.append("AVATAR_PREVIEW_RESTORE_CMD could not be parsed")
        return errors

    head = str(tokens[0]).strip()
    if head in {"if", "for", "while", "case"}:
        errors.append(
            "AVATAR_PREVIEW_RESTORE_CMD must start with an executable command "
            "(shell control keywords are not valid command heads)"
        )

    if not _command_head_callable(raw):
        errors.append("AVATAR_PREVIEW_RESTORE_CMD command head is not callable")
        return errors

    if head in {"python", "python3"} and len(tokens) >= 2:
        script_token = str(tokens[1]).strip()
        if script_token and "{" not in script_token and script_token.endswith(".py"):
            script_path = Path(script_token)
            if script_path.is_absolute() and not script_path.exists():
                errors.append(f"AVATAR_PREVIEW_RESTORE_CMD python script does not exist path={script_path}")

    head_path = Path(head)
    if head_path.is_absolute() and not head_path.exists():
        errors.append(f"AVATAR_PREVIEW_RESTORE_CMD executable does not exist path={head_path}")

    return errors


def _is_truthy_env(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _looks_like_storage_path(path: Path) -> bool:
    normalized = str(path).replace("\\", "/").lower()
    return "/storage_local/" in normalized or normalized.endswith("/storage_local")


def _autodetect_liveportrait_runtime() -> tuple[Path | None, Path | None]:
    candidate_entrypoints: list[Path] = []

    explicit_entrypoint = str(os.environ.get("AVATAR_LIVEPORTRAIT_ENTRYPOINT", "")).strip()
    explicit_home = str(os.environ.get("AVATAR_LIVEPORTRAIT_HOME", "")).strip()

    if explicit_entrypoint:
        candidate_entrypoints.append(Path(explicit_entrypoint))
    if explicit_home:
        candidate_entrypoints.append(Path(explicit_home) / "inference.py")

    candidate_entrypoints.extend(
        [
            Path("/opt/liveportrait/inference.py"),
            Path("/opt/LivePortrait/inference.py"),
            Path("/app/liveportrait/inference.py"),
        ]
    )

    seen: set[str] = set()
    for candidate in candidate_entrypoints:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate.parent.resolve(), candidate.resolve()
    return None, None


def _liveportrait_command_references_entrypoint(command: str, entrypoint: Path) -> bool:
    cmd = str(command or "").strip()
    if not cmd:
        return False
    entry = str(entrypoint)
    accepted_tokens = {
        entry,
        "${AVATAR_LIVEPORTRAIT_ENTRYPOINT}",
        "$AVATAR_LIVEPORTRAIT_ENTRYPOINT",
        "${AVATAR_LIVEPORTRAIT_HOME}/inference.py",
        "$AVATAR_LIVEPORTRAIT_HOME/inference.py",
    }
    return any(token in cmd for token in accepted_tokens)


def _composite_env_report() -> tuple[list[str], list[str], dict[str, bool]]:
    required_vars = [
        "AVATAR_ENGINE",
        "AVATAR_LIVEPORTRAIT_CMD",
        "AVATAR_MUSETALK_CMD",
        "AVATAR_LIVEPORTRAIT_HOME",
        "AVATAR_LIVEPORTRAIT_MODEL_PATH",
        "AVATAR_LIVEPORTRAIT_ENTRYPOINT",
        "AVATAR_PREVIEW_USE_LIVEPORTRAIT",
        "AVATAR_PREVIEW_USE_MUSETALK",
        "AVATAR_PREVIEW_USE_RESTORATION",
    ]
    if _is_truthy_env("AVATAR_PREVIEW_USE_RESTORATION"):
        required_vars.append("AVATAR_PREVIEW_RESTORE_CMD")

    configured_vars = [name for name in required_vars if str(os.environ.get(name, "")).strip()]
    missing_vars = [name for name in required_vars if name not in configured_vars]
    composite_flags = {
        "preview_liveportrait": _is_truthy_env("AVATAR_PREVIEW_USE_LIVEPORTRAIT"),
        "preview_musetalk": _is_truthy_env("AVATAR_PREVIEW_USE_MUSETALK", "1"),
        "preview_restoration": _is_truthy_env("AVATAR_PREVIEW_USE_RESTORATION"),
        "lesson_composite": _is_truthy_env("AVATAR_ENABLE_COMPOSITE_LESSON"),
    }
    return configured_vars, missing_vars, composite_flags


def _composite_fallback_enabled() -> bool:
    return False


def _check_runtime_imports() -> dict[str, str]:
    import torch  # type: ignore
    import torchaudio  # type: ignore
    import torchvision  # type: ignore
    import mmcv  # type: ignore
    import mmengine  # type: ignore
    import mmdet  # type: ignore
    import mmpose  # type: ignore
    from mmcv.ops import nms  # type: ignore  # noqa: F401
    from mmpose.apis import inference_topdown, init_model  # type: ignore  # noqa: F401
    from mmpose.structures import merge_data_samples  # type: ignore  # noqa: F401

    versions = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "torch": str(getattr(torch, "__version__", "unknown")),
        "torchvision": str(getattr(torchvision, "__version__", "unknown")),
        "torchaudio": str(getattr(torchaudio, "__version__", "unknown")),
        "mmcv": str(getattr(mmcv, "__version__", "unknown")),
        "mmengine": str(getattr(mmengine, "__version__", "unknown")),
        "mmdet": str(getattr(mmdet, "__version__", "unknown")),
        "mmpose": str(getattr(mmpose, "__version__", "unknown")),
    }

    if not versions["python"].startswith(EXPECTED_RUNTIME_VERSIONS["python"] + "."):
        raise RuntimeError(
            f"Python version mismatch: expected {EXPECTED_RUNTIME_VERSIONS['python']}.x got {versions['python']}"
        )
    for key in ["torch", "torchvision", "torchaudio", "mmcv", "mmpose"]:
        if not _version_matches(versions[key], EXPECTED_RUNTIME_VERSIONS[key]):
            raise RuntimeError(
                f"{key} version mismatch: expected {EXPECTED_RUNTIME_VERSIONS[key]} got {versions[key]}"
            )

    LOGGER.info(
        "Bootstrap: real backend imports ok python=%s torch=%s torchvision=%s torchaudio=%s mmcv=%s mmengine=%s mmdet=%s mmpose=%s",
        versions["python"],
        versions["torch"],
        versions["torchvision"],
        versions["torchaudio"],
        versions["mmcv"],
        versions["mmengine"],
        versions["mmdet"],
        versions["mmpose"],
    )
    return versions


def _popen_process_group_kwargs() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _process_group_exists(pgid: int) -> bool:
    if os.name == "nt" or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _wait_for_warmup_exit(
    *,
    process: subprocess.Popen[str] | None,
    pid: int,
    pgid: int,
    deadline: float,
) -> bool:
    while time.monotonic() < deadline:
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
        if os.name != "nt" and pgid > 0:
            if not _process_group_exists(pgid):
                return True
        elif not _pid_exists(pid):
            return True
        time.sleep(0.1)
    return False


def _terminate_warmup_process_group(
    *,
    process: subprocess.Popen[str] | None,
    pid: int,
    reason: str,
    grace_seconds: float = 5.0,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "pid": int(pid or 0),
        "pgid": 0,
        "reason": str(reason or ""),
        "terminated": False,
        "killed": False,
        "error": "",
    }
    pid = int(pid or 0)
    if pid <= 0:
        return payload

    try:
        if os.name == "nt":
            if process is not None and process.poll() is None:
                process.terminate()
                payload["terminated"] = True
            elif _pid_exists(pid):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
                payload["killed"] = True
            if process is not None:
                try:
                    process.wait(timeout=max(float(grace_seconds), 0.1))
                except subprocess.TimeoutExpired:
                    process.kill()
                    payload["killed"] = True
                    process.wait(timeout=2.0)
            return payload

        pgid = os.getpgid(pid)
        payload["pgid"] = int(pgid)
        current_pgid = os.getpgrp()
        if int(pgid) == int(current_pgid):
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(pgid, signal.SIGTERM)
        payload["terminated"] = True
        deadline = time.monotonic() + max(float(grace_seconds), 0.1)
        if _wait_for_warmup_exit(process=process, pid=pid, pgid=pgid, deadline=deadline):
            return payload

        if int(pgid) == int(current_pgid):
            os.kill(pid, signal.SIGKILL)
        else:
            os.killpg(pgid, signal.SIGKILL)
        payload["killed"] = True
        _wait_for_warmup_exit(
            process=process,
            pid=pid,
            pgid=pgid,
            deadline=time.monotonic() + 2.0,
        )
        return payload
    except ProcessLookupError:
        return payload
    except Exception as exc:
        payload["error"] = str(exc)
        try:
            if process is not None and process.poll() is None:
                process.kill()
                payload["killed"] = True
        except Exception:
            pass
        return payload
    finally:
        LOGGER.warning(
            "musetalk_warmup_process_group_killed reason=%s pid=%s pgid=%s terminated=%s killed=%s error=%s",
            payload.get("reason") or "",
            payload.get("pid") or "",
            payload.get("pgid") or "",
            payload.get("terminated"),
            payload.get("killed"),
            payload.get("error") or "",
        )


def _safe_proc_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def _decode_proc_cmdline(raw: bytes) -> str:
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _musetalk_warmup_cmdline_match(cmdline: str) -> bool:
    normalized = str(cmdline or "")
    return any(pattern in normalized for pattern in _MUSETALK_WARMUP_PROCESS_PATTERNS)


def _find_musetalk_warmup_processes(*, warmup_id: str) -> list[_WarmupProcessMatch]:
    if os.name == "nt":
        return []
    proc_root = Path("/proc")
    if not proc_root.exists():
        return []

    marker = f"AVATAR_MUSETALK_WARMUP_ID={warmup_id}".encode("utf-8") if warmup_id else b""
    matches: list[_WarmupProcessMatch] = []
    self_pid = os.getpid()
    for proc_dir in proc_root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        if pid == self_pid:
            continue
        cmdline = _decode_proc_cmdline(_safe_proc_bytes(proc_dir / "cmdline"))
        if not cmdline or not _musetalk_warmup_cmdline_match(cmdline):
            continue
        environ = _safe_proc_bytes(proc_dir / "environ")
        marked = bool(marker and marker in environ)
        if not marked and warmup_id:
            marked = warmup_id in cmdline
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pgid = 0
        matches.append(_WarmupProcessMatch(pid=pid, pgid=int(pgid or 0), cmdline=cmdline, marked=marked))
    return matches


def _check_and_kill_musetalk_warmup_orphans(*, warmup_id: str) -> _WarmupOrphanCheckResult:
    matches = _find_musetalk_warmup_processes(warmup_id=warmup_id)
    warmup_matches = [match for match in matches if match.marked]
    unscoped_matches = [match for match in matches if not match.marked]
    killed_count = 0
    details = [
        {
            "pid": match.pid,
            "pgid": match.pgid,
            "marked": match.marked,
            "cmdline": match.cmdline[:240],
        }
        for match in matches
    ]

    if warmup_matches:
        LOGGER.critical(
            "musetalk_warmup_orphan_check warmup_id=%s warmup_orphan_count=%s action=kill details=%s",
            warmup_id,
            len(warmup_matches),
            details,
        )
        killed_pgroups: set[int] = set()
        for match in warmup_matches:
            if match.pgid and match.pgid in killed_pgroups:
                killed_count += 1
                continue
            cleanup = _terminate_warmup_process_group(
                process=None,
                pid=match.pid,
                reason="orphan_check",
                grace_seconds=2.0,
            )
            if match.pgid:
                killed_pgroups.add(match.pgid)
            if cleanup.get("terminated") or cleanup.get("killed"):
                killed_count += 1

    remaining = [match for match in _find_musetalk_warmup_processes(warmup_id=warmup_id) if match.marked]
    result = _WarmupOrphanCheckResult(
        warmup_id=warmup_id,
        matched_count=len(matches),
        warmup_orphan_count=len(warmup_matches),
        unscoped_match_count=len(unscoped_matches),
        killed_count=killed_count,
        remaining_count=len(remaining),
        details=details,
    )
    log_fn = LOGGER.critical if result.remaining_count else LOGGER.info
    log_fn(
        "musetalk_warmup_orphan_check warmup_id=%s matched_count=%s warmup_orphan_count=%s "
        "unscoped_match_count=%s killed_count=%s remaining_count=%s",
        result.warmup_id,
        result.matched_count,
        result.warmup_orphan_count,
        result.unscoped_match_count,
        result.killed_count,
        result.remaining_count,
    )
    return result


def _run_musetalk_warmup_shell_command(
    *,
    command: str,
    env: dict[str, str],
    timeout_seconds: float,
    warmup_id: str,
) -> _WarmupCommandResult:
    shell_cmd = ["bash", "-lc", command] if os.name != "nt" else ["powershell", "-Command", command]
    started_at = time.monotonic()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            shell_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            **_popen_process_group_kwargs(),
        )
        try:
            pgid = os.getpgid(process.pid) if os.name != "nt" else 0
        except Exception:
            pgid = 0
        LOGGER.info(
            "musetalk_warmup_start warmup_id=%s pid=%s pgid=%s timeout_seconds=%s command=%s",
            warmup_id,
            process.pid,
            pgid,
            round(float(timeout_seconds), 1),
            command,
        )
        stdout, stderr = process.communicate(timeout=max(float(timeout_seconds), 0.1))
        return _WarmupCommandResult(
            returncode=process.returncode,
            stdout=str(stdout or ""),
            stderr=str(stderr or ""),
            elapsed_seconds=time.monotonic() - started_at,
            timed_out=False,
            pid=int(process.pid or 0),
            pgid=int(pgid or 0),
            cleanup={},
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started_at
        pid = int(process.pid if process is not None else 0)
        try:
            pgid = os.getpgid(pid) if os.name != "nt" and pid > 0 else 0
        except Exception:
            pgid = 0
        LOGGER.critical(
            "musetalk_warmup_timeout warmup_id=%s elapsed_seconds=%s timeout_seconds=%s pid=%s pgid=%s",
            warmup_id,
            round(float(elapsed), 1),
            round(float(timeout_seconds), 1),
            pid,
            pgid,
        )
        cleanup = _terminate_warmup_process_group(
            process=process,
            pid=pid,
            reason="timeout",
            grace_seconds=5.0,
        )
        try:
            partial_stdout, partial_stderr = (
                process.communicate(timeout=2.0) if process is not None else ("", "")
            )
        except Exception:
            partial_stdout, partial_stderr = "", ""
        return _WarmupCommandResult(
            returncode=(process.returncode if process is not None else None),
            stdout=str(partial_stdout or ""),
            stderr=str(partial_stderr or ""),
            elapsed_seconds=elapsed,
            timed_out=True,
            pid=pid,
            pgid=int(cleanup.get("pgid") or pgid or 0),
            cleanup=cleanup,
        )
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate_warmup_process_group(
                process=process,
                pid=int(process.pid or 0),
                reason="cancel_or_error",
                grace_seconds=5.0,
            )
        raise


def _warmup_musetalk(*, musetalk_home: Path, model_root: Path) -> int:
    """Run a one-shot, 1-frame dummy inference to warm MuseTalk before the first
    real preview job.

    This loads all model weights (DWPose, Whisper, SD-VAE, UNet, face-parse) into
    GPU memory and warms the PyTorch / mmpose allocators so that subsequent preview
    jobs only pay inference cost, not cold-start cost.

    Set AVATAR_MUSETALK_WARMUP=0 to skip (e.g. for fast local dev restarts).
    """
    if not _is_truthy_env("AVATAR_MUSETALK_WARMUP", "1"):
        LOGGER.info("Bootstrap: MuseTalk warmup skipped AVATAR_MUSETALK_WARMUP=0")
        return 0

    timeout_raw = str(os.environ.get("AVATAR_MUSETALK_WARMUP_TIMEOUT_SECONDS", "300")).strip()
    try:
        timeout_seconds = max(float(timeout_raw), 30.0)
    except Exception:
        timeout_seconds = 300.0

    warmup_id = f"musetalk-warmup-{int(time.time() * 1000)}-{os.getpid()}"
    LOGGER.info(
        "Bootstrap: MuseTalk warmup start musetalk_home=%s model_root=%s timeout_seconds=%s warmup_id=%s",
        musetalk_home,
        model_root,
        round(timeout_seconds, 1),
        warmup_id,
    )
    warmup_start = time.monotonic()

    musetalk_cmd_template = str(os.environ.get("AVATAR_MUSETALK_CMD", "")).strip()
    if not musetalk_cmd_template:
        LOGGER.warning("Bootstrap: MuseTalk warmup skipped AVATAR_MUSETALK_CMD not set")
        return 0

    import tempfile as _tempfile
    import wave as _wave

    with _tempfile.TemporaryDirectory(prefix="musetalk-warmup-") as td:
        work = Path(td)

        # Minimal 1-frame PNG (solid black 64×64).
        img_path = work / "warmup_face.png"
        try:
            import struct, zlib
            def _make_png(width: int, height: int) -> bytes:
                raw_rows = b"".join(b"\x00" + b"\x00" * width * 3 for _ in range(height))
                compressed = zlib.compress(raw_rows)
                def chunk(tag: bytes, data: bytes) -> bytes:
                    c = struct.pack(">I", len(data)) + tag + data
                    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
                return (
                    b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", compressed)
                    + chunk(b"IEND", b"")
                )
            img_path.write_bytes(_make_png(64, 64))
        except Exception:
            img_path.write_bytes(b"\x89PNG" + b"\x00" * 200)

        # Minimal WAV: 1 channel, 16 kHz, 16-bit, 0.5 s silence.
        audio_path = work / "warmup_audio.wav"
        try:
            n_samples = 8000  # 0.5 s at 16 kHz
            with _wave.open(str(audio_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * n_samples)
        except Exception as e:
            LOGGER.warning("Bootstrap: warmup audio creation failed reason=%s", e)
            return 0

        output_path = work / "warmup_out.mp4"

        # Build the actual warmup command using the configured AVATAR_MUSETALK_CMD template.
        source_image = str(img_path)
        source_video = str(img_path)
        audio_p = str(audio_path)
        output_p = str(output_path)
        command = musetalk_cmd_template
        for placeholder, value in [
            ("{source_image}", source_image),
            ("{source_video}", source_video),
            ("{input_path}", source_video),
            ("{audio_path}", audio_p),
            ("{output_path}", output_p),
        ]:
            command = command.replace(placeholder, value)

        env = os.environ.copy()
        # Use fast/small warmup settings.
        env["MUSETALK_PREVIEW_FAST_MODE"] = "1"
        env["MUSETALK_PREVIEW_MAX_WIDTH"] = "64"
        env["MUSETALK_TARGET_FRAME_COUNT"] = "0"
        env["MUSETALK_TARGET_DURATION_SECONDS"] = "0.000000"
        # Suppress diagnostic artefact writes during warmup.
        env["AVATAR_PREVIEW_DIAGNOSTIC_MODE"] = "0"
        env["AVATAR_MUSETALK_WARMUP_ID"] = warmup_id

        result: _WarmupCommandResult | None = None
        try:
            result = _run_musetalk_warmup_shell_command(
                command=command,
                env=env,
                timeout_seconds=timeout_seconds,
                warmup_id=warmup_id,
            )
        except Exception as exc:
            LOGGER.critical("Bootstrap: MuseTalk warmup unexpected error error=%s", exc)
            return 70
        finally:
            orphan_result = _check_and_kill_musetalk_warmup_orphans(warmup_id=warmup_id)
            if orphan_result.remaining_count:
                LOGGER.critical(
                    "musetalk_warmup_orphan_check warmup_id=%s remaining_after_cleanup=%s",
                    warmup_id,
                    orphan_result.remaining_count,
                )

        if result is None:
            return 70

        if result.timed_out:
            elapsed = time.monotonic() - warmup_start
            LOGGER.critical(
                "Bootstrap: MuseTalk warmup TIMED OUT elapsed_seconds=%s timeout_seconds=%s "
                "pid=%s pgid=%s cleanup=%s - models may be missing or GPU is unavailable",
                round(elapsed, 1),
                round(timeout_seconds, 1),
                result.pid,
                result.pgid,
                result.cleanup,
            )
            return 70

        elapsed = time.monotonic() - warmup_start
        if result.returncode != 0:
            stderr_tail = str(result.stderr or result.stdout or "")[-600:]
            LOGGER.critical(
                "Bootstrap: MuseTalk warmup FAILED return_code=%s elapsed_seconds=%s stderr=%s",
                result.returncode,
                round(elapsed, 1),
                stderr_tail,
            )
            return 70

        LOGGER.info(
            "musetalk_warmup_complete warmup_id=%s elapsed_seconds=%s output_exists=%s",
            warmup_id,
            round(elapsed, 1),
            bool(output_path.exists() and output_path.stat().st_size > 0),
        )

    return 0


def _start_musetalk_service(*, musetalk_home: Path, model_root: Path) -> bool:
    """Start musetalk_service.py as a background process and wait until /health is ready.

    Returns True if the service is healthy within the timeout, False otherwise.
    The service process is intentionally NOT waited on — it runs for the
    lifetime of the worker container.
    """
    import json as _json
    import urllib.error as _urllib_error
    import urllib.request as _urllib_req

    if not _is_truthy_env("AVATAR_MUSETALK_SERVICE_ENABLED", "1"):
        LOGGER.info("Bootstrap: MuseTalk persistent service disabled by AVATAR_MUSETALK_SERVICE_ENABLED=0")
        return False

    route_mode = str(os.environ.get("AVATAR_MUSETALK_ROUTE", "service")).strip().lower()
    if route_mode in {"subprocess", "standalone", "isolated", "runner", "entrypoint"}:
        LOGGER.info(
            "Bootstrap: ignoring legacy MuseTalk route hint AVATAR_MUSETALK_ROUTE=%s because AVATAR_MUSETALK_SERVICE_ENABLED=1",
            route_mode or "subprocess",
        )

    port = int(os.environ.get("AVATAR_MUSETALK_SERVICE_PORT", "17860"))
    health_url = f"http://127.0.0.1:{port}/health"

    def _read_health(timeout_seconds: float) -> dict | None:
        try:
            with _urllib_req.urlopen(health_url, timeout=timeout_seconds) as resp:
                data = _json.loads(resp.read())
                if isinstance(data, dict):
                    data.setdefault("http_status", int(resp.status))
                    return data
        except _urllib_error.HTTPError as exc:
            try:
                data = _json.loads(exc.read())
                if isinstance(data, dict):
                    data.setdefault("http_status", int(exc.code))
                    return data
            except Exception:
                return {"status": f"http_{exc.code}", "http_status": int(exc.code)}
        except Exception:
            return None
        return None

    # Find the service script.
    candidates = [
        Path("/app/scripts/musetalk_service.py"),
        Path(__file__).parent.parent / "scripts" / "musetalk_service.py",
    ]
    service_script = next((p for p in candidates if p.exists()), None)
    if service_script is None:
        LOGGER.warning(
            "Bootstrap: musetalk_service.py not found at any candidate path %s — "
            "persistent service unavailable",
            [str(p) for p in candidates],
        )
        return False

    timeout_raw = str(os.environ.get("AVATAR_MUSETALK_SERVICE_STARTUP_TIMEOUT_SECONDS", "300")).strip()
    try:
        startup_timeout = max(float(timeout_raw), 30.0)
    except Exception:
        startup_timeout = 300.0

    LOGGER.info(
        "Bootstrap: starting MuseTalk persistent service script=%s port=%d startup_timeout=%ss",
        service_script, port, round(startup_timeout, 1),
    )

    # If a healthy service is already listening (e.g. worker restarted but the previous
    # service process survived), reuse it instead of trying to bind the same port again.
    try:
        with _urllib_req.urlopen(health_url, timeout=3) as resp:
            import json as _json_precheck
            data = _json_precheck.loads(resp.read())
            if data.get("status") == "ready":
                LOGGER.info(
                    "Bootstrap: existing MuseTalk service already healthy on port=%d — reusing it",
                    port,
                )
                return True
            LOGGER.info(
                "Bootstrap: existing MuseTalk service on port=%d has status=%s — waiting for it",
                port, data.get("status"),
            )
    except Exception:
        pass  # No existing service; continue to start a new one

    existing_health = _read_health(3)
    proc: subprocess.Popen | None = None
    if existing_health:
        LOGGER.info(
            "Bootstrap: existing MuseTalk service detected on port=%d health=%s; waiting for readiness",
            port,
            existing_health,
        )
    else:
        proc = subprocess.Popen(
            [sys.executable, str(service_script),
             "--musetalk_home", str(musetalk_home),
             "--model_root", str(model_root),
             "--port", str(port)],
            stdout=None,   # inherit worker stdout so logs appear in docker logs
            stderr=None,
            close_fds=True,
        )

    deadline = time.monotonic() + startup_timeout
    poll_interval = 3.0
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        # Check if process died immediately.
        if proc is not None:
            ret = proc.poll()
            if ret is not None:
                LOGGER.critical("Bootstrap: MuseTalk service process exited early exit_code=%s", ret)
                return False
        health_data = _read_health(2)
        if health_data:
            status = health_data.get("status")
            if status == "ready":
                elapsed = startup_timeout - (deadline - time.monotonic())
                LOGGER.info(
                    "Bootstrap: MuseTalk persistent service READY elapsed_seconds=%s port=%d health=%s",
                    round(elapsed, 1), port, health_data,
                )
                return True
            if status == "error":
                LOGGER.critical(
                    "Bootstrap: MuseTalk service model load failed health=%s",
                    health_data,
                )
                return False
            LOGGER.info(
                "Bootstrap: MuseTalk service health=%s; still loading models...",
                health_data,
            )
            continue
        try:
            with _urllib_req.urlopen(health_url, timeout=2) as resp:
                body = resp.read()
                import json as _json
                data = _json.loads(body)
                status = data.get("status")
                if status == "ready":
                    elapsed = startup_timeout - (deadline - time.monotonic())
                    LOGGER.info(
                        "Bootstrap: MuseTalk persistent service READY elapsed_seconds=%s port=%d",
                        round(elapsed, 1), port,
                    )
                    return True
                if status == "error":
                    LOGGER.critical(
                        "Bootstrap: MuseTalk service model load failed error=%s",
                        data.get("error"),
                    )
                    return False
                LOGGER.info(
                    "Bootstrap: MuseTalk service health=%s — still loading models...", status,
                )
        except Exception:
            pass  # service not yet listening

    LOGGER.critical(
        "Bootstrap: MuseTalk persistent service did not become ready within %ss",
        round(startup_timeout, 1),
    )
    return False


def main() -> int:
    _configure_logging()

    engine = str(os.environ.get("AVATAR_ENGINE", "liveportrait+musetalk") or "liveportrait+musetalk").strip().lower()
    composite_errors: list[str] = []
    if engine == "liveportrait+musetalk":
        detected_home, detected_entrypoint = _autodetect_liveportrait_runtime()
        if detected_home is not None and detected_entrypoint is not None:
            if not str(os.environ.get("AVATAR_LIVEPORTRAIT_HOME", "")).strip():
                os.environ["AVATAR_LIVEPORTRAIT_HOME"] = str(detected_home)
            if not str(os.environ.get("AVATAR_LIVEPORTRAIT_ENTRYPOINT", "")).strip():
                os.environ["AVATAR_LIVEPORTRAIT_ENTRYPOINT"] = str(detected_entrypoint)
            LOGGER.info(
                "Bootstrap: auto-detected LivePortrait runtime home=%s entrypoint=%s",
                detected_home,
                detected_entrypoint,
            )

        liveportrait_home = Path(str(os.environ.get("AVATAR_LIVEPORTRAIT_HOME", "")).strip() or "/opt/liveportrait").resolve()
        default_entrypoint = liveportrait_home / "inference.py"
        if not str(os.environ.get("AVATAR_LIVEPORTRAIT_ENTRYPOINT", "")).strip():
            os.environ["AVATAR_LIVEPORTRAIT_ENTRYPOINT"] = str(default_entrypoint)
        configured_vars, missing_vars, composite_flags = _composite_env_report()
        LOGGER.info(
            "Bootstrap composite env check: selected_engine=%s configured_vars=%s missing_vars=%s flags=%s",
            engine,
            configured_vars,
            missing_vars,
            composite_flags,
        )
        if missing_vars:
            composite_errors.append(f"missing composite env vars={missing_vars}")

        liveportrait_cmd = str(os.environ.get("AVATAR_LIVEPORTRAIT_CMD", "")).strip()
        if not _command_head_callable(liveportrait_cmd):
            composite_errors.append("AVATAR_LIVEPORTRAIT_CMD command head is not callable")

        liveportrait_entrypoint = Path(
            str(os.environ.get("AVATAR_LIVEPORTRAIT_ENTRYPOINT", "")).strip() or str(default_entrypoint)
        ).resolve()
        liveportrait_model_root = Path(
            str(os.environ.get("AVATAR_LIVEPORTRAIT_MODEL_PATH", "")).strip() or "/app/storage_local/models/liveportrait"
        ).resolve()
        if not liveportrait_home.exists():
            composite_errors.append(f"AVATAR_LIVEPORTRAIT_HOME does not exist path={liveportrait_home}")
        if _looks_like_storage_path(liveportrait_home):
            composite_errors.append(
                "AVATAR_LIVEPORTRAIT_HOME points to storage path="
                f"{liveportrait_home}; set runtime to code checkout like /opt/liveportrait"
            )
        if not liveportrait_entrypoint.exists():
            composite_errors.append(
                "LivePortrait is not installed or AVATAR_LIVEPORTRAIT_ENTRYPOINT is misconfigured "
                f"path={liveportrait_entrypoint}"
            )
        if not liveportrait_model_root.exists():
            composite_errors.append(f"AVATAR_LIVEPORTRAIT_MODEL_PATH does not exist path={liveportrait_model_root}")
        if not _liveportrait_command_references_entrypoint(liveportrait_cmd, liveportrait_entrypoint):
            composite_errors.append(
                "AVATAR_LIVEPORTRAIT_CMD must reference AVATAR_LIVEPORTRAIT_ENTRYPOINT="
                f"{liveportrait_entrypoint}"
            )
        if not composite_errors:
            sync_errors = _sync_liveportrait_weights(
                model_root=liveportrait_model_root,
                runtime_home=liveportrait_home,
            )
            composite_errors.extend(sync_errors)
            runtime_missing = _validate_liveportrait_runtime_weights(runtime_home=liveportrait_home)
            composite_errors.extend([f"missing liveportrait runtime file={p}" for p in runtime_missing])
            if not sync_errors and not runtime_missing:
                LOGGER.info(
                    "Bootstrap: LivePortrait weights synced successfully source=%s target=%s",
                    liveportrait_model_root / "liveportrait",
                    liveportrait_home / "pretrained_weights" / "liveportrait",
                )

        if _is_truthy_env("AVATAR_PREVIEW_USE_RESTORATION"):
            restore_cmd = str(os.environ.get("AVATAR_PREVIEW_RESTORE_CMD", "")).strip()
            composite_errors.extend(_validate_restoration_command(restore_cmd))

        if composite_errors:
            LOGGER.critical(
                "Bootstrap failed: canonical avatar pipeline composite errors=%s",
                composite_errors,
            )
            return 70

    musetalk_home = Path(str(os.environ.get("MUSETALK_HOME", "")).strip() or "/opt/musetalk").resolve()
    model_root_raw = Path(str(os.environ.get("MUSETALK_MODEL_PATH", "")).strip() or "/app/storage_local/models").resolve()
    model_root = _resolve_model_root(model_root_raw)

    if not musetalk_home.exists():
        LOGGER.critical("Bootstrap failed: MUSETALK_HOME does not exist path=%s", musetalk_home)
        return 70
    if not model_root.exists():
        LOGGER.critical(
            "Bootstrap failed: MUSETALK_MODEL_PATH does not exist raw=%s resolved=%s",
            model_root_raw,
            model_root,
        )
        return 70

    dwpose_dir = musetalk_home / "musetalk" / "utils" / "dwpose"
    dwpose_dir.mkdir(parents=True, exist_ok=True)
    preprocessing_py = musetalk_home / "musetalk" / "utils" / "preprocessing.py"

    referenced_dwpose_files = _extract_dwpose_refs(preprocessing_py)
    if "dw-ll_ucoco_384.pth" not in referenced_dwpose_files:
        referenced_dwpose_files.append("dw-ll_ucoco_384.pth")

    _ensure_dwpose_checkpoint(
        target_dwpose_dir=dwpose_dir,
        model_root=model_root,
        checkpoint_name="dw-ll_ucoco_384.pth",
    )

    required_model_files = [
        model_root / "sd-vae" / "config.json",
        model_root / "sd-vae" / "diffusion_pytorch_model.bin",
        model_root / "musetalkV15" / "unet.pth",
        model_root / "whisper" / "config.json",
        model_root / "whisper" / "pytorch_model.bin",
        model_root / "whisper" / "preprocessor_config.json",
        model_root / "dwpose" / "dw-ll_ucoco_384.pth",
        model_root / "face-parse-bisent" / "79999_iter.pth",
        model_root / "face-parse-bisent" / "resnet18-5c106cde.pth",
    ]

    required_dwpose_home_files = [dwpose_dir / rel for rel in referenced_dwpose_files]

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")

    missing: list[str] = []
    if ffmpeg_bin is None:
        missing.append("ffmpeg (PATH lookup)")
    if ffprobe_bin is None:
        missing.append("ffprobe (PATH lookup)")

    for p in required_model_files:
        if not p.exists():
            missing.append(str(p))
    for p in required_dwpose_home_files:
        if not p.exists():
            missing.append(str(p))

    LOGGER.info(
        "Bootstrap: dwpose checkpoint exists=%s path=%s",
        _bool_text((dwpose_dir / "dw-ll_ucoco_384.pth").exists()),
        dwpose_dir / "dw-ll_ucoco_384.pth",
    )
    LOGGER.info(
        "Bootstrap: media tools ffmpeg=%s ffprobe=%s",
        ffmpeg_bin or "missing",
        ffprobe_bin or "missing",
    )
    LOGGER.info(
        "Bootstrap: MuseTalk home complete=%s home=%s model_root=%s dwpose_refs=%s",
        _bool_text(len(missing) == 0),
        musetalk_home,
        model_root,
        referenced_dwpose_files,
    )

    if missing:
        LOGGER.critical("Bootstrap failed: missing required MuseTalk assets/files count=%s", len(missing))
        LOGGER.critical("Bootstrap failed: missing_files=%s", missing)
        return 70

    try:
        runtime_versions = _check_runtime_imports()
    except Exception as exc:
        LOGGER.critical("Bootstrap failed: real backend import check failed error=%s", exc)
        return 70

    # Start the persistent MuseTalk service before handing off to Celery.
    # The service loads models once; subsequent preview jobs skip cold-start.
    service_ok = _start_musetalk_service(musetalk_home=musetalk_home, model_root=model_root)
    if not service_ok and not _is_truthy_env("AVATAR_MUSETALK_STANDALONE_FALLBACK", "0"):
        LOGGER.critical(
            "Bootstrap: MuseTalk persistent service is not ready and standalone fallback is disabled; "
            "preview jobs will fail clearly with musetalk_service_unavailable until service is healthy"
        )
        warmup_exit = 0
    elif not service_ok:
        LOGGER.critical(
            "Bootstrap: MuseTalk persistent service did not start — "
            "worker will fall back to warmup subprocess approach"
        )
        warmup_exit = _warmup_musetalk(musetalk_home=musetalk_home, model_root=model_root)
        if warmup_exit != 0:
            LOGGER.critical(
                "Bootstrap: MuseTalk warmup also failed exit_code=%s — worker starting anyway; "
                "first preview job will pay full cold-start cost",
                warmup_exit,
            )
    else:
        LOGGER.info("Bootstrap: MuseTalk persistent service is ready — skipping warmup subprocess")
        warmup_exit = 0

    LOGGER.info(
        "Bootstrap passed: worker is ready with engine=%s runtime=%s warmup_ok=%s",
        engine,
        runtime_versions,
        warmup_exit == 0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
