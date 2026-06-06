from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .models import UserProfile


def _sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_storage_path(storage_root: Path, rel_path: str) -> Path | None:
    rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or rel == ".." or rel.startswith("../") or "/../" in rel:
        return None
    return storage_root / rel


def active_avatar_source_paths(profile: UserProfile, *, storage_root: Path) -> dict[str, Any]:
    reference_type = str(profile.avatar_reference_type or "image").strip().lower()
    if reference_type not in {"image", "video"}:
        reference_type = "image"

    image_rel = str(profile.avatar_image_processed or profile.avatar_image_original or "").strip()
    image_original_rel = str(profile.avatar_image_original or image_rel or "").strip()
    video_rel = str(profile.avatar_video_processed or profile.avatar_video_original or "").strip()

    image_abs = _resolve_storage_path(storage_root, image_rel) if image_rel else None
    image_original_abs = _resolve_storage_path(storage_root, image_original_rel) if image_original_rel else None
    video_abs = _resolve_storage_path(storage_root, video_rel) if video_rel else None

    image_hash = _sha256_file(image_abs) if image_abs else ""
    image_original_hash = _sha256_file(image_original_abs) if image_original_abs else ""
    video_hash = _sha256_file(video_abs) if video_abs else ""
    source_hash = video_hash if reference_type == "video" else (image_hash or image_original_hash)

    return {
        "reference_type": reference_type,
        "image_rel_path": image_rel,
        "image_original_rel_path": image_original_rel,
        "video_rel_path": video_rel,
        "image_path": str(image_abs or ""),
        "image_original_path": str(image_original_abs or ""),
        "video_path": str(video_abs or ""),
        "image_exists": bool(image_abs and image_abs.exists() and image_abs.is_file()),
        "image_original_exists": bool(image_original_abs and image_original_abs.exists() and image_original_abs.is_file()),
        "video_exists": bool(video_abs and video_abs.exists() and video_abs.is_file()),
        "image_hash": image_hash,
        "image_original_hash": image_original_hash,
        "video_hash": video_hash,
        "source_hash": source_hash,
    }


def validate_active_avatar_source(profile: UserProfile, *, storage_root: Path) -> dict[str, Any]:
    from avatar.preprocess import AvatarValidationError
    from avatar.simple_input import canonicalize_avatar_input

    paths = active_avatar_source_paths(profile, storage_root=storage_root)
    reference_type = str(paths.get("reference_type") or "image")

    if reference_type == "video":
        if not paths.get("video_exists"):
            error = "avatar_input_source_missing:video"
            return {**paths, "valid": False, "error": error, "warning": "", "metrics": {}, "face_bbox": []}
        source_image_path = ""
        source_video_path = str(paths.get("video_path") or "")
        source_key = "video"
    else:
        source_path = str(paths.get("image_path") or paths.get("image_original_path") or "")
        if not source_path or not Path(source_path).exists():
            error = "avatar_input_source_missing:image"
            return {**paths, "valid": False, "error": error, "warning": "", "metrics": {}, "face_bbox": []}
        source_image_path = source_path
        source_video_path = ""
        source_key = "image"

    try:
        with tempfile.TemporaryDirectory(prefix="avatar-source-validate-") as temp_dir:
            canonical = canonicalize_avatar_input(
                source_image_path=source_image_path,
                source_video_path=source_video_path,
                output_path=str(Path(temp_dir) / "validation.mp4"),
                is_preview=False,
                engine_name=str(profile.avatar_lipsync_engine or profile.avatar_engine_primary or "liveportrait+musetalk"),
                source_key=source_key,
            )
    except AvatarValidationError as exc:
        return {
            **paths,
            "valid": False,
            "error": str(exc),
            "warning": "",
            "metrics": {},
            "face_bbox": [],
            "selected_source_key": "",
        }
    except Exception as exc:
        return {
            **paths,
            "valid": False,
            "error": str(exc or "avatar_source_validation_failed"),
            "warning": "",
            "metrics": {},
            "face_bbox": [],
            "selected_source_key": "",
        }

    return {
        **paths,
        "valid": True,
        "error": "",
        "warning": canonical.warning,
        "metrics": dict(canonical.metrics or {}),
        "face_bbox": list(canonical.face_bbox or []),
        "selected_source_key": str(canonical.selected_source_key or source_key),
        "source_kind": str(canonical.source_kind or reference_type),
    }


