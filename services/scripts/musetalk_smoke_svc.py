"""musetalk_smoke_svc.py – Smoke-test the RUNNING musetalk_service.py via HTTP.

This is the production code path:
  canonical_pipeline.py  →  HTTP POST /infer  →  musetalk_service.py._infer()

Usage (inside the worker container, after service is ready):

  # Check service health first
  curl http://127.0.0.1:17860/health

  # Run known-good (synthetic) test
  python /app/scripts/musetalk_smoke_svc.py --mode synthetic --output /tmp/smoke_svc_synthetic.mp4

  # Run real avatar test
  python /app/scripts/musetalk_smoke_svc.py \
      --image /app/storage_local/avatars/2/uploads/avatar_original.jpg \
      --audio /app/storage_local/avatars/2/preview/preview.wav \
      --output /tmp/smoke_svc_avatar2.mp4

Final line is always:
    PASS
    FAIL <failure_class>

Failure classes:
    service_not_ready
    cuda_gpu_unavailable
    oom_vram
    timeout_inference
    bad_input_format
    ffmpeg_media_failure
    service_inference_error
    path_permission_issue
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
import urllib.error
import wave
import zlib
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
_DEFAULT_SVC_PORT = int(os.environ.get("AVATAR_MUSETALK_SERVICE_PORT", "17860"))
_DEFAULT_IMAGE = "/app/storage_local/avatars/2/uploads/avatar_original.jpg"
# Use the longer test speech WAV when available, fall back to the short preview
_DEFAULT_AUDIO = (
    "/tmp/test_speech.wav"
    if __import__("pathlib").Path("/tmp/test_speech.wav").exists()
    else "/app/storage_local/avatars/2/preview/preview.wav"
)


# ---------------------------------------------------------------------------
# PASS / FAIL helpers
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    print(f"\n{'─'*60}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'─'*60}", flush=True)


def _fail(failure_class: str, reason: str) -> None:
    print(f"\n[RESULT] FAIL {failure_class}", flush=True)
    print(f"[REASON] {reason}", flush=True)
    sys.exit(1)


def _pass(elapsed: float) -> None:
    print(f"\n[RESULT] PASS  total_elapsed={elapsed:.2f}s", flush=True)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Synthetic input generators
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


def _make_silence_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 16000) -> None:
    n = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n)


def _make_synthetic_face_png(path: Path) -> None:
    """Generate a 256x256 solid-color PNG (closest we can get without PIL)."""
    _make_tiny_png(path, width=256, height=256)


def _generate_synthetic_inputs(work_dir: Path) -> tuple[Path, Path]:
    """Generate a 256x256 PNG and a 2 s sine WAV with ffmpeg."""
    img = work_dir / "synthetic_face.png"
    wav = work_dir / "synthetic_audio.wav"

    # Try ffmpeg first for a more realistic test image (color bars)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=256x256:rate=1:duration=1",
         "-frames:v", "1",
         str(img)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or not img.exists():
        _make_synthetic_face_png(img)  # fallback

    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "sine=frequency=220:sample_rate=16000:duration=2",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
         str(wav)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or not wav.exists():
        _make_silence_wav(wav, duration_s=2.0)

    return img, wav


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _check_service_health(*, port: int, wait_timeout_s: float = 30.0) -> dict:
    """Poll /health until ready or timeout. Returns health body."""
    _section("STAGE 1 – SERVICE HEALTH CHECK")
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + wait_timeout_s
    last_status = "unknown"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = json.loads(resp.read())
                last_status = body.get("status", "unknown")
                print(f"  /health → {body}", flush=True)
                if last_status == "ready":
                    return body
                if last_status == "error":
                    _fail("service_not_ready",
                          f"MuseTalk service reported error: {body.get('error')}")
        except urllib.error.URLError as exc:
            print(f"  /health → connection refused ({exc})", flush=True)
        time.sleep(3)
    _fail("service_not_ready",
          f"MuseTalk service on port {port} did not become ready within "
          f"{wait_timeout_s:.0f}s. Last status: {last_status}.")
    return {}  # never reached


def _check_vram(*, port: int) -> None:
    """Print current VRAM state via the running Python in the service env."""
    _section("STAGE 2 – GPU / VRAM STATE")
    probe = """
