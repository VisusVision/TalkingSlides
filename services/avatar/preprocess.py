from __future__ import annotations

import json
import io
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

from .hashing import sha256_bytes, sha256_file


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
MIN_FACE_DIMENSION = 256
MIN_FACE_AREA_RATIO = 0.06
MAX_FACE_AREA_RATIO = 0.62
MIN_FACE_BLUR_SCORE = 70.0


def _preview_reference_size() -> int:
    raw = str(os.environ.get("AVATAR_PREVIEW_REFERENCE_SIZE", "768")).strip()
    try:
        parsed = int(raw)
    except Exception:
        parsed = 768
    return max(768, min(1024, parsed))


@dataclass
class AvatarPreprocessResult:
    source_hash: str
    original_rel_path: str
    processed_rel_path: str
    identity_package_rel_path: str
    references_rel_paths: list[str]
    warnings: list[str]


class AvatarValidationError(ValueError):
    pass


def _opencv_blur_score(gray_image) -> float:
    if cv2 is None:
        return 0.0
    lap = cv2.Laplacian(gray_image, cv2.CV_64F)
    return float(lap.var())


def _extract_reference_frame_from_video(video_path: Path) -> tuple[bytes, dict[str, Any]]:
    if cv2 is None:
        raise AvatarValidationError("OpenCV is required for avatar video preprocessing.")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise AvatarValidationError("Unable to open avatar video. Please upload a valid portrait video.")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if frame_count > 0:
        sample_count = min(14, max(6, frame_count // 24))
        candidate_indices = [
            max(0, int(frame_count * (0.1 + (0.8 * i / max(sample_count - 1, 1)))))
            for i in range(sample_count)
        ]
    else:
        candidate_indices = [0, 10, 20, 30, 45, 60]

    best = None
    best_face = None
    best_score = -1.0
    best_meta: dict[str, Any] = {}
    accepted_frames = 0
    rejected_frames = 0

    frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")

    for idx in candidate_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            rejected_frames += 1
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = frontal.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(120, 120))
        if faces is None or len(faces) == 0:
            rejected_frames += 1
            continue

        strongest = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        x, y, w, h = [int(v) for v in strongest]
        frame_h, frame_w = gray.shape[:2]
        area_ratio = (w * h) / float(max(frame_w * frame_h, 1))
        cx = (x + (w / 2.0)) / float(max(frame_w, 1))
        face_aspect = w / float(max(h, 1))
        face_crop = gray[max(0, y):min(frame_h, y + h), max(0, x):min(frame_w, x + w)]
        blur_score = _opencv_blur_score(face_crop if face_crop.size else gray)
        profile_hits = profile.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(120, 120)) if profile is not None else []
        profile_penalty = 0.35 if profile_hits is not None and len(profile_hits) > 0 else 0.0

        if area_ratio < MIN_FACE_AREA_RATIO:
            rejected_frames += 1
            continue
        if blur_score < MIN_FACE_BLUR_SCORE:
            rejected_frames += 1
            continue
        if cx < 0.18 or cx > 0.82:
            rejected_frames += 1
            continue
        if face_aspect < 0.66 or face_aspect > 1.42:
            rejected_frames += 1
            continue

        accepted_frames += 1
        score = (area_ratio * 140.0) + min(blur_score / 25.0, 12.0) - profile_penalty
        if score > best_score:
            best_score = score
            best = frame
            best_face = (x, y, x + w, y + h)
            best_meta = {
                "frame_index": int(idx),
                "face_area_ratio": round(area_ratio, 4),
                "blur_score": round(float(blur_score), 3),
                "center_ratio_x": round(float(cx), 4),
                "face_aspect": round(float(face_aspect), 4),
                "profile_hits": int(len(profile_hits) if profile_hits is not None else 0),
            }

    cap.release()

    if best is None:
        raise AvatarValidationError(
            "No clear front-facing frame was found in the video. "
            "Please upload a steady front-camera portrait video with the full head visible."
        )

    if accepted_frames <= 0:
        raise AvatarValidationError(
            "Avatar video quality is too low for a stable identity frame. "
            "Use a clearer, better-lit front-facing clip."
        )

    ok, encoded = cv2.imencode(".png", best)
    if not ok:
        raise AvatarValidationError("Failed to extract reference frame from avatar video.")
    return bytes(encoded.tobytes()), {
        **best_meta,
        "accepted_frames": int(accepted_frames),
        "rejected_frames": int(rejected_frames),
        "detected_face_bbox": list(best_face) if best_face else None,
    }


def _variance_score(image: Image.Image) -> float:
    grayscale = image.convert("L").filter(ImageFilter.FIND_EDGES)
    hist = grayscale.histogram()
    total = sum(hist)
    if total == 0:
        return 0.0
    mean = sum(i * v for i, v in enumerate(hist)) / total
    return sum(((i - mean) ** 2) * v for i, v in enumerate(hist)) / total


def _face_like_crop_box(image: Image.Image) -> tuple[int, int, int, int]:
    width, height = image.size
    side = min(width, height)
    left = int((width - side) / 2)
    top = int((height - side) / 2)
    return (left, top, left + side, top + side)


def _detect_face_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    if cv2 is None:
        return None
    import numpy as np

    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(120, 120))
    if faces is None or len(faces) == 0:
        return None
    strongest = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    x, y, w, h = [int(v) for v in strongest]
    return (x, y, x + w, y + h)