def preview_source_hash(profile: UserProfile, *, storage_root: Path) -> str:
    stored = str(getattr(profile, "avatar_preview_source_hash", "") or "").strip()
    if stored:
        return stored

    preview_rel = str(profile.avatar_last_preview_path or profile.avatar_preview_video or "").strip()
    if not preview_rel:
        return ""
    preview_path = _resolve_storage_path(storage_root, preview_rel)
    if preview_path is None:
        return ""
    meta_path = preview_path.with_suffix(preview_path.suffix + ".meta.json")
    if not meta_path.exists() or not meta_path.is_file():
        return ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    reference_type = str(meta.get("avatar_reference_type") or "").strip().lower()
    if reference_type == "video":
        return str(meta.get("source_video_hash") or "")
    return str(meta.get("source_image_original_hash") or meta.get("source_image_hash") or "")


def avatar_preview_stale(profile: UserProfile, *, storage_root: Path, source_hash: str | None = None) -> bool:
    preview_rel = str(profile.avatar_last_preview_path or profile.avatar_preview_video or "").strip()
    if not preview_rel:
        return False
    active_hash = str(source_hash if source_hash is not None else active_avatar_source_paths(profile, storage_root=storage_root).get("source_hash") or "")
    preview_hash = preview_source_hash(profile, storage_root=storage_root)
    return bool(active_hash and preview_hash and active_hash != preview_hash)


def clear_stale_avatar_preview(profile: UserProfile) -> None:
    profile.avatar_preview_video = ""
    profile.avatar_last_preview_path = ""
    profile.avatar_last_preview_status = "stale"
    profile.avatar_preview_source_hash = ""
    profile.avatar_preview_stale = True


def apply_avatar_source_validation(
    profile: UserProfile,
    validation: dict[str, Any],
    *,
    storage_root: Path,
    invalidate_preview: bool = True,
) -> bool:
    previous_preview_hash = preview_source_hash(profile, storage_root=storage_root)
    source_hash = str(validation.get("source_hash") or "")

    profile.avatar_source_valid = bool(validation.get("valid"))
    profile.avatar_source_validation_error = str(validation.get("error") or "")
    profile.avatar_source_hash = source_hash
    profile.avatar_source_image_hash = str(validation.get("image_hash") or validation.get("image_original_hash") or "")
    profile.avatar_source_video_hash = str(validation.get("video_hash") or "")
    profile.avatar_source_reference_type = str(validation.get("reference_type") or profile.avatar_reference_type or "image")

    should_clear_preview = bool(invalidate_preview and previous_preview_hash and source_hash and previous_preview_hash != source_hash)
    if should_clear_preview:
        clear_stale_avatar_preview(profile)
    elif not str(profile.avatar_last_preview_path or profile.avatar_preview_video or "").strip():
        profile.avatar_preview_stale = False
    else:
        profile.avatar_preview_stale = avatar_preview_stale(profile, storage_root=storage_root, source_hash=source_hash)
    return should_clear_preview


def refresh_avatar_source_validation(
    profile: UserProfile,
    *,
    storage_root: Path,
    persist: bool = True,
    invalidate_preview: bool = True,
) -> dict[str, Any]:
    validation = validate_active_avatar_source(profile, storage_root=storage_root)
    stale_cleared = False
    if persist:
        stale_cleared = apply_avatar_source_validation(
            profile,
            validation,
            storage_root=storage_root,
            invalidate_preview=invalidate_preview,
        )
        update_fields = [
            "avatar_source_valid",
            "avatar_source_validation_error",
            "avatar_source_hash",
            "avatar_source_image_hash",
            "avatar_source_video_hash",
            "avatar_source_reference_type",
            "avatar_preview_stale",
            "updated_at",
        ]
        if stale_cleared:
            update_fields.extend(
                [
                    "avatar_preview_video",
                    "avatar_last_preview_path",
                    "avatar_last_preview_status",
                    "avatar_preview_source_hash",
                ]
            )
        profile.save(update_fields=update_fields)
    return {**validation, "preview_stale_cleared": stale_cleared}


def stored_avatar_source_state(profile: UserProfile, *, storage_root: Path) -> dict[str, Any]:
    paths = active_avatar_source_paths(profile, storage_root=storage_root)
    source_hash = str(paths.get("source_hash") or "")
    stored_hash = str(getattr(profile, "avatar_source_hash", "") or "")
    validation_current = bool(stored_hash and source_hash and stored_hash == source_hash)
    stale = avatar_preview_stale(profile, storage_root=storage_root, source_hash=source_hash)
    return {
        **paths,
        "valid": bool(getattr(profile, "avatar_source_valid", False) and validation_current),
        "validation_current": validation_current,
        "error": str(getattr(profile, "avatar_source_validation_error", "") or ""),
        "preview_stale": bool(stale or getattr(profile, "avatar_preview_stale", False)),
        "preview_source_hash": preview_source_hash(profile, storage_root=storage_root),
    }
