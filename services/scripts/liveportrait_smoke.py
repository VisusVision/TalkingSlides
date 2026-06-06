#!/usr/bin/env python3
"""
Standalone LivePortrait smoke test.

Runs inference.py directly (no MuseTalk, no avatar pipeline).
Prints CUDA/GPU state, model paths, timings, and a clear PASS/FAIL diagnosis.

Usage (inside worker container):
    python /app/scripts/liveportrait_smoke.py \\
        --image /opt/liveportrait/assets/examples/source/s0.jpg \\
        --output /tmp/lp_smoke_out.mp4

    python /app/scripts/liveportrait_smoke.py \\
        --image /app/storage_local/avatars/2/preview/preview.mp4.canonical_image_original.png \\
        --driving /opt/liveportrait/assets/examples/driving/d0.mp4 \\
        --output /tmp/lp_smoke_avatar.mp4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ─── defaults (match .env) ───────────────────────────────────────────────────
LP_HOME    = Path(os.environ.get("AVATAR_LIVEPORTRAIT_HOME",
                                  "/opt/liveportrait"))
LP_ENTRY   = Path(os.environ.get("AVATAR_LIVEPORTRAIT_ENTRYPOINT",
                                  "/opt/liveportrait/inference.py"))
LP_MODELS  = Path(os.environ.get("AVATAR_LIVEPORTRAIT_MODEL_PATH",
                                  "/opt/liveportrait/pretrained_weights"))
LP_TIMEOUT = int(os.environ.get("AVATAR_LIVEPORTRAIT_TIMEOUT_SECONDS", "300"))

DIVIDER = "─" * 68


def _banner(title: str) -> None:
    print(f"\n{'─'*4}  {title}  {'─'*(60 - len(title))}")


def _gpu_state() -> dict:
    state: dict = {
        "cuda_available": False,
        "device": "cpu",
        "vram_total_gb": 0.0,
        "vram_reserved_gb": 0.0,
        "vram_allocated_gb": 0.0,
        "vram_free_gb": 0.0,
    }
    try:
        import torch
        state["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            dev = torch.cuda.current_device()
            state["device"] = torch.cuda.get_device_name(dev)
            total    = torch.cuda.get_device_properties(dev).total_memory
            reserved = torch.cuda.memory_reserved(dev)
            allocated = torch.cuda.memory_allocated(dev)
            state["vram_total_gb"]     = round(total     / 2**30, 2)
            state["vram_reserved_gb"]  = round(reserved  / 2**30, 2)
            state["vram_allocated_gb"] = round(allocated / 2**30, 2)
            state["vram_free_gb"]      = round((total - reserved) / 2**30, 2)
    except Exception as exc:
        state["torch_error"] = str(exc)
    return state


def _probe_video(path: Path) -> dict:
    """Return {frames, duration_s, codec} for an mp4 via ffprobe."""
    result: dict[str, object] = {"frames": 0, "duration_s": 0.0, "codec": ""}
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=nb_frames,codec_name,duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True, text=True, check=False, timeout=15,
        )
        if probe.returncode == 0:
            data = json.loads(probe.stdout or "{}")
            s = (data.get("streams") or [{}])[0]
            result["frames"]     = int(s.get("nb_frames") or 0)
            result["duration_s"] = float(s.get("duration") or 0.0)
            result["codec"]      = s.get("codec_name", "")
    except Exception:
        pass
    return result


def _check_near_static(path: Path, *, min_mad_threshold: float = 0.3) -> bool:
    """
    Check if the output video is near-static using mean absolute difference
    between evenly spaced frame pairs via ffmpeg's SSIM/blend filter.
    Returns True (frozen) only if MAD < min_mad_threshold across the sample.

    0.3 MAD is a very conservative threshold — real LP animation with a real
    driving video will typically have MAD of 2-15+.  A truly frozen identical
    frame would have MAD=0.
    """
    try:
        # Sample 4 frame-pairs spread across the video and compute avg MAD
        probe_r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration,nb_frames",
             "-of", "default=nw=1", str(path)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        props: dict[str, str] = {}
        for line in (probe_r.stdout or "").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()
        duration = float(props.get("duration") or 0)
        nb_frames = int(props.get("nb_frames") or 0)
        if duration < 0.5 or nb_frames < 4:
            return False  # too short to judge

        # Use ffmpeg blend to compute SAD between consecutive frames
        # and read the psnr to judge motion; a truly static output gives psnr=inf
        r = subprocess.run(
            [
                "ffmpeg", "-i", str(path),
                "-vf", "tblend=all_mode=difference,metadata=print:file=-",
                "-frames:v", "30",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=30,
        )
        # Extract lavfi.td.field0.mean values from metadata
        means: list[float] = []
        for line in (r.stderr or "").splitlines():
            if "lavfi.td.field0.mean" in line or "lavfi.td.mean" in line:
                try:
                    val = float(line.strip().split("=")[-1])
                    means.append(val)
                except ValueError:
                    pass
        if not means:
            # Fallback: try to parse blend output differently
            # If we can't detect, assume not static
            return False
        avg_mean = sum(means) / len(means)
        # avg_mean < min_mad_threshold means consecutive frames are nearly identical
        return avg_mean < min_mad_threshold
    except Exception:
        return False


def _probe_dimensions(path: Path) -> tuple[int, int]:
    """Return (width, height) for video via ffprobe, or (0,0) on failure."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(path),
            ],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if probe.returncode == 0:
            data = json.loads(probe.stdout or "{}")
            s = (data.get("streams") or [{}])[0]
            return int(s.get("width") or 0), int(s.get("height") or 0)
    except Exception:
        pass
    return 0, 0