def _expanded_head_safe_crop(
    image: Image.Image,
    face_bbox: tuple[int, int, int, int] | None,
    *,
    top_expand: float = 1.20,
    bottom_expand: float = 1.00,
    side_expand: float = 0.85,
    frame_scale: float = 1.22,
) -> tuple[int, int, int, int]:
    width, height = image.size
    if face_bbox is None:
        return _face_like_crop_box(image)

    x0, y0, x1, y1 = face_bbox
    fw = max(x1 - x0, 1)
    fh = max(y1 - y0, 1)

    # Expand upward and around the jaw so the full head stays visible.
    top = y0 - int(top_expand * fh)
    bottom = y1 + int(bottom_expand * fh)
    left = x0 - int(side_expand * fw)
    right = x1 + int(side_expand * fw)

    crop_w = right - left
    crop_h = bottom - top
    side = int(max(crop_w, crop_h) * frame_scale)
    cx = (left + right) // 2
    cy = (top + bottom) // 2

    left = cx - side // 2
    top = cy - side // 2
    right = left + side
    bottom = top + side

    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > width:
        shift = right - width
        left = max(0, left - shift)
        right = width
    if bottom > height:
        shift = bottom - height
        top = max(0, top - shift)
        bottom = height

    return (int(left), int(top), int(right), int(bottom))


def _face_crop_safety_metrics(
    image: Image.Image,
    face_bbox: tuple[int, int, int, int] | None,
    crop_box: tuple[int, int, int, int],
) -> dict[str, float]:
    if face_bbox is None:
        return {
            "face_area_ratio_in_crop": 0.0,
            "top_margin_ratio": 0.0,
            "bottom_margin_ratio": 0.0,
            "left_margin_ratio": 0.0,
            "right_margin_ratio": 0.0,
            "center_offset_x_ratio": 0.0,
            "center_offset_y_ratio": 0.0,
        }

    x0, y0, x1, y1 = face_bbox
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    fw = max(x1 - x0, 1)
    fh = max(y1 - y0, 1)
    crop_w = max(crop_right - crop_left, 1)
    crop_h = max(crop_bottom - crop_top, 1)
    face_cx = (x0 + x1) / 2.0
    face_cy = (y0 + y1) / 2.0
    mouth_y = y0 + (0.78 * fh)
    crop_cx = (crop_left + crop_right) / 2.0
    crop_cy = (crop_top + crop_bottom) / 2.0
    mouth_position_ratio = (mouth_y - crop_top) / float(max(crop_h, 1))

    return {
        "face_area_ratio_in_crop": round((fw * fh) / float(max(crop_w * crop_h, 1)), 6),
        "top_margin_ratio": round((y0 - crop_top) / float(fh), 6),
        "bottom_margin_ratio": round((crop_bottom - y1) / float(fh), 6),
        "left_margin_ratio": round((x0 - crop_left) / float(fw), 6),
        "right_margin_ratio": round((crop_right - x1) / float(fw), 6),
        "center_offset_x_ratio": round(abs(face_cx - crop_cx) / float(max(crop_w, 1)), 6),
        "center_offset_y_ratio": round(abs(face_cy - crop_cy) / float(max(crop_h, 1)), 6),
        "mouth_position_ratio": round(float(mouth_position_ratio), 6),
    }


def _estimate_landmark_stability(image: Image.Image) -> dict[str, float]:
    """
    Estimate face landmark stability from a single image by running face detection
    under small perturbations. Lower jitter means more stable geometry for
    LivePortrait driving.
    """
    if cv2 is None:
        return {
            "landmark_jitter_estimate": 0.12,
            "landmark_detection_success_ratio": 0.0,
        }

    import numpy as np

    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape[:2]
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    variants: list[Any] = [gray]
    variants.append(cv2.convertScaleAbs(gray, alpha=1.05, beta=6))
    variants.append(cv2.convertScaleAbs(gray, alpha=0.95, beta=-4))
    variants.append(cv2.GaussianBlur(gray, (3, 3), 0.7))

    for tx, ty in [(2, 0), (-2, 0), (0, 2), (0, -2)]:
        m = np.float32([[1, 0, tx], [0, 1, ty]])
        shifted = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        variants.append(shifted)

    centers: list[tuple[float, float]] = []
    scales: list[float] = []
    success = 0
    for var in variants:
        faces = cascade.detectMultiScale(var, scaleFactor=1.1, minNeighbors=5, minSize=(120, 120))
        if faces is None or len(faces) == 0:
            continue
        x, y, fw, fh = [int(v) for v in max(faces, key=lambda f: int(f[2]) * int(f[3]))]
        success += 1
        centers.append(((x + (fw / 2.0)) / float(max(w, 1)), (y + (fh / 2.0)) / float(max(h, 1))))
        scales.append((fw * fh) / float(max(w * h, 1)))

    success_ratio = float(success) / float(max(len(variants), 1))
    if success < 2:
        # Treat near-undetectable faces as unstable for LP preflight ranking.
        return {
            "landmark_jitter_estimate": 0.18,
            "landmark_detection_success_ratio": round(success_ratio, 6),
        }

    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    x_std = float(np.std(xs))
    y_std = float(np.std(ys))
    s_std = float(np.std(scales))
    miss_penalty = max(0.0, 1.0 - success_ratio) * 0.05
    jitter = (x_std + y_std + (s_std * 1.6) + miss_penalty)
    return {
        "landmark_jitter_estimate": round(float(jitter), 6),
        "landmark_detection_success_ratio": round(success_ratio, 6),
    }


