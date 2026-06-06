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


TARGET_SIZE_PREVIEW = 768
TARGET_SIZE_LESSON = 1024
PREFERRED_FACE_AREA_MIN = 0.18
PREFERRED_FACE_AREA_MAX = 0.42
PREFERRED_TOP_MARGIN_MIN = 0.18
PREFERRED_BOTTOM_MARGIN_MIN = 0.18
PREFERRED_MOUTH_POSITION_MIN = 0.50
PREFERRED_MOUTH_POSITION_MAX = 0.82


def _read_image(path: Path) -> Image.Image | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        image = Image.open(path)
        image.load()
        return image.convert("RGB")
    except Exception:
        return None


def _read_video_frame(path: Path) -> Image.Image | None:
    if cv2 is None or not path.exists() or not path.is_file():
        return None
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return None
    frame = None
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_indices = [0, max(1, frame_count // 3), max(2, (2 * frame_count) // 3)] if frame_count > 0 else [0, 8, 20]
    for frame_index in sample_indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
        ok, array = capture.read()
        if ok and array is not None:
            frame = array
            break
    capture.release()
    if frame is None:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb).convert("RGB")


def _detect_face_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    if cv2 is None:
        return None
    import numpy as np  # type: ignore

    array = np.array(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(96, 96))
    if faces is None or len(faces) == 0:
        return None
    x, y, width, height = [int(value) for value in max(faces, key=lambda face: int(face[2]) * int(face[3]))]
    return (x, y, x + width, y + height)


def _square_crop_from_face(image_size: tuple[int, int], face_bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    width, height = image_size
    x0, y0, x1, y1 = face_bbox
    face_width = max(x1 - x0, 1)
    face_height = max(y1 - y0, 1)

    left = x0 - int(round(face_width * 0.55))
    right = x1 + int(round(face_width * 0.55))
    top = y0 - int(round(face_height * 1.10))
    bottom = y1 + int(round(face_height * 0.72))

    side = max(right - left, bottom - top)
    center_x = int(round((left + right) / 2.0))
    center_y = int(round((top + bottom) / 2.0))
    left = center_x - side // 2
    top = center_y - side // 2
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

    if right <= left:
        right = min(width, left + 2)
    if bottom <= top:
        bottom = min(height, top + 2)
    return (int(left), int(top), int(right), int(bottom))


def _crop_metrics(crop_box: tuple[int, int, int, int], face_bbox: tuple[int, int, int, int]) -> dict[str, float]:
    x0, y0, x1, y1 = face_bbox
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    face_width = max(x1 - x0, 1)
    face_height = max(y1 - y0, 1)
    crop_width = max(crop_right - crop_left, 1)
    crop_height = max(crop_bottom - crop_top, 1)
    mouth_y = y0 + (0.78 * face_height)
    face_center_x = (x0 + x1) / 2.0
    face_center_y = (y0 + y1) / 2.0
    crop_center_x = (crop_left + crop_right) / 2.0
    crop_center_y = (crop_top + crop_bottom) / 2.0
    return {
        "face_area_ratio_in_crop": round((face_width * face_height) / float(max(crop_width * crop_height, 1)), 6),
        "top_margin_ratio": round((y0 - crop_top) / float(face_height), 6),
        "bottom_margin_ratio": round((crop_bottom - y1) / float(face_height), 6),
        "center_offset_x_ratio": round(abs(face_center_x - crop_center_x) / float(crop_width), 6),
        "center_offset_y_ratio": round(abs(face_center_y - crop_center_y) / float(crop_height), 6),
        "mouth_position_ratio": round((mouth_y - crop_top) / float(crop_height), 6),
    }


def _pick_source(*, source_image_path: str, source_video_path: str, source_key: str) -> tuple[str, str, Image.Image, list[dict[str, Any]]]:
    image_path = Path(str(source_image_path or ""))
    video_path = Path(str(source_video_path or ""))
    image = _read_image(image_path)
    video_frame = _read_video_frame(video_path)
    candidates: list[tuple[str, str, Image.Image]] = []
    if image is not None:
        candidates.append(("image", str(image_path), image))
    if video_frame is not None:
        candidates.append(("video", str(video_path), video_frame))
    if not candidates:
        raise AvatarValidationError("avatar_input_missing_or_unreadable")

    requested = str(source_key or "").strip().lower()
    preferred_order: list[str] = []
    if requested in {"video", "video_frame"}:
        preferred_order = ["video", "image"]
    elif requested in {"image", "image_original", "image_processed"}:
        preferred_order = ["image", "video"]
    else:
        preferred_order = ["image", "video"]

    selected: tuple[str, str, Image.Image] | None = None
    ranking: list[dict[str, Any]] = []
    for preferred_kind in preferred_order:
        selected = next((candidate for candidate in candidates if candidate[0] == preferred_kind), None)
        if selected is not None:
            break
    if selected is None:
        selected = candidates[0]

    for rank, candidate in enumerate(candidates, start=1):
        ranking.append(
            {
                "rank": rank,
                "source_kind": candidate[0],
                "origin": candidate[1],
                "selected": bool(candidate[1] == selected[1]),
                "reason": "preferred_source_type" if candidate[1] == selected[1] else "available_alternative",
            }
        )
    return selected[0], selected[1], selected[2], ranking


def _warning_from_metrics(metrics: dict[str, float]) -> str:
    face_area = float(metrics.get("face_area_ratio_in_crop") or 0.0)
    top_margin = float(metrics.get("top_margin_ratio") or 0.0)
    bottom_margin = float(metrics.get("bottom_margin_ratio") or 0.0)
    mouth_position = float(metrics.get("mouth_position_ratio") or 0.0)
    if face_area < PREFERRED_FACE_AREA_MIN:
        return "face_scale_borderline_small"
    if face_area > PREFERRED_FACE_AREA_MAX:
        return "face_scale_borderline_large"
    if top_margin < PREFERRED_TOP_MARGIN_MIN or bottom_margin < PREFERRED_BOTTOM_MARGIN_MIN:
        return "head_visibility_borderline"
    if mouth_position < PREFERRED_MOUTH_POSITION_MIN or mouth_position > PREFERRED_MOUTH_POSITION_MAX:
        return "mouth_position_borderline"
    return ""


def canonicalize_avatar_input(
    *,
    source_image_path: str,
    source_video_path: str,
    output_path: str,
    is_preview: bool,
    engine_name: str = "liveportrait+musetalk",
    source_key: str = "",
) -> CanonicalAvatarInput:
    selected_kind, selected_origin, selected_image, ranking = _pick_source(
        source_image_path=source_image_path,
        source_video_path=source_video_path,
        source_key=source_key,
    )
    face_bbox = _detect_face_bbox(selected_image)
    if face_bbox is None:
        raise AvatarValidationError("avatar_input_face_not_detected")

    crop_box = _square_crop_from_face(selected_image.size, face_bbox)
    metrics = _crop_metrics(crop_box, face_bbox)
    target_size = TARGET_SIZE_PREVIEW if is_preview else TARGET_SIZE_LESSON
    normalized = ImageOps.fit(selected_image.crop(crop_box), (target_size, target_size), method=Image.Resampling.LANCZOS)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected_source_key = str(source_key or selected_kind)
    normalized_path = output.with_suffix(output.suffix + f".canonical_{selected_source_key}.png")
    normalized.save(normalized_path, format="PNG")

    warning = _warning_from_metrics(metrics)
    handoff = {
        "input_path": str(normalized_path),
        "selected_source_key": selected_source_key,
        "source_kind": selected_kind,
        "normalization_mode": "canonical_square_portrait",
    }
    preflight_score = 1.0 if not warning else 0.75

    return CanonicalAvatarInput(
        original_input_path=str(selected_origin),
        selected_source_key=selected_source_key,
        normalized_input_path=str(normalized_path),
        normalized_mode="canonical_square_portrait",
        engine_name=str(engine_name or "liveportrait+musetalk"),
        source_kind=selected_kind,
        preflight_score=preflight_score,
        face_detected=True,
        readable=True,
        crop_box=[int(value) for value in crop_box],
        face_bbox=[int(value) for value in face_bbox],
        metrics=metrics,
        ranking=ranking,
        handoff=handoff,
        warning=warning,
    )