def _region_mad_list(path: Path, *, crop: tuple[int, int, int, int] | None = None, sample_frames: int = 30) -> list[float]:
    """Compute per-frame MAD values for a region (using tblend difference).

    Returns list of float means (may be empty on error).
    """
    try:
        if crop:
            w, h, x, y = crop
            vf = f"crop={w}:{h}:{x}:{y},tblend=all_mode=difference,metadata=print:file=-"
        else:
            vf = "tblend=all_mode=difference,metadata=print:file=-"
        r = subprocess.run(
            [
                "ffmpeg", "-i", str(path),
                "-vf", vf,
                "-frames:v", str(sample_frames),
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
        lines = (r.stderr or "").splitlines()
        values: list[float] = []
        for line in lines:
            if "lavfi.td.field0.mean" in line or "lavfi.td.mean" in line:
                try:
                    val = float(line.strip().split("=")[-1])
                    values.append(val)
                except Exception:
                    pass
        return values
    except Exception:
        return []


def _extract_sample_frames(path: Path, out_dir: Path, *, desired_samples: int = 8) -> list[Path]:
    """Extract a small set of sample PNG frames and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = _probe_video(path)
    duration = float(probe.get("duration_s") or 0.0)
    frames = int(probe.get("frames") or 0)
    if duration <= 0 or frames <= 0:
        # fallback: extract 4 frames at 1fps
        sample_fps = 1.0
    else:
        sample_fps = max(1.0, float(desired_samples) / max(duration, 0.001))
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vf", f"fps={sample_fps}",
        "-vsync", "0", "-q:v", "2",
        str(out_dir / "frame_%04d.png"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
    except Exception:
        pass
    files = sorted(out_dir.glob("frame_*.png"))
    if not files:
        return []
    # Downsample to desired_samples evenly
    if len(files) <= desired_samples:
        return files
    step = max(1, len(files) // desired_samples)
    selected = [files[i] for i in range(0, len(files), step)][:desired_samples]
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Standalone LivePortrait smoke test (no MuseTalk, no pipeline)"
    )
    parser.add_argument("--image",   required=True,  help="Source portrait image or video")
    parser.add_argument("--driving", default="",     help="Driving video (optional; uses bundled d0.mp4 if absent)")
    parser.add_argument("--output",  required=True,  help="Output mp4 path")
    parser.add_argument("--timeout", type=int, default=LP_TIMEOUT, help="Inference timeout (s)")
    parser.add_argument("--multiplier", type=float, default=0.0,
                        help="driving-multiplier override (0 = use env or default)")
    parser.add_argument("--no-half", action="store_true",
                        help="Disable fp16 (use if black boxes appear)")
    parser.add_argument("--metrics-json", default="", help="Optional path to write JSON metrics about the output video")
    args = parser.parse_args()

    t_script_start = time.monotonic()

    print("=" * 68)
    print("  LIVEPORTRAIT STANDALONE SMOKE TEST")
    print("=" * 68)
    print(f"  python      = {sys.version.split()[0]}")
    print(f"  pid         = {os.getpid()}")
    print(f"  entrypoint  = {LP_ENTRY}")
    print(f"  model_path  = {LP_MODELS}")
    print(f"  lp_home     = {LP_HOME}")
    print(f"  timeout     = {args.timeout}s")
    print(f"  image       = {args.image}")
    print(f"  driving     = {args.driving or '(auto)'}")
    print(f"  output      = {args.output}")

    # ── Stage 1: environment checks ──────────────────────────────────────────
    _banner("STAGE 1 – ENVIRONMENT CHECKS")
    failures: list[str] = []

    if not LP_ENTRY.exists():
        failures.append(f"MISSING entrypoint: {LP_ENTRY}")
    if not LP_MODELS.exists():
        failures.append(f"MISSING model_path: {LP_MODELS}")
    if not LP_HOME.exists():
        failures.append(f"MISSING lp_home: {LP_HOME}")

    source_image = Path(args.image)
    if not source_image.exists():
        failures.append(f"MISSING source image: {source_image}")

    for check, label in [
        (LP_MODELS / "liveportrait" / "base_models" / "appearance_feature_extractor.pth",
         "appearance_feature_extractor.pth"),
        (LP_MODELS / "liveportrait" / "base_models" / "motion_extractor.pth",
         "motion_extractor.pth"),
        (LP_MODELS / "liveportrait" / "base_models" / "spade_generator.pth",
         "spade_generator.pth"),
        (LP_MODELS / "liveportrait" / "base_models" / "warping_module.pth",
         "warping_module.pth"),
    ]:
        status = "✓" if check.exists() else "✗ MISSING"
        print(f"  model  {status}  {label}")
        if not check.exists():
            failures.append(f"MISSING model weight: {label}")

    if failures:
        for f in failures:
            print(f"  [FAIL] {f}", file=sys.stderr)
        print("\n[RESULT] FAIL misinstallation")
        print(f"[REASON] {'; '.join(failures)}")
        return 1

    print("  All environment checks passed ✓")

    # ── Stage 2: GPU / VRAM state ────────────────────────────────────────────
    _banner("STAGE 2 – GPU / VRAM STATE")
    gpu = _gpu_state()
    for k, v in gpu.items():
        print(f"  {k:<24} = {v}")

    if not gpu["cuda_available"]:
        print("\n[RESULT] FAIL gpu_issue")
        print("[REASON] CUDA not available — LivePortrait will not run in fp16 mode")
        return 1

    # ── Stage 3: choose driving source ───────────────────────────────────────
    _banner("STAGE 3 – DRIVING SOURCE")
    bundled_d0   = LP_HOME / "assets" / "examples" / "driving" / "d0.mp4"
    driving_path = Path(args.driving) if args.driving.strip() else None

    if driving_path and driving_path.exists():
        motion_source = "provided"
        print(f"  motion_source = provided  ({driving_path})")
    elif bundled_d0.exists():
        driving_path  = bundled_d0
        motion_source = "bundled_d0"
        print(f"  motion_source = bundled_d0  ({driving_path})")
    else:
        # Generate a micro-motion clip from the source image
        with tempfile.TemporaryDirectory(prefix="lp-smoke-drive-") as td:
            _drv = Path(td) / "micro_drive.mp4"
            _filter = (
                "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
                "crop=iw-20:ih-20:10+6*sin(2*PI*t/1.1):10+5*cos(2*PI*t/1.4),"
                "scale=trunc(iw/2)*2:trunc(ih/2)*2"
            )
            _cmd = [
                "ffmpeg", "-y", "-loop", "1", "-framerate", "25",
                "-i", str(source_image),
                "-vf", _filter, "-t", "4",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                str(_drv),
            ]
            r = subprocess.run(_cmd, capture_output=True, text=True, check=False, timeout=30)
            if r.returncode == 0 and _drv.exists():
                # Copy out of temp dir before it's deleted
                _drv_copy = Path("/tmp/lp_smoke_micromotion.mp4")
                shutil.copy2(str(_drv), str(_drv_copy))
                driving_path  = _drv_copy
                motion_source = "generated_micro_motion"
                print(f"  motion_source = generated_micro_motion ({driving_path})")
            else:
                print(f"  [FAIL] Cannot generate micro-motion: {r.stderr[:200]}", file=sys.stderr)
                print("\n[RESULT] FAIL motion_source_issue")
                return 1

    # ── Stage 4: build CLI and run inference ─────────────────────────────────
    _banner("STAGE 4 – INFERENCE")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Wipe stale LP outputs dir before run
    lp_out_dir = LP_HOME / "outputs"
    if lp_out_dir.exists():
        for _stale in lp_out_dir.rglob("*.mp4"):
            try: _stale.unlink()
            except Exception: pass

    run_wall_start = time.time()

    # Build the CLI
    # Use a temp output directory; LP always writes to --output-dir
    with tempfile.TemporaryDirectory(prefix="lp-smoke-out-") as td:
        temp_out_dir = Path(td)

        # multiplier: CLI > arg > env > default (1.0)
        multiplier = args.multiplier
        if multiplier == 0.0:
            _env_mult = os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_STRENGTH", "").strip()
            multiplier = float(_env_mult) if _env_mult else 1.0

        cmd = [
            sys.executable, str(LP_ENTRY),
            "--source",  str(source_image),
            "--driving", str(driving_path),
            "--output-dir", str(temp_out_dir),
            "--driving-multiplier", str(multiplier),
            "--driving-option", "expression-friendly",
            "--animation-region", "all",
            # Natural-motion flags (tuning pass)
            "--flag-normalize-lip",        # neutral lip start
        ]
        if not args.no_half:
            cmd += ["--flag-use-half-precision"]
        else:
            cmd += ["--no-flag-use-half-precision"]

        # Tuning: variance for smooth motion from env
        smooth_var = os.environ.get("AVATAR_LIVEPORTRAIT_TEMPORAL_SMOOTHING", "").strip()
        # LP's flag is --driving-smooth-observation-variance; default 3e-7 is tiny
        # We expose it as AVATAR_LIVEPORTRAIT_TEMPORAL_SMOOTHING (our naming)
        if smooth_var:
            cmd += ["--driving-smooth-observation-variance", smooth_var]

        print(f"\n  cmd: {' '.join(cmd)}")
        print(f"  multiplier = {multiplier}  |  half_precision = {not args.no_half}")
        print(f"  --- running (timeout={args.timeout}s) ---\n")

        t_infer_start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=args.timeout,
                cwd=str(LP_HOME),   # run from LP home so relative paths resolve
            )
        except subprocess.TimeoutExpired:
            print(f"\n[RESULT] FAIL timeout_inference")
            print(f"[REASON] LivePortrait timed out after {args.timeout}s — "
                  f"likely VRAM pressure or model load issue")
            return 1

        elapsed_infer = time.monotonic() - t_infer_start
        print(proc.stdout or "")

        # ── Stage 5: locate output ───────────────────────────────────────────
        _banner("STAGE 5 – OUTPUT VERIFICATION")

        found_mp4: Path | None = None
        # Search temp_out_dir and LP's own outputs dir
        for search_dir in [temp_out_dir, lp_out_dir, LP_HOME / "animations"]:
            if not search_dir.exists():
                continue
            candidates = sorted(
                (p for p in search_dir.rglob("*.mp4")
                 if p.exists() and p.stat().st_size > 0
                 and p.stat().st_mtime >= run_wall_start - 1.0),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if candidates:
                found_mp4 = candidates[0]
                break

        if proc.returncode != 0:
            output_after_run = list(temp_out_dir.rglob("*.mp4"))
            print(f"  return_code = {proc.returncode}")
            print(f"  output_files_in_temp = {output_after_run}")
            # Diagnose failure from stdout/stderr
            combined = (proc.stdout or "").lower()
            if "out of memory" in combined or "oom" in combined:
                reason = "oom_vram"
                diag   = "CUDA OOM during inference — reduce batch or use --no-half (fp32)"
            elif "no face" in combined or "no landmark" in combined:
                reason = "input_crop_issue"
                diag   = "Face not detected in source image — ensure clear, frontal face at 512×512+"
            elif "error" in combined and "model" in combined:
                reason = "misinstallation"
                diag   = "Model load error — check pretrained_weights directory"
            elif not found_mp4:
                reason = "code_integration_issue"
                diag   = f"inference.py exited {proc.returncode} with no output"
            else:
                reason = f"return_code_{proc.returncode}"
                diag   = "Non-zero exit but output found — check logs above"
            print(f"\n[RESULT] FAIL {reason}")
            print(f"[REASON] {diag}")
            return 1

        if not found_mp4:
            print(f"  inference.py exited 0 but no mp4 found")
            print(f"  searched: {temp_out_dir}, {lp_out_dir}")
            print(f"\n[RESULT] FAIL liveportrait_output_missing")
            print(f"[REASON] inference.py returned 0 but produced no output file. "
                  f"Check that --output-dir is being respected by this LP version.")
            return 1

        # Copy to requested output path
        shutil.copy2(str(found_mp4), str(out_path))
        probe = _probe_video(out_path)

        print(f"  output_path   = {out_path}")
        print(f"  size_bytes    = {out_path.stat().st_size}")
        print(f"  frame_count   = {probe['frames']}")
        print(f"  duration_s    = {probe['duration_s']:.2f}")
        print(f"  codec         = {probe['codec']}")

        # Metric collection (optional)
        is_static = False
        try:
            if probe["frames"] > 0 and probe["duration_s"] > 1.0:
                is_static = _check_near_static(out_path)
        except Exception:
            is_static = False

        if args.metrics_json:
            try:
                metrics_path = Path(args.metrics_json)
                # probe dimensions
                w, h = _probe_dimensions(out_path)
                # extract small sample of frames for uniqueness
                sample_dir = temp_out_dir / "lp_metrics_frames"
                samples = _extract_sample_frames(out_path, sample_dir, desired_samples=8)
                unique_hashes = set()
                for f in samples:
                    try:
                        with open(f, "rb") as fh:
                            unique_hashes.add(hashlib.sha256(fh.read()).hexdigest())
                    except Exception:
                        pass
                unique_count = len(unique_hashes)

                # compute region MAD lists
                head_mads = _region_mad_list(out_path, crop=None, sample_frames=30)
                eye_mads = []
                mouth_mads = []
                if w > 0 and h > 0:
                    # define heuristic crops (centered)
                    cw = max(4, int(w * 0.5))
                    ch_eye = max(4, int(h * 0.18))
                    cx = max(0, int((w - cw) / 2))
                    cy_eye = max(0, int(h * 0.15))
                    eye_mads = _region_mad_list(out_path, crop=(cw, ch_eye, cx, cy_eye), sample_frames=30)

                    ch_mouth = max(4, int(h * 0.22))
                    cy_mouth = max(0, int(h * 0.58))
                    mouth_mads = _region_mad_list(out_path, crop=(cw, ch_mouth, cx, cy_mouth), sample_frames=30)

                def _avg(lst: list[float]) -> float:
                    return float(sum(lst) / len(lst)) if lst else 0.0

                eye_avg = _avg(eye_mads)
                eye_std = float(statistics.pstdev(eye_mads)) if len(eye_mads) > 1 else 0.0
                spike_thresh = eye_avg + max(1e-6, 1.5 * eye_std, 2.0 * eye_avg)
                spikes = sum(1 for v in eye_mads if v > spike_thresh) if eye_mads else 0
                blink_score = float(spikes) / len(eye_mads) if eye_mads else 0.0

                metrics = {
                    "input_path": str(source_image),
                    "canonical_source": str(source_image.resolve()),
                    "motion_source": motion_source,
                    "frame_count": int(probe.get("frames") or 0),
                    "unique_frames": int(unique_count),
                    "head_motion_mad": round(_avg(head_mads), 6),
                    "eye_motion_mad": round(_avg(eye_mads), 6),
                    "mouth_motion_mad": round(_avg(mouth_mads), 6),
                    "blink_score": round(float(blink_score), 6),
                    "duration_s": float(probe.get("duration_s") or 0.0),
                    "near_static": bool(is_static),
                }
                metrics_path.parent.mkdir(parents=True, exist_ok=True)
                with open(metrics_path, "w", encoding="utf-8") as fh:
                    json.dump(metrics, fh, indent=2)
                print(f"\n[METRICS] written {metrics_path}")
                print(json.dumps(metrics, indent=2))
            except Exception as me:
                print(f"[METRICS] failed to compute or write metrics: {me}", file=sys.stderr)

        # Near-static check
        if probe["frames"] > 0 and probe["duration_s"] > 1.0:
            if is_static:
                print(f"\n[RESULT] FAIL near_static_output")
                print(f"[REASON] Output video appears frozen/near-static. "
                      f"Likely cause: image driving without a real motion source, "
                      f"or multiplier too low ({multiplier}). "
                      f"Try --driving with a real video, or increase --multiplier.")
                return 1

    # ── Summary ──────────────────────────────────────────────────────────────
    total_elapsed = time.monotonic() - t_script_start
    _banner("RESULT")
    print(f"  motion_source     = {motion_source}")
    print(f"  multiplier        = {multiplier}")
    print(f"  inference_elapsed = {elapsed_infer:.1f}s")
    print(f"  total_elapsed     = {total_elapsed:.1f}s")
    print(f"  output            = {out_path}")
    print(f"\n[RESULT] PASS  total_elapsed={total_elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