import torch
avail = torch.cuda.is_available()
print(f"cuda_available={avail}")
if avail:
    print(f"device={torch.cuda.get_device_name(0)}")
    props = torch.cuda.get_device_properties(0)
    total = props.total_memory / 1024**3
    reserved = torch.cuda.memory_reserved(0) / 1024**3
    allocated = torch.cuda.memory_allocated(0) / 1024**3
    free = total - reserved
    print(f"vram_total_gb={total:.2f}")
    print(f"vram_reserved_gb={reserved:.2f}")
    print(f"vram_allocated_gb={allocated:.2f}")
    print(f"vram_free_gb={free:.2f}")
"""
    proc = subprocess.run([sys.executable, "-c", probe],
                          capture_output=True, text=True, check=False)
    for line in (proc.stdout + proc.stderr).splitlines():
        print(f"  {line}", flush=True)
    # Warn if less than 1 GB free
    for line in proc.stdout.splitlines():
        if line.startswith("vram_free_gb="):
            try:
                free_gb = float(line.split("=")[1])
                if free_gb < 1.0:
                    print(f"\n  ⚠ WARNING: only {free_gb:.2f} GB VRAM free — "
                          "inference may OOM or hang!", flush=True)
            except Exception:
                pass


def _stage_infer(
    *,
    port: int,
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    timeout_s: float,
    label: str,
) -> dict:
    """POST to /infer and stream timing."""
    _section(f"STAGE 3 – INFERENCE ({label})")

    # Sync tuning notes:
    #   audio_padding_length_left  – lookahead frames fed to Whisper BEFORE the
    #     current video frame.  left=2 shifts audio 2 frames FORWARD → lips lag.
    #     Set to 0 for tight 1-to-1 alignment.
    #   audio_padding_length_right – context frames AFTER current frame.
    #     Keeping right=2 improves quality without affecting sync.
    #   delay_frame – integer offset added inside datagen(); positive values
    #     shift the audio chunk forward relative to video (use 0 for no offset).
    payload = {
        "source_image": str(image_path),
        "source_video": "",
        "audio_path": str(audio_path),
        "output_path": str(output_path),
        "params": {
            "bbox_shift":   int(os.environ.get("MUSETALK_BBOX_SHIFT",   "0")),
            "extra_margin": int(os.environ.get("MUSETALK_EXTRA_MARGIN", "10")),
            "fps":          int(os.environ.get("MUSETALK_FPS",          "25")),
            # LEFT=0: no audio lookahead → video and audio are in sync
            "audio_padding_length_left":  int(os.environ.get("MUSETALK_PAD_LEFT",    "0")),
            "audio_padding_length_right": int(os.environ.get("MUSETALK_PAD_RIGHT",   "2")),
            "delay_frame":  int(os.environ.get("MUSETALK_DELAY_FRAME",  "0")),
            # batch_size=8 is a safe baseline for 4GB GPUs. 
            # (16 is faster but very risky for VRAM)
            "batch_size":   int(os.environ.get("MUSETALK_BATCH_SIZE",   "8")),
            "parsing_mode": "jaw",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    url = f"http://127.0.0.1:{port}/infer"

    print(f"  POST {url}", flush=True)
    print(f"  source_image = {image_path}", flush=True)
    print(f"  audio_path   = {audio_path}", flush=True)
    print(f"  output_path  = {output_path}", flush=True)
    print(f"  timeout_s    = {timeout_s}", flush=True)

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    t0 = time.monotonic()
    last_print = t0
    try:
        # urllib doesn't support per-read timeouts, use socket timeout
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_s + 5)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s + 5) as resp:
                resp_body = resp.read()
        finally:
            socket.setdefaulttimeout(old_timeout)
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - t0
        try:
            err_body = json.loads(exc.read())
        except Exception:
            err_body = {}
        err_msg = str(err_body.get("error") or exc)
        lower = err_msg.lower()
        if "out of memory" in lower:
            _fail("oom_vram", f"CUDA OOM during inference ({elapsed:.1f}s): {err_msg}")
        _fail("service_inference_error",
              f"HTTP {exc.code} after {elapsed:.1f}s: {err_msg}")
    except (TimeoutError, OSError) as exc:
        elapsed = time.monotonic() - t0
        _fail("timeout_inference",
              f"Service /infer timed out after {elapsed:.1f}s. "
              "DWPose or UNet is hanging — likely VRAM pressure or empty face bbox.")

    elapsed = time.monotonic() - t0
    try:
        result = json.loads(resp_body)
    except Exception:
        result = {"raw": resp_body.decode("utf-8", errors="replace")}

    print(f"\n  inference_elapsed = {elapsed:.2f}s", flush=True)
    print(f"  response          = {json.dumps(result, indent=2)}", flush=True)

    if not result.get("success"):
        err = str(result.get("error") or "unknown")
        lower = err.lower()
        if "out of memory" in lower:
            _fail("oom_vram", f"CUDA OOM in inference: {err}")
        if "timeout" in lower:
            _fail("timeout_inference", f"Inference timeout: {err}")
        _fail("service_inference_error", err)

    return result


def _stage_verify_output(*, output_path: Path) -> None:
    _section("STAGE 4 – OUTPUT VERIFICATION")
    if not output_path.exists():
        _fail("bad_input_format",
              f"Output mp4 not produced at {output_path}. "
              "MuseTalk either failed silently or the output path is wrong.")
    size = output_path.stat().st_size
    if size == 0:
        _fail("ffmpeg_media_failure", "Output mp4 is empty (0 bytes).")

    proc = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-count_frames",
         "-show_entries", "stream=nb_read_frames,avg_frame_rate,codec_name",
         "-show_entries", "format=duration,size",
         "-of", "json", str(output_path)],
        capture_output=True, text=True, check=False,
    )
    try:
        info = json.loads(proc.stdout)
        stream = (info.get("streams") or [{}])[0]
        fmt = info.get("format") or {}
        frame_count = int(stream.get("nb_read_frames") or 0)
        duration = float(fmt.get("duration") or 0)
        codec = stream.get("codec_name", "?")
    except Exception:
        frame_count = 0
        duration = 0.0
        codec = "?"

    print(f"  output_path   = {output_path}", flush=True)
    print(f"  size_bytes    = {size}", flush=True)
    print(f"  frame_count   = {frame_count}", flush=True)
    print(f"  duration_s    = {duration:.2f}", flush=True)
    print(f"  codec         = {codec}", flush=True)

    if frame_count < 2:
        _fail("bad_input_format",
              f"Output has only {frame_count} frames. "
              "DWPose likely found no face bbox in the input image — "
              "check for face_scale_borderline_small and increase MUSETALK_EXTRA_MARGIN.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Smoke-test the running musetalk_service via HTTP /infer.")
    p.add_argument("--mode", choices=["synthetic", "real"], default="real",
                   help="'synthetic' = generate test inputs; 'real' = use --image/--audio")
    p.add_argument("--image", default=_DEFAULT_IMAGE)
    p.add_argument("--audio", default=_DEFAULT_AUDIO)
    p.add_argument("--output", default="/tmp/musetalk_svc_smoke.mp4")
    p.add_argument("--port", type=int, default=_DEFAULT_SVC_PORT)
    p.add_argument("--timeout", type=float, default=120.0,
                   help="Inference timeout in seconds (default 120)")
    p.add_argument("--health_wait", type=float, default=30.0,
                   help="Max seconds to wait for service to be ready")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    t_total = time.monotonic()

    print("=" * 72, flush=True)
    print("MUSETALK SERVICE SMOKE TEST  (tests the production /infer path)", flush=True)
    print("=" * 72, flush=True)
    print(f"python   = {sys.version.split()[0]}", flush=True)
    print(f"pid      = {os.getpid()}", flush=True)
    print(f"port     = {args.port}", flush=True)
    print(f"mode     = {args.mode}", flush=True)
    print(f"timeout  = {args.timeout}s", flush=True)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="musetalk-svc-smoke-") as td:
        work_dir = Path(td)

        if args.mode == "synthetic":
            print("\n  [INPUT] generating synthetic test inputs", flush=True)
            image_path, audio_path = _generate_synthetic_inputs(work_dir)
            label = "synthetic"
        else:
            image_path = Path(args.image)
            audio_path = Path(args.audio)
            label = "real_avatar"
            if not image_path.exists():
                _fail("path_permission_issue", f"Image not found: {image_path}")
            if not audio_path.exists():
                _fail("path_permission_issue", f"Audio not found: {audio_path}")

        print(f"  image  = {image_path}", flush=True)
        print(f"  audio  = {audio_path}", flush=True)
        print(f"  output = {output_path}", flush=True)

        # Stage 1 – health
        _check_service_health(port=args.port, wait_timeout_s=args.health_wait)

        # Stage 2 – VRAM
        _check_vram(port=args.port)

        # Stage 3 – inference
        _stage_infer(
            port=args.port,
            image_path=image_path,
            audio_path=audio_path,
            output_path=output_path,
            timeout_s=args.timeout,
            label=label,
        )

        # Stage 4 – output
        _stage_verify_output(output_path=output_path)

    _pass(time.monotonic() - t_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
