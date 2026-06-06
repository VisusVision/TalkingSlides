from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    return float(parsed)


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        return int(default)
    return int(parsed)


def _history_path() -> Path:
    raw = str(os.environ.get("AVATAR_ORCH_METRICS_FILE", "storage_local/avatar_stage_metrics.json")).strip()
    if not raw:
        raw = "storage_local/avatar_stage_metrics.json"
    return Path(raw)


def _history_limit() -> int:
    return max(_safe_int(os.environ.get("AVATAR_ORCH_HISTORY_LIMIT", "64"), 64), 8)


def _history_enabled() -> bool:
    return str(os.environ.get("AVATAR_ORCH_USE_RECENT_RUNS", "1")).strip().lower() in {"1", "true", "yes", "on"}


def _read_history() -> dict[str, Any]:
    path = _history_path()
    if not path.exists() or not path.is_file():
        return {"version": 1, "stages": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "stages": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "stages": {}}
    payload.setdefault("version", 1)
    payload.setdefault("stages", {})
    if not isinstance(payload.get("stages"), dict):
        payload["stages"] = {}
    return payload


def _write_history(payload: dict[str, Any]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _parse_meminfo_linux() -> tuple[int, int] | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists() or not meminfo_path.is_file():
        return None
    total_kib = 0
    available_kib = 0
    try:
        for raw_line in meminfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = str(raw_line).strip()
            if line.startswith("MemTotal:"):
                total_kib = _safe_int(line.split()[1], 0)
            elif line.startswith("MemAvailable:"):
                available_kib = _safe_int(line.split()[1], 0)
    except Exception:
        return None
    if total_kib <= 0:
        return None
    if available_kib <= 0:
        available_kib = max(total_kib // 5, 0)
    total_mib = int(round(total_kib / 1024.0))
    available_mib = int(round(available_kib / 1024.0))
    return total_mib, available_mib


def _probe_system_memory() -> dict[str, Any]:
    total_mib = 0
    available_mib = 0
    source = "unknown"

    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        total_mib = int(round(float(vm.total) / (1024.0 * 1024.0)))
        available_mib = int(round(float(vm.available) / (1024.0 * 1024.0)))
        source = "psutil"
    except Exception:
        parsed = _parse_meminfo_linux()
        if parsed is not None:
            total_mib, available_mib = parsed
            source = "proc_meminfo"

    used_mib = max(total_mib - available_mib, 0)
    return {
        "total_mib": int(total_mib),
        "available_mib": int(available_mib),
        "used_mib": int(used_mib),
        "source": source,
    }


def _probe_gpu_state() -> dict[str, Any]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=4,
        )
    except Exception as exc:
        return {
            "available": False,
            "reason": f"nvidia_smi_unavailable:{exc}",
            "devices": [],
            "selected": {},
        }

    if int(result.returncode) != 0:
        return {
            "available": False,
            "reason": str(result.stderr or result.stdout or "nvidia_smi_failed").strip(),
            "devices": [],
            "selected": {},
        }

    devices: list[dict[str, Any]] = []
    for raw_line in (result.stdout or "").splitlines():
        line = str(raw_line).strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        index = _safe_int(parts[0], 0)
        name = str(parts[1] or "").strip()
        total_mib = _safe_int(parts[2], 0)
        free_mib = _safe_int(parts[3], 0)
        utilization_pct = _safe_int(parts[4], 0)
        devices.append(
            {
                "index": int(index),
                "name": name,
                "total_mib": int(total_mib),
                "free_mib": int(free_mib),
                "utilization_pct": int(utilization_pct),
            }
        )

    if not devices:
        return {
            "available": False,
            "reason": "no_gpu_rows",
            "devices": [],
            "selected": {},
        }

    selected = devices[0]
    requested_index_raw = str(os.environ.get("CUDA_VISIBLE_DEVICES", "")).strip()
    if requested_index_raw and requested_index_raw not in {"all", "-1"}:
        try:
            requested_index = _safe_int(requested_index_raw.split(",")[0], 0)
            matched = [device for device in devices if int(device.get("index") or -1) == requested_index]
            if matched:
                selected = matched[0]
        except Exception:
            selected = devices[0]

    return {
        "available": True,
        "reason": "ok",
        "devices": devices,
        "selected": selected,
    }


def probe_runtime_resources() -> dict[str, Any]:
    snapshot = {
        "captured_at_epoch": round(float(time.time()), 3),
        "system": _probe_system_memory(),
        "gpu": _probe_gpu_state(),
    }
    return snapshot


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(float(value) for value in values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    clamped_p = min(max(float(p), 0.0), 1.0)
    rank = clamped_p * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    if low == high:
        return float(sorted_values[low])
    ratio = rank - low
    return float(sorted_values[low] + ((sorted_values[high] - sorted_values[low]) * ratio))


def recent_success_durations(stage_name: str, *, limit: int = 8) -> list[float]:
    if not _history_enabled():
        return []
    payload = _read_history()
    stage_records = list((payload.get("stages") or {}).get(stage_name) or [])
    if not stage_records:
        return []
    selected: list[float] = []
    for record in reversed(stage_records):
        if not bool(record.get("success")):
            continue
        elapsed_seconds = _safe_float(record.get("elapsed_seconds"), 0.0)
        if elapsed_seconds <= 0.0:
            continue
        selected.append(float(elapsed_seconds))
        if len(selected) >= max(int(limit), 1):
            break
    selected.reverse()
    return selected


def _gpu_pressure_multiplier(resources: dict[str, Any] | None) -> tuple[float, dict[str, Any]]:
    if not isinstance(resources, dict):
        return 1.0, {"reason": "no_snapshot", "gpu_free_mib": 0, "gpu_total_mib": 0}

    gpu = dict(resources.get("gpu") or {})
    selected = dict(gpu.get("selected") or {})
    if not bool(gpu.get("available")) or not selected:
        cpu_multiplier = _safe_float(os.environ.get("AVATAR_ORCH_CPU_TIMEOUT_MULTIPLIER", "1.0"), 1.0)
        return max(cpu_multiplier, 1.0), {"reason": "cpu_or_unknown", "gpu_free_mib": 0, "gpu_total_mib": 0}

    free_mib = _safe_int(selected.get("free_mib"), 0)
    total_mib = max(_safe_int(selected.get("total_mib"), 0), 1)
    ratio = float(free_mib) / float(total_mib)

    low_headroom_mib = _safe_int(os.environ.get("AVATAR_ORCH_GPU_LOW_HEADROOM_MIB", "1800"), 1800)
    tight_headroom_mib = _safe_int(os.environ.get("AVATAR_ORCH_GPU_TIGHT_HEADROOM_MIB", "3000"), 3000)

    multiplier = 1.0
    reason = "headroom_ok"
    if free_mib <= low_headroom_mib or ratio < 0.16:
        multiplier = 1.55
        reason = "headroom_critical"
    elif free_mib <= tight_headroom_mib or ratio < 0.28:
        multiplier = 1.30
        reason = "headroom_tight"
    elif ratio < 0.40:
        multiplier = 1.15
        reason = "headroom_moderate"

    return float(multiplier), {
        "reason": reason,
        "gpu_free_mib": int(free_mib),
        "gpu_total_mib": int(total_mib),
        "gpu_free_ratio": round(float(ratio), 4),
    }


def compute_adaptive_timeout(
    *,
    stage_name: str,
    audio_duration_seconds: float,
    frame_count: int,
    base_seconds: float,
    min_seconds: float,
    max_seconds: float,
    per_audio_second: float,
    per_frame_second: float,
    explicit_timeout_seconds: float = 0.0,
    resources: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    safe_stage = str(stage_name or "stage")
    clamped_audio = max(float(audio_duration_seconds), 0.0)
    clamped_frames = max(int(frame_count), 0)

    timeout_floor = max(float(min_seconds), 1.0)
    timeout_ceiling = max(float(max_seconds), timeout_floor)

    explicit_value = float(explicit_timeout_seconds or 0.0)
    if explicit_value > 0.0:
        chosen = min(timeout_ceiling, explicit_value)
        return float(chosen), {
            "stage": safe_stage,
            "source": "explicit",
            "explicit_timeout_seconds": round(float(explicit_value), 4),
            "audio_duration_seconds": round(float(clamped_audio), 4),
            "frame_count": int(clamped_frames),
            "timeout_seconds": round(float(chosen), 4),
        }

    predicted_from_inputs = float(base_seconds) + (clamped_audio * float(per_audio_second)) + (clamped_frames * float(per_frame_second))

    history_limit = max(_safe_int(os.environ.get("AVATAR_ORCH_RECENT_SUCCESS_LIMIT", "8"), 8), 1)
    history_durations = recent_success_durations(safe_stage, limit=history_limit)
    history_p80 = _percentile(history_durations, 0.80)
    history_p95 = _percentile(history_durations, 0.95)
    history_safety = _safe_float(os.environ.get("AVATAR_ORCH_HISTORY_SAFETY_MULTIPLIER", "1.20"), 1.20)
    history_budget = max(history_p80, history_p95) * max(history_safety, 1.0)

    pre_pressure_timeout = max(predicted_from_inputs, history_budget, timeout_floor)

    pressure_multiplier, pressure_meta = _gpu_pressure_multiplier(resources)
    global_safety = max(_safe_float(os.environ.get("AVATAR_ORCH_TIMEOUT_SAFETY_MULTIPLIER", "1.0"), 1.0), 1.0)

    computed_timeout = pre_pressure_timeout * pressure_multiplier * global_safety
    chosen_timeout = max(timeout_floor, min(timeout_ceiling, computed_timeout))

    reason = {
        "stage": safe_stage,
        "source": "adaptive",
        "audio_duration_seconds": round(float(clamped_audio), 4),
        "frame_count": int(clamped_frames),
        "base_seconds": round(float(base_seconds), 4),
        "per_audio_second": round(float(per_audio_second), 4),
        "per_frame_second": round(float(per_frame_second), 4),
        "predicted_from_inputs_seconds": round(float(predicted_from_inputs), 4),
        "history_samples": [round(float(value), 4) for value in history_durations],
        "history_p80_seconds": round(float(history_p80), 4),
        "history_p95_seconds": round(float(history_p95), 4),
        "history_budget_seconds": round(float(history_budget), 4),
        "pre_pressure_timeout_seconds": round(float(pre_pressure_timeout), 4),
        "pressure_multiplier": round(float(pressure_multiplier), 4),
        "pressure_reason": str(pressure_meta.get("reason") or "unknown"),
        "gpu_free_mib": int(pressure_meta.get("gpu_free_mib") or 0),
        "gpu_total_mib": int(pressure_meta.get("gpu_total_mib") or 0),
        "global_safety_multiplier": round(float(global_safety), 4),
        "min_timeout_seconds": round(float(timeout_floor), 4),
        "max_timeout_seconds": round(float(timeout_ceiling), 4),
        "timeout_seconds": round(float(chosen_timeout), 4),
    }
    return float(chosen_timeout), reason


def record_stage_timing(
    *,
    stage_name: str,
    elapsed_seconds: float,
    success: bool,
    audio_duration_seconds: float = 0.0,
    frame_count: int = 0,
    resources: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    if not _history_enabled():
        return
    safe_stage = str(stage_name or "stage")
    elapsed = max(float(elapsed_seconds), 0.0)

    payload = _read_history()
    stage_records = list((payload.get("stages") or {}).get(safe_stage) or [])

    record = {
        "epoch": round(float(time.time()), 3),
        "elapsed_seconds": round(float(elapsed), 4),
        "success": bool(success),
        "audio_duration_seconds": round(max(float(audio_duration_seconds), 0.0), 4),
        "frame_count": int(max(int(frame_count), 0)),
        "resources": dict(resources or {}),
        "context": dict(context or {}),
    }
    stage_records.append(record)

    max_records = _history_limit()
    if len(stage_records) > max_records:
        stage_records = stage_records[-max_records:]

    payload.setdefault("stages", {})
    payload["stages"][safe_stage] = stage_records

    try:
        _write_history(payload)
    except Exception:
        logger.exception("Avatar resource manager failed to persist stage history stage=%s", safe_stage)


def release_stage_resources(*, reason: str, include_torch: bool = True) -> dict[str, Any]:
    before = probe_runtime_resources()
    gc_collected = 0
    try:
        gc_collected = int(gc.collect())
    except Exception:
        gc_collected = 0

    torch_meta: dict[str, Any] = {
        "attempted": bool(include_torch),
        "available": False,
        "cuda_available": False,
        "cache_cleared": False,
        "error": "",
    }
    if include_torch:
        try:
            import torch  # type: ignore

            torch_meta["available"] = True
            cuda_available = bool(torch.cuda.is_available())
            torch_meta["cuda_available"] = cuda_available
            if cuda_available:
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
                torch_meta["cache_cleared"] = True
        except Exception as exc:
            torch_meta["error"] = str(exc)

    after = probe_runtime_resources()
    cleanup_payload = {
        "reason": str(reason or ""),
        "gc_collected": int(gc_collected),
        "torch": torch_meta,
        "before": before,
        "after": after,
    }

    logger.info(
        "Avatar resource cleanup reason=%s gc_collected=%s torch_cache_cleared=%s before_gpu_free=%s after_gpu_free=%s before_mem_available=%s after_mem_available=%s",
        str(reason or ""),
        int(gc_collected),
        bool((cleanup_payload.get("torch") or {}).get("cache_cleared")),
        int((((before.get("gpu") or {}).get("selected") or {}).get("free_mib") or 0)),
        int((((after.get("gpu") or {}).get("selected") or {}).get("free_mib") or 0)),
        int(((before.get("system") or {}).get("available_mib") or 0)),
        int(((after.get("system") or {}).get("available_mib") or 0)),
    )
    return cleanup_payload
