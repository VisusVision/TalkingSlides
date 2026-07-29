"""Deterministic render planning for lesson slides.

This module is intentionally execution-free.  It builds canonical render
fingerprints from media-affecting inputs, compares them with a previous
planner manifest, and reports which slides are dirty or reusable.

Global invalidation rules:
- voice, language, provider, render resolution, and pipeline version changes
  invalidate every slide because those inputs are shared by the render job.
- avatar source/model/runtime/default-layout changes invalidate every slide
  when avatar rendering is enabled because the same avatar identity is shared
  across the lesson.
- non-render metadata such as timestamps, theme, notifications, sidebar state,
  and opened tabs are ignored by canonicalization.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from django.conf import settings

from core.avatar_image_moderation import avatar_image_moderation_gate
from core.avatar_placement import (
    avatar_layout_from_profile,
    normalize_avatar_placement,
    resolve_avatar_layout,
)
from core.avatar_readiness import normalize_avatar_engine
from core.avatar_runtime_settings import project_avatar_runtime_settings
from core.avatar_source_validation import stored_avatar_source_state
from core.capabilities import avatar_enabled
from core.models import Project, TranscriptPage
from core.serializers import canonical_project_tts_settings


RENDER_PLANNER_MANIFEST_VERSION = 1
RENDER_PIPELINE_VERSION = "industrial-render-pipeline:v1"
DEFAULT_RENDER_RESOLUTION = {"width": 1920, "height": 1080}

REASON_TRANSCRIPT_CHANGED = "TranscriptChanged"
REASON_IMAGE_CHANGED = "ImageChanged"
REASON_TIMING_CHANGED = "TimingChanged"
REASON_VOICE_CHANGED = "VoiceChanged"
REASON_LANGUAGE_CHANGED = "LanguageChanged"
REASON_PROVIDER_CHANGED = "ProviderChanged"
REASON_AVATAR_CHANGED = "AvatarChanged"
REASON_AVATAR_VISIBILITY_CHANGED = "AvatarVisibilityChanged"
REASON_AVATAR_POSITION_CHANGED = "AvatarPositionChanged"
REASON_RENDER_RESOLUTION_CHANGED = "RenderResolutionChanged"
REASON_PIPELINE_VERSION_CHANGED = "PipelineVersionChanged"
REASON_SLIDE_ORDER_CHANGED = "SlideOrderChanged"
REASON_MISSING_ARTIFACT = "MissingArtifact"
REASON_CORRUPT_ARTIFACT = "CorruptArtifact"
REASON_NEW_SLIDE = "NewSlide"
REASON_REMOVED_SLIDE = "RemovedSlide"
REASON_MISSING_BASELINE = "MissingBaseline"

RENDER_REASON_ORDER = (
    REASON_MISSING_BASELINE,
    REASON_NEW_SLIDE,
    REASON_REMOVED_SLIDE,
    REASON_PIPELINE_VERSION_CHANGED,
    REASON_RENDER_RESOLUTION_CHANGED,
    REASON_SLIDE_ORDER_CHANGED,
    REASON_VOICE_CHANGED,
    REASON_LANGUAGE_CHANGED,
    REASON_PROVIDER_CHANGED,
    REASON_AVATAR_CHANGED,
    REASON_AVATAR_VISIBILITY_CHANGED,
    REASON_AVATAR_POSITION_CHANGED,
    REASON_TRANSCRIPT_CHANGED,
    REASON_IMAGE_CHANGED,
    REASON_TIMING_CHANGED,
    REASON_MISSING_ARTIFACT,
    REASON_CORRUPT_ARTIFACT,
)

GLOBAL_INVALIDATION_REASONS = frozenset(
    {
        REASON_PIPELINE_VERSION_CHANGED,
        REASON_RENDER_RESOLUTION_CHANGED,
        REASON_VOICE_CHANGED,
        REASON_LANGUAGE_CHANGED,
        REASON_PROVIDER_CHANGED,
        REASON_AVATAR_CHANGED,
        REASON_AVATAR_VISIBILITY_CHANGED,
        REASON_AVATAR_POSITION_CHANGED,
    }
)

DEFAULT_REQUIRED_ARTIFACTS = ("composed_segment", "slide_image", "tts_audio")

_IGNORED_METADATA_KEYS = {
    "active_tab",
    "created_at",
    "draft_updated_at",
    "last_opened_tab",
    "notification",
    "notifications",
    "opened_tab",
    "sidebar",
    "sidebar_state",
    "theme",
    "timestamp",
    "updated_at",
}


def canonical_json(value: Any) -> str:
    """Serialize a value deterministically for hashing."""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def render_fingerprint(value: Any) -> str:
    """Return a SHA256 fingerprint for canonical render input data."""

    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_render_planner_manifest(
    *,
    project_id: Any,
    slides: Iterable[Mapping[str, Any]],
    voice: Mapping[str, Any] | None = None,
    language: Any = "",
    tts_provider: Any = "",
    tts_settings: Mapping[str, Any] | None = None,
    avatar: Mapping[str, Any] | None = None,
    render_resolution: Mapping[str, Any] | None = None,
    pipeline_version: str = RENDER_PIPELINE_VERSION,
    artifacts_by_page_key: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic manifest from current render inputs.

    The manifest is backend/internal data.  Public planner output is produced by
    :func:`plan_render_dirty_slides` and intentionally omits storage paths.
    """

    global_inputs = _global_render_inputs(
        voice=voice,
        language=language,
        tts_provider=tts_provider,
        tts_settings=tts_settings,
        avatar=avatar,
        render_resolution=render_resolution,
        pipeline_version=pipeline_version,
    )
    artifacts_by_key = artifacts_by_page_key if isinstance(artifacts_by_page_key, Mapping) else {}
    pages: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    used_keys: set[str] = set()
    for index, raw_slide in enumerate(slides):
        slide = dict(raw_slide or {})
        page_key = _clean_page_key(slide.get("page_key") or slide.get("id"), index, used_keys)
        slide_number = _int_value(
            slide.get("slide_number", slide.get("slide_num", slide.get("order", index + 1))),
            index + 1,
        )
        if slide.get("order") is not None and slide.get("slide_number") is None and slide.get("slide_num") is None:
            slide_number = _int_value(slide.get("order"), index) + 1
        inputs = _slide_render_inputs(slide=slide, global_inputs=global_inputs)
        page = {
            "page_key": page_key,
            "slide_number": slide_number,
            "index": index,
            "fingerprint": render_fingerprint(inputs),
            "inputs": inputs,
            "artifacts": deepcopy(dict(artifacts_by_key.get(page_key) or slide.get("artifacts") or {})),
        }
        pages[page_key] = page
        order.append(page_key)

    manifest = {
        "version": RENDER_PLANNER_MANIFEST_VERSION,
        "project_id": _canonical_scalar(project_id),
        "pipeline_version": str(pipeline_version or ""),
        "render_resolution": _canonical_resolution(render_resolution),
        "page_order": order,
        "pages": pages,
        "manifest_hash": "",
    }
    manifest["manifest_hash"] = render_fingerprint({key: value for key, value in manifest.items() if key != "manifest_hash"})
    return manifest


