from __future__ import annotations

import os
from typing import Any

from django.conf import settings


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

AVATAR_LEGACY_ENV_NAMES = (
    "AVATAR_ENGINE",
    "AVATAR_LIVEPORTRAIT_CMD",
    "AVATAR_MUSETALK_CMD",
    "AVATAR_ENABLE_COMPOSITE_LESSON",
    "AVATAR_BOOTSTRAP_ON_WORKER_STARTUP",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return bool(default)


def _setting_bool(name: str, default: bool = False) -> bool:
    if hasattr(settings, name):
        return bool(getattr(settings, name))
    return bool(default)


def _env_explicit(name: str) -> bool:
    return name in os.environ


def _legacy_avatar_env_enabled() -> bool:
    return any(str(os.environ.get(name, "")).strip() for name in AVATAR_LEGACY_ENV_NAMES)


def _chain_contains_ollama(raw_value: Any) -> bool:
    normalized = str(raw_value or "").replace(",", " ")
    return any(item.strip().lower() == "ollama" for item in normalized.split())


def avatar_enabled() -> bool:
    if _env_explicit("ENABLE_AVATAR"):
        return _env_bool("ENABLE_AVATAR", default=False)
    return bool(_setting_bool("ENABLE_AVATAR", False) or _legacy_avatar_env_enabled())


def intelligence_enabled() -> bool:
    if _env_explicit("ENABLE_INTELLIGENCE"):
        return _env_bool("ENABLE_INTELLIGENCE", default=False)
    return bool(
        _setting_bool("ENABLE_INTELLIGENCE", False)
        or _setting_bool("LESSON_INTELLIGENCE_ENABLED", False)
        or _setting_bool("ANALYTICS_INTELLIGENCE_ENABLED", False)
    )


def local_ollama_enabled() -> bool:
    if not intelligence_enabled():
        return False
    if _env_explicit("ENABLE_LOCAL_OLLAMA"):
        return _env_bool("ENABLE_LOCAL_OLLAMA", default=False)
    return bool(
        _setting_bool("ENABLE_LOCAL_OLLAMA", False)
        or _chain_contains_ollama(getattr(settings, "LESSON_INTELLIGENCE_PROVIDER_CHAIN", ""))
        or _chain_contains_ollama(getattr(settings, "ANALYTICS_INTELLIGENCE_PROVIDER_CHAIN", ""))
    )


def visual_moderation_enabled() -> bool:
    if _env_explicit("ENABLE_VISUAL_MODERATION"):
        return _env_bool("ENABLE_VISUAL_MODERATION", default=False)
    return bool(
        _setting_bool("ENABLE_VISUAL_MODERATION", False)
        or _setting_bool("VISUAL_MODERATION_AUTO_ENABLED", False)
        or _setting_bool("OCR_MODERATION_AUTO_ENABLED", False)
        or _setting_bool("VIDEO_FRAME_AUDIT_AUTO_ENABLED", False)
        or _setting_bool("VISUAL_SAFETY_CLASSIFIER_ENABLED", False)
        or _setting_bool("AZURE_CONTENT_SAFETY_ENABLED", False)
        or _setting_bool("AZURE_OCR_ENABLED", False)
    )


def xtts_enabled() -> bool:
    if _env_explicit("ENABLE_LOCAL_XTTS"):
        return _env_bool("ENABLE_LOCAL_XTTS", default=True)
    return _setting_bool("ENABLE_LOCAL_XTTS", _env_bool("XTTS_ENABLED", default=True))


def feature_disabled_reason(feature: str) -> str:
    return f"{feature} is disabled by environment."


def disabled_response_payload(feature: str) -> dict[str, Any]:
    feature_key = str(feature or "feature").strip().lower().replace(" ", "_")
    return {
        "enabled": False,
        "status": "disabled",
        "feature": feature_key,
        "error": feature_disabled_reason(feature),
        "message": feature_disabled_reason(feature),
    }


def _feature_payload(enabled: bool, *, feature: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"enabled": bool(enabled)}
    if not enabled:
        payload["reason"] = feature_disabled_reason(feature)
    if extra:
        payload.update(extra)
    return payload


def capabilities_payload() -> dict[str, Any]:
    avatar = avatar_enabled()
    intelligence = intelligence_enabled()
    local_ollama = local_ollama_enabled()
    visual = visual_moderation_enabled()
    local_tts = xtts_enabled()
    return {
        "features": {
            "avatar": _feature_payload(avatar, feature="Avatar"),
            "intelligence": _feature_payload(intelligence, feature="Intelligence"),
            "local_ollama": _feature_payload(local_ollama, feature="Local Ollama"),
            "visual_moderation": _feature_payload(visual, feature="Visual moderation"),
            "local_tts": _feature_payload(
                local_tts,
                feature="Local TTS",
                extra={"status": "enabled" if local_tts else "fallback"},
            ),
        }
    }
