"""Safe per-project avatar runtime settings."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any

from django.utils import timezone


SAFE_AVATAR_MOTION_PRESETS = {"natural_conservative", "natural_visible", "subtle_blink", "subtle_gaze"}
DEFAULT_AVATAR_MOTION_PRESET = "natural_conservative"
AVATAR_RUNTIME_SETTINGS_KEY = "avatar_runtime_settings"

_MOTION_ALIASES = {
    "": DEFAULT_AVATAR_MOTION_PRESET,
    "natural": DEFAULT_AVATAR_MOTION_PRESET,
    "natural_conservative": DEFAULT_AVATAR_MOTION_PRESET,
    "visible": "natural_visible",
    "natural_visible": "natural_visible",
    "blink": "subtle_blink",
    "blink_only": "subtle_blink",
    "subtle_blink": "subtle_blink",
    "gaze": "subtle_gaze",
    "subtle_gaze": "subtle_gaze",
    "expressive": DEFAULT_AVATAR_MOTION_PRESET,
    "expressive_debug": DEFAULT_AVATAR_MOTION_PRESET,
    "boosted": DEFAULT_AVATAR_MOTION_PRESET,
    "boosted_strong": DEFAULT_AVATAR_MOTION_PRESET,
}


def _truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def normalize_safe_avatar_motion_preset(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in SAFE_AVATAR_MOTION_PRESETS:
        return raw
    return _MOTION_ALIASES.get(raw, DEFAULT_AVATAR_MOTION_PRESET)


def default_avatar_runtime_settings() -> dict[str, Any]:
    return {
        "motion_preset": normalize_safe_avatar_motion_preset(os.environ.get("AVATAR_LIVEPORTRAIT_MOTION_PRESET", "")),
        "restoration_enabled": _truthy(os.environ.get("AVATAR_LESSON_AVATAR_USE_RESTORATION"), False),
        "liveportrait_enabled": _truthy(os.environ.get("AVATAR_LIVEPORTRAIT_ENABLED"), True),
    }


def normalize_avatar_runtime_settings(raw: Any = None, *, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    base = dict(default_avatar_runtime_settings())
    if isinstance(fallback, Mapping):
        base.update(
            {
                "motion_preset": normalize_safe_avatar_motion_preset(fallback.get("motion_preset", base["motion_preset"])),
                "restoration_enabled": _truthy(fallback.get("restoration_enabled"), bool(base["restoration_enabled"])),
                "liveportrait_enabled": _truthy(fallback.get("liveportrait_enabled"), bool(base["liveportrait_enabled"])),
            }
        )
    source = raw.get("avatar_runtime_settings") if isinstance(raw, Mapping) and isinstance(raw.get("avatar_runtime_settings"), Mapping) else raw
    if not isinstance(source, Mapping):
        return base
    return {
        "motion_preset": normalize_safe_avatar_motion_preset(source.get("motion_preset", base["motion_preset"])),
        "restoration_enabled": _truthy(source.get("restoration_enabled"), bool(base["restoration_enabled"])),
        "liveportrait_enabled": _truthy(source.get("liveportrait_enabled"), bool(base["liveportrait_enabled"])),
    }


def project_avatar_runtime_settings(project: Any | None) -> dict[str, Any]:
    draft_data = getattr(project, "draft_data", None) if project is not None else None
    metadata = draft_data.get("metadata") if isinstance(draft_data, Mapping) else None
    raw = metadata.get(AVATAR_RUNTIME_SETTINGS_KEY) if isinstance(metadata, Mapping) else None
    return normalize_avatar_runtime_settings(raw)


def save_project_avatar_runtime_settings(project: Any, raw: Any) -> dict[str, Any]:
    settings = normalize_avatar_runtime_settings(raw, fallback=project_avatar_runtime_settings(project))
    draft_data = dict(getattr(project, "draft_data", None) or {})
    metadata = dict(draft_data.get("metadata") or {})
    dirty = bool(metadata.get("dirty"))
    metadata.setdefault("created_at", timezone.now().isoformat())
    metadata["updated_at"] = timezone.now().isoformat()
    metadata["dirty"] = dirty
    metadata[AVATAR_RUNTIME_SETTINGS_KEY] = settings
    draft_data["metadata"] = metadata
    project.draft_data = draft_data
    project.save(update_fields=["draft_data", "updated_at"])
    return settings
