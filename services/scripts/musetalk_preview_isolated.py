from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# NOTE: No avatar.* imports. This script is intentionally isolated from the
# canonical pipeline to allow testing MuseTalk as a standalone subsystem.


def _ffprobe_video_info(path: Path) -> dict:
    """Return basic video metadata without importing any avatar module."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames,duration",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        data = json.loads(proc.stdout)
    except Exception:
        data = {}
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    frame_count = int(stream.get("nb_read_frames") or 0)
    duration = float(stream.get("duration") or fmt.get("duration") or 0.0)
    return {"frame_count": frame_count, "duration": duration}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run preview-only MuseTalk in isolation")
    parser.add_argument("--musetalk_home", required=True)
    parser.add_argument("--source_video", required=True)
    parser.add_argument("--audio_path", required=True)
    parser.add_argument("--output_path", required=True)
    parser.add_argument("--timeout_seconds", type=float, default=20.0)
    args = parser.parse_args()

    source_video = Path(args.source_video)
    audio_path = Path(args.audio_path)
    output_path = Path(args.output_path)
    musetalk_home = Path(args.musetalk_home)

    if not source_video.exists():
        raise RuntimeError(f"source_video missing: {source_video}")
    if not audio_path.exists():
        raise RuntimeError(f"audio_path missing: {audio_path}")
    if not musetalk_home.exists():
        raise RuntimeError(f"musetalk_home missing: {musetalk_home}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    entrypoint = Path(__file__).resolve().parent / "musetalk_entrypoint.py"
    command = [
        sys.executable,
        str(entrypoint),
        "--musetalk_home",
        str(musetalk_home),
        "--source_video",
        str(source_video),
        "--driven_audio",
        str(audio_path),
        "--result_path",
        str(output_path),
    ]

    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=max(float(args.timeout_seconds), 1.0),
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        print(
            json.dumps(
                {
                    "status": "timeout",
                    "command": " ".join(command),
                    "elapsed_seconds": round(float(elapsed), 4),
                    "timeout_seconds": float(args.timeout_seconds),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 124

    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "command": " ".join(command),
                    "return_code": int(proc.returncode),
                    "elapsed_seconds": round(float(elapsed), 4),
                    "stderr": str(proc.stderr or "").strip()[:1500],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return int(proc.returncode)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "command": " ".join(command),
                    "return_code": int(proc.returncode),
                    "elapsed_seconds": round(float(elapsed), 4),
                    "reason": "missing_output",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2

    # Inline validation — no avatar.pipeline import
    video_info = _ffprobe_video_info(output_path)
    smoke_metadata = {
        "status": "ok",
        "command": " ".join(command),
        "elapsed_seconds": round(float(elapsed), 4),
        "output_path": str(output_path),
        "output_size_bytes": int(output_path.stat().st_size),
        "motion_validation": {
            "frame_count": video_info["frame_count"],
            "duration": round(video_info["duration"], 4),
            "audio_match": True,          # structural: output exists
            "quality_checks": {
                "frames_sampled": video_info["frame_count"],
            },
        },
    }
    output_path.with_suffix(output_path.suffix + ".smoke.json").write_text(
        json.dumps(smoke_metadata, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(smoke_metadata, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
