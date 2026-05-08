from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .models import UserProfile, VoiceProfile
from .avatar_image_moderation import avatar_image_moderation_gate
from .avatar_source_validation import stored_avatar_source_state


def normalize_avatar_engine(value: str | None) -> str:
    requested = str(value or "").strip().lower()
    if requested in {"", "musetalk", "liveportrait+musetalk"}:
        return "liveportrait+musetalk"
    return "liveportrait+musetalk"


def engine_config_health(engine: str) -> dict[str, Any]:
    selected = normalize_avatar_engine(engine)
    checks: dict[str, bool] = {
        "musetalk_command": bool(str(os.environ.get("AVATAR_MUSETALK_CMD", "")).strip()),
        "liveportrait_command": bool(str(os.environ.get("AVATAR_LIVEPORTRAIT_CMD", "")).strip()),
    }
    valid = bool(checks["musetalk_command"] and checks["liveportrait_command"])
    return {
        "selected_engine": selected,
        "valid": valid,
        "checks": checks,
    }


def avatar_preview_readiness(
    profile: UserProfile,
    voice_profile: VoiceProfile | None,
    *,
    storage_root: Path,
) -> dict[str, Any]:
    selected_engine = normalize_avatar_engine(
        profile.avatar_lipsync_engine or profile.avatar_engine_primary or os.environ.get("AVATAR_ENGINE")
    )
    engine_health = engine_config_health(selected_engine)

    processed_rel = str(profile.avatar_image_processed or "").strip()
    processed_abs = (storage_root / processed_rel) if processed_rel else None
    processed_exists = bool(processed_abs and processed_abs.exists() and processed_abs.is_file())
    source_state = stored_avatar_source_state(profile, storage_root=storage_root)
    moderation_gate = avatar_image_moderation_gate(profile)

    voice_id = str((voice_profile.voice_id if voice_profile else "") or "").strip()
    checks: dict[str, Any] = {
        "avatar_enabled": bool(profile.avatar_enabled),
        "avatar_consent_confirmed": bool(profile.avatar_consent_confirmed),
        "avatar_image_original": bool(str(profile.avatar_image_original or "").strip()),
        "avatar_image_processed": bool(processed_rel),
        "processed_reference_exists": processed_exists,
        "processed_reference_path": str(processed_abs) if processed_abs else "",
        "avatar_source_valid": bool(source_state.get("valid")),
        "avatar_source_validation_current": bool(source_state.get("validation_current")),
        "avatar_source_validation_error": str(source_state.get("error") or ""),
        "avatar_source_hash": str(source_state.get("source_hash") or ""),
        "avatar_source_image_hash": str(source_state.get("image_hash") or source_state.get("image_original_hash") or ""),
        "avatar_source_video_hash": str(source_state.get("video_hash") or ""),
        "avatar_source_reference_type": str(source_state.get("reference_type") or ""),
        "avatar_preview_source_hash": str(source_state.get("preview_source_hash") or ""),
        "avatar_preview_stale": bool(source_state.get("preview_stale")),
        "avatar_moderation_status": str(getattr(profile, "avatar_moderation_status", "") or "not_scanned"),
        "avatar_moderation_summary": dict(getattr(profile, "avatar_moderation_summary", {}) or {}),
        "avatar_moderation_blocked": bool(moderation_gate.get("blocked")),
        "avatar_moderation_error_code": str(moderation_gate.get("error_code") or ""),
        "avatar_moderation_error": str(moderation_gate.get("message") or ""),
        "voice_profile_exists": bool(voice_profile),
        "voice_id": voice_id,
        "voice_id_exists": bool(voice_id),
        "requested_engine": selected_engine,
        "engine_config_valid": bool(engine_health.get("valid")),
        "engine_config_checks": dict(engine_health.get("checks") or {}),
    }

    missing: list[str] = []
    if not checks["avatar_enabled"]:
        missing.append("avatar_disabled")
    if not checks["avatar_consent_confirmed"]:
        missing.append("avatar_consent_missing")
    if not checks["avatar_image_original"]:
        missing.append("missing_avatar_image_original")
    if not checks["avatar_image_processed"]:
        missing.append("missing_avatar_image_processed")
    if not checks["processed_reference_exists"]:
        missing.append("missing_processed_reference_file")
    if not checks["voice_profile_exists"]:
        missing.append("missing_voice_profile")
    if checks["voice_profile_exists"] and not checks["voice_id_exists"]:
        missing.append("missing_voice_id")
    if not checks["engine_config_valid"]:
        missing.append("invalid_engine_config")
    if not checks["avatar_source_validation_current"]:
        missing.append("avatar_source_validation_stale")
    elif not checks["avatar_source_valid"]:
        missing.append("avatar_source_invalid")
    if checks["avatar_moderation_blocked"]:
        missing.append(checks["avatar_moderation_error_code"] or "avatar_image_moderation_blocked")

    unique_missing = sorted(set(missing))
    preview_rel = str(profile.avatar_last_preview_path or profile.avatar_preview_video or "").strip()
    preview_ready = bool(
        checks["avatar_source_valid"]
        and not checks["avatar_preview_stale"]
        and preview_rel
        and str(profile.avatar_last_preview_status or "").strip().lower() in {"ready", "warning", "done"}
    )
    if not unique_missing:
        return {
            "ready": True,
            "avatar_ready": preview_ready,
            "avatar_preview_stale": bool(checks["avatar_preview_stale"]),
            "error_code": "",
            "error": "",
            "missing_requirements": [],
            "checks": checks,
        }

    missing_guidance = {
        "avatar_disabled": "Enable avatar generation in avatar settings.",
        "avatar_consent_missing": "Confirm avatar consent in avatar settings.",
        "missing_avatar_image_original": "Upload an avatar portrait image.",
        "missing_avatar_image_processed": "Generate processed avatar image from uploaded source.",
        "missing_processed_reference_file": "Processed avatar reference file is missing on disk; re-prepare avatar.",
        "missing_voice_profile": "Upload or configure a voice profile first.",
        "missing_voice_id": "Voice profile exists but has no voice id; re-upload voice sample.",
        "invalid_engine_config": "Selected avatar engine configuration is not healthy in the current runtime.",
        "avatar_source_validation_stale": "Active avatar source has changed; re-prepare avatar validation.",
        "avatar_source_invalid": checks["avatar_source_validation_error"] or "Active avatar source does not contain a detectable face.",
        "avatar_image_moderation_blocked": checks["avatar_moderation_error"] or "Avatar source image needs moderation review.",
        "avatar_image_moderation_approval_required": checks["avatar_moderation_error"] or "Avatar source image needs moderation approval.",
        "avatar_image_moderation_pending": checks["avatar_moderation_error"] or "Avatar source image moderation is pending.",
    }
    guidance = [missing_guidance.get(item, item) for item in unique_missing]
    return {
        "ready": False,
        "avatar_ready": False,
        "avatar_preview_stale": bool(checks["avatar_preview_stale"]),
        "error_code": "setup_not_prepared",
        "error": "Avatar is not prepared for preview. " + " ".join(guidance),
        "missing_requirements": unique_missing,
        "checks": checks,
    }
