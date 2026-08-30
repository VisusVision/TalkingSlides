from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SERVICES_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar.digital_twin.lipsync_syncnet import S3FD_FILENAME, SYNCNET_FILENAME, evaluate_syncnet_lipsync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate model-backed lip sync for one or more avatar videos.")
    parser.add_argument("audio_path", type=Path)
    parser.add_argument("output_videos", type=Path, nargs="+")
    parser.add_argument("--runtime-home", type=Path, default=Path("/opt/latentsync"))
    parser.add_argument("--model-dir", type=Path, default=Path("storage_local/models/syncnet"))
    parser.add_argument(
        "--s3fd-model",
        type=Path,
        default=Path("storage_local/models/torch/hub/checkpoints") / S3FD_FILENAME,
    )
    parser.add_argument("--min-confidence", type=float, default=2.0)
    parser.add_argument("--max-offset-frames", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()

    results = []
    failed = False
    for video in args.output_videos:
        signal = evaluate_syncnet_lipsync(
            output_video=video,
            audio_path=args.audio_path,
            runtime_home=args.runtime_home,
            checkpoint=args.model_dir / SYNCNET_FILENAME,
            s3fd_checkpoint=args.s3fd_model,
            min_confidence=args.min_confidence,
            max_offset_frames=args.max_offset_frames,
            timeout_seconds=args.timeout_seconds,
        )
        failed = failed or signal.passed is not True
        results.append({"video": str(video), "lip_sync": signal.__dict__})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
