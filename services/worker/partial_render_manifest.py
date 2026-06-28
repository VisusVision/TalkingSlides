from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


VOLATILE_KEYS = {
    "completed_at",
    "created_at",
    "generated_at",
    "started_at",
    "time_ns",
    "timestamp",
    "timestamps",
    "updated_at",
}

PARTIAL_RENDER_REASONS = (
    "unchanged",
    "display_text_changed",
    "narration_text_changed",
    "subtitle_text_changed",
    "tts_input_changed",
    "tts_settings_changed",
    "avatar_input_changed",
    "avatar_display_changed",
    "background_changed",
    "layout_changed",
    "structural_changed",
    "missing_artifact",
    "unknown_requires_full",
)

PARTIAL_RENDER_PLAN_ACTIONS = (
    "reuse_all",
    "metadata_only_future",
    "recompose_visual_only_future",
    "rerun_avatar_future",
    "rerun_tts_avatar_future",
    "rerender_page_future",
    "full_rerender_required_future",
)

CLASSIFICATION_REASON_PRIORITY = (
    "unknown_requires_full",
    "structural_changed",
    "display_text_changed",
    "narration_text_changed",
    "subtitle_text_changed",
    "tts_input_changed",
    "tts_settings_changed",
    "avatar_input_changed",
    "avatar_display_changed",
    "background_changed",
    "layout_changed",
    "missing_artifact",
)

HASH_REASON_MAP = {
    "display_text_hash": "display_text_changed",
    "narration_text_hash": "narration_text_changed",
    "subtitle_text_hash": "subtitle_text_changed",
    "tts_input_hash": "tts_input_changed",
    "tts_settings_hash": "tts_settings_changed",
    "avatar_input_hash": "avatar_input_changed",
    "avatar_display_hash": "avatar_display_changed",
    "background_hash": "background_changed",
    "layout_hash": "layout_changed",
    "structural_hash": "structural_changed",
    "source_render_hash": "structural_changed",
}

