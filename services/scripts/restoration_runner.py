from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _build_command(*, input_path: Path, output_path: Path, model: str) -> list[str]:
    # Keep restoration explicit and deterministic: a constrained transcode with
    # a mild enhancement filter for known restoration model labels.
    model_key = str(model or "").strip().lower()
    if model_key in {"codeformer", "gfpgan", "realesrgan"}:
        video_filter = "eq=contrast=1.02:saturation=1.02,unsharp=5:5:0.6:3:3:0.0,format=yuv420p"
    else:
        video_filter = "format=yuv420p"

    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone preview restoration runner")
    parser.add_argument("--input_path", "--input", dest="input_path", required=True)
    parser.add_argument("--output_path", "--output", dest="output_path", required=True)
    parser.add_argument("--source_image", nargs="?", default="", const="")
    parser.add_argument("--audio_path", nargs="?", default="", const="")
    parser.add_argument(
        "--model",
        default=str(os.environ.get("AVATAR_PREVIEW_RESTORATION_MODEL", "codeformer") or "codeformer"),
    )
    parser.add_argument(
        "--timeout_seconds",
        type=float,
        default=float(str(os.environ.get("AVATAR_STAGE_TIMEOUT_RESTORATION_SECONDS", "180") or "180")),
    )
    args = parser.parse_args()

    input_path = Path(str(args.input_path)).expanduser().resolve()
    output_path = Path(str(args.output_path)).expanduser().resolve()

    if not input_path.exists() or not input_path.is_file():
        print(f"[ERROR] restoration_runner input_missing path={input_path}", file=sys.stderr)
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = _build_command(
        input_path=input_path,
        output_path=output_path,
        model=str(args.model or "codeformer"),
    )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=(float(args.timeout_seconds) if float(args.timeout_seconds) > 0 else None),
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        print(
            f"[ERROR] restoration_runner timeout input={input_path} output={output_path} "
            f"timeout_seconds={float(args.timeout_seconds):.1f} elapsed_seconds={elapsed:.3f}",
            file=sys.stderr,
        )
        return 3

    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        stderr_tail = str(completed.stderr or "")[-2000:]
        print(
            f"[ERROR] restoration_runner ffmpeg_failed return_code={int(completed.returncode)} "
            f"elapsed_seconds={elapsed:.3f}",
            file=sys.stderr,
        )
        if stderr_tail:
            print(stderr_tail, file=sys.stderr)
        return int(completed.returncode)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        print(f"[ERROR] restoration_runner output_missing path={output_path}", file=sys.stderr)
        return 4

    print(
        "[RESULT] restoration_runner done "
        f"model={str(args.model or 'codeformer')} "
        f"input={input_path} output={output_path} "
        f"size_bytes={int(output_path.stat().st_size)} elapsed_seconds={elapsed:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())