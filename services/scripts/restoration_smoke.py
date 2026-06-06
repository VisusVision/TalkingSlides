from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from avatar.canonical_adapters import run_restoration


def _probe_duration_seconds(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return float((proc.stdout or "0").strip() or "0")
    except Exception:
        return 0.0


def _probe_frame_count(path: Path) -> int:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int((proc.stdout or "0").strip() or 0)
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone restoration smoke test")
    parser.add_argument("--input", required=True, help="Input video path for restoration")
    parser.add_argument("--output", required=True, help="Output restored video path")
    parser.add_argument("--source-image", default="", help="Optional source image path")
    parser.add_argument("--audio-path", default="", help="Optional audio path")
    args = parser.parse_args()

    input_path = Path(str(args.input)).expanduser().resolve()
    output_path = Path(str(args.output)).expanduser().resolve()
    source_image = str(args.source_image or "").strip()
    audio_path = str(args.audio_path or "").strip()

    print("=" * 68)
    print("  RESTORATION STANDALONE SMOKE TEST")
    print("=" * 68)
    print(f"  input          = {input_path}")
    print(f"  output         = {output_path}")
    print(f"  source_image   = {source_image or '(none)'}")
    print(f"  audio_path     = {audio_path or '(none)'}")

    restore_template = str(os.environ.get("AVATAR_PREVIEW_RESTORE_CMD", "")).strip()
    if not restore_template:
        print("[RESULT] FAIL reason=AVATAR_PREVIEW_RESTORE_CMD is empty")
        return 2

    if not input_path.exists() or not input_path.is_file():
        print(f"[RESULT] FAIL reason=input_missing path={input_path}")
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    result = run_restoration(
        input_video=str(input_path),
        output_path=str(output_path),
        source_image=source_image,
        audio_path=audio_path,
        env_overrides=os.environ.copy(),
    )
    details = dict(result.details or {})

    print("\n---- Adapter result ----")
    print(f"  success        = {bool(result.success)}")
    print(f"  error          = {str(result.error or '(none)')}")
    print(f"  return_code    = {details.get('return_code')}")
    print(f"  elapsed_seconds= {details.get('elapsed_seconds')}")
    print(f"  command        = {str(result.command or '')}")

    if not result.success:
        print("[RESULT] FAIL reason=restoration_command_failed")
        return 1

    if not output_path.exists() or output_path.stat().st_size <= 0:
        print(f"[RESULT] FAIL reason=output_missing_or_empty path={output_path}")
        return 1

    frame_count = _probe_frame_count(output_path)
    duration_s = _probe_duration_seconds(output_path)
    print("\n---- Output verification ----")
    print(f"  frame_count    = {frame_count}")
    print(f"  duration_s     = {duration_s:.3f}")
    print(f"  size_bytes     = {int(output_path.stat().st_size)}")

    if frame_count < 1 or duration_s <= 0.0:
        print("[RESULT] FAIL reason=invalid_output_contract")
        return 1

    print("\n[RESULT] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())