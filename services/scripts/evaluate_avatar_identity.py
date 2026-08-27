from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SERVICES_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar.digital_twin.identity_sface import (  # noqa: E402
    SFACE_FILENAME,
    YUNET_FILENAME,
    evaluate_sface_identity,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate strong identity consistency for one or more avatar videos.")
    parser.add_argument("source_image", type=Path)
    parser.add_argument("output_videos", type=Path, nargs="+")
    parser.add_argument("--model-dir", type=Path, default=Path("storage_local/models/identity"))
    parser.add_argument("--min-cosine", type=float, default=0.363)
    parser.add_argument("--min-face-coverage", type=float, default=0.60)
    parser.add_argument("--min-face-frames", type=int, default=6)
    parser.add_argument("--max-samples", type=int, default=24)
    args = parser.parse_args()

    results = []
    failed = False
    for video in args.output_videos:
        signal = evaluate_sface_identity(
            source_image=args.source_image,
            output_video=video,
            yunet_model=args.model_dir / YUNET_FILENAME,
            sface_model=args.model_dir / SFACE_FILENAME,
            min_cosine=args.min_cosine,
            min_face_coverage=args.min_face_coverage,
            min_face_frames=args.min_face_frames,
            max_samples=args.max_samples,
        )
        failed = failed or signal.passed is not True
        results.append({"video": str(video), "identity": signal.__dict__})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
