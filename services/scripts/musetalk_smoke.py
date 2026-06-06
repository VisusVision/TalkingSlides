"""musetalk_smoke.py – Standalone MuseTalk smoke test.

Isolated from the avatar pipeline. Runs model-load probe and a full
inference run via `musetalk_entrypoint.py` and verifies an output mp4
is produced.

This script intentionally uses conservative defaults for smoke runs:
- enables `MUSETALK_PREVIEW_FAST_MODE` (downscale) to reduce VRAM use
- caps processed frames via `MUSETALK_SMOKE_MAX_FRAMES` (default 240)

Usage (inside the worker container):
    python /app/scripts/musetalk_smoke.py \
        --image /app/storage_local/avatars/2/uploads/avatar_original.jpg \
        --audio /app/storage_local/avatars/2/preview/preview.wav \
        --output /tmp/musetalk_smoke_out.mp4

Exit: prints either a PASS or FAIL line and exits accordingly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import shutil
import subprocess
import tempfile
import zlib
import wave
import struct
from pathlib import Path

# Defaults used by tests / CI
_DEFAULT_IMAGE = "/app/storage_local/avatars/2/uploads/avatar_original.jpg"
_DEFAULT_AUDIO = "/app/storage_local/avatars/2/preview/preview.wav"
_DEFAULT_OUTPUT = "/tmp/musetalk_smoke_out.mp4"

# Minimal required model files (mirrors musetalk_entrypoint checks)
_REQUIRED_MODEL_FILES = [
    "sd-vae/config.json",
    "sd-vae/diffusion_pytorch_model.bin",
    "musetalkV15/unet.pth",
    "whisper/config.json",
    "whisper/pytorch_model.bin",
    "whisper/preprocessor_config.json",
    "dwpose/dw-ll_ucoco_384.pth",
    "face-parse-bisent/79999_iter.pth",
    "face-parse-bisent/resnet18-5c106cde.pth",
]


# ---------------------------------------------------------------------------
# PASS / FAIL helpers
# ---------------------------------------------------------------------------

def _fail(failure_class: str, reason: str) -> None:
    print(f"\n[RESULT] FAIL {failure_class}", flush=True)
    print(f"[REASON] {reason}", flush=True)
    sys.exit(1)


def _pass(elapsed: float) -> None:
    print(f"\n[RESULT] PASS  total_elapsed={elapsed:.2f}s", flush=True)
    sys.exit(0)


class _Stage:
    def __init__(self, title: str):
        self.title = title

    def __enter__(self):
        print(f"\n{'-'*60}\n  {self.title}\n{'-'*60}", flush=True)

    def __exit__(self, exc_type, exc, tb):
        return False


# ---------------------------------------------------------------------------
# Synthetic inputs
# ---------------------------------------------------------------------------

def _make_tiny_png(path: Path, width: int = 64, height: int = 64) -> None:
    raw = b"".join(b"\x00" + b"\x00" * width * 3 for _ in range(height))
    compressed = zlib.compress(raw)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _make_silence_wav(path: Path, *, duration_s: float = 2.0, sample_rate: int = 16000) -> None:
    n = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n)


# ---------------------------------------------------------------------------
# Stage 1 – import / setup
# ---------------------------------------------------------------------------

def _stage_import_setup(*, musetalk_home: Path, model_root: Path) -> dict:
    with _Stage("IMPORT / SETUP"):
        try:
            import torch

            torch_version = getattr(torch, "__version__", "unknown")
            cuda_available = bool(torch.cuda.is_available())
            cuda_version = getattr(torch.version, "cuda", "unknown")
            if cuda_available:
                device_name = torch.cuda.get_device_name(0)
                vram_total_gb = round(torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, 2)
                vram_reserved_gb = round(torch.cuda.memory_reserved(0) / 1024 ** 3, 2)
                device_str = "cuda:0"
            else:
                device_name = "cpu"
                vram_total_gb = 0.0
                vram_reserved_gb = 0.0
                device_str = "cpu"
        except ImportError as exc:
            _fail("torch_cuda_mismatch", f"torch import failed: {exc}")

        print(f"  torch_version     = {torch_version}", flush=True)
        print(f"  cuda_available    = {cuda_available}", flush=True)
        print(f"  cuda_version      = {cuda_version}", flush=True)
        print(f"  device            = {device_str} ({device_name})", flush=True)
        print(f"  vram_total_gb     = {vram_total_gb}", flush=True)
        print(f"  vram_reserved_gb  = {vram_reserved_gb}", flush=True)

        if not cuda_available:
            _fail(
                "cuda_gpu_unavailable",
                "torch.cuda.is_available() returned False. Check NVIDIA driver, cuda runtime, and that the container has --gpus all.",
            )

        # Model files (no loading)
        musetalk_v_dir = musetalk_home
        if not musetalk_v_dir.exists():
            _fail("path_permission_issue", f"MUSETALK_HOME not found: {musetalk_v_dir}")

        print(f"\n  model_root        = {model_root}", flush=True)
        missing = []
        for rel in _REQUIRED_MODEL_FILES:
            p = model_root / rel
            exists = p.exists()
            size = p.stat().st_size if exists else -1
            flag = "✓" if exists else "✗ MISSING"
            size_str = f"{size / 1024 / 1024:.1f} MB" if exists else "---"
            print(f"  {flag}  {rel:<50s}  {size_str}", flush=True)
            if not exists:
                missing.append(rel)

        if missing:
            _fail(
                "model_load_failure",
                f"Missing model files under {model_root}: {missing}\nDownload MuseTalk weights and place them at MUSETALK_MODEL_PATH.",
            )

        # ffmpeg probe
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        print(f"\n  ffmpeg            = {ffmpeg or 'MISSING'}", flush=True)
        print(f"  ffprobe           = {ffprobe or 'MISSING'}", flush=True)
        if not ffmpeg or not ffprobe:
            _fail("ffmpeg_media_failure", "ffmpeg/ffprobe not found in PATH")

        # Entrypoint
        entrypoint = Path(__file__).resolve().parent / "musetalk_entrypoint.py"
        if not entrypoint.exists():
            _fail("path_permission_issue", f"musetalk_entrypoint.py not found at {entrypoint}")
        print(f"  entrypoint        = {entrypoint}", flush=True)

        return {
            "torch_version": torch_version,
            "cuda_available": cuda_available,
            "cuda_version": cuda_version,
            "device": device_str,
            "device_name": device_name,
            "vram_total_gb": vram_total_gb,
            "vram_reserved_gb": vram_reserved_gb,
            "musetalk_home": str(musetalk_home),
            "model_root": str(model_root),
            "missing_models": missing,
            "entrypoint": str(entrypoint),
        }


# ---------------------------------------------------------------------------
# Stage 2 – Input prep
# ---------------------------------------------------------------------------

def _stage_input_prep(*, image_path: Path | None, video_path: Path | None, audio_path: Path, work_dir: Path) -> tuple[Path, Path, bool]:
    with _Stage("INPUT/PREP"):
        # Prefer explicit video
        if video_path is not None and video_path.exists() and video_path.stat().st_size > 0:
            print(f"  video             = {video_path}  ({video_path.stat().st_size} bytes)", flush=True)
            final_source = video_path
            source_is_video = True
        else:
            if image_path is not None and image_path.exists() and image_path.stat().st_size > 0:
                print(f"  image             = {image_path}  ({image_path.stat().st_size} bytes)", flush=True)
                final_source = image_path
                source_is_video = False
            else:
                synthetic_img = work_dir / "smoke_face.png"
                print(f"  image             NOT FOUND — generating synthetic 64×64 PNG at {synthetic_img}", flush=True)
                _make_tiny_png(synthetic_img)
                final_source = synthetic_img
                source_is_video = False

        # Audio
        if audio_path.exists() and audio_path.stat().st_size > 0:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                dur = float(proc.stdout.strip() or "0")
            except Exception:
                dur = 0.0
            print(f"  audio             = {audio_path}  ({dur:.2f}s)", flush=True)
            final_audio = audio_path
        else:
            synthetic_wav = work_dir / "smoke_audio.wav"
            print(f"  audio             NOT FOUND — generating 2 s silence WAV at {synthetic_wav}", flush=True)
            _make_silence_wav(synthetic_wav, duration_s=2.0)
            final_audio = synthetic_wav

    return final_source, final_audio, source_is_video


# ---------------------------------------------------------------------------
# Stage 3 – Model load (probe)
# ---------------------------------------------------------------------------

def _stage_model_load(*, musetalk_home: Path, model_root: Path, timeout_s: float = 240.0) -> None:
    with _Stage("MODEL LOAD"):
        probe_code = f"""
