"""Personal performance-video analysis for Digital Twin motion packages.

The analyzer intentionally separates feature extraction from aggregation. GPU
workers can use ``face-alignment`` for 68-point landmarks while tests and future
providers can inject the same landmark interface. When landmarks are not
available, the analyzer returns an explicit limited report instead of silently
claiming behavioral coverage.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
import os
from pathlib import Path
from statistics import median
from typing import Any

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional worker dependency
    cv2 = None

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional worker dependency
    np = None


MOTION_STYLE_VERSION = "motion-style-v2"
_LANDMARK_COUNT = 68
_LEFT_EYE = (36, 37, 38, 39, 40, 41)
_RIGHT_EYE = (42, 43, 44, 45, 46, 47)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _round(value: Any, digits: int = 5) -> float:
    return round(_finite(value), digits)


def _percentile(values: Sequence[float], fraction: float, default: float = 0.0) -> float:
    finite_values: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            finite_values.append(number)
    ordered = sorted(finite_values)
    if not ordered:
        return float(default)
    position = _clamp(fraction) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _eye_aspect_ratio(points: Any, indexes: Sequence[int]) -> float:
    horizontal = max(_distance(points[indexes[0]], points[indexes[3]]), 1e-6)
    vertical = _distance(points[indexes[1]], points[indexes[5]]) + _distance(points[indexes[2]], points[indexes[4]])
    return vertical / (2.0 * horizontal)


def _landmark_features(points: Any, gray: Any, frame_width: int, frame_height: int) -> dict[str, float]:
    face_width = max(_distance(points[0], points[16]), 1e-6)
    face_height = max(_distance(points[8], points[27]), 1e-6)
    left_ear = _eye_aspect_ratio(points, _LEFT_EYE)
    right_ear = _eye_aspect_ratio(points, _RIGHT_EYE)
    inner_mouth_width = max(_distance(points[60], points[64]), 1e-6)
    mouth_open = (
        _distance(points[61], points[67])
        + _distance(points[62], points[66])
        + _distance(points[63], points[65])
    ) / (3.0 * inner_mouth_width)
    smile_width = _distance(points[48], points[54]) / face_width
    left_brow = sum(_distance(points[index], points[37 + min(index - 17, 3)]) for index in range(17, 21)) / 4.0
    right_brow = sum(_distance(points[index], points[43 + min(index - 22, 3)]) for index in range(22, 26)) / 4.0
    brow_raise = ((left_brow + right_brow) / 2.0) / face_height
    gaze_x, gaze_y = _gaze_proxy(gray, points)
    pitch, yaw, roll = _head_pose(points, frame_width=frame_width, frame_height=frame_height)
    return {
        "yaw": yaw,
        "pitch": pitch,
        "roll": roll,
        "eye_aspect_ratio": (left_ear + right_ear) / 2.0,
        "mouth_open": mouth_open,
        "smile_width": smile_width,
        "brow_raise": brow_raise,
        "gaze_x": gaze_x,
        "gaze_y": gaze_y,
    }


def _head_pose(points: Any, *, frame_width: int, frame_height: int) -> tuple[float, float, float]:
    if cv2 is None or np is None:
        return 0.0, 0.0, 0.0
    image_points = np.asarray(
        [points[30], points[8], points[36], points[45], points[48], points[54]],
        dtype=np.float64,
    )
    model_points = np.asarray(
        [
            (0.0, 0.0, 0.0),
            (0.0, -330.0, -65.0),
            (-225.0, 170.0, -135.0),
            (225.0, 170.0, -135.0),
            (-150.0, -150.0, -125.0),
            (150.0, -150.0, -125.0),
        ],
        dtype=np.float64,
    )
    focal_length = float(max(frame_width, frame_height, 1))
    camera_matrix = np.asarray(
        [
            (focal_length, 0.0, frame_width / 2.0),
            (0.0, focal_length, frame_height / 2.0),
            (0.0, 0.0, 1.0),
        ],
        dtype=np.float64,
    )
    try:
        solved, rotation_vector, _translation = cv2.solvePnP(
            model_points,
            image_points,
            camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not solved:
            return 0.0, 0.0, 0.0
        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        angles = cv2.RQDecomp3x3(rotation_matrix)[0]
        pitch, yaw, roll = (_finite(value) for value in angles[:3])
        return (
            _clamp(pitch, -90.0, 90.0),
            _clamp(yaw, -90.0, 90.0),
            _clamp(roll, -90.0, 90.0),
        )
    except Exception:
        return 0.0, 0.0, 0.0


def _gaze_proxy(gray: Any, points: Any) -> tuple[float, float]:
    """Estimate iris direction from the darkest pixels inside both eye polygons."""

    if cv2 is None or np is None or gray is None:
        return 0.0, 0.0
    estimates: list[tuple[float, float]] = []
    for indexes in (_LEFT_EYE, _RIGHT_EYE):
        polygon = np.asarray([points[index] for index in indexes], dtype=np.int32)
        x, y, width, height = cv2.boundingRect(polygon)
        if width < 4 or height < 3:
            continue
        x = max(int(x), 0)
        y = max(int(y), 0)
        eye = gray[y:y + height, x:x + width]
        if eye.size == 0:
            continue
        local_polygon = polygon - np.asarray((x, y), dtype=np.int32)
        mask = np.zeros(eye.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [local_polygon], 255)
        pixels = eye[mask > 0]
        if pixels.size < 6:
            continue
        threshold = float(np.percentile(pixels, 28.0))
        dark = (eye <= threshold) & (mask > 0)
        rows, columns = np.nonzero(dark)
        if len(columns) < 3:
            continue
        estimates.append(
            (
                _clamp((float(columns.mean()) / max(width - 1, 1) - 0.5) * 2.0, -1.0, 1.0),
                _clamp((float(rows.mean()) / max(height - 1, 1) - 0.5) * 2.0, -1.0, 1.0),
            )
        )
    if not estimates:
        return 0.0, 0.0
    return (
        sum(value[0] for value in estimates) / len(estimates),
        sum(value[1] for value in estimates) / len(estimates),
    )


def _normalize_landmarks(raw: Any) -> Any | None:
    if np is None or raw is None:
        return None
    candidate = raw
    if isinstance(candidate, (list, tuple)) and candidate:
        first = np.asarray(candidate[0])
        if first.ndim == 2:
            candidate = first
    points = np.asarray(candidate, dtype=np.float64)
    if points.ndim == 3 and points.shape[0] > 0:
        points = points[0]
    if points.ndim != 2 or points.shape[0] < _LANDMARK_COUNT or points.shape[1] < 2:
        return None
    return points[:_LANDMARK_COUNT, :2]


def _face_alignment_provider(environ: Mapping[str, str]) -> tuple[Callable[[Any], Any] | None, dict[str, Any]]:
    provider_name = str(environ.get("DIGITAL_TWIN_MOTION_LANDMARK_PROVIDER") or "face_alignment").strip().lower()
    if provider_name in {"", "none", "disabled", "off"}:
        return None, {"provider": "disabled", "available": False, "error": "landmark_provider_disabled"}
    if provider_name != "face_alignment":
        return None, {"provider": provider_name, "available": False, "error": "unsupported_landmark_provider"}
    try:
        import face_alignment  # type: ignore

        landmarks_type = getattr(face_alignment.LandmarksType, "TWO_D", None)
        if landmarks_type is None:
            landmarks_type = getattr(face_alignment.LandmarksType, "_2D")
        requested_device = str(environ.get("DIGITAL_TWIN_MOTION_LANDMARK_DEVICE") or "auto").strip().lower()
        if requested_device == "auto":
            try:
                import torch  # type: ignore

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        else:
            device = requested_device
        model = face_alignment.FaceAlignment(landmarks_type, flip_input=False, device=device)

        def detect(frame: Any) -> Any:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if cv2 is not None else frame
            predictions = model.get_landmarks_from_image(rgb_frame)
            return predictions[0] if predictions else None

        return detect, {"provider": "face_alignment", "available": True, "device": device, "version": "1.4.1"}
    except Exception as exc:
        return None, {
            "provider": "face_alignment",
            "available": False,
            "error": str(exc or "landmark_provider_unavailable")[:500],
        }


def _detect_largest_face(detector: Any, gray: Any) -> tuple[int, int, int, int] | None:
    if detector is None:
        return None
    try:
        faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(64, 64))
    except Exception:
        return None
    if len(faces) == 0:
        return None
    return tuple(int(value) for value in max(faces, key=lambda item: int(item[2]) * int(item[3])))


def _change_events(timeline: Sequence[dict[str, Any]], key: str, threshold: float) -> list[float]:
    events: list[float] = []
    active = False
    for item in timeline:
        current = abs(_finite(item.get(key))) >= threshold
        if current and not active:
            timestamp = _finite(item.get("timestamp"))
            if not events or timestamp - events[-1] >= 0.5:
                events.append(timestamp)
        active = current
    return events


def _interval_candidates(timeline: Sequence[dict[str, Any]], duration_seconds: float) -> dict[str, list[dict[str, Any]]]:
    if not timeline or duration_seconds <= 0.0:
        return {"calm": [], "natural": [], "expressive": []}
    window_seconds = max(_finite(os.environ.get("DIGITAL_TWIN_MOTION_WINDOW_SECONDS"), 8.0), 3.0)
    stride_seconds = max(_finite(os.environ.get("DIGITAL_TWIN_MOTION_WINDOW_STRIDE_SECONDS"), window_seconds / 2.0), 1.0)
    candidates: list[dict[str, Any]] = []
    start = 0.0
    while start < duration_seconds:
        end = min(start + window_seconds, duration_seconds)
        samples = [item for item in timeline if start <= _finite(item.get("timestamp")) < end]
        if len(samples) >= 3:
            face_coverage = sum(bool(item.get("face_present")) for item in samples) / len(samples)
            landmark_coverage = sum(bool(item.get("landmark_present")) for item in samples) / len(samples)
            if face_coverage >= 0.60:
                motion = sum(_finite(item.get("motion_intensity")) for item in samples) / len(samples)
                expression = sum(_finite(item.get("expression_intensity")) for item in samples) / len(samples)
                head = sum(_finite(item.get("head_activity")) for item in samples) / len(samples)
                score = _clamp(motion * 0.45 + expression * 0.30 + head * 0.25)
                candidates.append(
                    {
                        "start_seconds": _round(start, 3),
                        "end_seconds": _round(end, 3),
                        "duration_seconds": _round(end - start, 3),
                        "score": _round(score),
                        "motion_intensity": _round(motion),
                        "expression_intensity": _round(expression),
                        "head_activity": _round(head),
                        "face_coverage": _round(face_coverage),
                        "landmark_coverage": _round(landmark_coverage),
                    }
                )
        if end >= duration_seconds:
            break
        start += stride_seconds
    if not candidates:
        return {"calm": [], "natural": [], "expressive": []}

    ordered = sorted(candidates, key=lambda item: (_finite(item["score"]), _finite(item["start_seconds"])))
    target = _percentile([_finite(item["score"]) for item in ordered], 0.50)
    calm = sorted(ordered, key=lambda item: (_finite(item["score"]), -_finite(item["landmark_coverage"])))[:3]
    natural = sorted(
        ordered,
        key=lambda item: (abs(_finite(item["score"]) - target), -_finite(item["landmark_coverage"])),
    )[:3]
    expressive = sorted(
        ordered,
        key=lambda item: (-_finite(item["score"]), -_finite(item["landmark_coverage"])),
    )[:3]
    return {"calm": calm, "natural": natural, "expressive": expressive}


def build_motion_style_profile(
    observations: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    source_fps: float,
    analyzer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate sampled observations into a versioned behavioral profile."""

    timeline = [dict(item) for item in observations]
    sampled = len(timeline)
    face_samples = [item for item in timeline if bool(item.get("face_present"))]
    landmark_samples = [item for item in timeline if bool(item.get("landmark_present"))]
    face_coverage = len(face_samples) / max(sampled, 1)
    landmark_coverage = len(landmark_samples) / max(sampled, 1)

    eye_values = [_finite(item.get("eye_aspect_ratio")) for item in landmark_samples]
    mouth_values = [_finite(item.get("mouth_open")) for item in landmark_samples]
    smile_values = [_finite(item.get("smile_width")) for item in landmark_samples]
    brow_values = [_finite(item.get("brow_raise")) for item in landmark_samples]
    neutral_eye = median(eye_values) if eye_values else 0.0
    neutral_mouth = median(mouth_values) if mouth_values else 0.0
    neutral_smile = median(smile_values) if smile_values else 0.0
    neutral_brow = median(brow_values) if brow_values else 0.0
    blink_threshold = max(0.10, neutral_eye * 0.72) if neutral_eye > 0.0 else 0.0

    previous: dict[str, Any] | None = None
    blink_events: list[float] = []
    blink_active = False
    for item in timeline:
        if bool(item.get("landmark_present")):
            mouth_delta = abs(_finite(item.get("mouth_open")) - neutral_mouth) / 0.18
            smile_delta = abs(_finite(item.get("smile_width")) - neutral_smile) / 0.10
            brow_delta = abs(_finite(item.get("brow_raise")) - neutral_brow) / 0.08
            item["expression_intensity"] = _round(_clamp(mouth_delta * 0.45 + smile_delta * 0.35 + brow_delta * 0.20))
            if previous and bool(previous.get("landmark_present")):
                head_delta = (
                    abs(_finite(item.get("yaw")) - _finite(previous.get("yaw")))
                    + abs(_finite(item.get("pitch")) - _finite(previous.get("pitch")))
                    + abs(_finite(item.get("roll")) - _finite(previous.get("roll")))
                ) / 36.0
                gaze_delta = (
                    abs(_finite(item.get("gaze_x")) - _finite(previous.get("gaze_x")))
                    + abs(_finite(item.get("gaze_y")) - _finite(previous.get("gaze_y")))
                ) / 2.0
            else:
                head_delta = gaze_delta = 0.0
            item["head_activity"] = _round(_clamp(head_delta))
            item["gaze_activity"] = _round(_clamp(gaze_delta))
            closed = bool(blink_threshold > 0.0 and _finite(item.get("eye_aspect_ratio")) <= blink_threshold)
            if closed and not blink_active:
                timestamp = _finite(item.get("timestamp"))
                if not blink_events or timestamp - blink_events[-1] >= 0.35:
                    blink_events.append(timestamp)
            blink_active = closed
        else:
            item["expression_intensity"] = 0.0
            item["head_activity"] = 0.0
            item["gaze_activity"] = 0.0
            blink_active = False
        item["motion_intensity"] = _round(
            _clamp(
                _finite(item.get("frame_motion")) * 2.5 * 0.35
                + _finite(item.get("head_activity")) * 0.30
                + _finite(item.get("gaze_activity")) * 0.10
                + _finite(item.get("expression_intensity")) * 0.25
            )
        )
        for key in (
            "timestamp",
            "face_center_x",
            "face_center_y",
            "face_scale",
            "frame_motion",
            "yaw",
            "pitch",
            "roll",
            "eye_aspect_ratio",
            "mouth_open",
            "smile_width",
            "brow_raise",
            "gaze_x",
            "gaze_y",
        ):
            if key in item:
                item[key] = _round(item[key])
        previous = item

    yaw_values = [_finite(item.get("yaw")) for item in landmark_samples]
    pitch_values = [_finite(item.get("pitch")) for item in landmark_samples]
    roll_values = [_finite(item.get("roll")) for item in landmark_samples]
    gaze_x_values = [_finite(item.get("gaze_x")) for item in landmark_samples]
    gaze_y_values = [_finite(item.get("gaze_y")) for item in landmark_samples]
    expression_values = [_finite(item.get("expression_intensity")) for item in landmark_samples]
    motion_values = [_finite(item.get("motion_intensity")) for item in timeline]
    camera_contact = [
        item
        for item in landmark_samples
        if abs(_finite(item.get("yaw"))) <= 15.0
        and abs(_finite(item.get("gaze_x"))) <= 0.38
        and abs(_finite(item.get("gaze_y"))) <= 0.45
    ]
    gaze_events = _change_events(timeline, "gaze_x", 0.42)
    blink_intervals = [blink_events[index] - blink_events[index - 1] for index in range(1, len(blink_events))]

    warnings: list[str] = []
    if duration_seconds < 20.0:
        warnings.append("performance_video_shorter_than_recommended")
    if face_coverage < 0.75:
        warnings.append("face_presence_coverage_low")
    if landmark_coverage < 0.65:
        warnings.append("landmark_coverage_low")
    if not landmark_samples:
        warnings.append("behavioral_signals_unavailable")
    elif _percentile(yaw_values, 0.90) - _percentile(yaw_values, 0.10) < 8.0:
        warnings.append("head_pose_coverage_low")
    if expression_values and _percentile(expression_values, 0.90) < 0.20:
        warnings.append("expression_coverage_low")

    accepted = bool(sampled >= 3 and duration_seconds >= 3.0 and face_coverage >= 0.45)
    planning_ready = bool(accepted and landmark_coverage >= 0.50)
    status = "ready" if planning_ready and not {"face_presence_coverage_low", "landmark_coverage_low"}.intersection(warnings) else "limited"
    if not accepted:
        status = "failed"
    selected_intervals = _interval_candidates(timeline, float(duration_seconds))

    return {
        "version": MOTION_STYLE_VERSION,
        "status": status,
        "accepted": accepted,
        "usable_for_motion_planning": planning_ready,
        "duration_seconds": _round(duration_seconds, 3),
        "source_fps": _round(source_fps, 3),
        "frames_sampled": sampled,
        "analyzer": dict(analyzer or {}),
        "coverage": {
            "face_presence": _round(face_coverage),
            "landmarks": _round(landmark_coverage),
            "head_pose": _round(landmark_coverage),
            "gaze": _round(landmark_coverage),
            "expression": _round(landmark_coverage),
        },
        "head_pose": {
            "yaw_mean": _round(sum(yaw_values) / max(len(yaw_values), 1)),
            "pitch_mean": _round(sum(pitch_values) / max(len(pitch_values), 1)),
            "roll_mean": _round(sum(roll_values) / max(len(roll_values), 1)),
            "yaw_range": [_round(_percentile(yaw_values, 0.10), 3), _round(_percentile(yaw_values, 0.90), 3)],
            "pitch_range": [_round(_percentile(pitch_values, 0.10), 3), _round(_percentile(pitch_values, 0.90), 3)],
            "roll_range": [_round(_percentile(roll_values, 0.10), 3), _round(_percentile(roll_values, 0.90), 3)],
        },
        "gaze": {
            "camera_contact_ratio": _round(len(camera_contact) / max(len(landmark_samples), 1)),
            "horizontal_mean": _round(sum(gaze_x_values) / max(len(gaze_x_values), 1)),
            "vertical_mean": _round(sum(gaze_y_values) / max(len(gaze_y_values), 1)),
            "side_glance_events": [_round(value, 3) for value in gaze_events],
        },
        "blink": {
            "count": len(blink_events),
            "events_seconds": [_round(value, 3) for value in blink_events],
            "per_minute": _round(len(blink_events) * 60.0 / max(duration_seconds, 1.0), 3),
            "median_interval_seconds": _round(median(blink_intervals), 3) if blink_intervals else 0.0,
            "eye_aspect_ratio_median": _round(neutral_eye),
            "closed_threshold": _round(blink_threshold),
        },
        "expression": {
            "mean_intensity": _round(sum(expression_values) / max(len(expression_values), 1)),
            "p90_intensity": _round(_percentile(expression_values, 0.90)),
            "mouth_open_median": _round(neutral_mouth),
            "smile_width_median": _round(neutral_smile),
            "brow_raise_median": _round(neutral_brow),
        },
        "motion": {
            "mean_intensity": _round(sum(motion_values) / max(len(motion_values), 1)),
            "p90_intensity": _round(_percentile(motion_values, 0.90)),
        },
        "selected_intervals": selected_intervals,
        "warnings": list(dict.fromkeys(warnings)),
        "timeline": timeline,
    }


