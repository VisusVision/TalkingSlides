from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


SERVICES_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar.digital_twin.motion_analysis import analyze_performance_motion  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Motion Style V2 manifest from a performance video.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-samples", type=int, default=240)
    parser.add_argument("--sample-hz", type=float, default=5.0)
    parser.add_argument("--disable-landmarks", action="store_true")
    args = parser.parse_args()

    environment = dict(os.environ)
    if args.disable_landmarks:
        environment["DIGITAL_TWIN_MOTION_LANDMARK_PROVIDER"] = "disabled"
    report = analyze_performance_motion(
        args.video,
        max_samples=max(args.max_samples, 1),
        sample_hz=max(args.sample_hz, 0.5),
        environ=environment,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