import sys, time, os
sys.path.insert(0, {str(musetalk_home)!r})
os.chdir({str(musetalk_home)!r})
# patch torch.load
import torch
_orig = torch.load
def _safe(*a, **kw):
    kw.setdefault('weights_only', False)
    return _orig(*a, **kw)
torch.load = _safe
# patch mmengine registration guard
try:
    from mmengine.registry import Registry
    _o = Registry._register_module
    def _p(self, module, module_name=None, force=False):
        try:
            return _o(self, module, module_name=module_name, force=force)
        except KeyError as e:
            if 'already registered' in str(e) and 'Adafactor' in str(e):
                return None
            raise
    Registry._register_module = _p
except Exception:
    pass

t0 = time.monotonic()
from musetalk.utils.utils import load_all_model
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
vae, unet, pe = load_all_model(
    unet_model_path='models/musetalkV15/unet.pth',
    vae_type='sd-vae',
    unet_config='models/musetalk/config.json',
    device=device,
)
print(f'MODEL_LOAD_OK elapsed={{time.monotonic()-t0:.2f}}s device={{device}}', flush=True)
"""
        env = os.environ.copy()
        env["MUSETALK_HOME"] = str(musetalk_home)
        env["MUSETALK_MODEL_PATH"] = str(model_root)
        models_link = musetalk_home / "models"
        if not models_link.exists():
            try:
                models_link.symlink_to(model_root, target_is_directory=True)
            except Exception:
                pass

        try:
            proc = subprocess.run(
                [sys.executable, "-c", probe_code],
                cwd=str(musetalk_home),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            _fail(
                "timeout_warmup",
                f"Model loading probe timed out after {timeout_s:.0f}s — GPU may be OOM or model files are corrupt.",
            )

        combined = (proc.stdout or "") + (proc.stderr or "")
        if "MODEL_LOAD_OK" not in combined:
            stderr_tail = str(proc.stderr or proc.stdout or "")[-800:].strip()
            if "cuda" in stderr_tail.lower() and "out of memory" in stderr_tail.lower():
                _fail("cuda_gpu_unavailable", f"CUDA OOM during model load:\n{stderr_tail}")
            if "no module named" in stderr_tail.lower() or "importerror" in stderr_tail.lower():
                _fail("torch_cuda_mismatch", f"Import error during model load:\n{stderr_tail}")
            if "filenotfounderror" in stderr_tail.lower() or "no such file" in stderr_tail.lower():
                _fail("model_load_failure", f"File not found during model load:\n{stderr_tail}")
            _fail("model_load_failure", f"Model load probe returned rc={proc.returncode}:\n{stderr_tail}")

        print("  model_load probe  PASSED", flush=True)
        for line in combined.splitlines():
            if "MODEL_LOAD_OK" in line:
                print(f"  {line.strip()}", flush=True)


# ---------------------------------------------------------------------------
# Stage 4 – Inference
# ---------------------------------------------------------------------------

def _probe_video_info(p: Path) -> dict:
    try:
        j = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate,nb_frames,duration,width,height",
                "-of", "json",
                str(p),
            ],
            capture_output=True, text=True, check=False, timeout=10,
        )
        data = json.loads(j.stdout or "{}")
        s = (data.get("streams") or [{}])[0]
        avg = str(s.get("avg_frame_rate") or "0/0")
        fps = 0.0
        if "/" in avg:
            a, b = avg.split("/", 1)
            try:
                fps = float(a) / float(b) if float(b) != 0 else 0.0
            except Exception:
                fps = 0.0
        else:
            try:
                fps = float(avg)
            except Exception:
                fps = 0.0
        frames = int(float(str(s.get("nb_frames") or "0") or 0))
        duration = float(str(s.get("duration") or "0") or 0.0)
        width = int(s.get("width") or 0)
        height = int(s.get("height") or 0)
        return {"fps": fps, "frames": frames, "duration": duration, "width": width, "height": height}
    except Exception:
        return {"fps": 0.0, "frames": 0, "duration": 0.0, "width": 0, "height": 0}


def _probe_audio_duration(p: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, check=False, timeout=8,
        )
        return float((r.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


def _stage_inference(
    *,
    entrypoint: Path,
    musetalk_home: Path,
    source_path: Path,
    source_is_video: bool,
    audio_path: Path,
    output_path: Path,
    timeout_s: float = 240.0,
) -> dict:
    cmd = [
        sys.executable, str(entrypoint),
        "--musetalk_home", str(musetalk_home),
    ]
    if source_is_video:
        cmd.extend(["--source_video", str(source_path)])
    else:
        cmd.extend(["--source_image", str(source_path)])
    cmd.extend(["--driven_audio", str(audio_path), "--result_path", str(output_path)])

    env = os.environ.copy()
    env.setdefault("MUSETALK_HOME", str(musetalk_home))
    env.setdefault("AVATAR_PREVIEW_DIAGNOSTIC_MODE", "0")

    # Safety defaults: enable fast downscale and cap frames for smoke
    env.setdefault("MUSETALK_PREVIEW_FAST_MODE", os.environ.get("MUSETALK_PREVIEW_FAST_MODE", "1"))
    env.setdefault("MUSETALK_PREVIEW_MAX_WIDTH", os.environ.get("MUSETALK_PREVIEW_MAX_WIDTH", "512"))
    env.setdefault("MUSETALK_BATCH_SIZE", os.environ.get("MUSETALK_BATCH_SIZE", "1"))
    env.setdefault("MUSETALK_USE_FLOAT16", os.environ.get("MUSETALK_USE_FLOAT16", "1"))
    smoke_max_frames = int(str(os.environ.get("MUSETALK_SMOKE_MAX_FRAMES", "240")).strip() or "240")

    # Derive fps and durations
    fps = 0.0
    if source_is_video:
        vinfo = _probe_video_info(source_path)
        fps = float(vinfo.get("fps") or 0.0)
        if fps <= 0.0:
            fps = float(os.environ.get("MUSETALK_FPS", "25"))
        try:
            src_w = int(vinfo.get("width") or 0)
            if src_w > int(env.get("MUSETALK_PREVIEW_MAX_WIDTH", "512")):
                env["MUSETALK_PREVIEW_FAST_MODE"] = "1"
        except Exception:
            pass
    else:
        fps = float(os.environ.get("MUSETALK_FPS", "25"))

    audio_dur = _probe_audio_duration(audio_path)
    target_frames = int(round(audio_dur * fps)) if audio_dur > 0 and fps > 0 else 0
    if target_frames <= 0:
        target_frames = min(24, smoke_max_frames)
    target_frames = min(target_frames, smoke_max_frames)

    env.setdefault("MUSETALK_FPS", str(int(max(1, round(fps)))))
    env.setdefault("MUSETALK_TARGET_FRAME_COUNT", str(int(target_frames)))
    env.setdefault("MUSETALK_TARGET_DURATION_SECONDS", f"{max(0.0, audio_dur):.6f}")
    env.setdefault("MUSETALK_STAGE_TIMEOUT_MODEL_LOAD_SECONDS", os.environ.get("MUSETALK_STAGE_TIMEOUT_MODEL_LOAD_SECONDS", "900"))
    env.setdefault("MUSETALK_STAGE_TIMEOUT_FACE_LANDMARK_SECONDS", os.environ.get("MUSETALK_STAGE_TIMEOUT_FACE_LANDMARK_SECONDS", "900"))
    env.setdefault("MUSETALK_STAGE_TIMEOUT_INFERENCE_LOOP_SECONDS", os.environ.get("MUSETALK_STAGE_TIMEOUT_INFERENCE_LOOP_SECONDS", "3600"))
    env.setdefault("MUSETALK_STAGE_TIMEOUT_MUX_ENCODE_SECONDS", os.environ.get("MUSETALK_STAGE_TIMEOUT_MUX_ENCODE_SECONDS", "600"))
    env.setdefault("MUSETALK_STAGE_TIMEOUT_FINAL_SAVE_SECONDS", os.environ.get("MUSETALK_STAGE_TIMEOUT_FINAL_SAVE_SECONDS", "300"))

    print(f"\n  command           = {' '.join(cmd)}", flush=True)

    t_infer = time.monotonic()
    with _Stage("FACE/LANDMARK + INFERENCE + MUX/ENCODE"):
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _fail(
                "timeout_inference",
                f"musetalk_entrypoint.py timed out after {timeout_s:.0f}s — DWPose or UNet inference is hanging.",
            )

    elapsed = time.monotonic() - t_infer

    if proc.stdout.strip():
        print("\n  --- entrypoint stdout ---", flush=True)
        for line in proc.stdout.strip().splitlines()[-40:]:
            print(f"  {line}", flush=True)
    if proc.stderr.strip():
        print("\n  --- entrypoint stderr (last 40 lines) ---", flush=True)
        for line in proc.stderr.strip().splitlines()[-40:]:
            print(f"  {line}", flush=True)

    if proc.returncode != 0:
        combined = (proc.stdout or "") + (proc.stderr or "")
        lower = combined.lower()
        if "out of memory" in lower:
            _fail("cuda_gpu_unavailable", f"CUDA OOM during inference (rc={proc.returncode})")
        if "no module named" in lower or "importerror" in lower:
            _fail("torch_cuda_mismatch", f"Import error during inference (rc={proc.returncode})")
        if "missing model" in lower or "no such file" in lower:
            _fail("model_load_failure", f"Missing model during inference (rc={proc.returncode})")
        if "ffmpeg" in lower and ("failed" in lower or "error" in lower):
            _fail("ffmpeg_media_failure", f"ffmpeg error during inference (rc={proc.returncode})")
        if "permissionerror" in lower or "permission denied" in lower:
            _fail("path_permission_issue", f"Permission error during inference (rc={proc.returncode})")
        _fail("timeout_inference", f"musetalk_entrypoint.py returned rc={proc.returncode} after {elapsed:.1f}s")

    entrypoint_debug_path = output_path.with_suffix(output_path.suffix + ".musetalk_debug.json")
    entrypoint_debug = {}
    if entrypoint_debug_path.exists():
        try:
            entrypoint_debug = json.loads(entrypoint_debug_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  warning           = failed to parse entrypoint debug sidecar: {exc}", flush=True)

    stage_timings = dict(entrypoint_debug.get("stage_timings") or {})
    if stage_timings:
        print("\n  entrypoint_stage_timings", flush=True)
        for key, value in stage_timings.items():
            try:
                val = float(value)
                print(f"  {key:<26s} = {val:.2f}s", flush=True)
            except Exception:
                print(f"  {key:<26s} = {value}", flush=True)

    return {
        "elapsed_seconds": round(elapsed, 2),
        "entrypoint_debug_path": str(entrypoint_debug_path),
        "entrypoint_debug": entrypoint_debug,
    }


# ---------------------------------------------------------------------------
# Stage 7 – Output verification
# ---------------------------------------------------------------------------

def _stage_verify_output(*, output_path: Path, audio_path: Path) -> dict:
    with _Stage("OUTPUT VERIFY"):
        if not output_path.exists():
            _fail("bad_input_format", f"Output mp4 was not produced at {output_path}")
        size = output_path.stat().st_size
        if size == 0:
            _fail("ffmpeg_media_failure", "Output mp4 is empty (0 bytes)")

        fc_proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,duration",
                "-of",
                "json",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        frame_count = 0
        video_duration = 0.0
        try:
            fc_data = json.loads(fc_proc.stdout)
            stream = (fc_data.get("streams") or [{}])[0]
            frame_count = int(stream.get("nb_read_frames") or 0)
            video_duration = float(stream.get("duration") or 0.0)
        except Exception:
            pass

        ap_proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            audio_duration = float(ap_proc.stdout.strip() or "0")
        except Exception:
            audio_duration = 0.0

        print(f"  output_path       = {output_path}", flush=True)
        print(f"  output_size_bytes = {size}", flush=True)
        print(f"  frame_count       = {frame_count}", flush=True)
        print(f"  video_duration    = {video_duration:.2f}s", flush=True)
        print(f"  audio_duration    = {audio_duration:.2f}s", flush=True)

        stream_proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        stream_types: list[str] = []
        try:
            stream_data = json.loads(stream_proc.stdout or "{}")
            stream_types = [str(item.get("codec_type") or "") for item in (stream_data.get("streams") or [])]
        except Exception:
            stream_types = []

        has_video_stream = "video" in stream_types
        has_audio_stream = "audio" in stream_types
        print(f"  has_video_stream  = {has_video_stream}", flush=True)
        print(f"  has_audio_stream  = {has_audio_stream}", flush=True)

        if not has_video_stream:
            _fail("ffmpeg_media_failure", "Output mp4 does not contain a video stream")
        if not has_audio_stream:
            _fail("ffmpeg_media_failure", "Final mux/encode failed: output mp4 does not contain an audio stream")

        if frame_count < 2:
            _fail("bad_input_format", f"Output mp4 has only {frame_count} frames — MuseTalk produced a degenerate output")

        return {
            "output_size_bytes": int(size),
            "frame_count": int(frame_count),
            "video_duration_seconds": round(float(video_duration), 4),
            "audio_duration_seconds": round(float(audio_duration), 4),
            "has_video_stream": bool(has_video_stream),
            "has_audio_stream": bool(has_audio_stream),
            "mux_encode_succeeded": bool(has_video_stream and has_audio_stream and size > 0),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Standalone MuseTalk smoke test — no pipeline dependencies.")
    p.add_argument("--image", default=_DEFAULT_IMAGE, help=f"Source face image (default: {_DEFAULT_IMAGE})")
    p.add_argument("--video", default="", help="Optional source video (e.g., LivePortrait output) to drive MuseTalk")
    p.add_argument("--audio", default=_DEFAULT_AUDIO, help=f"Driven audio WAV (default: {_DEFAULT_AUDIO})")
    p.add_argument("--output", default=_DEFAULT_OUTPUT, help=f"Output mp4 path (default: {_DEFAULT_OUTPUT})")
    p.add_argument("--inference_timeout", type=float, default=240.0, help="Seconds to wait for inference subprocess (default: 240)")
    p.add_argument("--model_load_timeout", type=float, default=180.0, help="Seconds to wait for model-load probe (default: 180)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    t_total = time.monotonic()

    print("=" * 72, flush=True)
    print("MUSETALK STANDALONE SMOKE TEST", flush=True)
    print("=" * 72, flush=True)
    print(f"python             = {sys.version}", flush=True)
    print(f"script             = {Path(__file__).resolve()}", flush=True)
    print(f"pid                = {os.getpid()}", flush=True)

    musetalk_home = Path(str(os.environ.get("MUSETALK_HOME", "")).strip() or "/opt/musetalk").resolve()
    model_root_raw = Path(str(os.environ.get("MUSETALK_MODEL_PATH", "")).strip() or "/app/storage_local/models").resolve()
    model_root = (model_root_raw / "models") if (model_root_raw / "models").exists() else model_root_raw

    print(f"MUSETALK_HOME      = {musetalk_home}", flush=True)
    print(f"MUSETALK_MODEL_PATH= {model_root}", flush=True)
    print(f"inference_timeout  = {args.inference_timeout}s", flush=True)

    image_path = Path(args.image)
    audio_path = Path(args.audio)
    video_path = Path(str(args.video).strip()) if str(args.video).strip() else None
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="musetalk-smoke-") as td:
        work_dir = Path(td)
        stage_timings: dict[str, float] = {}

        t_stage = time.monotonic()
        setup_info = _stage_import_setup(musetalk_home=musetalk_home, model_root=model_root)
        stage_timings["import_setup_seconds"] = round(time.monotonic() - t_stage, 2)

        t_stage = time.monotonic()
        final_source, final_audio, source_is_video = _stage_input_prep(
            image_path=image_path, video_path=video_path, audio_path=audio_path, work_dir=work_dir
        )
        stage_timings["input_prep_seconds"] = round(time.monotonic() - t_stage, 2)

        t_stage = time.monotonic()
        _stage_model_load(musetalk_home=musetalk_home, model_root=model_root, timeout_s=args.model_load_timeout)
        stage_timings["model_load_probe_seconds"] = round(time.monotonic() - t_stage, 2)

        entrypoint = Path(__file__).resolve().parent / "musetalk_entrypoint.py"

        t_stage = time.monotonic()
        inference_info = _stage_inference(
            entrypoint=entrypoint,
            musetalk_home=musetalk_home,
            source_path=final_source,
            source_is_video=source_is_video,
            audio_path=final_audio,
            output_path=output_path,
            timeout_s=args.inference_timeout,
        )
        stage_timings["inference_seconds"] = round(float(inference_info.get("elapsed_seconds") or 0.0), 2)
        stage_timings["entrypoint_total_seconds"] = round(time.monotonic() - t_stage, 2)

        t_stage = time.monotonic()
        verify_info = _stage_verify_output(output_path=output_path, audio_path=final_audio)
        stage_timings["output_verify_seconds"] = round(time.monotonic() - t_stage, 2)

        total_elapsed = round(time.monotonic() - t_total, 2)
        stage_timings["total_elapsed_seconds"] = total_elapsed
        smoke_meta = {
            "result": "PASS",
            "output_path": str(output_path),
            "source_path": str(final_source),
            "source_is_video": bool(source_is_video),
            "audio_path": str(final_audio),
            "entrypoint_path": str(entrypoint),
            "model_root": str(model_root),
            "setup": setup_info,
            "stage_timings": stage_timings,
            "verify": verify_info,
            "entrypoint_debug_path": str(inference_info.get("entrypoint_debug_path") or ""),
            "entrypoint_stage_timings": dict((inference_info.get("entrypoint_debug") or {}).get("stage_timings") or {}),
            "entrypoint_stage_trace": list((inference_info.get("entrypoint_debug") or {}).get("stage_trace") or []),
        }
        metadata_path = output_path.with_suffix(output_path.suffix + ".smoke.json")
        metadata_path.write_text(json.dumps(smoke_meta, ensure_ascii=True, indent=2), encoding="utf-8")
        print("\nSTAGE TIMINGS", flush=True)
        for key, value in stage_timings.items():
            print(f"  {key:<26s} = {value:.2f}s", flush=True)
        print(f"  smoke_metadata_path       = {metadata_path}", flush=True)

    _pass(time.monotonic() - t_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
