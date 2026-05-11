"""Avatar PIP placement normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


AVATAR_PLACEMENT_POSITIONS = {"top-right", "top-left", "bottom-right", "bottom-left", "custom"}
AVATAR_PLACEMENT_SIZES = {"small", "medium", "large"}
AVATAR_PLACEMENT_WIDTHS = {
    "small": 0.18,
    "medium": 0.24,
    "large": 0.30,
}
DEFAULT_AVATAR_PLACEMENT = {
    "position": "top-right",
    "size": "medium",
    "x": 0.72,
    "y": 0.08,
    "width": 0.24,
}

_DEFAULT_MARGIN_X = 0.04
_DEFAULT_MARGIN_Y = 0.08
_AVATAR_ASPECT_HEIGHT_RATIO = 9.0 / 16.0
_MIN_WIDTH = 0.12
_MAX_WIDTH = 0.35


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _ratio_from_value(value: Any, default: float) -> float:
    number = _float_or_none(value)
    if number is None:
        return default
    return number


def _percent_ratio_from_value(value: Any, default: float) -> float:
    number = _float_or_none(value)
    if number is None:
        return default
    return number / 100.0


def _size_from_width(width: float) -> str:
    if width <= 0.205:
        return "small"
    if width >= 0.27:
        return "large"
    return "medium"


def _position_coordinates(position: str, width: float) -> tuple[float, float]:
    height = width * _AVATAR_ASPECT_HEIGHT_RATIO
    if position == "top-left":
        return _DEFAULT_MARGIN_X, _DEFAULT_MARGIN_Y
    if position == "bottom-left":
        return _DEFAULT_MARGIN_X, 1.0 - height - _DEFAULT_MARGIN_Y
    if position == "bottom-right":
        return 1.0 - width - _DEFAULT_MARGIN_X, 1.0 - height - _DEFAULT_MARGIN_Y
    return 1.0 - width - _DEFAULT_MARGIN_X, _DEFAULT_MARGIN_Y


def normalize_avatar_placement(raw: Any = None, *, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a clamped normalized placement payload.

    Public API coordinates use 0..1 ratios. Legacy preference rows store
    percentages, so this helper accepts either representation.
    """

    base = dict(DEFAULT_AVATAR_PLACEMENT)
    if isinstance(fallback, Mapping):
        base.update({key: fallback.get(key, base[key]) for key in base})

    source = raw
    if isinstance(source, Mapping) and isinstance(source.get("avatar_placement"), Mapping):
        source = source.get("avatar_placement")
    elif isinstance(source, Mapping) and isinstance(source.get("placement"), Mapping):
        source = source.get("placement")
    if not isinstance(source, Mapping):
        source = {}

    raw_position = source.get("position", source.get("anchor", base["position"]))
    position = str(raw_position or base["position"]).strip().lower()
    if position not in AVATAR_PLACEMENT_POSITIONS:
        position = str(base["position"] or "top-right")

    raw_size = source.get("size", base["size"])
    size = str(raw_size or base["size"]).strip().lower()
    if size not in AVATAR_PLACEMENT_SIZES:
        size = str(base["size"] or "medium")
    default_width = float(AVATAR_PLACEMENT_WIDTHS.get(size, AVATAR_PLACEMENT_WIDTHS["medium"]))
    if "width" in source:
        width = _ratio_from_value(source.get("width"), default_width)
    else:
        width = _percent_ratio_from_value(source.get("width_percent"), default_width)
    width = round(_clamp(width, _MIN_WIDTH, _MAX_WIDTH), 4)
    size = _size_from_width(width)

    if position == "custom":
        if "x" in source:
            x = _ratio_from_value(source.get("x"), float(base["x"]))
        else:
            x = _percent_ratio_from_value(source.get("x_percent"), float(base["x"]))
        if "y" in source:
            y = _ratio_from_value(source.get("y"), float(base["y"]))
        else:
            y = _percent_ratio_from_value(source.get("y_percent"), float(base["y"]))
    else:
        x, y = _position_coordinates(position, width)

    height = width * _AVATAR_ASPECT_HEIGHT_RATIO
    x = round(_clamp(float(x), 0.0, max(0.0, 1.0 - width)), 4)
    y = round(_clamp(float(y), 0.0, max(0.0, 1.0 - height)), 4)
    return {
        "position": position,
        "size": size,
        "x": x,
        "y": y,
        "width": width,
    }


def placement_from_overlay_preference(pref: Any | None) -> dict[str, Any]:
    if pref is None:
        return dict(DEFAULT_AVATAR_PLACEMENT)
    return normalize_avatar_placement(
        {
            "position": getattr(pref, "anchor", "top-right"),
            "x_percent": getattr(pref, "x_percent", 72.0),
            "y_percent": getattr(pref, "y_percent", 8.0),
            "width_percent": getattr(pref, "width_percent", 24.0),
        }
    )


def placement_from_profile(profile: Any | None) -> dict[str, Any]:
    if profile is None:
        return dict(DEFAULT_AVATAR_PLACEMENT)
    return normalize_avatar_placement(
        {
            "position": getattr(profile, "avatar_overlay_default_position", "top-right") or "top-right",
            "size": getattr(profile, "avatar_overlay_size", "medium") or "medium",
        }
    )


def owner_avatar_overlay_preference(project: Any | None):
    project_id = getattr(project, "id", None)
    user_id = getattr(project, "user_id", None)
    if not project_id or not user_id:
        return None
    from core.models import AvatarOverlayPreference

    return AvatarOverlayPreference.objects.filter(user_id=user_id, lesson_id=project_id).first()


def project_avatar_placement(project: Any | None) -> dict[str, Any]:
    pref = owner_avatar_overlay_preference(project)
    if pref is not None:
        return placement_from_overlay_preference(pref)
    profile = getattr(getattr(project, "user", None), "profile", None) if project is not None else None
    return placement_from_profile(profile)


def apply_avatar_placement_to_preference(pref: Any, raw: Any) -> dict[str, Any]:
    placement = normalize_avatar_placement(raw, fallback=placement_from_overlay_preference(pref))
    pref.anchor = placement["position"]
    pref.x_percent = placement["x"] * 100.0
    pref.y_percent = placement["y"] * 100.0
    pref.width_percent = placement["width"] * 100.0
    return placement