def _preview_preflight_assessment(
    metrics: dict[str, float],
    *,
    max_face_area_ratio: float,
    min_top_margin_ratio: float,
    min_bottom_margin_ratio: float,
    min_side_margin_ratio: float,
    max_center_offset_x_ratio: float,
    max_center_offset_y_ratio: float,
    min_mouth_position_ratio: float,
    max_mouth_position_ratio: float,
) -> tuple[bool, str, float]:
    if metrics.get("face_area_ratio_in_crop", 0.0) > max_face_area_ratio:
        return False, "Preview crop is over-zoomed. Re-upload with less torso and more full-head room.", 0.0
    if metrics.get("top_margin_ratio", 0.0) < min_top_margin_ratio:
        return False, "Preview forehead area is too close to the crop edge. Re-upload with full head visible.", 0.0
    if metrics.get("bottom_margin_ratio", 0.0) < min_bottom_margin_ratio:
        return False, "Preview jaw/chin area is too close to the crop edge. Re-upload with more space below the chin.", 0.0
    if metrics.get("left_margin_ratio", 0.0) < min_side_margin_ratio or metrics.get("right_margin_ratio", 0.0) < min_side_margin_ratio:
        return False, "Preview face is too close to the horizontal crop edge. Re-upload with a centered portrait.", 0.0
    if metrics.get("center_offset_x_ratio", 1.0) > max_center_offset_x_ratio or metrics.get("center_offset_y_ratio", 1.0) > max_center_offset_y_ratio:
        return False, "Preview face is off-center after crop stabilization. Re-upload a centered front-facing portrait.", 0.0
    mouth_position = float(metrics.get("mouth_position_ratio", 0.0))
    if mouth_position < min_mouth_position_ratio or mouth_position > max_mouth_position_ratio:
        return False, "Preview mouth is too close to crop edges. Re-upload with full headroom and the face centered.", 0.0

    # Higher score means safer headroom and centering while avoiding over-zoom.
    face_area = float(metrics.get("face_area_ratio_in_crop", 0.0))
    target_face_area = max_face_area_ratio * 0.84
    face_area_component = max(0.0, 1.0 - abs(face_area - target_face_area) / max(target_face_area, 1e-6))
    score = (
        (min(float(metrics.get("top_margin_ratio", 0.0)), 1.5) * 2.7)
        + (min(float(metrics.get("bottom_margin_ratio", 0.0)), 1.2) * 2.0)
        + (min(float(metrics.get("left_margin_ratio", 0.0)), 1.0) * 0.9)
        + (min(float(metrics.get("right_margin_ratio", 0.0)), 1.0) * 0.9)
        + (face_area_component * 2.0)
        + (max(0.0, 1.0 - (float(metrics.get("center_offset_x_ratio", 0.0)) / max(max_center_offset_x_ratio, 1e-6))) * 1.0)
        + (max(0.0, 1.0 - (float(metrics.get("center_offset_y_ratio", 0.0)) / max(max_center_offset_y_ratio, 1e-6))) * 1.0)
        + (max(0.0, 1.0 - (abs(mouth_position - 0.64) / 0.20)) * 2.2)
    )
    return True, "", round(float(score), 6)


