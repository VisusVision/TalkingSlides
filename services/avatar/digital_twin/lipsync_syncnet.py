"""Model-backed audio/video synchronization verification using LatentSync SyncNet."""

from __future__ import annotations

from hashlib import sha256
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from .verification import VerificationSignal


LATENTSYNC_CODE_REVISION = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
LATENTSYNC_MODEL_REVISION = "405eda8eab9f65c1a6e0c292a5dee5a08089e2ae"
SYNCNET_FILENAME = "syncnet_v2.model"
SYNCNET_SHA256 = "961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442"
SYNCNET_MODEL_LICENSE = "openrail++"
S3FD_FILENAME = "s3fd-619a316812.pth"
S3FD_SHA256 = "619a31681264d3f7f7fc7a16a42cbbe8b23f31a256f75a366e5a1bcd59b33543"

_CONFIDENCE_PATTERN = re.compile(r"SyncNet confidence:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
_OFFSET_PATTERN = re.compile(r"AV offset:\s*([-+]?\d+)", re.IGNORECASE)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _failure(error: str, *, details: dict[str, Any] | None = None) -> VerificationSignal:
    return VerificationSignal(
        name="lip_sync",
        passed=False,
        score=None,
        assurance="strong",
        provider="latentsync_syncnet",
        details=details or {},
        error=error,
    )


def _runtime_revision(runtime_home: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(runtime_home), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    value = (result.stdout or "").strip().lower()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _parse_result(output: str) -> tuple[float, int] | None:
    confidence = _CONFIDENCE_PATTERN.findall(str(output or ""))
    offsets = _OFFSET_PATTERN.findall(str(output or ""))
    if not confidence or not offsets:
        return None
    return float(confidence[-1]), int(offsets[-1])


def evaluate_syncnet_lipsync(
    *,
    output_video: str | Path,
    audio_path: str | Path,
    runtime_home: str | Path,
    checkpoint: str | Path,
    s3fd_checkpoint: str | Path,
    min_confidence: float = 2.0,
    max_offset_frames: int = 2,
    timeout_seconds: int = 300,
) -> VerificationSignal:
    """Evaluate synchronization against the requested audio, not embedded audio.

    A temporary MP4 is created with the rendered video stream and requested
    audio stream before the pinned upstream evaluator is executed.
    """

    video = Path(output_video)
    audio = Path(audio_path)
    home = Path(runtime_home)
    model = Path(checkpoint)
    face_detector = Path(s3fd_checkpoint)
    evaluator = home / "eval" / "eval_sync_conf.py"
    try:
        confidence_threshold = float(min_confidence)
        offset_limit = int(max_offset_frames)
        timeout = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        return _failure(f"lipsync_configuration_invalid:{exc}")
    if not math.isfinite(confidence_threshold) or confidence_threshold <= 0 or offset_limit < 0 or timeout < 30:
        return _failure(
            "lipsync_configuration_out_of_range",
            details={
                "minimum_confidence": confidence_threshold,
                "maximum_offset_frames": offset_limit,
                "timeout_seconds": timeout,
            },
        )
    details: dict[str, Any] = {
        "latentsync_code_revision_expected": LATENTSYNC_CODE_REVISION,
        "latentsync_model_revision": LATENTSYNC_MODEL_REVISION,
        "syncnet_model": str(model),
        "s3fd_model": str(face_detector),
        "syncnet_model_license": SYNCNET_MODEL_LICENSE,
        "minimum_confidence": round(confidence_threshold, 4),
        "maximum_offset_frames": offset_limit,
        "video_fps_contract": 25,
    }
    missing = [str(path) for path in (video, audio, home, model, face_detector, evaluator) if not path.exists()]
    if missing:
        return _failure("lipsync_input_model_or_runtime_missing", details={**details, "missing_paths": missing})
    try:
        actual_hash = _file_sha256(model)
        face_detector_hash = _file_sha256(face_detector)
    except Exception as exc:
        return _failure(f"lipsync_model_hash_failed:{exc}", details=details)
    details.update(
        {
            "syncnet_sha256": actual_hash,
            "expected_syncnet_sha256": SYNCNET_SHA256,
            "model_hash_verified": actual_hash == SYNCNET_SHA256,
            "s3fd_sha256": face_detector_hash,
            "expected_s3fd_sha256": S3FD_SHA256,
            "s3fd_hash_verified": face_detector_hash == S3FD_SHA256,
        }
    )
    if actual_hash != SYNCNET_SHA256 or face_detector_hash != S3FD_SHA256:
        return _failure("lipsync_model_checksum_mismatch", details=details)

    revision = _runtime_revision(home)
    details["latentsync_code_revision"] = revision
    details["runtime_revision_verified"] = revision == LATENTSYNC_CODE_REVISION
    if revision != LATENTSYNC_CODE_REVISION:
        return _failure("lipsync_runtime_revision_unverified", details=details)

    with tempfile.TemporaryDirectory(prefix="avatar-syncnet-") as temporary:
        work = Path(temporary)
        evaluation_video = work / "evaluation.mp4"
        try:
            mux = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(video), "-i", str(audio),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(evaluation_video),
                ],
                capture_output=True,
                text=True,
                timeout=min(timeout, 120),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _failure("lipsync_evaluation_mux_timeout", details=details)
        except OSError as exc:
            return _failure(f"lipsync_evaluation_mux_unavailable:{exc}", details=details)
        if mux.returncode != 0 or not evaluation_video.is_file():
            details["ffmpeg_error"] = (mux.stderr or mux.stdout or "")[-600:]
            return _failure("lipsync_evaluation_mux_failed", details=details)

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(home) + os.pathsep + str(environment.get("PYTHONPATH") or "")
        try:
            checkpoints_dir = face_detector.parent
            if checkpoints_dir.name != "checkpoints" or checkpoints_dir.parent.name != "hub":
                return _failure("lipsync_s3fd_torch_home_layout_invalid", details=details)
            environment["TORCH_HOME"] = str(checkpoints_dir.parent.parent)
        except Exception as exc:
            return _failure(f"lipsync_s3fd_torch_home_resolution_failed:{exc}", details=details)
        command = [
            sys.executable,
            str(evaluator),
            "--initial_model", str(model),
            "--video_path", str(evaluation_video),
            "--temp_dir", str(work / "syncnet-temp"),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(work),
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _failure("lipsync_provider_timeout", details=details)
        except OSError as exc:
            return _failure(f"lipsync_provider_unavailable:{exc}", details=details)

    provider_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        details["provider_output_tail"] = provider_output[-1200:]
        return _failure("lipsync_provider_failed", details=details)
    parsed = _parse_result(provider_output)
    if parsed is None:
        details["provider_output_tail"] = provider_output[-1200:]
        return _failure("lipsync_provider_output_invalid", details=details)

    confidence, offset = parsed
    confidence_ok = confidence >= confidence_threshold
    offset_ok = abs(offset) <= offset_limit
    passed = bool(confidence_ok and offset_ok)
    normalized_score = max(0.0, confidence) / (max(0.0, confidence) + confidence_threshold / 4.0)
    details.update(
        {
            "syncnet_confidence": round(confidence, 4),
            "av_offset_frames": offset,
            "av_offset_milliseconds": round(offset * 40.0, 3),
            "confidence_passed": confidence_ok,
            "offset_passed": offset_ok,
            "score_normalization": "confidence/(confidence+minimum_confidence/4)",
        }
    )
    return VerificationSignal(
        name="lip_sync",
        passed=passed,
        score=round(max(0.0, min(1.0, normalized_score)), 4),
        assurance="strong",
        provider="latentsync_syncnet",
        details=details,
        error="" if passed else "lipsync_confidence_or_offset_failed",
    )
