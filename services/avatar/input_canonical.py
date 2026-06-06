from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

from .preprocess import AvatarValidationError


@dataclass
class CanonicalAvatarInput:
    original_input_path: str
    selected_source_key: str
    normalized_input_path: str
    normalized_mode: str
    engine_name: str
    source_kind: str
    preflight_score: float
    face_detected: bool
    readable: bool
    crop_box: list[int]
    face_bbox: list[int]
    metrics: dict[str, float]
    ranking: list[dict[str, Any]]
    handoff: dict[str, Any]
    warning: str


def _read_image(path: Path) -> Image.Image | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        img = Image.open(path)
        img.load()
        return img.convert("RGB")
    except Exception:
        return None


def _extract_video_frame(path: Path) -> Image.Image | None:
    if cv2 is None or not path.exists() or not path.is_file():
        return None
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    frame = None
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    candidates = [0, max(1, frame_count // 3), max(2, (2 * frame_count) // 3)] if frame_count > 0 else [0, 8, 20]
    for idx in candidates:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, arr = cap.read()
        if ok and arr is not None:
            frame = arr
            break
    cap.release()
    if frame is None:
        return None
    arr_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(arr_rgb).convert("RGB")


def _detect_face_bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    if cv2 is None:
        return None
    import numpy as np

    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(96, 96))
    if faces is None or len(faces) == 0:
        return None
    x, y, w, h = [int(v) for v in max(faces, key=lambda f: int(f[2]) * int(f[3]))]
    return (x, y, x + w, y + h)


def _crop_metrics(*, crop_box: tuple[int, int, int, int], face_bbox: tuple[int, int, int, int]) -> dict[str, float]:
    x0, y0, x1, y1 = face_bbox
    fw = max(x1 - x0, 1)
    fh = max(y1 - y0, 1)
    face_cx = (x0 + x1) / 2.0
    face_cy = (y0 + y1) / 2.0
    crop_w = max(crop_box[2] - crop_box[0], 1)
    crop_h = max(crop_box[3] - crop_box[1], 1)
    mouth_y = y0 + (0.78 * fh)
    return {
        "face_area_ratio_in_crop": round((fw * fh) / float(max(crop_w * crop_h, 1)), 6),
        "center_offset_x_ratio": round(abs(face_cx - ((crop_box[0] + crop_box[2]) / 2.0)) / float(crop_w), 6),
        "center_offset_y_ratio": round(abs(face_cy - ((crop_box[1] + crop_box[3]) / 2.0)) / float(crop_h), 6),
        "top_margin_ratio": round((y0 - crop_box[1]) / float(max(fh, 1)), 6),
        "bottom_margin_ratio": round((crop_box[3] - y1) / float(max(fh, 1)), 6),
        "mouth_position_ratio": round((mouth_y - crop_box[1]) / float(crop_h), 6),
    }


def _engine_profile(*, engine_name: str, is_preview: bool) -> dict[str, Any]:
    engine = str(engine_name or "musetalk").strip().lower()
    if engine not in {"musetalk", "liveportrait+musetalk"}:
        engine = "musetalk"

    if engine == "liveportrait+musetalk":
        return {
            "engine_name": engine,
            "normalized_mode": "engine_liveportrait_balanced",
            "target_size": (768 if is_preview else 1024),
            "face_area_min": 0.22,
            "face_area_max": 0.40,
            "face_area_target": 0.30,
            "mouth_target": 0.64,
            "pad_x": 0.42,
            "pad_top": 0.72,
            "pad_bottom": 0.56,
            "center_bias_y": -0.01,
            "source_preference_bonus": {"image": 8.0, "video_frame": 2.0},
        }

    return {
        "engine_name": "musetalk",
        "normalized_mode": "engine_musetalk_detail",
        "target_size": (768 if is_preview else 1024),
        "face_area_min": 0.24,
        "face_area_max": 0.44,
        "face_area_target": 0.34,
        "mouth_target": 0.66,
        "pad_x": 0.36,
        "pad_top": 0.68,
        "pad_bottom": 0.50,
        "center_bias_y": 0.01,
        "source_preference_bonus": {"image": 5.0, "video_frame": 5.0},
    }


def _square_crop_from_face(
    *,
    image_size: tuple[int, int],
    face_bbox: tuple[int, int, int, int],
    pad_x: float,
    pad_top: float,
    pad_bottom: float,
    center_bias_y: float,
) -> tuple[int, int, int, int]:
    w, h = image_size
    x0, y0, x1, y1 = face_bbox
    fw = max(x1 - x0, 1)
    fh = max(y1 - y0, 1)

    left = x0 - int(round(pad_x * fw))
    right = x1 + int(round(pad_x * fw))
    top = y0 - int(round(pad_top * fh))
    bottom = y1 + int(round(pad_bottom * fh))

    side = max(right - left, bottom - top)
    cx = int(round((left + right) / 2.0))
    cy = int(round((top + bottom) / 2.0 + (center_bias_y * fh)))
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
    if right > w:
        shift = right - w
        left = max(0, left - shift)
        right = w
    if bottom > h:
        shift = bottom - h
        top = max(0, top - shift)
        bottom = h
    if right <= left:
        right = min(w, left + 2)
    if bottom <= top:
        bottom = min(h, top + 2)

    return (int(left), int(top), int(right), int(bottom))


def _normalized_square_crop(
    img: Image.Image,
    face_bbox: tuple[int, int, int, int],
    *,
    target_size: int,
    pad_x: float,
    pad_top: float,
    pad_bottom: float,
    center_bias_y: float,
    face_area_min: float,
    face_area_max: float,
) -> tuple[Image.Image, tuple[int, int, int, int], dict[str, float]]:
    w, h = img.size
    cur_pad_x = float(pad_x)
    cur_pad_top = float(pad_top)
    cur_pad_bottom = float(pad_bottom)
    crop_box = _square_crop_from_face(
        image_size=(w, h),
        face_bbox=face_bbox,
        pad_x=cur_pad_x,
        pad_top=cur_pad_top,
        pad_bottom=cur_pad_bottom,
        center_bias_y=center_bias_y,
    )
    metrics = _crop_metrics(crop_box=crop_box, face_bbox=face_bbox)
    for _ in range(10):
        ratio = float(metrics.get("face_area_ratio_in_crop") or 0.0)
        if face_area_min <= ratio <= face_area_max:
            break
        if ratio < face_area_min:
            # Face is too small: tighten crop.
            cur_pad_x *= 0.90
            cur_pad_top *= 0.90
            cur_pad_bottom *= 0.90
        else:
            # Face is too large: expand crop.
            cur_pad_x *= 1.10
            cur_pad_top *= 1.10
            cur_pad_bottom *= 1.10
        cur_pad_x = max(0.20, min(1.60, cur_pad_x))
        cur_pad_top = max(0.30, min(2.00, cur_pad_top))
        cur_pad_bottom = max(0.25, min(1.80, cur_pad_bottom))
        crop_box = _square_crop_from_face(
            image_size=(w, h),
            face_bbox=face_bbox,
            pad_x=cur_pad_x,
            pad_top=cur_pad_top,
            pad_bottom=cur_pad_bottom,
            center_bias_y=center_bias_y,
        )
        metrics = _crop_metrics(crop_box=crop_box, face_bbox=face_bbox)

    cropped = img.crop(crop_box)
    square = ImageOps.fit(cropped, (target_size, target_size), method=Image.Resampling.LANCZOS)

    metrics = dict(metrics)
    metrics["normalized_pad_x"] = round(cur_pad_x, 6)
    metrics["normalized_pad_top"] = round(cur_pad_top, 6)
    metrics["normalized_pad_bottom"] = round(cur_pad_bottom, 6)
    return square, crop_box, metrics


def canonicalize_avatar_input(
    *,
    source_image_path: str,
    source_video_path: str,
    output_path: str,
    is_preview: bool,
    engine_name: str = "musetalk",
    source_key: str = "",
) -> CanonicalAvatarInput:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    profile = _engine_profile(engine_name=engine_name, is_preview=is_preview)

    source_candidates: list[tuple[str, str, Image.Image]] = []

    image_path = Path(str(source_image_path or ""))
    video_path = Path(str(source_video_path or ""))

    img = _read_image(image_path)
    if img is not None:
        source_candidates.append(("image", str(image_path), img))

    video_frame = _extract_video_frame(video_path)
    if video_frame is not None:
        source_candidates.append(("video_frame", str(video_path), video_frame))

    if not source_candidates:
        raise AvatarValidationError("liveportrait_precheck_failed:missing_or_unreadable_source")

    ranked: list[tuple[float, str, str, Image.Image, tuple[int, int, int, int], tuple[int, int, int, int], dict[str, float]]] = []
    rejected: list[str] = []
    ranking_trace: list[dict[str, Any]] = []

    for kind, origin, candidate_img in source_candidates:
        bbox = _detect_face_bbox(candidate_img)
        if bbox is None:
            rejected.append(f"{kind}:no_face_detected")
            ranking_trace.append(
                {
                    "source_kind": kind,
                    "origin": origin,
                    "face_detected": False,
                    "ranking_score": 0.0,
                    "reason": "no_face_detected",
                }
            )
            continue

        normalized, crop_box, metrics = _normalized_square_crop(
            candidate_img,
            bbox,
            target_size=int(profile["target_size"]),
            pad_x=float(profile["pad_x"]),
            pad_top=float(profile["pad_top"]),
            pad_bottom=float(profile["pad_bottom"]),
            center_bias_y=float(profile["center_bias_y"]),
            face_area_min=float(profile["face_area_min"]),
            face_area_max=float(profile["face_area_max"]),
        )
        face_area = float(metrics.get("face_area_ratio_in_crop") or 0.0)
        center_x = float(metrics.get("center_offset_x_ratio") or 1.0)
        center_y = float(metrics.get("center_offset_y_ratio") or 1.0)
        mouth_pos = float(metrics.get("mouth_position_ratio") or 0.0)
        face_area_target = float(profile["face_area_target"])
        mouth_target = float(profile["mouth_target"])
        face_size_score = max(0.0, 1.0 - min(1.0, abs(face_area - face_area_target) / 0.20))
        center_score = max(0.0, 1.0 - min(1.0, center_x / 0.18)) * max(0.0, 1.0 - min(1.0, center_y / 0.20))
        mouth_score = max(0.0, 1.0 - min(1.0, abs(mouth_pos - mouth_target) / 0.24))
        score = (
            (mouth_score * 34.0)
            + (center_score * 34.0)
            + (face_size_score * 24.0)
            + float((profile.get("source_preference_bonus") or {}).get(kind, 0.0))
        )
        ranked.append((score, kind, origin, normalized, bbox, crop_box, metrics))
        ranking_trace.append(
            {
                "source_kind": kind,
                "origin": origin,
                "face_detected": True,
                "ranking_score": round(float(score), 6),
                "source_preference_bonus": round(float((profile.get("source_preference_bonus") or {}).get(kind, 0.0)), 6),
                "crop_box": [int(v) for v in crop_box],
                "face_bbox": [int(v) for v in bbox],
                "metrics": {
                    "face_area_ratio": round(face_area, 6),
                    "center_offset_x_ratio": round(center_x, 6),
                    "center_offset_y_ratio": round(center_y, 6),
                    "mouth_position_ratio": round(mouth_pos, 6),
                    "top_margin_ratio": round(float(metrics.get("top_margin_ratio") or 0.0), 6),
                    "bottom_margin_ratio": round(float(metrics.get("bottom_margin_ratio") or 0.0), 6),
                },
                "reason": "engine_aware_soft_ranking",
            }
        )

    if not ranked:
        raise AvatarValidationError(
            "liveportrait_precheck_failed:no_usable_source "
            f"reasons={rejected}"
        )

    ranked.sort(key=lambda item: float(item[0]), reverse=True)
    selected_score, selected_kind, selected_origin, selected_img, selected_bbox, selected_crop_box, selected_metrics = ranked[0]

    selected_key = str(source_key or selected_kind)
    normalized_path = output.with_suffix(output.suffix + f".canonical_{selected_key}.png")
    selected_img.save(normalized_path, format="PNG")

    warning = ""
    mouth_pos = float(selected_metrics.get("mouth_position_ratio") or 0.0)
    face_area = float(selected_metrics.get("face_area_ratio_in_crop") or 0.0)
    top_margin = float(selected_metrics.get("top_margin_ratio") or 0.0)
    bottom_margin = float(selected_metrics.get("bottom_margin_ratio") or 0.0)
    if mouth_pos < 0.50 or mouth_pos > 0.82:
        warning = "mouth_position_borderline"
    elif face_area < float(profile["face_area_min"]):
        warning = "face_scale_borderline_small"
    elif face_area > float(profile["face_area_max"]):
        warning = "face_scale_borderline_large"
    elif top_margin < 0.20 or bottom_margin < 0.20:
        warning = "head_visibility_borderline"

    selected_metrics = dict(selected_metrics)
    selected_metrics["face_area_target"] = round(float(profile["face_area_target"]), 6)
    selected_metrics["face_area_min"] = round(float(profile["face_area_min"]), 6)
    selected_metrics["face_area_max"] = round(float(profile["face_area_max"]), 6)
    selected_metrics["mouth_target"] = round(float(profile["mouth_target"]), 6)

    handoff = {
        "engine": str(profile["engine_name"]),
        "mode": ("preview" if is_preview else "lesson"),
        "normalized_input_path": str(normalized_path),
        "normalization_mode": str(profile["normalized_mode"]),
        "source_kind": selected_kind,
        "source_key": selected_key,
        "warning": warning,
    }

    ranking_trace.sort(key=lambda item: float(item.get("ranking_score") or 0.0), reverse=True)
    for idx, item in enumerate(ranking_trace, start=1):
        item["rank"] = int(idx)

    return CanonicalAvatarInput(
        original_input_path=str(selected_origin),
        selected_source_key=selected_key,
        normalized_input_path=str(normalized_path),
        normalized_mode=str(profile["normalized_mode"]),
        engine_name=str(profile["engine_name"]),
        source_kind=selected_kind,
        preflight_score=round(float(selected_score), 6),
        face_detected=True,
        readable=True,
        crop_box=[int(v) for v in selected_crop_box],
        face_bbox=[int(v) for v in selected_bbox],
        metrics=selected_metrics,
        ranking=ranking_trace,
        handoff=handoff,
        warning=warning,
    )