def build_preview_reference_image(*, original_image_path: str, output_path: str) -> dict[str, Any]:
    source_path = Path(original_image_path)
    if not source_path.exists():
        raise AvatarValidationError(f"Original avatar image not found: {source_path}")

    image = Image.open(source_path).convert("RGB")
    width, height = image.size
    if min(width, height) < MIN_FACE_DIMENSION:
        raise AvatarValidationError(
            f"Preview portrait is too small. Minimum dimension is {MIN_FACE_DIMENSION}px."
        )

    blur_score = _variance_score(image)
    if blur_score < 120.0:
        raise AvatarValidationError("Preview portrait is too blurry. Please upload a sharper front-facing photo.")

    detected_face = _detect_face_bbox(image)
    if cv2 is not None and detected_face is None:
        raise AvatarValidationError("Preview face was not detected. Please use a clear front-facing portrait.")

    crop_candidates = [
        {"top_expand": 1.50, "bottom_expand": 1.10, "side_expand": 0.80, "frame_scale": 1.20},
        {"top_expand": 1.70, "bottom_expand": 1.22, "side_expand": 0.90, "frame_scale": 1.28},
        {"top_expand": 1.90, "bottom_expand": 1.32, "side_expand": 1.00, "frame_scale": 1.36},
        {"top_expand": 2.05, "bottom_expand": 1.45, "side_expand": 1.08, "frame_scale": 1.44},
    ]
    crop_box: tuple[int, int, int, int] | None = None
    crop_metrics: dict[str, float] = {}
    chosen_candidate: dict[str, float] | None = None
    rejection_reason = ""
    preflight_score = 0.0
    best_pass: dict[str, Any] | None = None

    for candidate in crop_candidates:
        candidate_crop = _expanded_head_safe_crop(image, detected_face, **candidate)
        candidate_metrics = _face_crop_safety_metrics(image, detected_face, candidate_crop)
        if detected_face is None:
            crop_box = candidate_crop
            crop_metrics = candidate_metrics
            chosen_candidate = candidate
            break

        candidate_ok, candidate_reason, candidate_score = _preview_preflight_assessment(
            candidate_metrics,
            max_face_area_ratio=0.54,
            min_top_margin_ratio=0.56,
            min_bottom_margin_ratio=0.36,
            min_side_margin_ratio=0.14,
            max_center_offset_x_ratio=0.09,
            max_center_offset_y_ratio=0.11,
            min_mouth_position_ratio=0.56,
            max_mouth_position_ratio=0.78,
        )
        if not candidate_ok:
            rejection_reason = candidate_reason
            continue

        if best_pass is None or candidate_score > float(best_pass.get("preflight_score") or 0.0):
            best_pass = {
                "crop_box": candidate_crop,
                "crop_metrics": candidate_metrics,
                "candidate": candidate,
                "preflight_score": candidate_score,
            }

    if best_pass is not None:
        crop_box = best_pass["crop_box"]
        crop_metrics = dict(best_pass["crop_metrics"])
        chosen_candidate = dict(best_pass["candidate"])
        preflight_score = float(best_pass["preflight_score"])

    if crop_box is None:
        raise AvatarValidationError(rejection_reason or "Preview crop could not be stabilized safely.")

    cropped = image.crop(crop_box)
    processed = ImageOps.autocontrast(cropped)
    ref_size = _preview_reference_size()
    processed = processed.resize((ref_size, ref_size), Image.Resampling.LANCZOS)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    processed.save(destination, format="PNG")
    stability = _estimate_landmark_stability(processed)
    return {
        "preview_reference_path": str(destination),
        "crop_box": list(crop_box),
        "detected_face_bbox": list(detected_face) if detected_face else None,
        "crop_metrics": crop_metrics,
        "crop_candidate": chosen_candidate or {},
        "preflight_passed": True,
        "preflight_score": preflight_score,
        "blur_score": round(blur_score, 3),
        "landmark_stability": stability,
        "reference_size": ref_size,
    }


def build_preview_video_reference_image(*, source_video_path: str, output_path: str) -> dict[str, Any]:
    video_path = Path(source_video_path)
    if not video_path.exists():
        raise AvatarValidationError(f"Preview avatar video not found: {video_path}")

    frame_png, frame_meta = _extract_reference_frame_from_video(video_path)
    image = Image.open(io.BytesIO(frame_png)).convert("RGB")
    detected_face = _detect_face_bbox(image)
    if cv2 is not None and detected_face is None:
        raise AvatarValidationError("Preview video frame is not front-facing enough for stable crop preflight.")

    crop_candidates = [
        {"top_expand": 1.62, "bottom_expand": 1.18, "side_expand": 0.88, "frame_scale": 1.24},
        {"top_expand": 1.82, "bottom_expand": 1.30, "side_expand": 0.98, "frame_scale": 1.32},
        {"top_expand": 2.00, "bottom_expand": 1.40, "side_expand": 1.06, "frame_scale": 1.40},
    ]
    crop_box: tuple[int, int, int, int] | None = None
    crop_metrics: dict[str, float] = {}
    chosen_candidate: dict[str, float] | None = None
    rejection_reason = ""
    preflight_score = 0.0
    best_pass: dict[str, Any] | None = None

    for candidate in crop_candidates:
        candidate_crop = _expanded_head_safe_crop(image, detected_face, **candidate)
        candidate_metrics = _face_crop_safety_metrics(image, detected_face, candidate_crop)
        candidate_ok, candidate_reason, candidate_score = _preview_preflight_assessment(
            candidate_metrics,
            max_face_area_ratio=0.58,
            min_top_margin_ratio=0.48,
            min_bottom_margin_ratio=0.30,
            min_side_margin_ratio=0.12,
            max_center_offset_x_ratio=0.10,
            max_center_offset_y_ratio=0.13,
            min_mouth_position_ratio=0.54,
            max_mouth_position_ratio=0.80,
        )
        if not candidate_ok:
            rejection_reason = candidate_reason
            continue
        if best_pass is None or candidate_score > float(best_pass.get("preflight_score") or 0.0):
            best_pass = {
                "crop_box": candidate_crop,
                "crop_metrics": candidate_metrics,
                "candidate": candidate,
                "preflight_score": candidate_score,
            }

    if best_pass is not None:
        crop_box = best_pass["crop_box"]
        crop_metrics = dict(best_pass["crop_metrics"])
        chosen_candidate = dict(best_pass["candidate"])
        preflight_score = float(best_pass["preflight_score"])

    if crop_box is None:
        raise AvatarValidationError(rejection_reason or "Preview video crop could not be stabilized safely.")

    processed = ImageOps.autocontrast(image.crop(crop_box))
    ref_size = _preview_reference_size()
    processed = processed.resize((ref_size, ref_size), Image.Resampling.LANCZOS)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    processed.save(destination, format="PNG")
    stability = _estimate_landmark_stability(processed)
    return {
        "preview_reference_path": str(destination),
        "crop_box": list(crop_box),
        "detected_face_bbox": list(detected_face) if detected_face else None,
        "crop_metrics": crop_metrics,
        "crop_candidate": chosen_candidate or {},
        "preflight_passed": True,
        "preflight_score": preflight_score,
        "frame_meta": frame_meta,
        "source_type": "video",
        "landmark_stability": stability,
        "reference_size": ref_size,
    }