def build_project_render_planner_manifest(
    project: Project,
    *,
    pages: Iterable[Mapping[str, Any]] | None = None,
    voice_profile: Any | None = None,
    language: Any = "",
    tts_provider: Any = "",
    render_resolution: Mapping[str, Any] | None = None,
    pipeline_version: str = RENDER_PIPELINE_VERSION,
    artifacts_by_page_key: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a planner manifest from the current backend project state."""

    if pages is None:
        page_rows = [_page_payload(page) for page in _active_project_pages(project)]
    else:
        page_rows = [dict(page) for page in pages if isinstance(page, Mapping)]

    resolved_voice = _voice_payload(voice_profile if voice_profile is not None else _project_voice_profile(project))
    resolved_language = language or resolved_voice.get("language") or "auto"
    resolved_provider = tts_provider or resolved_voice.get("provider") or ""
    return build_render_planner_manifest(
        project_id=project.id,
        slides=page_rows,
        voice=resolved_voice,
        language=resolved_language,
        tts_provider=resolved_provider,
        tts_settings=canonical_project_tts_settings(getattr(project, "tts_settings", None)),
        avatar=_project_avatar_payload(project),
        render_resolution=render_resolution,
        pipeline_version=pipeline_version,
        artifacts_by_page_key=artifacts_by_page_key,
    )


def manifest_from_playback_sidecar(sidecar: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the planner manifest embedded in a playback sidecar, if present."""

    if not isinstance(sidecar, Mapping):
        return None
    for key in ("render_planner_manifest", "industrial_render_manifest"):
        candidate = sidecar.get(key)
        if _valid_manifest(candidate):
            return deepcopy(dict(candidate))
    return None


def plan_render_dirty_slides(
    *,
    previous_manifest: Mapping[str, Any] | None,
    current_manifest: Mapping[str, Any],
    required_artifacts: Iterable[str] = DEFAULT_REQUIRED_ARTIFACTS,
    storage_root: str | os.PathLike[str] | None = None,
    artifact_exists: Callable[[Mapping[str, Any]], bool] | None = None,
    artifact_sha256: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Compare manifests and return sanitized dirty/reusable slide decisions."""

    current_pages = _manifest_pages(current_manifest)
    previous_pages = _manifest_pages(previous_manifest) if _valid_manifest(previous_manifest) else {}
    previous_valid = _valid_manifest(previous_manifest)
    current_valid = _valid_manifest(current_manifest)
    current_order = _ordered_current_page_keys(current_manifest, current_pages)
    previous_order = _ordered_current_page_keys(previous_manifest, previous_pages) if previous_valid else []
    sequence_changed = bool(previous_valid and current_valid and previous_order != current_order)
    required = tuple(str(item) for item in required_artifacts if str(item))

    rows: list[dict[str, Any]] = []
    dirty: list[int] = []
    reusable: list[int] = []
    reasons_by_slide: dict[str, list[str]] = {}

    for page_key in current_order:
        current_page = current_pages[page_key]
        previous_page = previous_pages.get(page_key)
        reasons: list[str] = []
        if not previous_valid:
            reasons.append(REASON_MISSING_BASELINE)
        elif not current_valid:
            reasons.append(REASON_MISSING_BASELINE)
        elif previous_page is None:
            reasons.append(REASON_NEW_SLIDE)
        else:
            reasons.extend(_input_change_reasons(previous_page, current_page))
            reasons.extend(
                _artifact_reasons(
                    previous_page,
                    required_artifacts=required,
                    storage_root=storage_root,
                    artifact_exists=artifact_exists,
                    artifact_sha256=artifact_sha256,
                )
            )

        ordered_reasons = _ordered_reasons(reasons)
        slide_number = _page_slide_number(current_page)
        status = "dirty" if ordered_reasons else "reusable"
        if ordered_reasons:
            dirty.append(slide_number)
            reasons_by_slide[str(slide_number)] = ordered_reasons
        else:
            reusable.append(slide_number)
        rows.append(
            {
                "slide": slide_number,
                "page_key": str(current_page.get("page_key") or page_key),
                "status": status,
                "reasons": ordered_reasons,
            }
        )

    removed_keys = sorted(set(previous_pages.keys()) - set(current_pages.keys()))
    for page_key in removed_keys:
        previous_page = previous_pages[page_key]
        slide_number = _page_slide_number(previous_page)
        dirty.append(slide_number)
        reasons_by_slide[str(slide_number)] = [REASON_REMOVED_SLIDE]
        rows.append(
            {
                "slide": slide_number,
                "page_key": str(previous_page.get("page_key") or page_key),
                "status": "dirty",
                "reasons": [REASON_REMOVED_SLIDE],
            }
        )

    dirty = sorted(dict.fromkeys(dirty))
    reusable = sorted(number for number in dict.fromkeys(reusable) if number not in set(dirty))
    rows.sort(key=lambda row: (_int_value(row.get("slide"), 0), str(row.get("page_key") or "")))
    current_slide_numbers = {_page_slide_number(page) for page in current_pages.values()}
    if current_slide_numbers and current_slide_numbers.issubset(set(dirty)):
        global_reasons = {
            reason
            for reasons in reasons_by_slide.values()
            for reason in reasons
            if reason in GLOBAL_INVALIDATION_REASONS
        }
    else:
        global_reasons = set()

    return {
        "version": 1,
        "mode": "planning_only",
        "dirty_slides": dirty,
        "reusable_slides": reusable,
        "reasons": reasons_by_slide,
        "global_reasons": _ordered_reasons(global_reasons),
        "slides": rows,
        "sequence_changed": sequence_changed,
        "actual_behavior_changed": False,
    }


def _global_render_inputs(
    *,
    voice: Mapping[str, Any] | None,
    language: Any,
    tts_provider: Any,
    tts_settings: Mapping[str, Any] | None,
    avatar: Mapping[str, Any] | None,
    render_resolution: Mapping[str, Any] | None,
    pipeline_version: str,
) -> dict[str, Any]:
    voice_payload = {
        "voice_id": _clean_text((voice or {}).get("voice_id")),
        "speed": _canonical_number((voice or {}).get("speed", 1.0)),
        "pitch": _canonical_number((voice or {}).get("pitch", 1.0)),
    }
    return {
        "voice": voice_payload,
        "language": _clean_text(language or (voice or {}).get("language") or "auto").lower(),
        "tts_provider": _clean_text(tts_provider or (voice or {}).get("provider") or ""),
        "tts_settings": canonical_project_tts_settings(tts_settings),
        "avatar": _canonical_avatar(avatar),
        "render_resolution": _canonical_resolution(render_resolution),
        "pipeline_version": str(pipeline_version or ""),
    }


def _slide_render_inputs(*, slide: Mapping[str, Any], global_inputs: Mapping[str, Any]) -> dict[str, Any]:
    editor_document = _clean_editor_document(slide.get("editor_document"))
    scene = editor_document.get("scene") if isinstance(editor_document.get("scene"), Mapping) else {}
    avatar = dict(global_inputs.get("avatar") or {})
    slide_layout = scene.get("avatar_layout") if isinstance(scene, Mapping) else {}
    if avatar.get("enabled"):
        resolved_layout = resolve_avatar_layout(
            slide_layout,
            avatar.get("lesson_layout"),
            avatar.get("publisher_layout"),
        )
        semantic_layout = _semantic_avatar_layout(resolved_layout)
        semantic_layout["visible"] = bool(avatar.get("avatar_visible", True) and semantic_layout["visible"])
        avatar["layout"] = semantic_layout
        avatar["placement"] = normalize_avatar_placement(resolved_layout)

    return {
        "transcript": {
            "narration_text": _clean_text(
                _first_present(slide.get("narration_text"), slide.get("text"), slide.get("notes_text"), "")
            ),
            "display_text": _clean_text(
                _first_present(slide.get("display_text"), slide.get("original_text"), slide.get("notes_text"), "")
            ),
            "subtitle_chunks": [_clean_text(item) for item in _list_value(slide.get("subtitle_chunks"))],
            "rich_text_html": _clean_text(slide.get("rich_text_html")),
        },
        "image": _slide_image_identity(slide, scene),
        "timing": {
            "duration_seconds": _canonical_number(slide.get("duration_seconds", slide.get("duration"))),
            "pause_seconds": _canonical_number(slide.get("pause_seconds")),
        },
        "voice": global_inputs["voice"],
        "language": global_inputs["language"],
        "tts_provider": global_inputs["tts_provider"],
        "tts_settings": global_inputs["tts_settings"],
        "avatar": avatar,
        "render_resolution": global_inputs["render_resolution"],
        "pipeline_version": global_inputs["pipeline_version"],
    }


def _input_change_reasons(previous_page: Mapping[str, Any], current_page: Mapping[str, Any]) -> list[str]:
    previous_inputs = previous_page.get("inputs") if isinstance(previous_page.get("inputs"), Mapping) else {}
    current_inputs = current_page.get("inputs") if isinstance(current_page.get("inputs"), Mapping) else {}
    reasons: list[str] = []
    comparisons = (
        ("pipeline_version", REASON_PIPELINE_VERSION_CHANGED),
        ("render_resolution", REASON_RENDER_RESOLUTION_CHANGED),
        ("voice", REASON_VOICE_CHANGED),
        ("language", REASON_LANGUAGE_CHANGED),
        ("tts_provider", REASON_PROVIDER_CHANGED),
        ("tts_settings", REASON_PROVIDER_CHANGED),
        ("transcript", REASON_TRANSCRIPT_CHANGED),
        ("image", REASON_IMAGE_CHANGED),
        ("timing", REASON_TIMING_CHANGED),
    )
    for key, reason in comparisons:
        if _canonical_value(previous_inputs.get(key)) != _canonical_value(current_inputs.get(key)):
            reasons.append(reason)

    previous_avatar = previous_inputs.get("avatar") if isinstance(previous_inputs.get("avatar"), Mapping) else {}
    current_avatar = current_inputs.get("avatar") if isinstance(current_inputs.get("avatar"), Mapping) else {}
    if _avatar_core(previous_avatar) != _avatar_core(current_avatar):
        reasons.append(REASON_AVATAR_CHANGED)
    if _avatar_visibility(previous_avatar) != _avatar_visibility(current_avatar):
        reasons.append(REASON_AVATAR_VISIBILITY_CHANGED)
    if _avatar_position(previous_avatar) != _avatar_position(current_avatar):
        reasons.append(REASON_AVATAR_POSITION_CHANGED)

    previous_fingerprint = str(previous_page.get("fingerprint") or "")
    current_fingerprint = str(current_page.get("fingerprint") or "")
    if previous_fingerprint and current_fingerprint and previous_fingerprint == current_fingerprint:
        return [reason for reason in reasons if reason in {REASON_MISSING_ARTIFACT, REASON_CORRUPT_ARTIFACT}]
    return reasons


def _artifact_reasons(
    page: Mapping[str, Any],
    *,
    required_artifacts: Iterable[str],
    storage_root: str | os.PathLike[str] | None,
    artifact_exists: Callable[[Mapping[str, Any]], bool] | None,
    artifact_sha256: Callable[[Mapping[str, Any]], str] | None,
) -> list[str]:
    artifacts = page.get("artifacts") if isinstance(page.get("artifacts"), Mapping) else {}
    missing = False
    corrupt = False
    for name in required_artifacts:
        artifact = _artifact_mapping(artifacts.get(name))
        if not artifact:
            missing = True
            continue
        if artifact.get("corrupt") is True or artifact.get("valid") is False:
            corrupt = True
            continue
        if not _artifact_exists(artifact, storage_root=storage_root, artifact_exists=artifact_exists):
            missing = True
            continue
        expected_hash = str(artifact.get("sha256") or artifact.get("hash") or "").strip()
        if expected_hash:
            actual_hash = _artifact_hash(artifact, storage_root=storage_root, artifact_sha256=artifact_sha256)
            if not actual_hash or _strip_hash_prefix(actual_hash) != _strip_hash_prefix(expected_hash):
                corrupt = True
    reasons: list[str] = []
    if missing:
        reasons.append(REASON_MISSING_ARTIFACT)
    if corrupt:
        reasons.append(REASON_CORRUPT_ARTIFACT)
    return reasons


def _artifact_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        return {"path": value.strip()}
    return {}


def _artifact_exists(
    artifact: Mapping[str, Any],
    *,
    storage_root: str | os.PathLike[str] | None,
    artifact_exists: Callable[[Mapping[str, Any]], bool] | None,
) -> bool:
    if artifact_exists is not None:
        return bool(artifact_exists(artifact))
    if artifact.get("exists") is False:
        return False
    if artifact.get("exists") is True and not artifact.get("path"):
        return True
    path = _artifact_path(artifact, storage_root=storage_root)
    if path is None:
        return bool(artifact.get("exists", True))
    return path.is_file()


def _artifact_hash(
    artifact: Mapping[str, Any],
    *,
    storage_root: str | os.PathLike[str] | None,
    artifact_sha256: Callable[[Mapping[str, Any]], str] | None,
) -> str:
    if artifact_sha256 is not None:
        return str(artifact_sha256(artifact) or "")
    path = _artifact_path(artifact, storage_root=storage_root)
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path(artifact: Mapping[str, Any], *, storage_root: str | os.PathLike[str] | None) -> Path | None:
    raw_path = str(artifact.get("path") or artifact.get("rel_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path
    root = Path(storage_root or getattr(settings, "STORAGE_ROOT", "storage_local"))
    return root / raw_path


def _strip_hash_prefix(value: str) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1] if text.startswith("sha256:") else text


def _project_voice_profile(project: Project) -> Any | None:
    user = getattr(project, "user", None)
    if user is None:
        return None
    try:
        return user.voice_profile
    except Exception:
        return None


def _voice_payload(voice_profile: Any | None) -> dict[str, Any]:
    if voice_profile is None:
        return {"provider": "", "voice_id": "", "language": "auto", "speed": 1.0, "pitch": 1.0}
    return {
        "provider": getattr(voice_profile, "provider", "") or "",
        "voice_id": getattr(voice_profile, "voice_id", "") or "",
        "language": getattr(voice_profile, "language", "") or "auto",
        "speed": getattr(voice_profile, "speed", 1.0),
        "pitch": getattr(voice_profile, "pitch", 1.0),
    }


def _project_avatar_payload(project: Project) -> dict[str, Any]:
    profile = None
    user = getattr(project, "user", None)
    if user is not None:
        try:
            profile = user.profile
        except Exception:
            profile = None
    publisher_layout = avatar_layout_from_profile(profile)
    runtime_settings = project_avatar_runtime_settings(project)
    if profile is None or not avatar_enabled():
        return {
            "requested": False,
            "enabled": False,
            "avatar_visible": bool(getattr(project, "avatar_visible", True)),
            "publisher_layout": publisher_layout,
            "lesson_layout": None,
            "avatar_runtime_settings": runtime_settings,
        }

    storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
    source_state = stored_avatar_source_state(profile, storage_root=storage_root)
    moderation_gate = avatar_image_moderation_gate(profile)
    selected_engine = normalize_avatar_engine(
        getattr(profile, "avatar_lipsync_engine", "")
        or getattr(profile, "avatar_engine_primary", "")
        or os.environ.get("AVATAR_ENGINE")
    )
    has_source = bool(
        str(getattr(profile, "avatar_image_processed", "") or getattr(profile, "avatar_image_original", "") or "").strip()
        or str(getattr(profile, "avatar_video_processed", "") or getattr(profile, "avatar_video_original", "") or "").strip()
    )
    profile_enabled = bool(
        getattr(profile, "avatar_enabled", False)
        and getattr(profile, "avatar_consent_confirmed", False)
        and has_source
        and not bool(moderation_gate.get("blocked"))
    )
    if getattr(project, "avatar_enabled_override", None) is None:
        requested = bool(getattr(profile, "avatar_enabled", False))
    else:
        requested = bool(getattr(project, "avatar_enabled_override", False))
    enabled = bool(requested and profile_enabled)
    return {
        "requested": requested,
        "enabled": enabled,
        "avatar_visible": bool(getattr(project, "avatar_visible", True)),
        "publisher_layout": publisher_layout,
        "lesson_layout": None,
        "placement": normalize_avatar_placement(publisher_layout),
        "source_hash": source_state.get("source_hash") or getattr(profile, "avatar_source_hash", "") or "",
        "preview_source_hash": source_state.get("preview_source_hash") or getattr(profile, "avatar_preview_source_hash", "") or "",
        "source_valid": bool(source_state.get("valid")),
        "preview_stale": bool(source_state.get("preview_stale")),
        "moderation_status": str(moderation_gate.get("status") or getattr(profile, "avatar_moderation_status", "") or ""),
        "model_version": getattr(profile, "avatar_model_version", "") or f"{selected_engine}:v1",
        "motion_preset": runtime_settings["motion_preset"],
        "quality_preset": getattr(profile, "avatar_quality_preset", "") or "",
        "lipsync_engine": selected_engine,
        "avatar_engine_selected": selected_engine,
        "reference_type": source_state.get("reference_type") or getattr(profile, "avatar_reference_type", "") or "",
        "avatar_runtime_settings": runtime_settings,
        "restoration_enabled": bool(runtime_settings["restoration_enabled"]),
        "liveportrait_enabled": bool(runtime_settings["liveportrait_enabled"]),
    }


def _canonical_avatar(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    enabled = bool(raw.get("enabled", raw.get("requested", False)))
    if not enabled:
        return {"enabled": False}
    avatar_visible = bool(raw.get("avatar_visible", raw.get("visible", raw.get("default_visible", True))))
    publisher_layout = raw.get("publisher_layout") or raw.get("avatar_layout") or {
        "position": raw.get("position", raw.get("default_position")),
        "size": raw.get("size", raw.get("default_size")),
        "visible": avatar_visible,
    }
    layout = resolve_avatar_layout(raw.get("slide_layout"), raw.get("lesson_layout"), publisher_layout)
    semantic_layout = _semantic_avatar_layout(layout)
    semantic_layout["visible"] = bool(avatar_visible and semantic_layout["visible"])
    placement = normalize_avatar_placement(raw.get("placement") or raw.get("avatar_placement") or layout)
    return {
        "enabled": True,
        "avatar_visible": avatar_visible,
        "source_hash": _clean_text(raw.get("source_hash", raw.get("avatar_source_hash"))),
        "preview_source_hash": _clean_text(raw.get("preview_source_hash", raw.get("avatar_preview_source_hash"))),
        "source_valid": bool(raw.get("source_valid", raw.get("avatar_source_valid", True))),
        "preview_stale": bool(raw.get("preview_stale", raw.get("avatar_preview_stale", False))),
        "moderation_status": _clean_text(raw.get("moderation_status", raw.get("avatar_moderation_status"))),
        "model_version": _clean_text(raw.get("model_version", raw.get("avatar_model_version"))),
        "motion_preset": _clean_text(raw.get("motion_preset")),
        "quality_preset": _clean_text(raw.get("quality_preset")),
        "lipsync_engine": _clean_text(raw.get("lipsync_engine", raw.get("avatar_engine_selected"))),
        "reference_type": _clean_text(raw.get("reference_type", raw.get("avatar_reference_type"))),
        "runtime": _drop_ignored(raw.get("avatar_runtime_settings") or raw.get("runtime") or {}),
        "publisher_layout": _semantic_avatar_layout(publisher_layout),
        "lesson_layout": _drop_ignored(raw.get("lesson_layout") or {}),
        "layout": semantic_layout,
        "placement": placement,
    }


def _avatar_core(value: Mapping[str, Any]) -> Any:
    return _canonical_value(
        {
            key: value.get(key)
            for key in (
                "enabled",
                "source_hash",
                "preview_source_hash",
                "model_version",
                "motion_preset",
                "quality_preset",
                "lipsync_engine",
                "reference_type",
                "runtime",
                "source_valid",
                "preview_stale",
                "moderation_status",
            )
        }
    )


def _avatar_visibility(value: Mapping[str, Any]) -> Any:
    if not value.get("enabled"):
        return None
    layout = value.get("layout") if isinstance(value.get("layout"), Mapping) else {}
    return bool(value.get("avatar_visible", True) and layout.get("visible", True))


def _avatar_position(value: Mapping[str, Any]) -> Any:
    if not value.get("enabled"):
        return None
    layout = value.get("layout") if isinstance(value.get("layout"), Mapping) else {}
    placement = value.get("placement") if isinstance(value.get("placement"), Mapping) else {}
    return _canonical_value(
        {
            "position": layout.get("position"),
            "size": layout.get("size"),
            "x": placement.get("x"),
            "y": placement.get("y"),
            "width": placement.get("width"),
        }
    )


def _page_position(page: Mapping[str, Any]) -> tuple[int, int]:
    return (
        _int_value(page.get("index"), 0),
        _page_slide_number(page),
    )


def _semantic_avatar_layout(value: Mapping[str, Any] | None) -> dict[str, Any]:
    layout = value if isinstance(value, Mapping) else {}
    return {
        "position": str(layout.get("position") or "top-right"),
        "size": str(layout.get("size") or "medium"),
        "visible": bool(layout.get("visible", True)),
    }


def _slide_image_identity(slide: Mapping[str, Any], scene: Mapping[str, Any]) -> dict[str, Any]:
    image_hash = _first_present(
        slide.get("image_hash"),
        slide.get("slide_image_hash"),
        scene.get("image_hash"),
        scene.get("custom_background_hash"),
        "",
    )
    return {
        "image_hash": _clean_text(image_hash),
        "image_token": _clean_text(
            _first_present(
                slide.get("image_token"),
                slide.get("image_path"),
                slide.get("slide_path"),
                scene.get("custom_background_path"),
                scene.get("source_background_path"),
                "",
            )
        ),
        "background_mode": _clean_text(scene.get("background_mode", slide.get("scene_background_mode", ""))),
        "background_fit": _clean_text(scene.get("background_fit", slide.get("scene_background_fit", ""))),
        "whiteboard_mode": bool(slide.get("whiteboard_mode", False)),
        "source_slide_index": _int_or_none(slide.get("source_slide_index")),
        "split_index": _int_or_none(slide.get("split_index")),
    }


def _clean_editor_document(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    document = _drop_ignored(value)
    scene = document.get("scene")
    if isinstance(scene, Mapping):
        document["scene"] = _drop_ignored(scene)
    return document


def _active_project_pages(project: Project):
    relation = getattr(project, "transcript_pages", None)
    if relation is None:
        return TranscriptPage.objects.none()
    return relation.filter(is_active=True).order_by("order", "id")


def _page_payload(page: TranscriptPage) -> dict[str, Any]:
    return {
        "id": page.id,
        "order": page.order,
        "source_slide_index": page.source_slide_index,
        "split_index": page.split_index,
        "page_key": page.page_key,
        "original_text": page.original_text or "",
        "narration_text": page.narration_text or "",
        "rich_text_html": page.rich_text_html or "",
        "editor_document": deepcopy(page.editor_document or {}),
        "subtitle_chunks": deepcopy(page.subtitle_chunks or []),
        "whiteboard_mode": bool(page.whiteboard_mode),
        "duration_seconds": page.duration_seconds,
    }


def _valid_manifest(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and int(value.get("version") or 0) == RENDER_PLANNER_MANIFEST_VERSION
        and isinstance(value.get("pages"), Mapping)
    )


def _manifest_pages(value: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not isinstance(value.get("pages"), Mapping):
        return {}
    pages = {}
    for key, page in value["pages"].items():
        if isinstance(page, Mapping):
            pages[str(key)] = dict(page)
    return pages


def _ordered_current_page_keys(manifest: Mapping[str, Any], pages: Mapping[str, Any]) -> list[str]:
    order = [str(key) for key in _list_value(manifest.get("page_order")) if str(key) in pages]
    for key in pages:
        if key not in order:
            order.append(key)
    return order


def _page_slide_number(page: Mapping[str, Any]) -> int:
    return _int_value(page.get("slide_number", page.get("index", 0)), 0 if page.get("slide_number") else 0) or 0


def _ordered_reasons(reasons: Iterable[str]) -> list[str]:
    seen = {str(reason) for reason in reasons if str(reason)}
    return [reason for reason in RENDER_REASON_ORDER if reason in seen]


def _canonical_resolution(value: Mapping[str, Any] | None) -> dict[str, int]:
    raw = value if isinstance(value, Mapping) else DEFAULT_RENDER_RESOLUTION
    return {
        "width": _int_value(raw.get("width"), DEFAULT_RENDER_RESOLUTION["width"]),
        "height": _int_value(raw.get("height"), DEFAULT_RENDER_RESOLUTION["height"]),
    }


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in _IGNORED_METADATA_KEYS
        }
    if isinstance(value, (list, tuple, set)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, Path):
        return str(value).replace("\\", "/")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    return value


def _drop_ignored(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): deepcopy(val)
        for key, val in value.items()
        if str(key) not in _IGNORED_METADATA_KEYS
    }


def _canonical_scalar(value: Any) -> str | int | float | bool | None:
    clean = _canonical_value(value)
    if isinstance(clean, (str, int, float, bool)) or clean is None:
        return clean
    return str(clean)


def _clean_text(value: Any) -> str:
    return str(_canonical_value("" if value is None else value))


def _canonical_number(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 6)


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return values[-1] if values else None


def _clean_page_key(value: Any, index: int, used: set[str]) -> str:
    base = str(value or f"slide-{index + 1}").strip() or f"slide-{index + 1}"
    candidate = base[:64]
    suffix = 2
    while candidate in used:
        marker = f"-{suffix}"
        candidate = f"{base[: max(1, 64 - len(marker))]}{marker}"
        suffix += 1
    used.add(candidate)
    return candidate