_PLAN_VISUAL_REASONS = {
    "display_text_changed",
    "background_changed",
    "layout_changed",
}
_PLAN_TTS_REASONS = {
    "narration_text_changed",
    "subtitle_text_changed",
    "tts_input_changed",
    "tts_settings_changed",
}
_PLAN_FULL_REASONS = {
    "unknown_requires_full",
    "structural_changed",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        raw = "\n".join(str(item) for item in value)
    else:
        raw = str(value)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return unicodedata.normalize("NFC", text)


def build_partial_render_manifest(
    *,
    project_id: Any,
    job_id: Any = None,
    render_job_id: Any = None,
    ordered_results: Iterable[Mapping[str, Any]] | None = None,
    playback_assets: Mapping[str, Any] | None = None,
    slides: Iterable[Mapping[str, Any]] | None = None,
    avatar_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    playback = dict(playback_assets or {})
    results = _mapping_list(ordered_results)
    slide_rows = _mapping_list(slides)
    final_segments = _mapping_list(playback.get("final_segments"))
    timeline = _mapping_list(playback.get("timeline"))
    tts_normalization = _mapping_list(playback.get("tts_normalization"))
    avatar_metadata = _mapping_list(playback.get("avatar_slide_metadata"))
    source_render_metadata = _mapping_list(playback.get("source_render_metadata"))

    count = _page_count(
        results,
        final_segments,
        slide_rows,
        playback.get("slides"),
        playback.get("transcript"),
        playback.get("tts_audio"),
        playback.get("avatar_clips"),
        timeline,
    )

    pages: dict[str, dict[str, Any]] = {}
    sequence_items: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for position in range(count):
        result = _row_for_position(results, position)
        segment = _matching_row(final_segments, position, result)
        slide = _matching_row(slide_rows, position, result)
        tts_meta = _matching_row(tts_normalization, position, result)
        avatar_meta = _matching_row(avatar_metadata, position, result)
        source_meta = _matching_row(source_render_metadata, position, result)
        timeline_meta = _matching_row(timeline, position, result)
        index = _int_or(
            _first_present(result.get("index"), segment.get("index"), slide.get("index")),
            position,
        )
        page_key = _manifest_page_key(
            _first_present(
                result.get("page_key"),
                segment.get("page_key"),
                slide.get("page_key"),
                timeline_meta.get("page_key"),
            ),
            index,
            used_keys,
        )
        page_id = _first_present(
            result.get("page_id"),
            result.get("transcript_page_id"),
            segment.get("page_id"),
            segment.get("transcript_page_id"),
            slide.get("page_id"),
            slide.get("id"),
            timeline_meta.get("page_id"),
        )

        display_text = normalize_text(
            _first_present(
                result.get("display_text"),
                slide.get("display_text"),
                result.get("original_text"),
                slide.get("original_text"),
                segment.get("display_text"),
                segment.get("transcript"),
                _sequence_item(playback.get("transcript"), position),
            )
        )
        narration_text = normalize_text(
            _first_present(
                result.get("text"),
                result.get("narration_text"),
                slide.get("narration_text"),
                slide.get("notes_text"),
                segment.get("transcript"),
                _sequence_item(playback.get("transcript"), position),
            )
        )
        subtitle_text = _normalized_text_list(
            _first_present(
                result.get("subtitle_chunks"),
                slide.get("subtitle_chunks"),
                segment.get("subtitle_chunks"),
            )
        )
        if not subtitle_text and narration_text:
            subtitle_text = [narration_text]

        artifacts = {
            "tts_audio": _artifact_path(
                _first_present(
                    segment.get("tts_audio"),
                    _sequence_item(playback.get("tts_audio"), position),
                    result.get("tts_audio_rel_path"),
                    result.get("tts_audio_path"),
                    slide.get("audio_out"),
                )
            ),
            "avatar_clip": _artifact_path(
                _first_present(
                    segment.get("avatar_clip"),
                    avatar_meta.get("avatar_segment_rel_path"),
                    _sequence_item(playback.get("avatar_clips"), position),
                    result.get("avatar_segment_rel_path"),
                )
            ),
            "composed_segment": _artifact_path(
                _first_present(
                    segment.get("part_rel_path"),
                    result.get("part_rel_path"),
                    result.get("part_path"),
                    slide.get("part_out"),
                )
            ),
            "slide_image": _artifact_path(
                _first_present(
                    segment.get("slide"),
                    _sequence_item(playback.get("slides"), position),
                    result.get("slide_rel_path"),
                    result.get("slide_path"),
                    slide.get("image_path"),
                )
            ),
        }

        tts_settings = _drop_volatile(
            _first_present(
                result.get("tts_settings"),
                tts_meta.get("project_tts_settings"),
                slide.get("tts_settings"),
                {},
            )
        )
        tts_input = {
            "language": _first_present(
                result.get("tts_normalization_language"),
                tts_meta.get("tts_normalization_language"),
                "",
            ),
            "narration_text": narration_text,
            "spoken_text": normalize_text(_first_present(result.get("spoken_text"), "")),
        }
        avatar_settings = _drop_volatile(
            {
                "options": dict(avatar_options or {}),
                "engine_selected": _first_present(
                    segment.get("avatar_engine_selected"),
                    avatar_meta.get("avatar_engine_selected"),
                    result.get("avatar_engine_selected"),
                    result.get("avatar_engine_used"),
                    playback.get("avatar_engine_selected"),
                    "",
                ),
                "attempted": bool(
                    _first_present(
                        segment.get("avatar_attempted"),
                        avatar_meta.get("avatar_attempted"),
                        result.get("avatar_attempted"),
                        False,
                    )
                ),
                "applied": bool(
                    _first_present(
                        segment.get("avatar_applied"),
                        avatar_meta.get("avatar_applied"),
                        result.get("avatar_applied"),
                        False,
                    )
                ),
                "status": _first_present(
                    segment.get("avatar_status"),
                    avatar_meta.get("avatar_status"),
                    result.get("avatar_status"),
                    "none",
                ),
                "tts_audio": artifacts["tts_audio"],
                "narration_text": narration_text,
            }
        )
        avatar_display = _drop_volatile(
            {
                "default_position": _nested_value(playback.get("avatar"), "default_position"),
                "default_size": _nested_value(playback.get("avatar"), "default_size"),
                "quality": _first_present(
                    segment.get("avatar_quality"),
                    avatar_meta.get("avatar_quality"),
                    _nested_value(playback.get("avatar"), "quality"),
                ),
                "enhanced_available": _first_present(
                    segment.get("avatar_enhanced_available"),
                    avatar_meta.get("avatar_enhanced_available"),
                    _nested_value(playback.get("avatar"), "enhanced_available"),
                ),
                "enhanced_pending": _first_present(
                    segment.get("avatar_enhanced_pending"),
                    avatar_meta.get("avatar_enhanced_pending"),
                    _nested_value(playback.get("avatar"), "enhanced_pending"),
                ),
            }
        )
        background = _drop_volatile(
            {
                "slide_image": artifacts["slide_image"],
                "mode": _first_present(
                    result.get("scene_background_mode"),
                    slide.get("scene_background_mode"),
                    slide.get("background_mode"),
                    "",
                ),
                "source_render_method": _first_present(
                    segment.get("source_render_method"),
                    result.get("source_render_method"),
                    source_meta.get("method"),
                    "",
                ),
                "source_render_dependency_report": _first_present(
                    segment.get("source_render_dependency_report"),
                    result.get("source_render_dependency_report"),
                    source_meta.get("dependency_report"),
                    {},
                ),
            }
        )
        layout = _drop_volatile(
            {
                "background_fit": _first_present(
                    result.get("scene_background_fit"),
                    slide.get("scene_background_fit"),
                    "",
                ),
                "text_scale": _first_present(result.get("scene_text_scale"), slide.get("scene_text_scale"), ""),
                "whiteboard_mode": bool(
                    _first_present(result.get("whiteboard_mode"), slide.get("whiteboard_mode"), False)
                ),
                "editor_scene": _nested_value(
                    _first_present(result.get("editor_document"), slide.get("editor_document"), {}),
                    "scene",
                )
                or {},
            }
        )
        structural = {
            "index": index,
            "page_key": page_key,
            "page_id": _canonical_value(page_id),
            "source_slide_index": _first_present(result.get("source_slide_index"), slide.get("source_slide_index"), ""),
            "split_index": _first_present(result.get("split_index"), slide.get("split_index"), ""),
            "duration": _first_present(segment.get("duration"), result.get("duration"), ""),
            "pause_seconds": _first_present(
                segment.get("pause_seconds"),
                result.get("pause_seconds"),
                slide.get("pause_seconds"),
                "",
            ),
        }
        source_render = _drop_volatile(
            {
                "method": _first_present(
                    segment.get("source_render_method"),
                    result.get("source_render_method"),
                    source_meta.get("method"),
                    "",
                ),
                "warnings": _first_present(
                    segment.get("source_render_warnings"),
                    result.get("source_render_warnings"),
                    source_meta.get("warnings"),
                    [],
                ),
                "details": _first_present(
                    segment.get("source_render_details"),
                    result.get("source_render_details"),
                    source_meta.get("details"),
                    [],
                ),
                "dependency_report": _first_present(
                    segment.get("source_render_dependency_report"),
                    result.get("source_render_dependency_report"),
                    source_meta.get("dependency_report"),
                    {},
                ),
                "source_background_warnings": _first_present(result.get("source_background_warnings"), []),
                "source_background_details": _first_present(result.get("source_background_details"), []),
            }
        )

        page_manifest = {
            "index": index,
            "page_key": page_key,
            "page_id": _canonical_value(page_id),
            "display_text_hash": stable_hash(display_text),
            "narration_text_hash": stable_hash(narration_text),
            "subtitle_text_hash": stable_hash(subtitle_text),
            "tts_input_hash": stable_hash(tts_input),
            "tts_settings_hash": stable_hash(tts_settings),
            "avatar_input_hash": stable_hash(avatar_settings),
            "avatar_display_hash": stable_hash(avatar_display),
            "background_hash": stable_hash(background),
            "layout_hash": stable_hash(layout),
            "structural_hash": stable_hash(structural),
            "source_render_hash": stable_hash(source_render),
            "artifacts": artifacts,
            "invalidation_reasons": [],
        }
        pages[page_key] = page_manifest
        sequence_items.append(
            {
                "index": index,
                "page_key": page_key,
                "page_id": _canonical_value(page_id),
                "structural_hash": page_manifest["structural_hash"],
            }
        )

    manifest: dict[str, Any] = {
        "version": 1,
        "project_id": _canonical_value(project_id),
        "job_id": _canonical_value(_first_present(job_id, render_job_id)),
        "sequence_hash": stable_hash(sequence_items),
        "manifest_hash": "",
        "pages": pages,
    }
    manifest["manifest_hash"] = stable_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    return manifest


def build_expected_partial_render_manifest(
    *,
    project_id: Any,
    job_id: Any = None,
    slides: Iterable[Mapping[str, Any]],
    previous_playback_assets: Mapping[str, Any] | None = None,
    tts_settings: Mapping[str, Any] | None = None,
    avatar_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a pre-render manifest from current slide inputs.

    Previous playback data is intentionally limited to artifact paths matched by
    page_key. Text, sequence, layout, and other dependency hashes come from the
    supplied slides/options so old sidecar positions cannot mask current input.
    """

    artifacts_by_key = _previous_artifacts_by_page_key(previous_playback_assets)
    rows: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for position, slide in enumerate(_mapping_list(slides)):
        row = dict(slide)
        index = _int_or(_first_present(row.get("index"), position), position)
        page_key = _manifest_page_key(row.get("page_key"), index, used_keys)
        narration_text = normalize_text(
            _first_present(
                row.get("narration_text"),
                row.get("text"),
                row.get("notes_text"),
                "",
            )
        )
        display_text = normalize_text(
            _first_present(
                row.get("display_text"),
                row.get("original_text"),
                row.get("notes_text"),
                narration_text,
            )
        )
        subtitle_chunks = _normalized_text_list(row.get("subtitle_chunks"))
        if not subtitle_chunks and narration_text:
            subtitle_chunks = [narration_text]

        artifacts = artifacts_by_key.get(page_key, {})
        row.update(
            {
                "index": index,
                "slide_num": _int_or(_first_present(row.get("slide_num"), index + 1), index + 1),
                "page_key": page_key,
                "page_id": _first_present(
                    row.get("page_id"),
                    row.get("transcript_page_id"),
                    row.get("draft_page_id"),
                    row.get("id"),
                ),
                "text": narration_text,
                "narration_text": narration_text,
                "display_text": display_text,
                "original_text": display_text,
                "spoken_text": normalize_text(_first_present(row.get("spoken_text"), narration_text)),
                "subtitle_chunks": subtitle_chunks,
                "tts_settings": dict(_first_present(row.get("tts_settings"), tts_settings, {}) or {}),
                "tts_audio_rel_path": artifacts.get("tts_audio", ""),
                "avatar_segment_rel_path": artifacts.get("avatar_clip", ""),
                "part_rel_path": artifacts.get("composed_segment", ""),
                "slide_rel_path": artifacts.get("slide_image", ""),
            }
        )
        rows.append(row)

    playback_assets = _expected_playback_assets_from_options(avatar_options)
    return build_partial_render_manifest(
        project_id=project_id,
        job_id=job_id,
        ordered_results=rows,
        playback_assets=playback_assets,
        avatar_options=avatar_options,
    )


def classify_partial_render_changes(
    *,
    old_manifest: Mapping[str, Any] | None,
    expected_manifest: Mapping[str, Any] | None,
    required_artifacts: Iterable[str] = ("tts_audio", "composed_segment", "slide_image"),
) -> dict[str, Any]:
    """Compare two manifests and return report-only per-page classifications."""

    required = tuple(str(item) for item in (required_artifacts or ()) if str(item))
    old_valid = _valid_manifest(old_manifest)
    expected_valid = _valid_manifest(expected_manifest)
    old_pages = _manifest_pages(old_manifest) if old_valid else {}
    expected_pages = _manifest_pages(expected_manifest) if expected_valid else {}
    old_order = _manifest_page_order(old_manifest) if old_valid else []
    expected_order = _manifest_page_order(expected_manifest) if expected_valid else []
    global_reasons: list[str] = []
    if not old_valid or not expected_valid:
        global_reasons.append("unknown_requires_full")
    elif _manifest_value(old_manifest, "sequence_hash") != _manifest_value(expected_manifest, "sequence_hash"):
        global_reasons.append("structural_changed")

    pages: dict[str, dict[str, Any]] = {}
    summary = {reason: 0 for reason in PARTIAL_RENDER_REASONS}
    for page_key in _classification_page_keys(old_pages, expected_pages):
        old_page = old_pages.get(page_key)
        expected_page = expected_pages.get(page_key)
        reasons: list[str] = []
        changed_hashes: list[str] = []
        missing_artifacts: list[str] = []

        if not old_valid or not expected_valid:
            reasons.append("unknown_requires_full")
        elif old_page is None or expected_page is None:
            reasons.append("structural_changed")
        else:
            for hash_key, reason in HASH_REASON_MAP.items():
                if _manifest_value(old_page, hash_key) != _manifest_value(expected_page, hash_key):
                    changed_hashes.append(hash_key)
                    reasons.append(reason)
            if old_order != expected_order and _page_order_index(old_order, page_key) != _page_order_index(expected_order, page_key):
                reasons.append("structural_changed")
            missing_artifacts = _missing_required_artifacts(expected_page, required)
            if missing_artifacts:
                reasons.append("missing_artifact")

        ordered_reasons = _ordered_reasons(reasons)
        classification = ordered_reasons[0] if ordered_reasons else "unchanged"
        summary[classification] += 1
        pages[page_key] = {
            "page_key": page_key,
            "index": _page_index(expected_page),
            "old_index": _page_index(old_page),
            "classification": classification,
            "reasons": ordered_reasons,
            "changed_hashes": sorted(dict.fromkeys(changed_hashes)),
            "missing_artifacts": missing_artifacts,
            "requires_full": bool({"unknown_requires_full", "structural_changed"} & set(ordered_reasons)),
        }

    if not pages and "unknown_requires_full" in global_reasons:
        summary["unknown_requires_full"] += 1

    return {
        "version": 1,
        "old_manifest_hash": _manifest_report_hash(old_manifest),
        "expected_manifest_hash": _manifest_report_hash(expected_manifest),
        "old_sequence_hash": _manifest_value(old_manifest, "sequence_hash") if old_valid else "",
        "expected_sequence_hash": _manifest_value(expected_manifest, "sequence_hash") if expected_valid else "",
        "global_reasons": _ordered_reasons(global_reasons),
        "summary": summary,
        "pages": pages,
    }


def build_partial_render_plan(classification_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map report-only classifications to future recommended actions."""

    summary = _empty_plan_summary()
    pages: dict[str, dict[str, Any]] = {}
    if not isinstance(classification_result, Mapping):
        summary["full_rerender_required_future"] += 1
        summary["unknown_requires_full"] += 1
        return {
            "version": 1,
            "mode": "report_only",
            "summary": summary,
            "pages": pages,
        }

    report_pages = _classification_report_pages(classification_result)
    for page_key, page in sorted(report_pages.items(), key=_plan_page_sort_key):
        classification = _plan_classification(page)
        reasons = _plan_reasons(page, classification)
        recommended_action = _partial_render_plan_action(reasons)
        summary[recommended_action] += 1
        if "unknown_requires_full" in reasons:
            summary["unknown_requires_full"] += 1
        pages[page_key] = {
            "page_key": page_key,
            "classification": classification,
            "reasons": reasons,
            "recommended_action": recommended_action,
            "future_only": True,
            "actual_behavior_changed": False,
        }

    if not pages:
        global_reasons = _ordered_reasons(_sequence_of_strings(classification_result.get("global_reasons")))
        if "unknown_requires_full" in global_reasons:
            summary["full_rerender_required_future"] += 1
            summary["unknown_requires_full"] += 1
        elif "structural_changed" in global_reasons:
            summary["full_rerender_required_future"] += 1

    return {
        "version": 1,
        "mode": "report_only",
        "summary": summary,
        "pages": pages,
    }


def _empty_plan_summary() -> dict[str, int]:
    summary = {action: 0 for action in PARTIAL_RENDER_PLAN_ACTIONS}
    summary["unknown_requires_full"] = 0
    return summary


def _classification_report_pages(classification_result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    pages = classification_result.get("pages")
    if not isinstance(pages, Mapping):
        return {}
    return {
        str(key): dict(page)
        for key, page in pages.items()
        if isinstance(page, Mapping)
    }


def _plan_page_sort_key(item: tuple[str, Mapping[str, Any]]) -> tuple[bool, int, str]:
    page_key, page = item
    index = _page_index(page)
    return (index is None, int(index or 0), str(page_key))


def _plan_classification(page: Mapping[str, Any]) -> str:
    classification = str(page.get("classification") or "").strip()
    if classification in PARTIAL_RENDER_REASONS:
        return classification
    return "unknown_requires_full" if classification else "unchanged"


def _plan_reasons(page: Mapping[str, Any], classification: str) -> list[str]:
    reasons = [
        reason
        for reason in _sequence_of_strings(page.get("reasons"))
        if reason in PARTIAL_RENDER_REASONS and reason != "unchanged"
    ]
    if classification != "unchanged" and classification in PARTIAL_RENDER_REASONS:
        reasons.append(classification)
    return _ordered_reasons(reasons)


def _partial_render_plan_action(reasons: Iterable[str]) -> str:
    reason_set = {str(reason) for reason in reasons if str(reason)}
    if reason_set & _PLAN_FULL_REASONS:
        return "full_rerender_required_future"
    if "missing_artifact" in reason_set:
        return "rerender_page_future"
    if reason_set & _PLAN_TTS_REASONS:
        return "rerun_tts_avatar_future"
    if "avatar_input_changed" in reason_set:
        return "rerun_avatar_future"
    if reason_set & _PLAN_VISUAL_REASONS:
        return "recompose_visual_only_future"
    if "avatar_display_changed" in reason_set:
        return "metadata_only_future"
    return "reuse_all"


def _sequence_of_strings(value: Any) -> list[str]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    if not isinstance(value, Iterable):
        return []
    return [str(item) for item in value if str(item)]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical_value(item) for item in value), key=canonical_json)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    return str(value)


def _drop_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in VOLATILE_KEYS:
                continue
            result[key_text] = _drop_volatile(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_drop_volatile(item) for item in value]
    return value


def _mapping_list(value: Iterable[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _page_count(*values: Any) -> int:
    lengths = []
    for value in values:
        if isinstance(value, Mapping) or isinstance(value, (str, bytes)) or value is None:
            continue
        try:
            lengths.append(len(value))
        except TypeError:
            continue
    return max(lengths, default=0)


def _row_for_position(rows: list[dict[str, Any]], position: int) -> dict[str, Any]:
    if 0 <= position < len(rows):
        return rows[position]
    return {}


def _matching_row(rows: list[dict[str, Any]], position: int, result: Mapping[str, Any]) -> dict[str, Any]:
    if not rows:
        return {}
    result_key = str(result.get("page_key") or "").strip()
    if result_key:
        for row in rows:
            if str(row.get("page_key") or "").strip() == result_key:
                return row
    result_index = result.get("index")
    if result_index is not None:
        for row in rows:
            if _int_or(row.get("index"), None) == _int_or(result_index, None):
                return row
    return _row_for_position(rows, position)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        return value
    return None


def _int_or(value: Any, fallback: int | None) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0 if fallback is None else int(fallback)


def _manifest_page_key(value: Any, index: int, used: set[str]) -> str:
    raw = str(value or "").strip()
    key = raw or f"slide:{index}"
    if key in used:
        key = f"slide:{index}"
    suffix = 2
    base = key
    while key in used:
        key = f"{base}:{suffix}"
        suffix += 1
    used.add(key)
    return key


def _sequence_item(value: Any, index: int) -> Any:
    if isinstance(value, (str, bytes, Mapping)) or value is None:
        return None
    try:
        return value[index]
    except (IndexError, TypeError):
        return None


def _nested_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _normalized_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [normalize_text(item) for item in value]
    return [normalize_text(value)]


def _artifact_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("/") or text.startswith("//") or re.match(r"^[A-Za-z]:/", text):
        return ""
    return text


def _expected_playback_assets_from_options(avatar_options: Mapping[str, Any] | None) -> dict[str, Any]:
    options = dict(avatar_options or {})
    avatar = {
        "default_position": _first_present(
            options.get("default_position"),
            options.get("avatar_default_position"),
        ),
        "default_size": _first_present(
            options.get("default_size"),
            options.get("avatar_default_size"),
        ),
        "quality": _first_present(
            options.get("quality"),
            options.get("quality_preset"),
            options.get("avatar_quality"),
        ),
        "enhanced_available": options.get("enhanced_available"),
        "enhanced_pending": options.get("enhanced_pending"),
    }
    return {"avatar": _drop_empty(avatar)}


def _previous_artifacts_by_page_key(previous_playback_assets: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    if not isinstance(previous_playback_assets, Mapping):
        return {}

    result: dict[str, dict[str, str]] = {}
    manifest = previous_playback_assets.get("partial_render_manifest")
    pages = _manifest_pages(manifest) if _valid_manifest(manifest) else {}
    for page_key, page in pages.items():
        artifacts = page.get("artifacts") if isinstance(page, Mapping) else None
        cleaned = _clean_artifacts(artifacts)
        if cleaned:
            result[str(page_key)] = cleaned

    final_segments = _mapping_list(previous_playback_assets.get("final_segments"))
    for segment in final_segments:
        page_key = str(segment.get("page_key") or "").strip()
        if not page_key:
            continue
        result.setdefault(page_key, {})
        result[page_key].update(
            {
                key: value
                for key, value in _clean_artifacts(
                    {
                        "tts_audio": segment.get("tts_audio"),
                        "avatar_clip": segment.get("avatar_clip"),
                        "composed_segment": segment.get("part_rel_path"),
                        "slide_image": segment.get("slide"),
                    }
                ).items()
                if value
            }
        )
    return result


def _clean_artifacts(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "tts_audio": _artifact_path(value.get("tts_audio")),
        "avatar_clip": _artifact_path(value.get("avatar_clip")),
        "composed_segment": _artifact_path(value.get("composed_segment")),
        "slide_image": _artifact_path(value.get("slide_image")),
    }


def _drop_empty(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if item is not None and item != ""
    }


def _valid_manifest(value: Any) -> bool:
    try:
        version = int(value.get("version") or 0) if isinstance(value, Mapping) else 0
    except (TypeError, ValueError):
        version = 0
    return (
        isinstance(value, Mapping)
        and version == 1
        and isinstance(value.get("pages"), Mapping)
    )


def _manifest_pages(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not isinstance(value.get("pages"), Mapping):
        return {}
    return {
        str(key): dict(page)
        for key, page in value.get("pages", {}).items()
        if isinstance(page, Mapping)
    }


def _manifest_page_order(value: Any) -> list[str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("pages"), Mapping):
        return []
    return [
        str(key)
        for key, page in value.get("pages", {}).items()
        if isinstance(page, Mapping)
    ]


def _classification_page_keys(
    old_pages: Mapping[str, Mapping[str, Any]],
    expected_pages: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    expected_keys = sorted(
        expected_pages,
        key=lambda key: (_page_index(expected_pages.get(key)), str(key)),
    )
    old_only = sorted(
        (key for key in old_pages if key not in expected_pages),
        key=lambda key: (_page_index(old_pages.get(key)), str(key)),
    )
    return [*expected_keys, *old_only]


def _page_index(page: Mapping[str, Any] | None) -> int | None:
    if not isinstance(page, Mapping):
        return None
    if page.get("index") is None:
        return None
    return _int_or(page.get("index"), 0)


def _page_order_index(order: list[str], page_key: str) -> int | None:
    try:
        return order.index(page_key)
    except ValueError:
        return None


def _missing_required_artifacts(page: Mapping[str, Any], required_artifacts: tuple[str, ...]) -> list[str]:
    artifacts = page.get("artifacts") if isinstance(page, Mapping) else None
    if not isinstance(artifacts, Mapping):
        artifacts = {}
    return [
        artifact_key
        for artifact_key in required_artifacts
        if not _artifact_path(artifacts.get(artifact_key))
    ]


def _ordered_reasons(reasons: Iterable[str]) -> list[str]:
    seen = {str(reason) for reason in reasons if str(reason)}
    return [
        reason
        for reason in CLASSIFICATION_REASON_PRIORITY
        if reason in seen
    ]


def _manifest_value(manifest: Mapping[str, Any] | None, key: str) -> str:
    if not isinstance(manifest, Mapping):
        return ""
    return str(manifest.get(key) or "")


def _manifest_report_hash(manifest: Mapping[str, Any] | None) -> str:
    if not isinstance(manifest, Mapping):
        return ""
    existing = str(manifest.get("manifest_hash") or "")
    if existing:
        return existing
    return stable_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