def build_preview_focus_reference_image(*, processed_image_path: str, output_path: str) -> dict[str, Any]:
    source_path = Path(processed_image_path)
    if not source_path.exists():
        raise AvatarValidationError(f"Processed avatar image not found: {source_path}")

    image = Image.open(source_path).convert("RGB")
    detected_face = _detect_face_bbox(image)
    if cv2 is not None and detected_face is None:
        raise AvatarValidationError("Preview focus crop could not detect a front-facing face.")

    crop_candidates = [
        {"top_expand": 0.95, "bottom_expand": 0.85, "side_expand": 0.52, "frame_scale": 1.06},
        {"top_expand": 1.02, "bottom_expand": 0.90, "side_expand": 0.58, "frame_scale": 1.10},
        {"top_expand": 1.10, "bottom_expand": 0.98, "side_expand": 0.64, "frame_scale": 1.16},
    ]
    crop_box: tuple[int, int, int, int] | None = None
    crop_metrics: dict[str, float] = {}
    chosen_candidate: dict[str, float] | None = None

    preflight_score = 0.0
    best_pass: dict[str, Any] | None = None

    for candidate in crop_candidates:
        candidate_crop = _expanded_head_safe_crop(image, detected_face, **candidate)
        candidate_metrics = _face_crop_safety_metrics(image, detected_face, candidate_crop)
        if detected_face is None:
            crop_box = candidate_crop
            crop_metrics = candidate_metrics
            chosen_candidate = candidate
            break
        candidate_ok, _, candidate_score = _preview_preflight_assessment(
            candidate_metrics,
            max_face_area_ratio=0.64,
            min_top_margin_ratio=0.26,
            min_bottom_margin_ratio=0.20,
            min_side_margin_ratio=0.10,
            max_center_offset_x_ratio=0.08,
            max_center_offset_y_ratio=0.10,
            min_mouth_position_ratio=0.52,
            max_mouth_position_ratio=0.82,
        )
        if not candidate_ok:
            continue
        if best_pass is None or candidate_score > float(best_pass.get("preflight_score") or 0.0):
            best_pass = {
                "crop_box": candidate_crop,
                "crop_metrics": candidate_metrics,
                "candidate": candidate,
                "preflight_score": candidate_score,
            }

    if best_pass is not None:
        crop_box = best_pass["crop_box"]
        crop_metrics = dict(best_pass["crop_metrics"])
        chosen_candidate = dict(best_pass["candidate"])
        preflight_score = float(best_pass["preflight_score"])

    if crop_box is None:
        raise AvatarValidationError("Preview focus crop could not find a tighter safe face framing.")

    processed = ImageOps.autocontrast(image.crop(crop_box))
    ref_size = _preview_reference_size()
    processed = processed.resize((ref_size, ref_size), Image.Resampling.LANCZOS)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    processed.save(destination, format="PNG")
    stability = _estimate_landmark_stability(processed)
    return {
        "preview_reference_path": str(destination),
        "crop_box": list(crop_box),
        "detected_face_bbox": list(detected_face) if detected_face else None,
        "crop_metrics": crop_metrics,
        "crop_candidate": chosen_candidate or {},
        "preflight_passed": True,
        "preflight_score": preflight_score,
        "mode": "focus",
        "landmark_stability": stability,
        "reference_size": ref_size,
    }


