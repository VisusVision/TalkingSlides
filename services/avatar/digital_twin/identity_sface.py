"""Local, model-backed identity consistency scoring for avatar renders.

This module intentionally does not download model weights at import time.  A
configured YuNet detector and SFace recognizer are treated as one strong,
fail-closed provider; the caller decides whether the provider is enabled.
"""

from __future__ import annotations

from hashlib import sha256
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .verification import VerificationSignal

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None


OPENCV_ZOO_REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("identity_similarity_samples_empty")
    position = max(0.0, min(float(fraction), 1.0)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _failure(error: str, *, details: dict[str, Any] | None = None) -> VerificationSignal:
    return VerificationSignal(
        name="identity_similarity",
        passed=False,
        score=None,
        assurance="strong",
        provider="opencv_yunet_sface",
        details=details or {},
        error=error,
    )


def _load_models(yunet_path: Path, sface_path: Path):
    if cv2 is None:
        raise RuntimeError("opencv_unavailable")
    detector = cv2.FaceDetectorYN.create(
        str(yunet_path), "", (320, 320), 0.9, 0.3, 5000
    )
    recognizer = cv2.FaceRecognizerSF.create(str(sface_path), "")
    return detector, recognizer


def _largest_face(detector, image):
    height, width = image.shape[:2]
    detector.setInputSize((int(width), int(height)))
    result = detector.detect(image)
    faces = result[1] if isinstance(result, tuple) else result
    if faces is None or len(faces) == 0:
        return None
    return max(faces, key=lambda row: float(row[2]) * float(row[3]))


def _feature(recognizer, image, face):
    aligned = recognizer.alignCrop(image, face)
    return recognizer.feature(aligned)


def _sample_video_frames(video_path: Path, *, max_samples: int) -> tuple[list[Any], int]:
    if cv2 is None:
        return [], 0
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return [], 0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    stride = max(1, frame_count // max(max_samples, 1)) if frame_count > 0 else 5
    frames: list[Any] = []
    decoded = index = 0
    while len(frames) < max(max_samples, 1):
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        decoded += 1
        if index % stride == 0:
            frames.append(frame)
        index += 1
    capture.release()
    return frames, decoded


def evaluate_sface_identity(
    *,
    source_image: str | Path,
    output_video: str | Path,
    yunet_model: str | Path,
    sface_model: str | Path,
    min_cosine: float = 0.363,
    min_face_coverage: float = 0.60,
    min_face_frames: int = 6,
    max_samples: int = 24,
) -> VerificationSignal:
    """Compare one source identity against sampled output frames.

    The score is the tenth-percentile cosine similarity, so a small number of
    identity-drift frames cannot be hidden by an otherwise high mean.
    """

    source_path = Path(source_image)
    video_path = Path(output_video)
    yunet_path = Path(yunet_model)
    sface_path = Path(sface_model)
    configured = {
        "opencv_zoo_revision": OPENCV_ZOO_REVISION,
        "yunet_model": str(yunet_path),
        "sface_model": str(sface_path),
    }
    if cv2 is None:
        return _failure("opencv_unavailable", details=configured)
    missing = [str(path) for path in (source_path, video_path, yunet_path, sface_path) if not path.is_file()]
    if missing:
        return _failure("identity_input_or_model_missing", details={**configured, "missing_paths": missing})
    try:
        yunet_hash = _file_sha256(yunet_path)
        sface_hash = _file_sha256(sface_path)
    except Exception as exc:
        return _failure(f"identity_model_hash_failed:{exc}", details=configured)

    provenance = {
        **configured,
        "yunet_sha256": yunet_hash,
        "sface_sha256": sface_hash,
        "expected_yunet_sha256": YUNET_SHA256,
        "expected_sface_sha256": SFACE_SHA256,
        "model_hashes_verified": bool(yunet_hash == YUNET_SHA256 and sface_hash == SFACE_SHA256),
        "opencv_version": str(getattr(cv2, "__version__", "unknown")),
    }
    if not provenance["model_hashes_verified"]:
        return _failure("identity_model_checksum_mismatch", details=provenance)
    try:
        detector, recognizer = _load_models(yunet_path, sface_path)
    except Exception as exc:
        return _failure(f"identity_model_load_failed:{exc}", details=provenance)

    source = cv2.imread(str(source_path))
    if source is None:
        return _failure("identity_source_unreadable", details=provenance)
    try:
        source_face = _largest_face(detector, source)
        if source_face is None:
            return _failure("identity_source_face_not_detected", details=provenance)
        source_feature = _feature(recognizer, source, source_face)
        frames, decoded = _sample_video_frames(video_path, max_samples=max_samples)
        similarities: list[float] = []
        for frame in frames:
            face = _largest_face(detector, frame)
            if face is None:
                continue
            target_feature = _feature(recognizer, frame, face)
            value = float(recognizer.match(source_feature, target_feature, cv2.FaceRecognizerSF_FR_COSINE))
            if math.isfinite(value):
                similarities.append(max(-1.0, min(1.0, value)))
    except Exception as exc:
        return _failure(f"identity_inference_failed:{exc}", details=provenance)

    sampled = len(frames)
    matched = len(similarities)
    coverage = matched / max(sampled, 1)
    details = {
        **provenance,
        "frames_decoded": decoded,
        "frames_sampled": sampled,
        "face_frames": matched,
        "face_coverage": round(coverage, 4),
        "minimum_face_coverage": round(max(0.0, min(float(min_face_coverage), 1.0)), 4),
        "minimum_face_frames": max(int(min_face_frames), 1),
        "minimum_cosine": round(max(-1.0, min(float(min_cosine), 1.0)), 4),
        "aggregation": "cosine_p10",
    }
    if sampled == 0:
        return _failure("identity_video_unreadable", details=details)
    if matched < max(int(min_face_frames), 1) or coverage < max(0.0, min(float(min_face_coverage), 1.0)):
        return _failure("identity_face_coverage_below_threshold", details=details)

    p10 = _percentile(similarities, 0.10)
    threshold = max(-1.0, min(float(min_cosine), 1.0))
    details.update(
        {
            "cosine_min": round(min(similarities), 4),
            "cosine_p10": round(p10, 4),
            "cosine_median": round(median(similarities), 4),
            "cosine_max": round(max(similarities), 4),
        }
    )
    passed = p10 >= threshold
    return VerificationSignal(
        name="identity_similarity",
        passed=passed,
        score=round(max(0.0, min(1.0, p10)), 4),
        assurance="strong",
        provider="opencv_yunet_sface",
        details=details,
        error="" if passed else "identity_cosine_below_threshold",
    )