def analyze_performance_motion(
    video_path: str | Path,
    *,
    landmark_provider: Callable[[Any], Any] | None = None,
    max_samples: int = 240,
    sample_hz: float = 5.0,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Analyze one verified performance recording into a Motion Style V2 manifest."""

    if cv2 is None or np is None:
        raise RuntimeError("motion_analysis_opencv_unavailable")
    path = Path(video_path)
    if not path.exists() or not path.is_file():
        raise RuntimeError("motion_analysis_video_missing")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("motion_analysis_video_unreadable")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    duration = frame_count / fps if frame_count > 0 and fps > 0.0 else 0.0
    target_hz = max(float(sample_hz or 0.0), 0.5)
    stride_for_hz = max(int(round(fps / target_hz)), 1) if fps > 0.0 else 5
    stride_for_cap = max(frame_count // max(int(max_samples), 1), 1) if frame_count > 0 else 1
    stride = max(stride_for_hz, stride_for_cap)
    env = environ if environ is not None else os.environ

    analyzer_info: dict[str, Any]
    provider = landmark_provider
    if provider is None:
        provider, analyzer_info = _face_alignment_provider(env)
    else:
        analyzer_info = {"provider": "injected", "available": True}
    detector = None
    try:
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception:
        detector = None

    observations: list[dict[str, Any]] = []
    previous_small = None
    frame_index = 0
    while len(observations) < max(int(max_samples), 1):
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        if frame_index % stride:
            frame_index += 1
            continue
        timestamp = frame_index / fps if fps > 0.0 else len(observations) / target_hz
        frame_index += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))
        frame_motion = 0.0 if previous_small is None else float(cv2.absdiff(previous_small, small).mean()) / 255.0
        previous_small = small
        height, width = gray.shape[:2]

        points = None
        if provider is not None:
            try:
                points = _normalize_landmarks(provider(frame))
            except Exception as exc:
                analyzer_info = {
                    **analyzer_info,
                    "available": False,
                    "error": str(exc or "landmark_inference_failed")[:500],
                }
                provider = None
        face = None
        if points is not None:
            min_x, min_y = np.min(points, axis=0)
            max_x, max_y = np.max(points, axis=0)
            face = (int(min_x), int(min_y), max(int(max_x - min_x), 1), max(int(max_y - min_y), 1))
        else:
            face = _detect_largest_face(detector, gray)
        observation: dict[str, Any] = {
            "timestamp": timestamp,
            "face_present": face is not None,
            "landmark_present": points is not None,
            "frame_motion": frame_motion,
        }
        if face is not None:
            x, y, face_width, face_height = face
            observation.update(
                {
                    "face_center_x": (x + face_width / 2.0) / max(width, 1),
                    "face_center_y": (y + face_height / 2.0) / max(height, 1),
                    "face_scale": (face_width * face_height) / max(width * height, 1),
                }
            )
        if points is not None:
            observation.update(_landmark_features(points, gray, width, height))
        observations.append(observation)
    capture.release()

    if duration <= 0.0 and observations:
        duration = _finite(observations[-1].get("timestamp")) + (1.0 / target_hz)
    report = build_motion_style_profile(
        observations,
        duration_seconds=duration,
        source_fps=fps,
        analyzer={
            **analyzer_info,
            "sample_hz": _round(target_hz, 3),
            "sample_stride_frames": stride,
            "max_samples": int(max_samples),
        },
    )
    if not bool(analyzer_info.get("available")):
        report["warnings"] = list(
            dict.fromkeys([*list(report.get("warnings") or []), str(analyzer_info.get("error") or "landmark_provider_unavailable")])
        )
    return report