def build_preview_processed_safe_reference_image(
    *,
    processed_image_path: str,
    output_path: str,
    scale: float | None = None,
    vertical_bias: float | None = None,
) -> dict[str, Any]:
    """
    Build a conservative fallback preview reference from an already-processed identity image.

    Unlike focus crops, this path intentionally adds headroom and side margins so
    LivePortrait receives a less aggressive framing when original-image preflight fails.
    """
    source_path = Path(processed_image_path)
    if not source_path.exists():
        raise AvatarValidationError(f"Processed avatar image not found: {source_path}")

    image = Image.open(source_path).convert("RGB")
    width, height = image.size
    if min(width, height) < MIN_FACE_DIMENSION:
        raise AvatarValidationError(
            f"Preview portrait is too small. Minimum dimension is {MIN_FACE_DIMENSION}px."
        )

    ref_size = _preview_reference_size()
    canvas = Image.new("RGB", (ref_size, ref_size), (16, 16, 16))

    # Keep the identity image large enough for landmark detail while preserving headroom.
    default_scale = float(os.environ.get("AVATAR_PREVIEW_PROCESSED_SAFE_SCALE", "0.92") or 0.92)
    if scale is None:
        scale = default_scale
    scale = max(0.78, min(0.96, float(scale)))
    target_side = max(512, min(ref_size, int(ref_size * scale)))
    placed = ImageOps.autocontrast(image).resize((target_side, target_side), Image.Resampling.LANCZOS)

    x_off = int((ref_size - target_side) / 2)
    # Push face slightly down to create forehead room while avoiding over-loose framing.
    if vertical_bias is None:
        vertical_bias = float(os.environ.get("AVATAR_PREVIEW_PROCESSED_SAFE_VERTICAL_BIAS", "0.56") or 0.56)
    vertical_bias = max(0.50, min(0.64, float(vertical_bias)))
    y_off = int((ref_size - target_side) * vertical_bias)
    y_off = max(0, min(ref_size - target_side, y_off))
    canvas.paste(placed, (x_off, y_off))

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")

    detected_face = _detect_face_bbox(canvas)
    full_box = (0, 0, ref_size, ref_size)
    crop_metrics = _face_crop_safety_metrics(canvas, detected_face, full_box)

    # Reuse strict preflight scoring for transparent diagnostics.
    preflight_ok, preflight_reason, preflight_score = _preview_preflight_assessment(
        crop_metrics,
        max_face_area_ratio=0.60,
        min_top_margin_ratio=0.34,
        min_bottom_margin_ratio=0.22,
        min_side_margin_ratio=0.10,
        max_center_offset_x_ratio=0.14,
        max_center_offset_y_ratio=0.16,
        min_mouth_position_ratio=0.52,
        max_mouth_position_ratio=0.84,
    )
    stability = _estimate_landmark_stability(canvas)

    return {
        "preview_reference_path": str(destination),
        "crop_box": list(full_box),
        "detected_face_bbox": list(detected_face) if detected_face else None,
        "crop_metrics": crop_metrics,
        "crop_candidate": {
            "mode": "processed_safe_headroom",
            "scale": round(scale, 4),
            "x_offset": int(x_off),
            "y_offset": int(y_off),
            "vertical_bias": round(float(vertical_bias), 4),
        },
        "preflight_passed": bool(preflight_ok),
        "preflight_score": float(preflight_score),
        "preflight_reason": str(preflight_reason or ""),
        "mode": "processed_safe_headroom",
        "landmark_stability": stability,
        "reference_size": ref_size,
    }


