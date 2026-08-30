"""Production-oriented render quality report with pluggable strong metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
from typing import Any, Mapping

from .identity_sface import evaluate_sface_identity
from .lipsync_syncnet import evaluate_syncnet_lipsync
from .verification import VerificationSignal, _provider_signal, _run_json_provider, analyze_video_evidence, enforce_signal_threshold

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None


@dataclass(frozen=True)
class RenderQualityReport:
    decision: str
    publish_allowed: bool
    strict_gate: bool
    identity: VerificationSignal
    lip_sync: VerificationSignal
    temporal: VerificationSignal
    technical: dict[str, Any]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _source_face(source_image: str | Path):
    if cv2 is None:
        return None
    image = cv2.imread(str(source_image))
    if image is None:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(64, 64))
    if len(faces) == 0:
        return None
    x, y, width, height = [int(value) for value in max(faces, key=lambda row: int(row[2]) * int(row[3]))]
    crop = image[y:y + height, x:x + width]
    return cv2.resize(crop, (96, 96)) if crop.size else None


def _identity_proxy(source_image: str | Path, output_video: str | Path) -> float | None:
    source = _source_face(source_image)
    output = analyze_video_evidence(output_video, max_samples=24)
    if cv2 is None or source is None or not output.get("face_crops"):
        return None
    source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    scores: list[float] = []
    for target in output["face_crops"]:
        target_gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
        correlation = float(cv2.matchTemplate(source_gray, target_gray, cv2.TM_CCOEFF_NORMED)[0][0])
        scores.append(max(0.0, min(1.0, (correlation + 1.0) / 2.0)))
    return round(sum(scores) / len(scores), 4) if scores else None


def evaluate_render_quality(
    *,
    source_image: str | Path,
    output_video: str | Path,
    audio_path: str | Path,
    render_info: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> RenderQualityReport:
    env = environ if environ is not None else os.environ
    validation = dict(render_info.get("motion_validation") or {})
    checks = dict(validation.get("quality_checks") or {})
    strict_validation = bool(render_info.get("strict_validation_passed"))
    audio_match = bool(validation.get("audio_match", True))
    duration_mismatch = bool(validation.get("duration_mismatch"))
    artifact = bool(
        checks.get("face_artifact_detected")
        or checks.get("structural_face_artifact_detected")
        or checks.get("face_warp_detected")
        or checks.get("glitch_detected")
        or checks.get("drift_detected")
    )
    landmark_stable = bool(checks.get("landmark_stable", True))
    technical_pass = bool(strict_validation and audio_match and not duration_mismatch and not artifact and landmark_stable)
    technical = {
        "strict_validation_passed": strict_validation,
        "audio_match": audio_match,
        "duration_mismatch": duration_mismatch,
        "artifact_detected": artifact,
        "landmark_stable": landmark_stable,
        "lip_movement_score": checks.get("lip_movement_score"),
        "mouth_openness_change": checks.get("mouth_openness_change"),
        "failure_reason": str(validation.get("failure_reason") or ""),
    }
    replacements = {
        "source_image": str(source_image),
        "output_video": str(output_video),
        "audio_path": str(audio_path),
    }

    identity_template = str(env.get("DIGITAL_TWIN_IDENTITY_VERIFY_CMD") or "").strip()
    yunet_model = str(env.get("DIGITAL_TWIN_YUNET_MODEL_PATH") or "").strip()
    sface_model = str(env.get("DIGITAL_TWIN_SFACE_MODEL_PATH") or "").strip()
    if identity_template:
        try:
            identity = enforce_signal_threshold(
                _provider_signal(
                    "identity_similarity",
                    _run_json_provider(identity_template, replacements),
                    "configured_identity_provider",
                ),
                float(env.get("DIGITAL_TWIN_IDENTITY_MIN_SCORE") or 0.82),
            )
        except Exception as exc:
            identity = VerificationSignal("identity_similarity", None, None, "unavailable", "configured_identity_provider", {}, str(exc))
    elif yunet_model or sface_model:
        if not yunet_model or not sface_model:
            identity = VerificationSignal(
                "identity_similarity", False, None, "strong", "opencv_yunet_sface", {},
                "identity_model_configuration_incomplete",
            )
        else:
            identity = evaluate_sface_identity(
                source_image=source_image,
                output_video=output_video,
                yunet_model=yunet_model,
                sface_model=sface_model,
                min_cosine=float(env.get("DIGITAL_TWIN_IDENTITY_MIN_COSINE") or 0.363),
                min_face_coverage=float(env.get("DIGITAL_TWIN_IDENTITY_MIN_FACE_COVERAGE") or 0.60),
                min_face_frames=int(env.get("DIGITAL_TWIN_IDENTITY_MIN_FACE_FRAMES") or 6),
                max_samples=int(env.get("DIGITAL_TWIN_IDENTITY_MAX_SAMPLES") or 24),
            )
    else:
        score = _identity_proxy(source_image, output_video)
        identity = VerificationSignal(
            "identity_similarity", score is not None and score >= 0.58, score, "heuristic",
            "opencv_appearance_consistency", {"auto_release_eligible": False},
        )

    lipsync_template = str(env.get("DIGITAL_TWIN_LIPSYNC_VERIFY_CMD") or "").strip()
    syncnet_home = str(env.get("DIGITAL_TWIN_SYNCNET_HOME") or "").strip()
    syncnet_model = str(env.get("DIGITAL_TWIN_SYNCNET_MODEL_PATH") or "").strip()
    syncnet_s3fd = str(env.get("DIGITAL_TWIN_SYNCNET_S3FD_MODEL_PATH") or "").strip()
    if lipsync_template:
        try:
            lip_sync = enforce_signal_threshold(
                _provider_signal(
                    "lip_sync",
                    _run_json_provider(lipsync_template, replacements),
                    "configured_lipsync_provider",
                ),
                float(env.get("DIGITAL_TWIN_LIPSYNC_MIN_SCORE") or 0.80),
            )
        except Exception as exc:
            lip_sync = VerificationSignal("lip_sync", None, None, "unavailable", "configured_lipsync_provider", {}, str(exc))
    elif syncnet_model:
        if not syncnet_home or not syncnet_s3fd:
            lip_sync = VerificationSignal(
                "lip_sync", False, None, "strong", "latentsync_syncnet", {},
                "lipsync_model_configuration_incomplete",
            )
        else:
            lip_sync = evaluate_syncnet_lipsync(
                output_video=output_video,
                audio_path=audio_path,
                runtime_home=syncnet_home,
                checkpoint=syncnet_model,
                s3fd_checkpoint=syncnet_s3fd,
                min_confidence=env.get("DIGITAL_TWIN_SYNCNET_MIN_CONFIDENCE") or 2.0,
                max_offset_frames=env.get("DIGITAL_TWIN_SYNCNET_MAX_OFFSET_FRAMES") or 2,
                timeout_seconds=env.get("DIGITAL_TWIN_SYNCNET_TIMEOUT_SECONDS") or 300,
            )
    else:
        raw_lip = float(checks.get("lip_movement_score") or 0.0)
        target = max(float(checks.get("min_lip_movement") or 0.002), 0.0001)
        proxy = max(0.0, min(1.0, raw_lip / target))
        lip_sync = VerificationSignal(
            "lip_sync", bool(technical_pass and proxy >= 0.5), round(proxy, 4), "heuristic",
            "motion_audio_contract_proxy", {"auto_release_eligible": False},
        )

    temporal_score = 1.0 if technical_pass else max(0.0, 1.0 - float(checks.get("glitch_score") or 1.0))
    temporal = VerificationSignal(
        "temporal_stability", technical_pass, round(temporal_score, 4), "technical",
        "canonical_pipeline_validation", {"artifact_detected": artifact, "landmark_stable": landmark_stable},
    )
    strong = {"strong", "biometric", "verified"}
    strong_signals = (identity, lip_sync)
    strong_failure = any(signal.assurance in strong and signal.passed is False for signal in strong_signals)
    all_strong_pass = all(signal.assurance in strong and signal.passed is True for signal in strong_signals)
    strict_gate = _truthy(env.get("DIGITAL_TWIN_STRICT_QUALITY_GATE"))
    reasons: list[str] = []
    if not technical_pass:
        reasons.append(technical["failure_reason"] or "technical_validation_failed")
    if strong_failure:
        reasons.extend(signal.name + "_failed" for signal in strong_signals if signal.assurance in strong and signal.passed is False)
    if technical_pass and not all_strong_pass:
        reasons.append("strong_identity_or_lipsync_metric_pending")
    if not technical_pass or strong_failure:
        decision = "failed"
        publish_allowed = False
    elif all_strong_pass:
        decision = "passed"
        publish_allowed = True
    else:
        decision = "review_required"
        publish_allowed = not strict_gate
    return RenderQualityReport(
        decision=decision,
        publish_allowed=publish_allowed,
        strict_gate=strict_gate,
        identity=identity,
        lip_sync=lip_sync,
        temporal=temporal,
        technical=technical,
        reasons=tuple(dict.fromkeys(reasons)),
    )
