"""Fail-closed consent evidence verification.

Local OpenCV checks are useful for rejecting corrupt evidence and routing human
review, but they are deliberately not treated as biometric proof. Automatic
approval requires configured strong face, liveness, and ASR providers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import unicodedata
from typing import Any, Mapping

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None


@dataclass(frozen=True)
class VerificationSignal:
    name: str
    passed: bool | None
    score: float | None
    assurance: str
    provider: str
    details: dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class ConsentVerificationReport:
    decision: str
    reasons: tuple[str, ...]
    face_match: VerificationSignal
    liveness: VerificationSignal
    challenge: VerificationSignal
    evidence_quality: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def _clamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


def _run_json_provider(template: str, replacements: Mapping[str, str], *, timeout: int = 300) -> dict[str, Any]:
    rendered = str(template or "").format(**{key: str(value) for key, value in replacements.items()})
    command = shlex.split(rendered, posix=(os.name != "nt"))
    if not command:
        raise RuntimeError("provider_command_empty")
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"provider_failed:{(result.stderr or result.stdout or '')[-600:]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("provider_invalid_json") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("provider_payload_must_be_object")
    return payload


def _largest_face(gray) -> tuple[int, int, int, int] | None:
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(64, 64))
    if len(faces) == 0:
        return None
    return tuple(int(value) for value in max(faces, key=lambda item: int(item[2]) * int(item[3])))


def analyze_video_evidence(video_path: str | Path, *, max_samples: int = 48) -> dict[str, Any]:
    path = Path(video_path)
    if cv2 is None:
        return {"readable": False, "error": "opencv_unavailable"}
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"readable": False, "error": "video_unreadable"}
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    duration = frame_count / fps if frame_count > 0 and fps > 0 else 0.0
    stride = max(1, frame_count // max(max_samples, 1)) if frame_count > 0 else 5
    sampled = faces_found = multiple_face_frames = 0
    centers: list[tuple[float, float]] = []
    scales: list[float] = []
    frame_diffs: list[float] = []
    face_crops: list[Any] = []
    previous = None
    index = 0
    while sampled < max_samples:
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        if index % stride:
            index += 1
            continue
        index += 1
        sampled += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))
        if previous is not None:
            frame_diffs.append(float(cv2.absdiff(previous, small).mean()) / 255.0)
        previous = small
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(64, 64))
        if len(faces) > 1:
            multiple_face_frames += 1
        face = tuple(int(value) for value in max(faces, key=lambda item: int(item[2]) * int(item[3]))) if len(faces) else None
        if face is None:
            continue
        faces_found += 1
        x, y, width, height = face
        frame_h, frame_w = gray.shape[:2]
        centers.append(((x + width / 2) / max(frame_w, 1), (y + height / 2) / max(frame_h, 1)))
        scales.append((width * height) / max(frame_w * frame_h, 1))
        crop = frame[max(y, 0):min(y + height, frame_h), max(x, 0):min(x + width, frame_w)]
        if crop.size:
            face_crops.append(cv2.resize(crop, (96, 96)))
    capture.release()
    face_ratio = faces_found / max(sampled, 1)
    center_span = 0.0
    if centers:
        xs = [value[0] for value in centers]
        ys = [value[1] for value in centers]
        center_span = max(max(xs) - min(xs), max(ys) - min(ys))
    scale_span = max(scales) - min(scales) if scales else 0.0
    mean_frame_diff = sum(frame_diffs) / max(len(frame_diffs), 1)
    passive_motion = max(center_span * 4.0, scale_span * 8.0, mean_frame_diff * 3.0)
    return {
        "readable": sampled > 0,
        "duration_seconds": round(duration, 3),
        "frames_sampled": sampled,
        "face_frames": faces_found,
        "face_presence_ratio": round(face_ratio, 4),
        "multiple_face_ratio": round(multiple_face_frames / max(sampled, 1), 4),
        "center_span": round(center_span, 5),
        "scale_span": round(scale_span, 5),
        "mean_frame_diff": round(mean_frame_diff, 5),
        "passive_motion_score": round(min(passive_motion, 1.0), 4),
        "face_crops": face_crops,
    }


def _face_appearance_proxy(first: dict[str, Any], second: dict[str, Any]) -> float | None:
    if cv2 is None or not first.get("face_crops") or not second.get("face_crops"):
        return None
    scores: list[float] = []
    for source in first["face_crops"][:6]:
        source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        for target in second["face_crops"][:6]:
            target_gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
            correlation = float(cv2.matchTemplate(source_gray, target_gray, cv2.TM_CCOEFF_NORMED)[0][0])
            scores.append(max(0.0, min(1.0, (correlation + 1.0) / 2.0)))
    return round(max(scores), 4) if scores else None


def _normalize_words(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", ascii_text)


def challenge_similarity(expected: str, transcript: str) -> tuple[float, bool]:
    expected_words = _normalize_words(expected)
    actual_words = _normalize_words(transcript)
    sequence = SequenceMatcher(a=expected_words, b=actual_words).ratio() if expected_words and actual_words else 0.0
    expected_counts = {word: expected_words.count(word) for word in set(expected_words)}
    actual_counts = {word: actual_words.count(word) for word in set(actual_words)}
    overlap = sum(min(count, actual_counts.get(word, 0)) for word, count in expected_counts.items())
    coverage = overlap / max(len(expected_words), 1)
    nonce_match = re.search(r"\bkod\s*[:\-]?\s*([a-z0-9]+)", " ".join(expected_words))
    nonce_ok = not nonce_match or nonce_match.group(1) in actual_words
    return round(sequence * 0.45 + coverage * 0.55, 4), bool(nonce_ok)


def _provider_signal(name: str, payload: dict[str, Any], provider: str) -> VerificationSignal:
    passed = payload.get("passed")
    return VerificationSignal(
        name=name,
        passed=bool(passed) if isinstance(passed, bool) else None,
        score=_clamp(payload.get("score")),
        assurance=str(payload.get("assurance") or "provider").lower(),
        provider=provider,
        details={key: value for key, value in payload.items() if key not in {"passed", "score", "assurance"}},
    )


def enforce_signal_threshold(signal: VerificationSignal, minimum: float) -> VerificationSignal:
    """Require both the provider verdict and a finite normalized score."""

    if signal.passed is None:
        return signal
    score_ok = signal.score is not None and signal.score >= max(0.0, min(float(minimum), 1.0))
    details = dict(signal.details)
    details["minimum_score"] = round(float(minimum), 4)
    return replace(
        signal,
        passed=bool(signal.passed and score_ok),
        details=details,
        error=signal.error or ("provider_score_missing_or_below_threshold" if not score_ok else ""),
    )


def verify_consent_evidence(
    *,
    performance_video: str | Path,
    consent_video: str | Path,
    challenge_text: str,
    environ: Mapping[str, str] | None = None,
) -> ConsentVerificationReport:
    env = environ if environ is not None else os.environ
    performance = analyze_video_evidence(performance_video)
    consent = analyze_video_evidence(consent_video)
    evidence = {
        "performance": {key: value for key, value in performance.items() if key != "face_crops"},
        "consent": {key: value for key, value in consent.items() if key != "face_crops"},
    }
    invalid_reasons: list[str] = []
    if not consent.get("readable") or int(consent.get("frames_sampled") or 0) < 3:
        invalid_reasons.append("consent_video_unreadable")
    if float(consent.get("face_presence_ratio") or 0.0) < 0.45:
        invalid_reasons.append("consent_face_missing")
    if float(consent.get("multiple_face_ratio") or 0.0) > 0.25:
        invalid_reasons.append("multiple_faces_in_consent")

    replacements = {
        "performance_video": str(performance_video),
        "consent_video": str(consent_video),
        "challenge_text": challenge_text,
    }
    face_template = str(env.get("DIGITAL_TWIN_FACE_VERIFY_CMD") or "").strip()
    if face_template:
        try:
            face = enforce_signal_threshold(
                _provider_signal("face_match", _run_json_provider(face_template, replacements), "configured_face_provider"),
                float(env.get("DIGITAL_TWIN_FACE_MIN_SCORE") or 0.82),
            )
        except Exception as exc:
            face = VerificationSignal("face_match", None, None, "unavailable", "configured_face_provider", {}, str(exc))
    else:
        proxy = _face_appearance_proxy(performance, consent)
        face = VerificationSignal(
            "face_match", proxy is not None and proxy >= 0.58, proxy, "heuristic", "opencv_appearance_precheck",
            {"auto_approval_eligible": False},
        )

    liveness_template = str(env.get("DIGITAL_TWIN_LIVENESS_VERIFY_CMD") or "").strip()
    if liveness_template:
        try:
            liveness = enforce_signal_threshold(
                _provider_signal("liveness", _run_json_provider(liveness_template, replacements), "configured_liveness_provider"),
                float(env.get("DIGITAL_TWIN_LIVENESS_MIN_SCORE") or 0.80),
            )
        except Exception as exc:
            liveness = VerificationSignal("liveness", None, None, "unavailable", "configured_liveness_provider", {}, str(exc))
    else:
        live_score = _clamp(float(consent.get("passive_motion_score") or 0.0))
        live_pass = bool(
            not invalid_reasons
            and float(consent.get("duration_seconds") or 0.0) >= 3.0
            and float(live_score or 0.0) >= 0.035
        )
        liveness = VerificationSignal(
            "liveness", live_pass, live_score, "heuristic", "opencv_passive_motion_precheck",
            {"auto_approval_eligible": False},
        )

    asr_template = str(env.get("DIGITAL_TWIN_ASR_VERIFY_CMD") or "").strip()
    if asr_template:
        try:
            payload = _run_json_provider(asr_template, replacements)
            transcript = str(payload.get("transcript") or "")
            similarity, nonce_ok = challenge_similarity(challenge_text, transcript)
            threshold = float(env.get("DIGITAL_TWIN_CHALLENGE_MIN_SIMILARITY") or 0.72)
            payload["score"] = similarity
            payload["passed"] = bool(similarity >= threshold and nonce_ok)
            payload["nonce_match"] = nonce_ok
            payload["transcript"] = transcript[:2000]
            challenge = _provider_signal("spoken_challenge", payload, "configured_asr_provider")
        except Exception as exc:
            challenge = VerificationSignal("spoken_challenge", None, None, "unavailable", "configured_asr_provider", {}, str(exc))
    else:
        challenge = VerificationSignal(
            "spoken_challenge", None, None, "unavailable", "none", {"auto_approval_eligible": False}, "asr_provider_not_configured"
        )

    signals = (face, liveness, challenge)
    strong_assurance = {"strong", "biometric", "verified"}
    strong_failure = any(signal.assurance in strong_assurance and signal.passed is False for signal in signals)
    all_strong_pass = all(signal.assurance in strong_assurance and signal.passed is True for signal in signals)
    if invalid_reasons or strong_failure:
        decision = "rejected"
        reasons = tuple(invalid_reasons or [f"{signal.name}_failed" for signal in signals if signal.passed is False])
    elif all_strong_pass:
        decision = "approved"
        reasons = ()
    else:
        decision = "pending_review"
        reasons = tuple(
            [
                signal.error or f"{signal.name}_requires_strong_provider"
                for signal in signals
                if signal.assurance not in strong_assurance or signal.passed is not True
            ]
        )
    return ConsentVerificationReport(decision, reasons, face, liveness, challenge, evidence)
