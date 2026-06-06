"""Persistent MuseTalk inference service.

Loads VAE, UNet, PositionalEncoding, Whisper, and FaceParsing ONCE at startup.
Subsequent inference requests skip the entire cold-start cost.

Protocol:
  GET  /health  → 200 {"status":"ready"} or 503 {"status":"loading"}
  POST /infer   → 200 {"success":true,"elapsed_seconds":N} or 500 {"success":false,"error":"..."}

Environment variables:
  MUSETALK_HOME          — path to MuseTalk checkout (default /opt/musetalk)
  MUSETALK_MODEL_PATH    — path to model weights root (default /app/storage_local/models)
  AVATAR_MUSETALK_SERVICE_PORT — TCP port to bind (default 17860)
"""
from __future__ import annotations

import argparse
import copy
import gc
import glob
import hashlib
import json
import logging
import os
import pickle
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("musetalk.service")

# ---------------------------------------------------------------------------
# Global model state (populated once by _load_models)
# ---------------------------------------------------------------------------
_models_loaded = threading.Event()
_models_error: str | None = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()

_device = None
_vae = None
_unet = None
_pe = None
_timesteps = None
_weight_dtype = None
_whisper = None
_audio_processor = None
_fp = None
_fp_cheek_widths: tuple[int, int] | None = None
_workspace: Path | None = None   # stable dir with CWD-relative model symlinks
_model_load_started_at: float = 0.0
_model_load_finished_at: float = 0.0
_model_load_seconds: float = 0.0
_cuda_available: bool = False
_cuda_device: str = ""
_provider_diagnostics: dict[str, object] = {}
_service_use_float16: bool = True


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _apply_guards() -> None:
    """Apply torch.load and mmengine compat patches needed by MuseTalk weights."""
    try:
        import torch
        orig_load = torch.load
        def _safe_load(*a, **kw):
            kw.setdefault("weights_only", False)
            return orig_load(*a, **kw)
        torch.load = _safe_load
    except Exception:
        pass
    try:
        from mmengine.registry import Registry
        _orig = Registry._register_module
        def _patched(self, module, module_name=None, force=False):
            try:
                return _orig(self, module, module_name=module_name, force=force)
            except KeyError as exc:
                if "already registered in optimizer" in str(exc) and "Adafactor" in str(exc):
                    return None
                raise
        Registry._register_module = _patched
    except Exception:
        pass


def _build_workspace(*, musetalk_home: Path, model_root: Path) -> Path:
    ws = Path("/tmp/musetalk-service-ws")
    ws.mkdir(parents=True, exist_ok=True)

    def _link_or_copy(src: Path, dst: Path, is_dir: bool) -> None:
        if dst.exists() or dst.is_symlink():
            return
        try:
            dst.symlink_to(src, target_is_directory=is_dir)
        except Exception:
            if is_dir:
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    _link_or_copy(model_root, ws / "models", is_dir=True)
    _link_or_copy(musetalk_home / "musetalk", ws / "musetalk", is_dir=True)

    # Some MuseTalk distributions ship musetalk.json instead of config.json.
    musetalk_model_dir = ws / "models" / "musetalk"
    if musetalk_model_dir.exists():
        config_json = musetalk_model_dir / "config.json"
        legacy_json = musetalk_model_dir / "musetalk.json"
        if not config_json.exists() and legacy_json.exists():
            shutil.copy2(legacy_json, config_json)

    return ws