def build_preview_liveportrait_normalized_input(
    *,
    source_image_path: str,
    output_path: str,
    source_kind: str = "image",
) -> dict[str, Any]:
    """
    Build a canonical preview-only LivePortrait input.

    Goals:
    - keep full head visible with neutral padding
    - tighten centering while preserving forehead/chin margins
    - align eye line when detectable
    - keep mouth in lower third-ish region for stable driving
    """
    source_path = Path(source_image_path)
    if not source_path.exists() or (not source_path.is_file()) or source_path.stat().st_size <= 0:
        raise AvatarValidationError(f"Preview normalization source is missing or unreadable: {source_path}")

    image = Image.open(source_path).convert("RGB")
    detected_face = _detect_face_bbox(image)
    if cv2 is not None and detected_face is None:
        raise AvatarValidationError("Preview normalization could not detect a usable face in the selected source.")

    eye_line_angle = 0.0
    eye_alignment_applied = False
    if cv2 is not None and detected_face is not None:
        import numpy as np

        x0, y0, x1, y1 = detected_face
        arr = np.array(image.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        fx0, fy0 = max(0, x0), max(0, y0)
        fx1, fy1 = min(gray.shape[1], x1), min(gray.shape[0], y1)
        face_roi = gray[fy0:fy1, fx0:fx1]
        if face_roi.size > 0:
            eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
            eyes = eye_cascade.detectMultiScale(face_roi, scaleFactor=1.1, minNeighbors=5, minSize=(18, 18))
            if eyes is not None and len(eyes) >= 2:
                strongest = sorted(eyes, key=lambda e: int(e[2]) * int(e[3]), reverse=True)[:2]
                centers = [
                    (float(ex + (ew / 2.0)), float(ey + (eh / 2.0)))
                    for ex, ey, ew, eh in strongest
                ]
                centers = sorted(centers, key=lambda c: c[0])
                dx = centers[1][0] - centers[0][0]
                dy = centers[1][1] - centers[0][1]
                if abs(dx) > 1e-3:
                    eye_line_angle = float(np.degrees(np.arctan2(dy, dx)))
                    if abs(eye_line_angle) >= 1.2:
                        rotated = image.rotate(
                            -eye_line_angle,
                            resample=Image.Resampling.BICUBIC,
                            expand=True,
                            fillcolor=(16, 16, 16),
                        )
                        image = rotated
                        eye_alignment_applied = True
                        detected_face = _detect_face_bbox(image)

    if cv2 is not None and detected_face is None:
        raise AvatarValidationError("Preview normalization failed after eye alignment; face is no longer detectable.")

    crop_box = _expanded_head_safe_crop(
        image,
        detected_face,
        top_expand=2.05,
        bottom_expand=1.48,
        side_expand=1.06,
        frame_scale=1.44,
    )
    cropped = ImageOps.autocontrast(image.crop(crop_box))

    ref_size = _preview_reference_size()
    canvas = Image.new("RGB", (ref_size, ref_size), (18, 18, 18))

    norm_scale = float(os.environ.get("AVATAR_PREVIEW_LP_NORMALIZED_SCALE", "0.86") or 0.86)
    norm_scale = max(0.78, min(0.92, norm_scale))
    target_side = max(560, min(ref_size, int(ref_size * norm_scale)))
    placed = cropped.resize((target_side, target_side), Image.Resampling.LANCZOS)

    x_off = int((ref_size - target_side) / 2)

    mouth_ratio_in_crop = 0.64
    if detected_face is not None:
        dx0, dy0, dx1, dy1 = detected_face
        fw = max(dx1 - dx0, 1)
        fh = max(dy1 - dy0, 1)
        mouth_y = dy0 + (0.78 * fh)
        mouth_ratio_in_crop = float((mouth_y - crop_box[1]) / float(max(crop_box[3] - crop_box[1], 1)))

    target_mouth_ratio = float(os.environ.get("AVATAR_PREVIEW_LP_TARGET_MOUTH_RATIO", "0.66") or 0.66)
    target_mouth_ratio = max(0.58, min(0.74, target_mouth_ratio))
    y_off = int((target_mouth_ratio * ref_size) - (mouth_ratio_in_crop * target_side))
    y_off = max(0, min(ref_size - target_side, y_off))

    canvas.paste(placed, (x_off, y_off))
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")

    normalized_face = _detect_face_bbox(canvas)
    full_box = (0, 0, ref_size, ref_size)
    normalized_metrics = _face_crop_safety_metrics(canvas, normalized_face, full_box)
    stability = _estimate_landmark_stability(canvas)

    return {
        "source_type": str(source_kind or "image"),
        "original_source_path": str(source_path),
        "normalized_source_path": str(destination),
        "crop_box": [int(v) for v in crop_box],
        "face_area_ratio": float(normalized_metrics.get("face_area_ratio_in_crop") or 0.0),
        "eye_line_angle": round(float(eye_line_angle), 6),
        "mouth_position_ratio": float(normalized_metrics.get("mouth_position_ratio") or 0.0),
        "headroom_ratio": float(normalized_metrics.get("top_margin_ratio") or 0.0),
        "center_offset": {
            "x": float(normalized_metrics.get("center_offset_x_ratio") or 0.0),
            "y": float(normalized_metrics.get("center_offset_y_ratio") or 0.0),
        },
        "face_alignment_applied": bool(eye_alignment_applied),
        "crop_metrics": normalized_metrics,
        "landmark_stability": stability,
        "reference_size": int(ref_size),
    }


def preprocess_avatar_image(
    *,
    image_bytes: bytes,
    original_filename: str,
    storage_root: str,
    teacher_id: int,
    model_version: str,
) -> AvatarPreprocessResult:
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AvatarValidationError(f"Unsupported image format '{ext}'.")

    source_hash = sha256_bytes(image_bytes)
    avatar_dir = Path(storage_root) / "avatars" / str(teacher_id) / source_hash
    avatar_dir.mkdir(parents=True, exist_ok=True)

    original_path = avatar_dir / "original.png"
    processed_path = avatar_dir / "processed.png"
    identity_path = avatar_dir / "identity.json"
    refs_dir = avatar_dir / "refs"
    refs_dir.mkdir(exist_ok=True)

    if identity_path.exists() and processed_path.exists() and original_path.exists():
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
        return AvatarPreprocessResult(
            source_hash=source_hash,
            original_rel_path=payload["original_rel_path"],
            processed_rel_path=payload["processed_rel_path"],
            identity_package_rel_path=payload["identity_package_rel_path"],
            references_rel_paths=payload.get("references_rel_paths", []),
            warnings=payload.get("warnings", []),
        )

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    if min(width, height) < MIN_FACE_DIMENSION:
        raise AvatarValidationError(
            f"Portrait is too small. Minimum dimension is {MIN_FACE_DIMENSION}px."
        )

    warnings: list[str] = []
    blur_score = _variance_score(image)
    if blur_score < 120.0:
        raise AvatarValidationError("Portrait is too blurry. Please upload a sharper front-facing photo.")

    detected_face = _detect_face_bbox(image)
    if cv2 is not None and detected_face is None:
        raise AvatarValidationError("Front-facing face was not detected. Please upload a clear front-camera portrait.")

    if detected_face is not None:
        x0, y0, x1, y1 = detected_face
        fw = max(x1 - x0, 1)
        fh = max(y1 - y0, 1)
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        face_center_ratio = cx / float(width)
        face_center_ratio_y = cy / float(height)
        face_aspect = fw / float(fh)
        face_area_ratio = (fw * fh) / float(max(width * height, 1))
        if face_center_ratio < 0.2 or face_center_ratio > 0.8:
            raise AvatarValidationError("Face is too close to the image edge. Please use a centered front-facing portrait.")
        if face_center_ratio_y < 0.24 or face_center_ratio_y > 0.76:
            raise AvatarValidationError("Face is vertically off-center. Please keep forehead and chin visible in frame.")
        if face_aspect > 1.45:
            raise AvatarValidationError("Detected pose looks like a side profile. Please use a front-facing portrait.")
        if face_area_ratio > MAX_FACE_AREA_RATIO:
            raise AvatarValidationError("Face crop is too tight. Step back and include full head, forehead, and hairline.")

    crop_box = _expanded_head_safe_crop(image, detected_face)
    cropped = image.crop(crop_box)
    processed = ImageOps.autocontrast(cropped)
    processed = processed.resize((768, 768), Image.Resampling.LANCZOS)

    # Reference crops are deterministic variants used by avatar engines.
    ref_512 = processed.resize((512, 512), Image.Resampling.LANCZOS)
    ref_384 = processed.resize((384, 384), Image.Resampling.LANCZOS)

    normalized_original = ImageOps.exif_transpose(image)
    normalized_original.save(original_path, format="PNG")
    processed.save(processed_path, format="PNG")
    ref512_path = refs_dir / "ref_512.png"
    ref384_path = refs_dir / "ref_384.png"
    ref_512.save(ref512_path, format="PNG")
    ref_384.save(ref384_path, format="PNG")

    original_rel = str(original_path.relative_to(Path(storage_root))).replace("\\", "/")
    processed_rel = str(processed_path.relative_to(Path(storage_root))).replace("\\", "/")
    identity_rel = str(identity_path.relative_to(Path(storage_root))).replace("\\", "/")
    refs_rel = [
        str(ref512_path.relative_to(Path(storage_root))).replace("\\", "/"),
        str(ref384_path.relative_to(Path(storage_root))).replace("\\", "/"),
    ]

    payload: dict[str, Any] = {
        "source_hash": source_hash,
        "teacher_id": teacher_id,
        "model_version": model_version,
        "original_rel_path": original_rel,
        "processed_rel_path": processed_rel,
        "identity_package_rel_path": identity_rel,
        "references_rel_paths": refs_rel,
        "warnings": warnings,
        "image": {
            "width": width,
            "height": height,
            "blur_score": round(blur_score, 3),
            "detected_face_bbox": list(detected_face) if detected_face else None,
            "crop_box": list(crop_box),
        },
        "processed_hash": sha256_file(processed_path),
    }
    identity_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    return AvatarPreprocessResult(
        source_hash=source_hash,
        original_rel_path=original_rel,
        processed_rel_path=processed_rel,
        identity_package_rel_path=identity_rel,
        references_rel_paths=refs_rel,
        warnings=warnings,
    )


def preprocess_avatar_video(
    *,
    video_bytes: bytes,
    original_filename: str,
    storage_root: str,
    teacher_id: int,
    model_version: str,
) -> dict[str, Any]:
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise AvatarValidationError(f"Unsupported video format '{ext}'.")

    source_hash = sha256_bytes(video_bytes)
    avatar_dir = Path(storage_root) / "avatars" / str(teacher_id) / source_hash
    avatar_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = avatar_dir / "raw"
    identity_dir = avatar_dir / "identity"
    refs_dir = identity_dir / "refs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    original_video_path = raw_dir / f"source{ext}"
    original_video_path.write_bytes(video_bytes)

    identity_video_path = avatar_dir / "identity_video.json"
    processed_path = identity_dir / "processed.png"
    extracted_path = identity_dir / "frame.png"
    ref512_path = refs_dir / "ref_512.png"
    ref384_path = refs_dir / "ref_384.png"

    if identity_video_path.exists() and processed_path.exists() and extracted_path.exists() and ref512_path.exists() and ref384_path.exists():
        cached = json.loads(identity_video_path.read_text(encoding="utf-8"))
        return {
            "source_hash": source_hash,
            "video_rel_path": cached.get("video_rel_path", ""),
            "processed_rel_path": cached.get("processed_rel_path", ""),
            "identity_package_rel_path": cached.get("identity_package_rel_path", ""),
            "references_rel_paths": cached.get("references_rel_paths", []),
            "warnings": cached.get("warnings", []),
        }

    frame_bytes, frame_meta = _extract_reference_frame_from_video(original_video_path)
    image_result = preprocess_avatar_image(
        image_bytes=frame_bytes,
        original_filename="video_frame.png",
        storage_root=storage_root,
        teacher_id=teacher_id,
        model_version=model_version,
    )

    storage_root_path = Path(storage_root)
    source_original = storage_root_path / image_result.original_rel_path
    source_processed = storage_root_path / image_result.processed_rel_path
    source_ref512 = storage_root_path / image_result.references_rel_paths[0]
    source_ref384 = storage_root_path / image_result.references_rel_paths[1]

    shutil.copyfile(source_original, extracted_path)
    shutil.copyfile(source_processed, processed_path)
    shutil.copyfile(source_ref512, ref512_path)
    shutil.copyfile(source_ref384, ref384_path)

    video_rel = str(original_video_path.relative_to(Path(storage_root))).replace("\\", "/")
    processed_rel = str(processed_path.relative_to(Path(storage_root))).replace("\\", "/")
    identity_rel = str(identity_video_path.relative_to(Path(storage_root))).replace("\\", "/")
    refs_rel = [
        str(ref512_path.relative_to(Path(storage_root))).replace("\\", "/"),
        str(ref384_path.relative_to(Path(storage_root))).replace("\\", "/"),
    ]

    payload: dict[str, Any] = {
        "source_hash": source_hash,
        "teacher_id": teacher_id,
        "model_version": model_version,
        "video_rel_path": video_rel,
        "processed_rel_path": processed_rel,
        "identity_package_rel_path": identity_rel,
        "references_rel_paths": refs_rel,
        "warnings": [],
        "video": {
            "extension": ext,
            "frame_selection": frame_meta,
        },
        "processed_hash": sha256_file(processed_path),
    }
    identity_video_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    return {
        "source_hash": source_hash,
        "video_rel_path": video_rel,
        "processed_rel_path": processed_rel,
        "identity_package_rel_path": identity_rel,
        "references_rel_paths": refs_rel,
        "warnings": [],
    }
