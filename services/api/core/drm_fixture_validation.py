"""Report-only staging validation for externally packaged DRM fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings

from core.models import Project


DRM_SYSTEMS = (
    ("widevine", False, "video/mp4"),
    ("playready", False, "video/mp4"),
    ("fairplay", True, "application/vnd.apple.mpegurl"),
)

KEY_SYSTEM_TO_NAME = {
    "com.widevine.alpha": "widevine",
    "com.microsoft.playready": "playready",
    "com.apple.fps.1_0": "fairplay",
}


def build_drm_fixture_validation_report(
    *,
    project_id: int | str,
    storage_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic, read-only report for a staged DRM fixture."""

    root = Path(storage_root or getattr(settings, "STORAGE_ROOT", "storage_local")).expanduser().resolve()
    project_id_str = str(project_id)
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    try:
        project_exists = Project.objects.filter(id=project_id).exists()
    except (TypeError, ValueError):
        project_exists = False
    _record_check(
        checks,
        blockers,
        "project_exists",
        project_exists,
        "project_not_found",
        {"project_id": project_id_str},
    )

    sidecar_rel_path = f"{project_id_str}/playback_assets.json"
    sidecar_payload, sidecar_exists, sidecar_valid_json = _read_sidecar(root, sidecar_rel_path)
    _record_check(
        checks,
        blockers,
        "sidecar_exists",
        sidecar_exists,
        "missing_playback_sidecar",
        {"path": sidecar_rel_path},
    )
    if sidecar_exists:
        _record_check(
            checks,
            blockers,
            "sidecar_valid_json",
            sidecar_valid_json,
            "playback_sidecar_invalid_json",
            {"path": sidecar_rel_path},
        )

    sidecar = sidecar_payload if isinstance(sidecar_payload, dict) else {}
    protection_mode = str(sidecar.get("protection_mode") or "").strip().lower()
    _record_check(
        checks,
        blockers,
        "protection_mode_is_drm_protected",
        protection_mode == "drm_protected",
        "protection_mode_not_drm_protected",
        {"protection_mode": protection_mode},
    )

    hls = sidecar.get("hls") if isinstance(sidecar.get("hls"), dict) else {}
    manifest_rel_path = str(hls.get("manifest_rel_path") or "").strip()
    normalized_manifest, manifest_path_notes = _normalize_storage_path(manifest_rel_path)
    manifest_exists = False
    if manifest_rel_path and normalized_manifest and "invalid" not in manifest_path_notes:
        manifest_exists = (root / normalized_manifest).is_file()
    _record_check(
        checks,
        blockers,
        "hls_manifest_path_present",
        bool(manifest_rel_path),
        "missing_hls_manifest_path",
        {"manifest_rel_path": manifest_rel_path},
    )
    if manifest_rel_path:
        _record_check(
            checks,
            blockers,
            "hls_manifest_path_storage_relative",
            bool(normalized_manifest) and not manifest_path_notes,
            "invalid_hls_manifest_path",
            {"manifest_rel_path": manifest_rel_path, "notes": manifest_path_notes},
        )
        _record_check(
            checks,
            blockers,
            "hls_manifest_file_exists",
            manifest_exists,
            "missing_hls_manifest_file",
            {"manifest_rel_path": manifest_rel_path},
        )

    drm_scheme = str(hls.get("drm_scheme") or "").strip().lower()
    if drm_scheme == "hls-aes-128":
        warnings.append("hls_aes128_is_not_widevine_cenc_cmaf_packaging")

    asset_id = str(sidecar.get("asset_id") or "").strip()
    content_id = str(sidecar.get("content_id") or "").strip()
    resolved_asset_id = asset_id or _default_asset_id(project_id_str)
    resolved_content_id = content_id or _default_content_id(project_id_str)
    if not asset_id:
        warnings.append("asset_id_missing_from_sidecar_default_will_be_used")
    if not content_id:
        warnings.append("content_id_missing_from_sidecar_default_will_be_used")

    drm_enabled = bool(getattr(settings, "DRM_ENABLED", False))
    _record_check(
        checks,
        blockers,
        "drm_enabled",
        drm_enabled,
        "drm_disabled",
        {"DRM_ENABLED": drm_enabled},
    )

    systems, preferred_system_name, preferred_system, drm_any_ready = _resolve_drm_systems(
        asset_id=resolved_asset_id,
        content_id=resolved_content_id,
        playback_session_id=f"validation-{project_id_str}",
    )
    enabled_systems = [name for name, payload in systems.items() if payload["enabled"]]
    _record_check(
        checks,
        blockers,
        "drm_has_enabled_system",
        bool(enabled_systems),
        "missing_enabled_drm_system",
        {"enabled_systems": enabled_systems},
    )
    _record_check(
        checks,
        blockers,
        "drm_has_preferred_system_or_key_system",
        bool(preferred_system_name or any(payload["key_system"] for payload in systems.values())),
        "missing_preferred_system_or_key_system",
        {"preferred_system": preferred_system_name},
    )

    selected_key_system = str((preferred_system or {}).get("key_system") or "").strip()
    selected_license_url = str((preferred_system or {}).get("license_url") or "").strip()
    _record_check(
        checks,
        blockers,
        "drm_key_system_present",
        bool(selected_key_system),
        "missing_key_system",
        {"key_system": selected_key_system},
    )
    _record_check(
        checks,
        blockers,
        "license_url_present",
        bool(selected_license_url),
        "missing_license_url",
        {"license_url": selected_license_url},
    )
    if selected_license_url:
        _record_check(
            checks,
            blockers,
            "license_url_absolute",
            _is_absolute_http_url(selected_license_url),
            "license_url_not_absolute",
            {"license_url": selected_license_url},
        )

    _record_check(
        checks,
        blockers,
        "drm_system_ready",
        bool(drm_any_ready),
        "drm_system_not_ready",
        {"preferred_system": preferred_system_name},
    )

    mp4_rel_path = str(sidecar.get("mp4_rel_path") or "").strip()
    if mp4_rel_path:
        warnings.append("mp4_fallback_path_present_but_drm_playback_should_not_use_mp4_fallback")

    blocker_list = sorted(dict.fromkeys(blockers))
    warning_list = sorted(dict.fromkeys(warnings))

    return {
        "mode": "staging-read-only/report-only",
        "project_id": project_id_str,
        "storage_root": str(root),
        "project_exists": project_exists,
        "sidecar": {
            "path": sidecar_rel_path,
            "exists": sidecar_exists,
            "valid_json": sidecar_valid_json,
            "protection_mode": protection_mode,
        },
        "hls": {
            "manifest_rel_path": manifest_rel_path,
            "manifest_exists": manifest_exists,
            "encrypted": bool(hls.get("encrypted")),
            "drm_scheme": str(hls.get("drm_scheme") or ""),
            "packaging_status": str(hls.get("packaging_status") or ""),
            "warnings": _string_list(hls.get("warnings")),
        },
        "drm": {
            "enabled": drm_enabled,
            "provider": str(getattr(settings, "DRM_PROVIDER_NAME", "external") or "external"),
            "preferred_system": preferred_system_name,
            "key_system": selected_key_system,
            "license_url": selected_license_url,
            "license_url_absolute": _is_absolute_http_url(selected_license_url),
            "asset_id": resolved_asset_id,
            "content_id": resolved_content_id,
            "asset_id_source": "sidecar" if asset_id else "default",
            "content_id_source": "sidecar" if content_id else "default",
            "systems": systems,
        },
        "mp4_fallback_expected": False,
        "checks": sorted(checks, key=lambda item: item["name"]),
        "blockers": blocker_list,
        "warnings": warning_list,
        "summary": {
            "ready_for_staging_fixture_attempt": not blocker_list,
            "blocker_count": len(blocker_list),
            "warning_count": len(warning_list),
        },
    }


