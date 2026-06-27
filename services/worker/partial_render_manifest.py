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
