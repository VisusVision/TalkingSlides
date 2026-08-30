#!/usr/bin/env python3
"""Run one real MuseTalk recovery attempt against a fault-injection proxy."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


SERVICES_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from avatar.canonical_pipeline import _run_musetalk_with_transient_recovery  # noqa: E402
from avatar.resource_manager import probe_runtime_resources  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe_video(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"recovery_smoke_ffprobe_failed:{process.stderr[-300:]}")
    return json.loads(process.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-image", type=Path, required=True)
    parser.add_argument("--source-video", type=Path, required=True)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()

    for name, path in (
        ("source_image", args.source_image),
        ("source_video", args.source_video),
        ("audio_path", args.audio_path),
    ):
        if not path.is_file():
            parser.error(f"{name} does not exist: {path}")
    if args.output_path.exists():
        parser.error(f"output already exists: {args.output_path}")

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_hash_before = _sha256(args.source_video)
    resources_before = probe_runtime_resources()
    started_at = time.monotonic()
    result = _run_musetalk_with_transient_recovery(
        source_image=str(args.source_image),
        source_video=str(args.source_video),
        audio_path=str(args.audio_path),
        output_path=str(args.output_path),
        env_overrides={"MUSETALK_FPS": "25"},
        timeout_seconds=max(float(args.timeout_seconds), 1.0),
        stage_name="recovery_smoke_musetalk",
    )
    elapsed_seconds = time.monotonic() - started_at
    handoff_hash_after = _sha256(args.source_video)
    output_probe = _probe_video(args.output_path) if result.success and args.output_path.is_file() else {}
    output_hash = _sha256(args.output_path) if result.success and args.output_path.is_file() else ""
    details = dict(result.details or {})
    report = {
        "version": "avatar-musetalk-recovery-smoke-v1",
        "success": bool(result.success),
        "error": str(result.error or ""),
        "elapsed_seconds": round(elapsed_seconds, 4),
        "handoff": {
            "path": str(args.source_video),
            "sha256_before": handoff_hash_before,
            "sha256_after": handoff_hash_after,
            "unchanged": handoff_hash_before == handoff_hash_after,
        },
        "output": {
            "path": str(args.output_path),
            "sha256": output_hash,
            "probe": output_probe,
        },
        "recovery": {
            "retry_limit": int(details.get("transient_retry_limit") or 0),
            "retry_count": int(details.get("transient_retry_count") or 0),
            "attempts": list(details.get("transient_retry_attempts") or []),
            "failure_classification": str(details.get("transient_failure_classification") or ""),
            "recovery_succeeded": bool(details.get("transient_recovery_succeeded")),
            "retry_exhausted": bool(details.get("transient_retry_exhausted")),
            "attempt_elapsed_seconds": float(details.get("transient_attempt_elapsed_seconds") or 0.0),
            "recovery_elapsed_seconds": float(details.get("transient_recovery_elapsed_seconds") or 0.0),
        },
        "resources": {
            "before": resources_before,
            "after": probe_runtime_resources(),
        },
    }
    args.report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True), flush=True)

    valid = bool(
        report["success"]
        and report["handoff"]["unchanged"]
        and report["recovery"]["retry_count"] == 1
        and report["recovery"]["recovery_succeeded"]
        and not report["recovery"]["retry_exhausted"]
        and report["output"]["sha256"]
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