def _read_sidecar(root: Path, rel_path: str) -> tuple[dict[str, Any] | None, bool, bool]:
    normalized, notes = _normalize_storage_path(rel_path)
    if not normalized or notes:
        return None, False, False
    path = root / normalized
    if not path.is_file():
        return None, False, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, True, False
    return (payload if isinstance(payload, dict) else None), True, isinstance(payload, dict)


def _resolve_drm_systems(*, asset_id: str, content_id: str, playback_session_id: str) -> tuple[dict[str, dict[str, Any]], str, dict[str, Any] | None, bool]:
    legacy_enabled = bool(getattr(settings, "DRM_ENABLED", False))
    legacy_key_system = str(getattr(settings, "DRM_KEY_SYSTEM", "") or "").strip()
    legacy_license_url = str(getattr(settings, "DRM_LICENSE_URL", "") or "").strip()
    legacy_certificate_url = str(getattr(settings, "DRM_CERTIFICATE_URL", "") or "").strip()
    preferred_system = str(getattr(settings, "DRM_PREFERRED_SYSTEM", "") or "").strip().lower()
    inferred_legacy_system = KEY_SYSTEM_TO_NAME.get(legacy_key_system.lower(), "")
    systems: dict[str, dict[str, Any]] = {}

    for name, requires_certificate, default_content_type in DRM_SYSTEMS:
        env_prefix = f"DRM_{name.upper()}"
        system_enabled = bool(getattr(settings, f"{env_prefix}_ENABLED", False))
        key_system = str(getattr(settings, f"{env_prefix}_KEY_SYSTEM", "") or "").strip()
        license_url = str(getattr(settings, f"{env_prefix}_LICENSE_URL", "") or "").strip()
        certificate_url = str(getattr(settings, f"{env_prefix}_CERTIFICATE_URL", "") or "").strip()
        content_type = str(getattr(settings, f"{env_prefix}_CONTENT_TYPE", default_content_type) or default_content_type).strip()

        if not any((key_system, license_url, certificate_url)) and legacy_enabled:
            if preferred_system == name or inferred_legacy_system == name:
                system_enabled = True
                key_system = legacy_key_system
                license_url = legacy_license_url
                certificate_url = legacy_certificate_url

        if not system_enabled and legacy_enabled and (preferred_system == name or inferred_legacy_system == name):
            system_enabled = bool(key_system or license_url or certificate_url)

        ready = bool(system_enabled and key_system and license_url and (not requires_certificate or certificate_url))
        systems[name] = {
            "name": name,
            "enabled": system_enabled,
            "ready": ready,
            "key_system": key_system,
            "license_url": license_url,
            "certificate_url": certificate_url,
            "requires_certificate": requires_certificate,
            "content_type": content_type,
            "asset_id": asset_id,
            "content_id": content_id,
            "playback_session_id": playback_session_id,
        }

    selected_name = preferred_system if preferred_system in systems else ""
    if selected_name and not systems[selected_name]["enabled"]:
        selected_name = ""
    if not selected_name and inferred_legacy_system in systems and systems[inferred_legacy_system]["enabled"]:
        selected_name = inferred_legacy_system
    if not selected_name:
        selected_name = next((name for name, payload in systems.items() if payload["ready"]), "")
    if not selected_name:
        selected_name = next((name for name, payload in systems.items() if payload["enabled"]), "")

    selected_system = systems.get(selected_name) if selected_name else None
    return systems, selected_name, selected_system, any(payload["ready"] for payload in systems.values())