def _is_debug_enabled() -> bool:
    return str(os.environ.get("AVATAR_PREVIEW_DIAGNOSTIC_MODE", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _read_float_env(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip() or str(default))
    except Exception:
        return float(default)


def _stage_timeout_seconds(stage_name: str, default_seconds: float) -> float:
    specific_name = f"MUSETALK_SERVICE_STAGE_TIMEOUT_{stage_name.upper()}_SECONDS"
    return max(
        _read_float_env(
            specific_name,
            _read_float_env("MUSETALK_SERVICE_STAGE_TIMEOUT_DEFAULT_SECONDS", default_seconds),
        ),
        10.0,
    )


def _request_stage_timeout_floor(params: dict[str, Any]) -> float:
    return max(
        _float_param(params, "stage_budget_timeout_seconds", 0.0),
        _float_param(params, "chunk_timeout_seconds", 0.0),
        _float_param(params, "idle_timeout_seconds", 0.0),
        0.0,
    )


def _cuda_memory_snapshot() -> dict[str, float]:
    snapshot: dict[str, float] = {}
    try:
        import torch

        if torch.cuda.is_available():
            snapshot["cuda_allocated_mib"] = round(float(torch.cuda.memory_allocated(0)) / (1024.0 * 1024.0), 2)
            snapshot["cuda_reserved_mib"] = round(float(torch.cuda.memory_reserved(0)) / (1024.0 * 1024.0), 2)
            total = float(torch.cuda.get_device_properties(0).total_memory) / (1024.0 * 1024.0)
            snapshot["cuda_total_mib"] = round(total, 2)
    except Exception:
        pass
    return snapshot


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _debug_sidecar_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".musetalk_debug.json")


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_param(params: dict, name: str, default: int) -> int:
    try:
        return int(str(params.get(name, default)).strip() or str(default))
    except Exception:
        return int(default)


def _float_param(params: dict, name: str, default: float) -> float:
    try:
        return float(str(params.get(name, default)).strip() or str(default))
    except Exception:
        return float(default)


def _probe_media(path: Path | str) -> dict[str, Any]:
    media_path = Path(path)
    if not media_path.exists():
        return {"path": str(media_path), "exists": False}
    payload: dict[str, Any] = {"path": str(media_path), "exists": True}
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(media_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode != 0:
            return payload
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if streams:
            stream = streams[0]
            for key in ("width", "height", "nb_frames"):
                if key in stream:
                    try:
                        payload[key] = int(stream[key])
                    except Exception:
                        payload[key] = stream[key]
            for key in ("r_frame_rate", "avg_frame_rate"):
                if key in stream:
                    payload[key] = str(stream[key])
            if stream.get("duration") not in {None, "N/A"}:
                payload["stream_duration_seconds"] = round(float(stream.get("duration") or 0.0), 6)
        fmt = data.get("format") or {}
        if fmt.get("duration") not in {None, "N/A"}:
            payload["duration_seconds"] = round(float(fmt.get("duration") or 0.0), 6)
    except Exception:
        pass
    return payload


def _media_duration_seconds(path: str | Path) -> float:
    info = _probe_media(path)
    return float(info.get("duration_seconds") or info.get("stream_duration_seconds") or 0.0)


def _landmark_cache_dir() -> Path | None:
    raw = str(os.environ.get("MUSETALK_LANDMARK_CACHE_DIR", "") or "").strip()
    if not raw:
        return None
    cache_dir = Path(raw)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _landmark_cache_path(*, source_sha256: str, bbox_shift: int, parsing_mode: str, version: str) -> Path | None:
    cache_dir = _landmark_cache_dir()
    if cache_dir is None or not source_sha256:
        return None
    key = f"{source_sha256}_{bbox_shift}_{parsing_mode}_{version}.pkl"
    return cache_dir / key


def _prepare_preview_fast_source(
    *,
    source_path: Path,
    source_kind: str,
    work_dir: Path,
    params: dict,
) -> tuple[Path, dict[str, Any]]:
    before = _probe_media(source_path)
    requested = _truthy(params.get("preview_fast_mode"), False)
    auto_downscale = _truthy(params.get("auto_downscale"), False) or _truthy(
        os.environ.get("MUSETALK_AUTO_DOWNSCALE"),
        False,
    )
    max_width = max(_int_param(params, "preview_max_width", 512), 256)
    source_width = int(before.get("width") or 0)
    should_downscale = bool(requested or (auto_downscale and source_width > max_width))
    info: dict[str, Any] = {
        "enabled": bool(should_downscale),
        "max_width": int(max_width),
        "auto_downscale": bool(auto_downscale),
        "original_path": str(source_path),
        "prepared_path": str(source_path),
        "source_kind": str(source_kind),
        "source_resolution_before": before,
        "source_resolution_after": before,
        "used": False,
        "error": "",
    }
    if not should_downscale:
        return source_path, info

    scaled_path = work_dir / ("preview_source_fast.mp4" if source_kind == "video" else "preview_source_fast.png")
    vf = f"scale='min({max_width},iw)':'-2':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd = ["ffmpeg", "-y", "-i", str(source_path), "-vf", vf]
    if source_kind == "video":
        cmd.extend(["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"])
    cmd.append(str(scaled_path))
    try:
        _run_ffmpeg_stage(
            stage_name="preview_fast_source_prepare",
            command=cmd,
            expected_output=scaled_path,
            timeout_seconds=_stage_timeout_seconds("preview_fast_source_prepare", 240.0),
        )
    except Exception as exc:
        info["error"] = str(exc)
        logger.warning(
            "MuseTalk service: preview fast source preparation failed source_kind=%s max_width=%s reason=fallback_to_original error=%s",
            source_kind,
            max_width,
            exc,
        )
        return source_path, info

    after = _probe_media(scaled_path)
    info.update({
        "prepared_path": str(scaled_path),
        "source_resolution_after": after,
        "used": True,
    })
    logger.info(
        "MuseTalk service: preview fast source prepared source_kind=%s max_width=%s original=%s prepared=%s before=%s after=%s",
        source_kind,
        max_width,
        source_path,
        scaled_path,
        before,
        after,
    )
    return scaled_path, info


def _configure_face_parser_for_request(fp: Any, *, left_cheek_width: int, right_cheek_width: int) -> Any:
    global _fp_cheek_widths
    widths = (int(left_cheek_width), int(right_cheek_width))
    if fp is None or _fp_cheek_widths == widths:
        return fp
    if hasattr(fp, "_create_cheek_mask"):
        fp.cheek_mask = fp._create_cheek_mask(
            left_cheek_width=widths[0],
            right_cheek_width=widths[1],
        )
        _fp_cheek_widths = widths
        logger.info(
            "MuseTalk service: face parser cheek widths applied left=%s right=%s",
            widths[0],
            widths[1],
        )
        return fp
    logger.warning(
        "MuseTalk service: face parser cannot update cheek widths dynamically requested_left=%s requested_right=%s",
        widths[0],
        widths[1],
    )
    return fp


def _health_payload() -> dict[str, object]:
    if _models_error:
        status = "error"
    elif _models_loaded.is_set():
        status = "ready"
    else:
        status = "loading"
    return {
        "status": status,
        "process_alive": True,
        "models_loaded": bool(_models_loaded.is_set() and not _models_error),
        "models_error": _models_error or "",
        "cuda_available": bool(_cuda_available),
        "cuda_device": _cuda_device,
        "ready_for_inference": bool(status == "ready"),
        "model_load_started_at": round(float(_model_load_started_at), 6) if _model_load_started_at else 0.0,
        "model_load_finished_at": round(float(_model_load_finished_at), 6) if _model_load_finished_at else 0.0,
        "model_load_seconds": round(float(_model_load_seconds), 3) if _model_load_seconds else 0.0,
        "provider_diagnostics": dict(_provider_diagnostics),
        "use_float16": bool(_service_use_float16),
        "face_parser_cheek_widths": list(_fp_cheek_widths or ()),
        "memory_snapshot": _cuda_memory_snapshot(),
    }


def _write_debug_sidecar(
    *,
    output_path: Path,
    source_image: str,
    source_video: str,
    selected_source: str,
    audio_path: str,
    params: dict,
    run: dict,
    stage_timings: dict[str, float],
    elapsed_seconds: float,
    runtime_info: dict[str, Any] | None = None,
) -> None:
    selected_source_path = Path(selected_source)
    audio = Path(audio_path)
    runtime_payload = dict(runtime_info or {})
    payload = {
        "route": "service",
        "musetalk_run_id": str(run.get("run_id") or ""),
        "started_epoch": run.get("started_epoch", ""),
        "input_reference_image_path": str(source_image or ""),
        "input_reference_video_path": str(source_video or ""),
        "source_video_original_path": str(source_video or ""),
        "selected_source_path": str(selected_source_path),
        "preview_fast_source_path": str(runtime_payload.get("preview_fast_source_path") or ""),
        "input_audio_path": str(audio),
        "input_reference_image_sha256": str(run.get("source_image_sha256") or ""),
        "input_reference_video_sha256": str(run.get("source_video_sha256") or ""),
        "input_source_sha256": str(run.get("source_sha256") or ""),
        "input_audio_sha256": str(run.get("audio_sha256") or ""),
        "selected_musetalk_params": dict(params),
        "runtime_settings": dict(params),
        "service_route": True,
        "device": str(runtime_payload.get("device") or _cuda_device or ""),
        "fp16": bool(runtime_payload.get("use_float16", _service_use_float16)),
        "use_float16": bool(runtime_payload.get("use_float16", _service_use_float16)),
        "batch_size": int(runtime_payload.get("batch_size") or params.get("batch_size") or 0),
        "requested_batch_size": int(runtime_payload.get("requested_batch_size") or params.get("batch_size") or 0),
        "version": str(params.get("version") or "v15"),
        "chunk_count": int(runtime_payload.get("chunk_count") or params.get("estimated_chunk_count") or 1),
        "source_resolution_before": dict(runtime_payload.get("source_resolution_before") or {}),
        "source_resolution_after": dict(runtime_payload.get("source_resolution_after") or {}),
        "source_preprocessing": dict(runtime_payload.get("source_preprocessing") or {}),
        "per_frame_timings": dict(runtime_payload.get("per_frame_timings") or {}),
        "stage_timings": dict(stage_timings),
        "model_load_seconds": 0.0,
        "service_model_load_seconds": round(float(_model_load_seconds), 3) if _model_load_seconds else 0.0,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "output_path": str(output_path),
    }
    if not payload["input_reference_image_sha256"] and source_image and Path(source_image).exists():
        payload["input_reference_image_sha256"] = _sha256_file(Path(source_image))
    if not payload["input_reference_video_sha256"] and source_video and Path(source_video).exists():
        payload["input_reference_video_sha256"] = _sha256_file(Path(source_video))
    if not payload["input_source_sha256"] and selected_source_path.exists():
        payload["input_source_sha256"] = _sha256_file(selected_source_path)
    if not payload["input_audio_sha256"] and audio.exists():
        payload["input_audio_sha256"] = _sha256_file(audio)
    _debug_sidecar_path(output_path).write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _assert_onnxruntime_cuda_provider() -> dict[str, object]:
    require_cuda = str(os.environ.get("MUSETALK_REQUIRE_CUDA_PROVIDER", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    try:
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "musetalk_provider_check_failed stage=provider_setup reason=onnxruntime_import_failed "
            f"error={exc}"
        ) from exc

    providers: list[str] = []
    try:
        providers = list(ort.get_available_providers())
    except Exception as exc:
        raise RuntimeError(
            "musetalk_provider_check_failed stage=provider_setup reason=provider_query_failed "
            f"error={exc}"
        ) from exc

    try:
        device = str(getattr(ort, "get_device", lambda: "unknown")())
    except Exception:
        device = "unknown"

    has_cuda_provider = "CUDAExecutionProvider" in providers
    if require_cuda and not has_cuda_provider:
        raise RuntimeError(
            "musetalk_provider_check_failed stage=provider_setup reason=missing_cuda_execution_provider "
            f"available_providers={providers} device={device}"
        )
    return {
        "require_cuda_provider": bool(require_cuda),
        "available_providers": providers,
        "device": device,
        "cuda_provider_available": bool(has_cuda_provider),
    }


def _runtime_cleanup(*, stage_name: str, include_cuda: bool = True) -> dict[str, object]:
    gc_collected = 0
    try:
        gc_collected = int(gc.collect())
    except Exception:
        gc_collected = 0

    torch_cache_cleared = False
    torch_error = ""
    if include_cuda:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch_cache_cleared = True
        except Exception as exc:
            torch_error = str(exc)

    snapshot = _cuda_memory_snapshot()
    logger.info(
        "MuseTalk service: runtime cleanup stage=%s gc_collected=%s torch_cache_cleared=%s snapshot=%s",
        stage_name,
        gc_collected,
        torch_cache_cleared,
        snapshot,
    )
    return {
        "stage_name": stage_name,
        "gc_collected": gc_collected,
        "torch_cache_cleared": torch_cache_cleared,
        "torch_error": torch_error,
        "memory_snapshot": snapshot,
    }


def _persist_failure_workspace(
    *,
    output_path: Path,
    workspace_path: Path,
    stage_name: str,
    error: Exception,
    commands: list[list[str]] | None = None,
) -> None:
    if not _is_debug_enabled():
        return
    try:
        diag_root = output_path.parent / "musetalk_runtime" / output_path.stem / "service_failed_run"
        diag_root.mkdir(parents=True, exist_ok=True)
        if workspace_path.exists():
            shutil.copytree(workspace_path, diag_root / "workspace", dirs_exist_ok=True)
        failure_meta = {
            "failure_reason": "musetalk_service_stage_failed",
            "stage_name": stage_name,
            "error": str(error),
            "output_path": str(output_path),
            "commands": [" ".join(cmd) for cmd in (commands or [])],
        }
        (diag_root / "failure_meta.json").write_text(
            json.dumps(failure_meta, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        logger.warning("MuseTalk service failure artifacts preserved path=%s", diag_root)
    except Exception as persist_exc:
        logger.warning("MuseTalk service failed to preserve artifacts reason=%s", persist_exc)


def _run_ffmpeg_stage(
    *,
    stage_name: str,
    command: list[str],
    expected_output: Path | None = None,
    timeout_seconds: float | None = None,
) -> None:
    run_timeout = float(timeout_seconds) if timeout_seconds is not None else _stage_timeout_seconds(stage_name, 240.0)
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=run_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"musetalk_stage_timeout stage={stage_name} timeout_seconds={run_timeout:.1f} "
            f"command={' '.join(command)}"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"musetalk_stage={stage_name} return_code={int(proc.returncode)} "
            f"command={' '.join(command)} stderr_tail={str(proc.stderr or '')[-400:]}"
        )
    if expected_output is not None:
        if (not expected_output.exists()) or expected_output.stat().st_size <= 0:
            raise RuntimeError(
                f"musetalk_stage={stage_name} output_missing_or_empty output_path={expected_output} "
                f"command={' '.join(command)}"
            )


def _load_models(*, musetalk_home: Path, model_root: Path, gpu_id: int = 0) -> None:
    global _device, _vae, _unet, _pe, _timesteps, _weight_dtype
    global _whisper, _audio_processor, _fp, _workspace, _models_error
    global _model_load_started_at, _model_load_finished_at, _model_load_seconds
    global _cuda_available, _cuda_device, _provider_diagnostics
    global _fp_cheek_widths, _service_use_float16

    t_start = time.monotonic()
    _model_load_started_at = time.time()
    _model_load_finished_at = 0.0
    _model_load_seconds = 0.0
    try:
        _apply_guards()
        provider_diagnostics = _assert_onnxruntime_cuda_provider()
        _provider_diagnostics = dict(provider_diagnostics)
        logger.info("MuseTalk service: provider diagnostics %s", provider_diagnostics)
        ws = _build_workspace(musetalk_home=musetalk_home, model_root=model_root)

        if str(musetalk_home) not in sys.path:
            sys.path.insert(0, str(musetalk_home))

        old_cwd = Path.cwd()
        os.chdir(ws)
        try:
            import torch
            from musetalk.utils.utils import load_all_model        # type: ignore
            from musetalk.utils.audio_processor import AudioProcessor  # type: ignore
            from musetalk.utils.face_parsing import FaceParsing        # type: ignore
            from transformers import WhisperModel                       # type: ignore

            device = torch.device(f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu")
            _cuda_available = bool(torch.cuda.is_available())
            _cuda_device = str(device)
            logger.info("MuseTalk service: loading models device=%s", device)

            t0 = time.monotonic()
            vae, unet, pe = load_all_model(
                unet_model_path="models/musetalkV15/unet.pth",
                vae_type="sd-vae",
                unet_config="models/musetalk/config.json",
                device=device,
            )
            logger.info("MuseTalk service: load_all_model done elapsed=%.1fs", time.monotonic() - t0)

            use_float16 = _truthy(os.environ.get("MUSETALK_USE_FLOAT16", "1"), True)
            weight_dtype = torch.float16 if use_float16 else torch.float32
            _service_use_float16 = bool(use_float16)
            # Match the standalone --use_float16 path: VAE, UNet, PE, and Whisper
            # use the same dtype so the service does not take a divergent runtime path.
            vae.vae = vae.vae.to(device=device, dtype=weight_dtype)
            unet.model = unet.model.to(device=device, dtype=weight_dtype)
            pe = pe.to(device=device, dtype=weight_dtype)
            timesteps = torch.tensor([0], device=device)
            torch.cuda.empty_cache()

            t1 = time.monotonic()
            audio_processor = AudioProcessor(feature_extractor_path="models/whisper")
            whisper = WhisperModel.from_pretrained("models/whisper")
            whisper = whisper.to(device=device, dtype=weight_dtype).eval()
            whisper.requires_grad_(False)
            logger.info("MuseTalk service: whisper ready elapsed=%.1fs", time.monotonic() - t1)

            t2 = time.monotonic()
            try:
                left_cheek_width = int(os.environ.get("MUSETALK_LEFT_CHEEK_WIDTH", "90"))
            except Exception:
                left_cheek_width = 90
            try:
                right_cheek_width = int(os.environ.get("MUSETALK_RIGHT_CHEEK_WIDTH", "90"))
            except Exception:
                right_cheek_width = 90
            fp = FaceParsing(left_cheek_width=left_cheek_width, right_cheek_width=right_cheek_width)
            _fp_cheek_widths = (left_cheek_width, right_cheek_width)
            logger.info("MuseTalk service: face_parsing ready elapsed=%.1fs", time.monotonic() - t2)

            # Eagerly import preprocessing while CWD=workspace so the module-level
            # init_model('./musetalk/utils/dwpose/...') call can find its config.
            # After this import the module is cached in sys.modules and re-imports
            # in _infer() are no-ops that don't need a specific CWD.
            t3 = time.monotonic()
            from musetalk.utils.preprocessing import (  # type: ignore  # noqa: F401
                get_landmark_and_bbox, read_imgs, coord_placeholder,
            )
            logger.info("MuseTalk service: preprocessing/dwpose ready elapsed=%.1fs", time.monotonic() - t3)

            # — DWPose CUDA warmup — (Fix H)
            # DWPose's wholebody estimator compiles CUDA kernels on the very first
            # forward pass. If that first pass happens in the HTTP-server thread
            # (a different CUDA context than the loader thread), it deadlocks.
            # Forcing a dummy inference here, in the loader thread, pre-compiles
            # all kernels so subsequent calls in any thread are instant.
            t4 = time.monotonic()
            try:
                import numpy as np  # type: ignore
                import cv2  # type: ignore

                warmup_path = ws / "_dwpose_warmup.png"

                # Prefer a real preview frame (if present) to reliably exercise
                # DWPose and precompile CUDA kernels. Fall back to a synthetic
                # image only if no candidate preview is available.
                candidate = None
                try:
                    avatars_dir = Path("/app/storage_local/avatars")
                    for p in avatars_dir.glob("**/preview/preview.liveportrait.mp4"):
                        candidate = p
                        break
                except Exception:
                    candidate = None

                if candidate and candidate.exists():
                    try:
                        cap = cv2.VideoCapture(str(candidate))
                        ret, frame = cap.read()
                        cap.release()
                        if ret:
                            cv2.imwrite(str(warmup_path), frame)
                            get_landmark_and_bbox([str(warmup_path)], 0)
                            logger.info(
                                "MuseTalk service: DWPose warmup using preview frame done elapsed=%.1fs",
                                time.monotonic() - t4,
                            )
                        else:
                            raise RuntimeError("failed to read preview frame for DWPose warmup")
                    except Exception:
                        # Fallback to synthetic image warmup below
                        candidate = None

                if not candidate:
                    _warmup_img = np.zeros((512, 512, 3), dtype=np.uint8)
                    cv2.imwrite(str(warmup_path), _warmup_img)
                    get_landmark_and_bbox([str(warmup_path)], 0)  # dummy forward pass
                    logger.info(
                        "MuseTalk service: DWPose warmup using synthetic image done elapsed=%.1fs",
                        time.monotonic() - t4,
                    )
            except Exception as _warmup_exc:
                logger.warning(
                    "MuseTalk service: DWPose warmup raised (non-fatal) %s",
                    _warmup_exc,
                )

            _runtime_cleanup(stage_name="post_model_load")

        finally:
            os.chdir(old_cwd)

        _device = device
        _vae = vae
        _unet = unet
        _pe = pe
        _timesteps = timesteps
        _weight_dtype = weight_dtype
        _whisper = whisper
        _audio_processor = audio_processor
        _fp = fp
        _workspace = ws

        _model_load_seconds = time.monotonic() - t_start
        _model_load_finished_at = time.time()
        logger.info(
            "MuseTalk service: ALL MODELS READY total_load_seconds=%.1f device=%s",
            _model_load_seconds,
            device,
        )
        _models_loaded.set()

    except Exception as exc:
        _models_error = str(exc)
        _model_load_seconds = time.monotonic() - t_start
        _model_load_finished_at = time.time()
        logger.critical("MuseTalk service: model loading FAILED error=%s", exc, exc_info=True)
        _models_loaded.set()   # unblock health checks so they can return an error


# ---------------------------------------------------------------------------
# Per-request inference  (extracted from inference.py:main, using cached models)
# ---------------------------------------------------------------------------

def _infer(
    *,
    source_image: str,
    source_video: str,
    audio_path: str,
    output_path: str,
    params: dict,
    run: dict | None = None,
) -> dict:
    # Switch to the workspace directory FIRST so that any module that still needs
    # relative CWD-based paths (e.g. musetalk.utils.preprocessing's module-level
    # init_model call) can resolve './musetalk/...'. Imports come after chdir.
    if _workspace is None:
        raise RuntimeError("MuseTalk service: models not loaded yet (workspace is None)")
    _pre_cwd = Path.cwd()  # restored unconditionally in the finally block below
    os.chdir(_workspace)
    try:
        # Imports come inside the try so that the module-level init_model call in
        # musetalk.utils.preprocessing correctly resolves its relative config path.
        import numpy as np                                                         # type: ignore
        import cv2                                                                  # type: ignore
        import torch                                                                # type: ignore
        from musetalk.utils.blending import get_image                              # type: ignore
        from musetalk.utils.utils import get_file_type, get_video_fps, datagen    # type: ignore
        from musetalk.utils.preprocessing import (                                 # type: ignore
            get_landmark_and_bbox, read_imgs, coord_placeholder,
        )

        t_start = time.monotonic()
        device      = _device
        vae         = _vae
        unet        = _unet
        pe          = _pe
        timesteps   = _timesteps
        weight_dtype = _weight_dtype
        whisper     = _whisper
        ap          = _audio_processor
        fp          = _fp

        version = str(params.get("version", "v15")).strip() or "v15"
        bbox_shift   = int(params.get("bbox_shift", 0))
        if version == "v15":
            bbox_shift = 0
        extra_margin = int(params.get("extra_margin", 10))
        fps_param    = int(params.get("fps", 25))
        pad_left     = int(params.get("audio_padding_length_left", 0))   # 0 = tight sync
        pad_right    = int(params.get("audio_padding_length_right", 2))
        delay_frame  = int(params.get("delay_frame", 0))
        requested_batch_size = max(int(params.get("batch_size", 8)), 1)
        batch_size = int(requested_batch_size)
        parsing_mode = str(params.get("parsing_mode", "jaw"))
        left_cheek_width = max(int(params.get("left_cheek_width", os.environ.get("MUSETALK_LEFT_CHEEK_WIDTH", "90"))), 1)
        right_cheek_width = max(int(params.get("right_cheek_width", os.environ.get("MUSETALK_RIGHT_CHEEK_WIDTH", "90"))), 1)
        requested_use_float16 = _truthy(params.get("use_float16"), bool(weight_dtype == torch.float16))

        run = dict(run or {})
        run_id = str(run.get("run_id") or "")
        stage_timings: dict[str, float] = {"model_load_seconds": 0.0}
        request_stage_budget = _request_stage_timeout_floor(params)
        timeout_face_landmark = _stage_timeout_seconds("face_landmark_extraction", 900.0)
        timeout_inference_loop = _stage_timeout_seconds("inference_loop", 3600.0)
        timeout_frame_blend = _stage_timeout_seconds("frame_blend", 1200.0)
        timeout_mux_encode = _stage_timeout_seconds("mux_encode", 600.0)
        timeout_final_save = _stage_timeout_seconds("final_save", 300.0)
        if request_stage_budget > 0.0:
            timeout_face_landmark = max(timeout_face_landmark, request_stage_budget)
            timeout_inference_loop = max(timeout_inference_loop, request_stage_budget)
            timeout_frame_blend = max(timeout_frame_blend, request_stage_budget)

        logger.info(
            "MuseTalk service: request_start run_id=%s output_path=%s model_load_seconds=0.0 "
            "version=%s use_float16_requested=%s use_float16_loaded=%s batch_size=%s fps=%s "
            "cheek_widths=%s/%s target_frame_count=%s target_duration_seconds=%s preview_fast_mode=%s "
            "stage_timeouts face_landmark=%.1fs inference_loop=%.1fs frame_blend=%.1fs mux_encode=%.1fs final_save=%.1fs",
            run_id,
            output_path,
            version,
            requested_use_float16,
            bool(weight_dtype == torch.float16),
            requested_batch_size,
            fps_param,
            left_cheek_width,
            right_cheek_width,
            params.get("target_frame_count", 0),
            params.get("target_duration_seconds", 0.0),
            params.get("preview_fast_mode", False),
            timeout_face_landmark,
            timeout_inference_loop,
            timeout_frame_blend,
            timeout_mux_encode,
            timeout_final_save,
        )
        logger.info("MuseTalk service: memory_snapshot stage=request_start snapshot=%s", _cuda_memory_snapshot())

        if torch.cuda.is_available():
            try:
                total_mib = float(torch.cuda.get_device_properties(0).total_memory) / (1024.0 * 1024.0)
                reserved_mib = float(torch.cuda.memory_reserved(0)) / (1024.0 * 1024.0)
                free_estimate_mib = max(total_mib - reserved_mib, 0.0)
                low_vram_threshold_mib = float(os.environ.get("MUSETALK_LOW_VRAM_THRESHOLD_MIB", "2200"))
                low_vram_batch_size = max(int(os.environ.get("MUSETALK_LOW_VRAM_BATCH_SIZE", "2")), 1)
                if free_estimate_mib <= low_vram_threshold_mib:
                    batch_size = min(batch_size, low_vram_batch_size)
                logger.info(
                    "MuseTalk service: batch profile requested=%d effective=%d free_estimate_mib=%.1f threshold_mib=%.1f",
                    requested_batch_size,
                    batch_size,
                    free_estimate_mib,
                    low_vram_threshold_mib,
                )
            except Exception as batch_exc:
                logger.debug("MuseTalk service: adaptive batch probe skipped reason=%s", batch_exc)

        fp = _configure_face_parser_for_request(
            fp,
            left_cheek_width=left_cheek_width,
            right_cheek_width=right_cheek_width,
        )

        video_path = source_video if (source_video and Path(source_video).exists()) else source_image
        out_path   = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        target_frame_count = max(_int_param(params, "target_frame_count", 0), 0)
        target_duration_seconds = max(_float_param(params, "target_duration_seconds", 0.0), 0.0)
        chunk_max_seconds = max(_float_param(params, "chunk_max_seconds", 0.0), 0.0)
        media_duration_seconds = max(_media_duration_seconds(video_path), _media_duration_seconds(audio_path))
        chunk_count = 1
        if chunk_max_seconds > 0.0 and media_duration_seconds > 0.0:
            chunk_count = max(int((media_duration_seconds + chunk_max_seconds - 1e-6) // chunk_max_seconds), 1)
        if chunk_count > 1:
            raise RuntimeError(
                "musetalk_service_chunking_required "
                f"chunk_count={chunk_count} duration_seconds={media_duration_seconds:.3f} "
                f"chunk_max_seconds={chunk_max_seconds:.3f}"
            )

        with tempfile.TemporaryDirectory(prefix="musetalk-svc-") as td:
            work = Path(td)
            temp_v15 = work / "v15"
            temp_v15.mkdir(parents=True)

            source_kind = get_file_type(video_path)
            prepared_source_path, source_preprocessing = _prepare_preview_fast_source(
                source_path=Path(video_path),
                source_kind=source_kind,
                work_dir=work,
                params=params,
            )
            video_path = str(prepared_source_path)

            input_basename  = Path(video_path).stem
            audio_basename  = Path(audio_path).stem
            output_basename = f"{input_basename}_{audio_basename}"
            result_img_dir  = temp_v15 / output_basename
            result_img_dir.mkdir()
            coord_pkl = work / f"{input_basename}.pkl"

            # — Frame extraction —
            if get_file_type(video_path) == "video":
                frame_dir = temp_v15 / input_basename
                frame_dir.mkdir(parents=True, exist_ok=True)
                extract_cmd = [
                    "ffmpeg",
                    "-v",
                    "fatal",
                    "-i",
                    str(video_path),
                    "-start_number",
                    "0",
                    str(frame_dir / "%08d.png"),
                ]
                try:
                    _run_ffmpeg_stage(stage_name="frame_extract", command=extract_cmd)
                except Exception as exc:
                    _persist_failure_workspace(
                        output_path=out_path,
                        workspace_path=work,
                        stage_name="frame_extract",
                        error=exc,
                        commands=[extract_cmd],
                    )
                    raise
                input_img_list = sorted(glob.glob(str(frame_dir / "*.[jpJP][pnPN]*[gG]")))
                fps = get_video_fps(video_path)
            elif get_file_type(video_path) == "image":
                input_img_list = [video_path]
                fps = fps_param
            else:
                raise RuntimeError(f"Unsupported source type: {video_path}")

            # Use no_grad() to avoid building computation graphs, which saves massive VRAM.
            # This is ESSENTIAL for 4GB GPUs like the RTX 3050.
            with torch.no_grad():
                # — Audio features —
                t_aud = time.monotonic()
                # Extract Whisper features and chunk them for the UNet batch loop.
                # Whisper encoder stays in fp32 for compute accuracy on this GPU.
                whisper_features, librosa_len = ap.get_audio_feature(audio_path)
                whisper_chunks = ap.get_whisper_chunk(
                    whisper_features,
                    device,
                    weight_dtype,
                    whisper,
                    librosa_len,
                    fps=fps,
                    audio_padding_length_left=pad_left,
                    audio_padding_length_right=pad_right,
                )
                logger.info("MuseTalk service: audio features done elapsed=%.1fs", time.monotonic() - t_aud)

                # — Landmark detection (DWPose) —
                t_lm = time.monotonic()
                logger.info("MuseTalk service: stage_start face_landmark_extraction")
                landmark_cache_path = _landmark_cache_path(
                    source_sha256=_sha256_file(Path(video_path)),
                    bbox_shift=int(bbox_shift),
                    parsing_mode=str(params.get("parsing_mode") or "jaw"),
                    version=str(version),
                )
                landmark_cache_hit = False
                try:
                    if landmark_cache_path is not None and landmark_cache_path.exists():
                        with open(landmark_cache_path, "rb") as cache_file:
                            coord_list = pickle.load(cache_file)
                        frame_list = [cv2.imread(str(frame_path)) for frame_path in input_img_list]
                        landmark_cache_hit = True
                        logger.info(
                            "MuseTalk service: landmark_cache_hit path=%s frames=%s",
                            landmark_cache_path,
                            len(frame_list),
                        )
                    else:
                        coord_list, frame_list = get_landmark_and_bbox(input_img_list, bbox_shift)
                        if landmark_cache_path is not None:
                            landmark_cache_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(landmark_cache_path, "wb") as cache_file:
                                pickle.dump(coord_list, cache_file)
                except ZeroDivisionError:
                    raise RuntimeError(
                        "no_face_detected: DWPose found no valid face bounding boxes in the input. "
                        "Ensure the source image contains a clearly visible frontal face at "
                        "reasonable resolution (512×512 recommended). "
                        "Raw high-resolution or non-face images must be pre-cropped/canonicalized."
                    )
                lm_elapsed = time.monotonic() - t_lm
                stage_timings["face_landmark_extraction_seconds"] = round(lm_elapsed, 2)
                if lm_elapsed > timeout_face_landmark:
                    raise RuntimeError(
                        "musetalk_stage_timeout stage=face_landmark_extraction "
                        f"elapsed_seconds={lm_elapsed:.1f} timeout_seconds={timeout_face_landmark:.1f}"
                    )
                logger.info(
                    "MuseTalk service: landmarks done elapsed=%.1fs frames=%d",
                    lm_elapsed,
                    len(frame_list),
                )
                logger.info("MuseTalk service: memory_snapshot stage=face_landmark_extraction snapshot=%s", _cuda_memory_snapshot())
                with open(str(coord_pkl), "wb") as f:
                    pickle.dump(coord_list, f)

                # — VAE encode —
                input_latent_list: list = []
                for bbox, frame in zip(coord_list, frame_list):
                    if bbox == coord_placeholder:
                        continue
                    x1, y1, x2, y2 = bbox
                    if version == "v15":
                        y2 = min(y2 + extra_margin, frame.shape[0])
                    crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
                    input_latent_list.append(vae.get_latents_for_unet(crop))

                if not input_latent_list:
                    raise RuntimeError(
                        "no_face_detected: DWPose returned only coord_placeholder entries — "
                        "no valid face crop could be extracted from the input. "
                        "Use a pre-canonicalized 512×512 face image."
                    )

                frame_list_cycle  = frame_list  + frame_list[::-1]
                coord_list_cycle  = coord_list  + coord_list[::-1]
                latent_list_cycle = input_latent_list + input_latent_list[::-1]

                # — UNet inference —
                t_unet = time.monotonic()
                logger.info("MuseTalk service: stage_start inference_loop")
                video_num = len(whisper_chunks)
                if video_num <= 0:
                    raise RuntimeError(
                        "musetalk_stage_failed stage=inference_loop reason=empty_whisper_chunks"
                    )
                n_batches = int((video_num + batch_size - 1) // batch_size)
                logger.info(
                    "MuseTalk service: UNet starting video_num=%d requested_batch_size=%d effective_batch_size=%d n_batches=%d",
                    video_num, requested_batch_size, batch_size, n_batches,
                )
                gen = datagen(
                    whisper_chunks=whisper_chunks,
                    vae_encode_latents=latent_list_cycle,
                    batch_size=batch_size,
                    delay_frame=delay_frame,
                    device=device,
                )
                res_frame_list: list = []
                inference_deadline = t_unet + timeout_inference_loop
                for batch_idx, (whisper_batch, latent_batch) in enumerate(gen):
                    if time.monotonic() > inference_deadline:
                        raise RuntimeError(
                            "musetalk_stage_timeout stage=inference_loop "
                            f"elapsed_seconds={time.monotonic() - t_unet:.1f} timeout_seconds={timeout_inference_loop:.1f} "
                            f"batch_index={batch_idx + 1} total_batches={n_batches}"
                        )
                    if getattr(whisper_batch, "dtype", None) != weight_dtype or getattr(whisper_batch, "device", None) != device:
                        whisper_batch = whisper_batch.to(device=device, dtype=weight_dtype)
                    if getattr(latent_batch, "dtype", None) != weight_dtype or getattr(latent_batch, "device", None) != device:
                        latent_batch = latent_batch.to(device=device, dtype=weight_dtype)
                    audio_feat = pe(whisper_batch)
                    pred = unet.model(latent_batch, timesteps, encoder_hidden_states=audio_feat).sample
                    for res_frame in vae.decode_latents(pred):
                        res_frame_list.append(res_frame)

                    del pred, audio_feat, latent_batch, whisper_batch
                    if _truthy(os.environ.get("MUSETALK_SERVICE_CLEAR_CACHE_EACH_BATCH", "0"), False):
                        torch.cuda.empty_cache()

                    if batch_idx % 5 == 0 or batch_idx == n_batches - 1:
                        logger.info(
                            "MuseTalk service: UNet batch %d/%d elapsed=%.1fs",
                            batch_idx + 1, n_batches, time.monotonic() - t_unet,
                        )
                logger.info(
                    "MuseTalk service: UNet done elapsed=%.1fs out_frames=%d",
                    time.monotonic() - t_unet, len(res_frame_list),
                )
                stage_timings["inference_loop_seconds"] = round(time.monotonic() - t_unet, 2)
                stage_timings["generated_frame_count"] = int(len(res_frame_list))
                logger.info("MuseTalk service: memory_snapshot stage=inference_loop snapshot=%s", _cuda_memory_snapshot())
                _runtime_cleanup(stage_name="post_inference_loop")
                if not res_frame_list:
                    raise RuntimeError(
                        "musetalk_stage_failed stage=inference_loop reason=no_generated_frames"
                    )

                # — Blend and write frames —
                t_blend = time.monotonic()
                logger.info("MuseTalk service: stage_start frame_blend")
                written_frames = 0
                for i, res_frame in enumerate(res_frame_list):
                    if i % 24 == 0 and (time.monotonic() - t_blend) > timeout_frame_blend:
                        raise RuntimeError(
                            "musetalk_stage_timeout stage=frame_blend "
                            f"elapsed_seconds={time.monotonic() - t_blend:.1f} timeout_seconds={timeout_frame_blend:.1f} "
                            f"written_frames={written_frames}"
                        )
                    bbox = coord_list_cycle[i % len(coord_list_cycle)]
                    ori  = copy.deepcopy(frame_list_cycle[i % len(frame_list_cycle)])
                    x1, y1, x2, y2 = bbox
                    if version == "v15":
                        y2 = min(y2 + extra_margin, ori.shape[0])
                    try:
                        res_frame = cv2.resize(res_frame.astype("uint8"), (x2 - x1, y2 - y1))
                    except Exception:
                        continue
                    if version == "v15":
                        combined = get_image(ori, res_frame, [x1, y1, x2, y2], mode=parsing_mode, fp=fp)
                    else:
                        combined = get_image(ori, res_frame, [x1, y1, x2, y2], fp=fp)
                    cv2.imwrite(str(result_img_dir / f"{str(i).zfill(8)}.png"), combined)
                    written_frames += 1

                stage_timings["frame_blend_seconds"] = round(time.monotonic() - t_blend, 2)
                stage_timings["written_frame_count"] = int(written_frames)
                logger.info(
                    "MuseTalk service: frame_blend done elapsed=%.1fs written_frames=%d",
                    time.monotonic() - t_blend,
                    written_frames,
                )
                logger.info("MuseTalk service: memory_snapshot stage=frame_blend snapshot=%s", _cuda_memory_snapshot())
                if written_frames <= 0:
                    raise RuntimeError(
                        "musetalk_stage_failed stage=frame_blend reason=no_blended_frames_written"
                    )

                # — Assemble output video —
                temp_vid = temp_v15 / f"temp_{output_basename}.mp4"
                out_vid  = temp_v15 / out_path.name
                staged_frames = sorted(result_img_dir.glob("*.png"))
                if not staged_frames:
                    raise RuntimeError(
                        "musetalk_stage_failed stage=mux_encode reason=no_frame_images_available "
                        f"result_img_dir={result_img_dir}"
                    )
                image_to_video_cmd = [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "warning",
                    "-r",
                    str(fps),
                    "-f",
                    "image2",
                    "-i",
                    str(result_img_dir / "%08d.png"),
                    "-vcodec",
                    "libx264",
                    "-vf",
                    "format=yuv420p",
                    "-crf",
                    "18",
                    str(temp_vid),
                ]
                mux_audio_cmd = [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "warning",
                    "-i",
                    str(audio_path),
                    "-i",
                    str(temp_vid),
                    str(out_vid),
                ]
                try:
                    t_mux = time.monotonic()
                    logger.info("MuseTalk service: stage_start mux_encode")
                    _run_ffmpeg_stage(
                        stage_name="image_to_video_encode",
                        command=image_to_video_cmd,
                        expected_output=temp_vid,
                        timeout_seconds=timeout_mux_encode,
                    )
                    stage_timings["mux_encode_seconds"] = round(time.monotonic() - t_mux, 2)

                    t_save = time.monotonic()
                    logger.info("MuseTalk service: stage_start final_save")
                    _run_ffmpeg_stage(
                        stage_name="audio_mux_encode",
                        command=mux_audio_cmd,
                        expected_output=out_vid,
                        timeout_seconds=timeout_final_save,
                    )
                    stage_timings["final_save_seconds"] = round(time.monotonic() - t_save, 2)
                except Exception as exc:
                    _persist_failure_workspace(
                        output_path=out_path,
                        workspace_path=work,
                        stage_name="mux_encode",
                        error=exc,
                        commands=[image_to_video_cmd, mux_audio_cmd],
                    )
                    raise

                shutil.copy2(str(out_vid), str(out_path))
                logger.info("MuseTalk service: memory_snapshot stage=final_save snapshot=%s", _cuda_memory_snapshot())
                _runtime_cleanup(stage_name="post_final_save")
    finally:
        os.chdir(_pre_cwd)  # restore CWD that was saved before os.chdir(_workspace)

    elapsed = time.monotonic() - t_start
    frames_for_rate = int(stage_timings.get("generated_frame_count") or target_frame_count or 0)
    if frames_for_rate <= 0:
        try:
            frames_for_rate = int(_probe_media(out_path).get("nb_frames") or 0)
        except Exception:
            frames_for_rate = 0
    per_frame_timings = {
        "face_landmark_seconds_per_frame": (
            round(float(stage_timings.get("face_landmark_extraction_seconds", 0.0)) / frames_for_rate, 6)
            if frames_for_rate > 0
            else 0.0
        ),
        "inference_loop_seconds_per_frame": (
            round(float(stage_timings.get("inference_loop_seconds", 0.0)) / frames_for_rate, 6)
            if frames_for_rate > 0
            else 0.0
        ),
        "frame_count": int(frames_for_rate),
    }
    runtime_info = {
        "device": str(device),
        "use_float16": bool(weight_dtype == torch.float16),
        "requested_use_float16": bool(requested_use_float16),
        "requested_batch_size": int(requested_batch_size),
        "batch_size": int(batch_size),
        "version": str(version),
        "target_frame_count": int(target_frame_count),
        "target_duration_seconds": round(float(target_duration_seconds), 6),
        "chunk_count": int(chunk_count),
        "media_duration_seconds": round(float(media_duration_seconds), 6),
        "source_preprocessing": dict(source_preprocessing),
        "source_resolution_before": dict(source_preprocessing.get("source_resolution_before") or {}),
        "source_resolution_after": dict(source_preprocessing.get("source_resolution_after") or {}),
        "preview_fast_source_path": str(source_preprocessing.get("prepared_path") or ""),
        "per_frame_timings": per_frame_timings,
    }
    _write_debug_sidecar(
        output_path=out_path,
        source_image=source_image,
        source_video=source_video,
        selected_source=video_path,
        audio_path=audio_path,
        params=params,
        run=run,
        stage_timings=stage_timings,
        elapsed_seconds=elapsed,
        runtime_info=runtime_info,
    )
    logger.info(
        "MuseTalk service: inference complete elapsed=%.1fs cold_start=0.0s (preloaded) output=%s",
        elapsed, out_path,
    )
    # cold_start_seconds is always 0 in the persistent service (models are preloaded).
    # inference_seconds is the wall time of this specific request.
    return {
        "success": True,
        "output_path": str(out_path),
        "elapsed_seconds": round(elapsed, 2),
        "cold_start_seconds": 0.0,
        "model_load_seconds": 0.0,
        "service_model_load_seconds": round(float(_model_load_seconds), 3) if _model_load_seconds else 0.0,
        "inference_seconds": round(elapsed, 2),
        "stage_timings": stage_timings,
        "runtime_settings": dict(params),
        "runtime_info": runtime_info,
        "per_frame_timings": per_frame_timings,
        "memory_snapshot": _cuda_memory_snapshot(),
        "run_id": str(run.get("run_id") or ""),
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        logger.debug("HTTP %s", fmt % args)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            payload = _health_payload()
            self._json(200 if payload.get("status") == "ready" else 503, payload)
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/infer":
            self._json(404, {"error": "not_found"})
            return
        if _models_error:
            self._json(503, {"success": False, "error": f"models_load_failed:{_models_error}"})
            return
        if not _models_loaded.is_set():
            self._json(503, {"success": False, "error": "models_not_ready"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length))
        except Exception as exc:
            self._json(400, {"success": False, "error": f"invalid_json:{exc}"})
            return

        with _infer_lock:   # GPU inference must be serialized
            try:
                result = _infer(
                    source_image=str(req.get("source_image") or ""),
                    source_video=str(req.get("source_video") or ""),
                    audio_path=str(req.get("audio_path") or ""),
                    output_path=str(req.get("output_path") or ""),
                    params=dict(req.get("params") or {}),
                    run=dict(req.get("run") or {}),
                )
                self._json(200, result)
            except Exception as exc:
                logger.exception("MuseTalk service: infer failed")
                self._json(500, {"success": False, "error": str(exc)})

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent MuseTalk inference service")
    parser.add_argument("--musetalk_home", default=os.environ.get("MUSETALK_HOME", "/opt/musetalk"))
    parser.add_argument("--model_root", default=os.environ.get("MUSETALK_MODEL_PATH", "/app/storage_local/models"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AVATAR_MUSETALK_SERVICE_PORT", "17860")))
    parser.add_argument("--gpu_id", type=int, default=0)
    args = parser.parse_args()

    musetalk_home = Path(args.musetalk_home).resolve()
    model_root_raw = Path(args.model_root).resolve()
    model_root = (model_root_raw / "models") if (model_root_raw / "models").exists() else model_root_raw

    logger.info(
        "MuseTalk service starting musetalk_home=%s model_root=%s port=%d gpu_id=%d",
        musetalk_home, model_root, args.port, args.gpu_id,
    )

    # Load models in a background thread so /health can report "loading".
    threading.Thread(
        target=_load_models,
        kwargs={"musetalk_home": musetalk_home, "model_root": model_root, "gpu_id": args.gpu_id},
        daemon=True,
        name="musetalk-model-loader",
    ).start()

    server = _ThreadedHTTPServer(("127.0.0.1", args.port), _Handler)
    logger.info("MuseTalk service: HTTP server listening on 127.0.0.1:%d", args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