def _record_check(
    checks: list[dict[str, Any]],
    blockers: list[str],
    name: str,
    passed: bool,
    blocker: str,
    details: dict[str, Any] | None = None,
) -> None:
    if not passed:
        blockers.append(blocker)
    checks.append(
        {
            "name": name,
            "status": "pass" if passed else "fail",
            "blocker": "" if passed else blocker,
            "details": details or {},
        }
    )


def _normalize_storage_path(raw_path: str) -> tuple[str, list[str]]:
    raw = str(raw_path or "").strip().replace("\\", "/")
    if not raw:
        return "", ["invalid:empty_path"]
    lowered = raw.lower()
    if "://" in raw or lowered.startswith(("http:", "https:", "data:", "blob:")):
        return "", ["invalid:url_not_storage_relative"]
    if _has_windows_drive_prefix(raw) or raw.startswith("/"):
        return "", ["invalid:absolute_path"]
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return "", ["invalid:path_traversal"]
    return "/".join(parts), []


def _has_windows_drive_prefix(raw_path: str) -> bool:
    return len(raw_path) >= 3 and raw_path[0].isalpha() and raw_path[1:3] == ":/"


def _is_absolute_http_url(raw_url: str) -> bool:
    parsed = urlparse(str(raw_url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _default_asset_id(project_id: str) -> str:
    prefix = str(getattr(settings, "DRM_ASSET_ID_PREFIX", "lesson-") or "lesson-")
    return f"{prefix}{project_id}"


def _default_content_id(project_id: str) -> str:
    prefix = str(getattr(settings, "DRM_CONTENT_ID_PREFIX", "project-") or "project-")
    return f"{prefix}{project_id}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item or "").strip()]
