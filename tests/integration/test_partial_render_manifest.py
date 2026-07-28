# pyright: reportMissingImports=false

from copy import deepcopy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402

from core.models import Job, Project, UserProfile, default_project_tts_settings  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.partial_render_manifest import (  # noqa: E402
    build_expected_partial_render_manifest,
    build_partial_render_manifest,
    build_partial_render_plan,
    canonicalize_tts_input,
    canonicalize_tts_settings,
    classify_partial_render_changes,
    canonical_json,
    get_narration_only_recompose_eligibility,
    get_visual_only_recompose_eligibility,
    normalize_text,
    stable_hash,
)


def _make_user(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="publisher")
    return user


def _render_result(
    *,
    index: int,
    page_key: str | None,
    display_text: str,
    narration_text: str,
    project_id: int = 42,
) -> dict:
    return {
        "index": index,
        "slide_num": index + 1,
        "page_key": page_key,
        "page_id": 1000 + index,
        "source_slide_index": index,
        "split_index": 0,
        "duration": 2.0 + index,
        "pause_seconds": 0.25,
        "text": narration_text,
        "narration_text": narration_text,
        "display_text": display_text,
        "spoken_text": narration_text,
        "subtitle_chunks": [narration_text],
        "tts_settings": {"provider_preference": "gtts", "speech_speed": 1.05},
        "scene_background_mode": "source_background",
        "scene_background_fit": "cover",
        "scene_text_scale": 1.1,
        "editor_document": {"scene": {"overlay_layout": {"padding": 24}, "font": {"size": 36}}},
        "source_render_method": "pptx_source",
        "source_render_warnings": [],
        "source_render_details": [{"code": "source_ok"}],
        "source_render_dependency_report": {"renderer": "libreoffice"},
        "part_path": f"{project_id}/parts/part_{index + 1:03d}.mp4",
        "slide_path": f"{project_id}/images/slide_{index + 1:03d}.png",
        "tts_audio_path": f"{project_id}/audio/slide_{index + 1:03d}.mp3",
        "avatar_segment_rel_path": f"{project_id}/avatar_segments/avatar_{index + 1:03d}.mp4" if index == 1 else "",
        "avatar_attempted": index == 1,
        "avatar_applied": index == 1,
        "avatar_status": "ready" if index == 1 else "none",
        "avatar_engine_used": "musetalk" if index == 1 else "none",
    }


def _playback_assets(project_id: int = 42) -> dict:
    return {
        "mp4_rel_path": f"{project_id}/{project_id}.mp4",
        "slides": [f"{project_id}/images/slide_001.png", f"{project_id}/images/slide_002.png"],
        "transcript": ["Narration one", "Narration two"],
        "tts_audio": [f"{project_id}/audio/slide_001.mp3", f"{project_id}/audio/slide_002.mp3"],
        "avatar_clips": ["", f"{project_id}/avatar_segments/avatar_002.mp4"],
        "tts_normalization": [
            {"index": 0, "page_key": "s1-p1", "project_tts_settings": {"provider_preference": "gtts"}},
            {"index": 1, "page_key": "s2-p1", "project_tts_settings": {"provider_preference": "gtts"}},
        ],
        "avatar": {"default_position": "top-right", "default_size": "medium"},
        "avatar_slide_metadata": [
            {"index": 0, "page_key": "s1-p1", "avatar_status": "none"},
            {
                "index": 1,
                "page_key": "s2-p1",
                "avatar_status": "ready",
                "avatar_segment_rel_path": f"{project_id}/avatar_segments/avatar_002.mp4",
            },
        ],
        "source_render_metadata": [
            {"index": 0, "page_key": "s1-p1", "method": "pptx_source", "warnings": [], "details": []},
            {"index": 1, "page_key": "s2-p1", "method": "pptx_source", "warnings": [], "details": []},
        ],
        "final_segments": [
            {
                "index": 0,
                "page_key": "s1-p1",
                "transcript": "Narration one",
                "tts_audio": f"{project_id}/audio/slide_001.mp3",
                "slide": f"{project_id}/images/slide_001.png",
                "part_rel_path": f"{project_id}/parts/part_001.mp4",
                "duration": 2.0,
                "pause_seconds": 0.25,
                "source_render_method": "pptx_source",
                "source_render_dependency_report": {"renderer": "libreoffice"},
            },
            {
                "index": 1,
                "page_key": "s2-p1",
                "transcript": "Narration two",
                "tts_audio": f"{project_id}/audio/slide_002.mp3",
                "avatar_clip": f"{project_id}/avatar_segments/avatar_002.mp4",
                "slide": f"{project_id}/images/slide_002.png",
                "part_rel_path": f"{project_id}/parts/part_002.mp4",
                "duration": 3.0,
                "pause_seconds": 0.25,
                "source_render_method": "pptx_source",
                "source_render_dependency_report": {"renderer": "libreoffice"},
            },
        ],
    }


def _two_page_manifest(project_id: int = 42) -> dict:
    first = _render_result(index=0, page_key="s1-p1", display_text="Visible one", narration_text="Narration one")
    second = _render_result(index=1, page_key="s2-p1", display_text="Visible two", narration_text="Narration two")
    return build_partial_render_manifest(
        project_id=project_id,
        job_id=77,
        ordered_results=[first, second],
        playback_assets=_playback_assets(project_id),
        avatar_options={"enabled": True, "teacher_id": 9},
    )


def _classification_page(
    *,
    page_key: str = "s1-p1",
    classification: str = "unchanged",
    reasons: list[str] | None = None,
    index: int = 0,
) -> dict:
    return {
        "page_key": page_key,
        "index": index,
        "old_index": index,
        "classification": classification,
        "reasons": list(reasons if reasons is not None else ([] if classification == "unchanged" else [classification])),
        "changed_hashes": [],
        "missing_artifacts": ["tts_audio"] if classification == "missing_artifact" else [],
        "requires_full": classification in {"unknown_requires_full", "structural_changed"},
    }


def _classification_result(*pages: dict, global_reasons: list[str] | None = None) -> dict:
    return {
        "version": 1,
        "old_manifest_hash": "sha256:old",
        "expected_manifest_hash": "sha256:expected",
        "old_sequence_hash": "sha256:old-sequence",
        "expected_sequence_hash": "sha256:expected-sequence",
        "global_reasons": list(global_reasons or []),
        "summary": {},
        "pages": {str(page["page_key"]): page for page in pages},
    }


def test_hash_helpers_are_stable():
    left = {"b": [2, 1], "a": {"z": True, "m": None}}
    right = {"a": {"m": None, "z": True}, "b": [2, 1]}

    assert canonical_json(left) == canonical_json(right)
    assert stable_hash(left) == stable_hash(right)
    assert normalize_text("  Alpha \r\nBeta  ") == "Alpha\nBeta"
    assert stable_hash(normalize_text("Alpha\r\nBeta")) == stable_hash(normalize_text("Alpha\nBeta"))


def test_tts_input_identity_uses_effective_spoken_text_for_raw_and_finalized_shapes():
    raw_narration = "Second slide narration with TTS-settings terminology."
    finalized_spoken = "Second slide narration with T T S-settings terminology."
    raw_settings = default_project_tts_settings()

    expected_input = canonicalize_tts_input(
        raw_narration,
        tts_settings=raw_settings,
        effective_language="en",
    )
    finalized_input = canonicalize_tts_input(
        raw_narration,
        tts_settings=worker_tasks._summarize_tts_settings(raw_settings),
        effective_language="en",
        spoken_text=finalized_spoken,
    )

    assert expected_input == finalized_input
    assert expected_input == {"language": "en", "spoken_text": finalized_spoken}
    assert stable_hash(raw_narration) != stable_hash(expected_input)


def test_tts_input_identity_changes_for_effective_normalization_and_overrides():
    baseline = canonicalize_tts_input("TTS settings use API and ChatGPT pipeline.", effective_language="en")
    disabled_settings = {**default_project_tts_settings(), "normalization_enabled": False}
    abbreviation_settings = {
        **default_project_tts_settings(),
        "overrides": {"abbreviation": {"API": "application programming interface"}},
    }
    technical_settings = {
        **default_project_tts_settings(),
        "overrides": {"technical": {"pipeline": "pipe line"}},
    }
    mixed_word_settings = {
        **default_project_tts_settings(),
        "overrides": {"mixed_word": {"ChatGPT": "chat jee pee tee"}},
    }

    assert canonicalize_tts_input(
        "TTS settings use API and ChatGPT pipeline.",
        tts_settings=disabled_settings,
        effective_language="en",
    ) != baseline
    assert canonicalize_tts_input(
        "TTS settings use API and ChatGPT pipeline.",
        tts_settings=abbreviation_settings,
        effective_language="en",
    ) != baseline
    assert canonicalize_tts_input(
        "TTS settings use API and ChatGPT pipeline.",
        tts_settings=technical_settings,
        effective_language="en",
    ) != baseline
    assert canonicalize_tts_input(
        "TTS settings use API and ChatGPT pipeline.",
        tts_settings=mixed_word_settings,
        effective_language="en",
    ) != baseline
    assert canonicalize_tts_input("ASP ve Pipeline açıklaması.", effective_language="tr") != canonicalize_tts_input(
        "ASP ve Pipeline açıklaması.",
        effective_language="en",
    )


def test_tts_input_identity_does_not_double_normalize_finalized_spoken_text():
    finalized = canonicalize_tts_input(
        "TTS-settings",
        effective_language="en",
        spoken_text="T T S-settings",
    )

    assert finalized == {"language": "en", "spoken_text": "T T S-settings"}


def test_tts_input_identity_handles_malformed_values_deterministically():
    malformed_settings = {
        "normalization_enabled": True,
        "overrides": {"technical": {"API": {"replacement": "A P I"}}},
    }

    left = canonicalize_tts_input(
        ["TTS", "settings"],
        tts_settings=malformed_settings,
        effective_language={"language": "en"},
    )
    right = canonicalize_tts_input(
        ["TTS", "settings"],
        tts_settings=deepcopy(malformed_settings),
        effective_language={"language": "en"},
    )

    assert left == right
    assert left["language"] == "en"
    assert isinstance(left["spoken_text"], str)


def test_fresh_project_and_finalized_tts_settings_share_semantic_identity():
    fresh_settings = default_project_tts_settings()
    finalized_settings = {
        "provider_preference": "auto",
        "normalization_enabled": True,
        "normalization_mode": "loose",
        "unknown_word_strategy": "keep",
        "speech_speed": 1.0,
        "volume_gain_db": 0,
        "pause_seconds": None,
        "applied_overrides": {
            "technical_count": 0,
            "abbreviation_count": 0,
            "mixed_word_count": 0,
            "merged_override_count": 0,
        },
    }

    canonical_fresh = canonicalize_tts_settings(fresh_settings)
    canonical_finalized = canonicalize_tts_settings(finalized_settings)

    assert canonical_fresh == canonical_finalized
    assert stable_hash(canonical_fresh) == "sha256:be86cb0453b1e6efe4fa2f7a7e6453179ecc6889ebb2aa64ff28378070c1df51"


@pytest.mark.parametrize(
    "override_shape",
    [
        None,
        {},
        {"technical": {}, "abbreviation": {}, "mixed_word": {}},
    ],
)
def test_missing_and_empty_tts_override_containers_are_equivalent(override_shape):
    settings = default_project_tts_settings()
    if override_shape is None:
        settings.pop("overrides")
    else:
        settings["overrides"] = override_shape
    settings["page_overrides"] = {}
    settings["voice_overrides"] = {}
    settings["provider_overrides"] = {}
    settings["speech_speed"] = "1.0"

    canonical = canonicalize_tts_settings(settings)

    assert canonical == canonicalize_tts_settings(
        {
            **default_project_tts_settings(),
            "applied_overrides": {
                "technical_count": "0",
                "abbreviation_count": 0,
                "mixed_word_count": 0.0,
                "merged_override_count": 0,
            },
        }
    )
    assert "effective_overrides_hash" not in canonical
    assert canonical == canonicalize_tts_settings(
        {
            **default_project_tts_settings(),
            "overrides": {"technical": {"API": "", "": "A P I", "CPU": {"replacement": "C P U"}}},
            "applied_overrides": {
                "technical_count": 0,
                "abbreviation_count": 0,
                "mixed_word_count": 0,
                "merged_override_count": 0,
            },
        }
    )


def test_nonempty_tts_override_values_have_stable_detectable_identity():
    first = default_project_tts_settings()
    first["overrides"]["technical"] = {"API": "A P I"}
    equivalent = deepcopy(first)
    changed = deepcopy(first)
    changed["overrides"]["technical"]["API"] = "application programming interface"

    canonical_first = canonicalize_tts_settings(first)
    canonical_equivalent = canonicalize_tts_settings(equivalent)
    canonical_changed = canonicalize_tts_settings(changed)

    assert canonical_first["applied_overrides"] == {
        "technical_count": 1,
        "abbreviation_count": 0,
        "mixed_word_count": 0,
        "merged_override_count": 1,
    }
    assert canonical_first["effective_overrides_hash"].startswith("sha256:")
    assert canonicalize_tts_settings(canonical_first) == canonical_first
    assert stable_hash(canonical_first) == stable_hash(canonical_equivalent)
    assert stable_hash(canonical_first) != stable_hash(canonical_changed)


def test_tts_override_fingerprint_excludes_paths_secrets_and_transient_metadata():
    baseline = {
        **default_project_tts_settings(),
        "page_overrides": {
            "s1-p1": {
                "speech_speed": 1.1,
                "voice_id": "voice-1",
                "voice_sample_path": "/storage/private/sample.wav",
                "api_key": "not-semantic",
                "generated_at": "2026-07-02T10:00:00Z",
                "job_id": 100,
            }
        },
    }
    transient_changes = deepcopy(baseline)
    transient_changes["page_overrides"]["s1-p1"].update(
        {
            "voice_sample_path": "/different/private/sample.wav",
            "api_key": "different-secret",
            "generated_at": "2026-07-03T10:00:00Z",
            "job_id": 101,
        }
    )
    semantic_change = deepcopy(baseline)
    semantic_change["page_overrides"]["s1-p1"]["speech_speed"] = 1.2

    canonical = canonicalize_tts_settings(baseline)

    assert canonical["effective_overrides_hash"].startswith("sha256:")
    assert canonical == canonicalize_tts_settings(transient_changes)
    assert canonical != canonicalize_tts_settings(semantic_change)
    assert "/storage/private/sample.wav" not in json.dumps(canonical)
    assert "not-semantic" not in json.dumps(canonical)
    assert "effective_overrides_hash" not in canonicalize_tts_settings(
        {"effective_overrides_hash": "/storage/private/sample.wav"}
    )


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("provider_preference", "gtts"),
        ("normalization_mode", "strict"),
        ("unknown_word_strategy", "phonetic"),
        ("model", "xtts-v3"),
        ("voice_id", "voice-2"),
        ("speech_speed", 1.15),
        ("volume_gain_db", 3),
        ("pitch", 1.2),
        ("style", "conversational"),
        ("emotion", "calm"),
        ("temperature", 0.7),
        ("language", "tr"),
    ],
)
def test_output_affecting_tts_settings_change_semantic_identity(field, changed_value):
    baseline = {
        **default_project_tts_settings(),
        "model": "xtts-v2",
        "voice_id": "voice-1",
        "language": "en",
    }
    changed = {**baseline, field: changed_value}

    assert stable_hash(canonicalize_tts_settings(baseline)) != stable_hash(
        canonicalize_tts_settings(changed)
    )


def test_tts_setting_aliases_and_malformed_values_normalize_deterministically():
    assert canonicalize_tts_settings({"rate": "1.15"}) == canonicalize_tts_settings(
        {"speech_speed": 1.15}
    )
    assert canonicalize_tts_settings({"volume": "2"}) == canonicalize_tts_settings(
        {"volume_gain_db": 2}
    )
    assert canonicalize_tts_settings(
        {
            "provider_preference": "silent",
            "normalization_mode": "medium",
            "unknown_word_strategy": "spell",
        }
    ) == canonicalize_tts_settings(
        {
            "provider_preference": "auto",
            "normalization_mode": "loose",
            "unknown_word_strategy": "keep",
        }
    )

    malformed_left = {
        "provider_preference": {"secondary": "gtts", "primary": "auto"},
        "model": ["xtts-v2"],
        "overrides": {"technical": {"API": {"b": 2, "a": 1}}},
    }
    malformed_right = {
        "provider_preference": {"primary": "auto", "secondary": "gtts"},
        "model": ["xtts-v2"],
        "overrides": {"technical": {"API": {"a": 1, "b": 2}}},
    }

    assert canonicalize_tts_settings(malformed_left) == canonicalize_tts_settings(malformed_right)
    assert worker_tasks._summarize_tts_settings(["invalid"]) == canonicalize_tts_settings(None)


def test_manifest_shape_and_hash_dependencies():
    project_id = 42
    first = _render_result(index=0, page_key="s1-p1", display_text="Visible one", narration_text="Narration one")
    second = _render_result(index=1, page_key="s2-p1", display_text="Visible two", narration_text="Narration two")
    manifest = build_partial_render_manifest(
        project_id=project_id,
        job_id=77,
        ordered_results=[first, second],
        playback_assets=_playback_assets(project_id),
        avatar_options={"enabled": True, "teacher_id": 9},
    )

    assert manifest["version"] == 1
    assert manifest["hash_semantics_version"] == 2
    assert manifest["project_id"] == project_id
    assert manifest["job_id"] == 77
    assert list(manifest["pages"]) == ["s1-p1", "s2-p1"]
    assert manifest["pages"]["s1-p1"]["artifacts"] == {
        "tts_audio": "42/audio/slide_001.mp3",
        "avatar_clip": "",
        "composed_segment": "42/parts/part_001.mp4",
        "slide_image": "42/images/slide_001.png",
    }
    assert manifest["pages"]["s2-p1"]["artifacts"]["avatar_clip"] == "42/avatar_segments/avatar_002.mp4"
    assert manifest["pages"]["s1-p1"]["duration"] == 2.0
    assert manifest["pages"]["s1-p1"]["pause_seconds"] == 0.25
    assert manifest["pages"]["s1-p1"]["invalidation_reasons"] == []

    reordered = build_partial_render_manifest(
        project_id=project_id,
        job_id=77,
        ordered_results=[second, first],
        playback_assets=_playback_assets(project_id),
    )
    display_changed = build_partial_render_manifest(
        project_id=project_id,
        job_id=77,
        ordered_results=[
            {**first, "display_text": "Visible one edited"},
            second,
        ],
        playback_assets=_playback_assets(project_id),
    )
    narration_changed = build_partial_render_manifest(
        project_id=project_id,
        job_id=77,
        ordered_results=[
            {**first, "text": "Narration one edited", "narration_text": "Narration one edited"},
            second,
        ],
        playback_assets=_playback_assets(project_id),
    )

    assert reordered["sequence_hash"] != manifest["sequence_hash"]
    assert display_changed["pages"]["s1-p1"]["display_text_hash"] != manifest["pages"]["s1-p1"]["display_text_hash"]
    assert display_changed["pages"]["s1-p1"]["narration_text_hash"] == manifest["pages"]["s1-p1"]["narration_text_hash"]
    assert (
        narration_changed["pages"]["s1-p1"]["narration_text_hash"]
        != manifest["pages"]["s1-p1"]["narration_text_hash"]
    )


def test_output_duration_is_metadata_not_structural_identity_but_pause_remains_structural():
    baseline_result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Visible one",
        narration_text="Narration one",
    )
    baseline = build_partial_render_manifest(
        project_id=42,
        ordered_results=[baseline_result],
        playback_assets={"final_segments": [{**baseline_result, "page_key": "s1-p1"}]},
        avatar_options={"enabled": False},
    )
    duration_changed_result = {
        **baseline_result,
        "text": "Narration one edited",
        "narration_text": "Narration one edited",
        "spoken_text": "Narration one edited",
        "subtitle_chunks": ["Narration one edited"],
        "duration": 7.5,
    }
    duration_changed = build_partial_render_manifest(
        project_id=42,
        ordered_results=[duration_changed_result],
        playback_assets={"final_segments": [{**duration_changed_result, "page_key": "s1-p1"}]},
        avatar_options={"enabled": False},
    )
    pause_changed_result = {**duration_changed_result, "pause_seconds": 0.75}
    pause_changed = build_partial_render_manifest(
        project_id=42,
        ordered_results=[pause_changed_result],
        playback_assets={"final_segments": [{**pause_changed_result, "page_key": "s1-p1"}]},
        avatar_options={"enabled": False},
    )

    assert baseline["pages"]["s1-p1"]["duration"] == 2.0
    assert duration_changed["pages"]["s1-p1"]["duration"] == 7.5
    assert baseline["pages"]["s1-p1"]["structural_hash"] == duration_changed["pages"]["s1-p1"]["structural_hash"]
    assert baseline["sequence_hash"] == duration_changed["sequence_hash"]
    assert duration_changed["pages"]["s1-p1"]["structural_hash"] != pause_changed["pages"]["s1-p1"]["structural_hash"]
    assert duration_changed["sequence_hash"] != pause_changed["sequence_hash"]
    duration_report = classify_partial_render_changes(
        old_manifest=baseline,
        expected_manifest=duration_changed,
        required_artifacts=(),
    )
    pause_report = classify_partial_render_changes(
        old_manifest=duration_changed,
        expected_manifest=pause_changed,
        required_artifacts=(),
    )
    assert "structural_changed" not in duration_report["pages"]["s1-p1"]["reasons"]
    assert duration_report["global_reasons"] == []
    assert pause_report["pages"]["s1-p1"]["classification"] == "structural_changed"
    assert pause_report["global_reasons"] == ["structural_changed"]


def test_old_hash_semantics_are_rejected_conservatively():
    old_manifest = _two_page_manifest()
    old_manifest.pop("hash_semantics_version")

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=_two_page_manifest(),
    )

    assert report["global_reasons"] == ["unknown_requires_full"]
    assert all(page["requires_full"] for page in report["pages"].values())


def test_expected_and_finalized_txt_defaults_share_layout_and_language_identity():
    finalized_result = {
        **_render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible one",
            narration_text="Narration one",
        ),
        "spoken_text": "Narration one.",
        "whiteboard_mode": True,
        "scene_background_mode": "whiteboard",
        "scene_background_fit": None,
        "scene_text_scale": None,
        "editor_document": {},
        "tts_normalization_language": "en",
        "avatar_engine_used": "none",
    }
    finalized = build_partial_render_manifest(
        project_id=42,
        ordered_results=[finalized_result],
        playback_assets={"final_segments": [{**finalized_result, "page_key": "s1-p1"}]},
        avatar_options={"enabled": False, "requested": "0"},
    )
    expected_slide = {
        "index": 0,
        "page_key": "s1-p1",
        "source_slide_index": 0,
        "split_index": 0,
        "display_text": "Visible one",
        "narration_text": "Narration one",
        "subtitle_chunks": ["Narration one"],
        "whiteboard_mode": True,
        "scene_background_mode": "whiteboard",
        "scene_background_fit": "contain",
        "scene_text_scale": 1.0,
        "editor_document": {
            "scene": {
                "source_type": "txt",
                "source_background_generated": False,
                "background_mode": "whiteboard",
                "background_fit": "contain",
                "text_scale": 1.0,
            }
        },
    }
    expected = build_expected_partial_render_manifest(
        project_id=42,
        slides=[expected_slide],
        previous_playback_assets={"partial_render_manifest": finalized},
        tts_settings={"provider_preference": "gtts", "speech_speed": 1.05},
        avatar_options={"enabled": 0},
        effective_language="EN",
    )

    assert finalized["pages"]["s1-p1"]["layout_hash"] == expected["pages"]["s1-p1"]["layout_hash"]
    assert finalized["pages"]["s1-p1"]["tts_input_hash"] == expected["pages"]["s1-p1"]["tts_input_hash"]
    assert finalized["pages"]["s1-p1"]["avatar_input_hash"] == expected["pages"]["s1-p1"]["avatar_input_hash"]

    edited_layout = build_expected_partial_render_manifest(
        project_id=42,
        slides=[
            {
                **expected_slide,
                "scene_text_scale": 1.25,
                "editor_document": {
                    "scene": {
                        **expected_slide["editor_document"]["scene"],
                        "text_scale": 1.25,
                        "overlay_layout": {"padding": 32},
                    }
                },
            }
        ],
        previous_playback_assets={"partial_render_manifest": finalized},
        tts_settings={"provider_preference": "gtts", "speech_speed": 1.05},
        avatar_options={"enabled": False},
        effective_language="en",
    )
    changed_language = build_expected_partial_render_manifest(
        project_id=42,
        slides=[expected_slide],
        previous_playback_assets={"partial_render_manifest": finalized},
        tts_settings={"provider_preference": "gtts", "speech_speed": 1.05},
        avatar_options={"enabled": False},
        effective_language="tr",
    )

    assert edited_layout["pages"]["s1-p1"]["layout_hash"] != expected["pages"]["s1-p1"]["layout_hash"]
    assert changed_language["pages"]["s1-p1"]["tts_input_hash"] != expected["pages"]["s1-p1"]["tts_input_hash"]
    language_report = classify_partial_render_changes(
        old_manifest=expected,
        expected_manifest=changed_language,
        required_artifacts=(),
    )
    assert language_report["pages"]["s1-p1"]["classification"] == "tts_input_changed"


@pytest.mark.parametrize("malformed_scene", ["invalid", ["invalid"], 7])
def test_layout_canonicalization_treats_malformed_scene_as_safe_default(malformed_scene):
    result = {
        **_render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible one",
            narration_text="Narration one",
        ),
        "scene_background_fit": None,
        "scene_text_scale": None,
        "editor_document": {"scene": malformed_scene},
    }
    manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[result],
        avatar_options={"enabled": False},
    )
    default_manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[{**result, "editor_document": {}}],
        avatar_options={"enabled": False},
    )

    assert manifest["pages"]["s1-p1"]["layout_hash"] == default_manifest["pages"]["s1-p1"]["layout_hash"]


@pytest.mark.parametrize("disabled_value", [False, 0, "0", "false", None])
def test_avatar_disabled_representations_hash_identically(disabled_value):
    result = _without_avatar(
        _render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible one",
            narration_text="Narration one",
        )
    )
    baseline = build_partial_render_manifest(
        project_id=42,
        ordered_results=[result],
        avatar_options={"enabled": False, "requested": False},
    )
    candidate = build_partial_render_manifest(
        project_id=42,
        ordered_results=[{**result, "avatar_engine_used": "cached"}],
        avatar_options={"enabled": disabled_value, "requested": disabled_value},
    )
    enabled = build_partial_render_manifest(
        project_id=42,
        ordered_results=[result],
        avatar_options={"enabled": True, "requested": True},
    )

    assert candidate["pages"]["s1-p1"]["avatar_input_hash"] == baseline["pages"]["s1-p1"]["avatar_input_hash"]
    assert enabled["pages"]["s1-p1"]["avatar_input_hash"] != baseline["pages"]["s1-p1"]["avatar_input_hash"]


def test_manifest_fallback_page_key_and_targeted_merge_artifact_stability():
    unchanged = _render_result(index=0, page_key="s1-p1", display_text="Visible one", narration_text="Narration one")
    changed = _render_result(index=1, page_key=None, display_text="Visible two", narration_text="Narration two")
    playback_assets = _playback_assets(42)
    playback_assets["final_segments"][1].pop("page_key")
    manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[unchanged, changed],
        playback_assets=playback_assets,
    )
    changed_narration = build_partial_render_manifest(
        project_id=42,
        ordered_results=[
            unchanged,
            {**changed, "text": "Narration two changed", "narration_text": "Narration two changed"},
        ],
        playback_assets=playback_assets,
    )

    assert "slide:1" in manifest["pages"]
    assert manifest["pages"]["s1-p1"]["artifacts"] == changed_narration["pages"]["s1-p1"]["artifacts"]
    assert (
        manifest["pages"]["slide:1"]["narration_text_hash"]
        != changed_narration["pages"]["slide:1"]["narration_text_hash"]
    )


def test_classifier_identical_manifests_are_unchanged_even_when_job_hash_changes():
    old_manifest = _two_page_manifest()
    expected_manifest = deepcopy(old_manifest)
    expected_manifest["job_id"] = 999
    expected_manifest["manifest_hash"] = stable_hash(
        {key: value for key, value in expected_manifest.items() if key != "manifest_hash"}
    )

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=expected_manifest,
    )

    assert report["summary"]["unchanged"] == 2
    assert report["summary"]["unknown_requires_full"] == 0
    assert report["pages"]["s1-p1"]["classification"] == "unchanged"
    assert report["pages"]["s2-p1"]["classification"] == "unchanged"


@pytest.mark.parametrize(
    ("hash_key", "reason", "requires_full"),
    [
        ("display_text_hash", "display_text_changed", False),
        ("narration_text_hash", "narration_text_changed", False),
        ("subtitle_text_hash", "subtitle_text_changed", False),
        ("tts_input_hash", "tts_input_changed", False),
        ("tts_settings_hash", "tts_settings_changed", False),
        ("avatar_input_hash", "avatar_input_changed", False),
        ("avatar_display_hash", "avatar_display_changed", False),
        ("background_hash", "background_changed", False),
        ("layout_hash", "layout_changed", False),
        ("structural_hash", "structural_changed", True),
        ("source_render_hash", "structural_changed", True),
    ],
)
def test_classifier_maps_hash_fields_to_reasons(hash_key, reason, requires_full):
    old_manifest = _two_page_manifest()
    expected_manifest = deepcopy(old_manifest)
    expected_manifest["pages"]["s1-p1"][hash_key] = stable_hash({"changed": hash_key})

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=expected_manifest,
    )
    page = report["pages"]["s1-p1"]

    assert page["classification"] == reason
    assert page["reasons"] == [reason]
    assert page["changed_hashes"] == [hash_key]
    assert page["requires_full"] is requires_full


def _single_page_avatar_layout_manifest(
    *,
    slide_layout: dict | None = None,
    publisher_layout: dict | None = None,
) -> dict:
    result = _render_result(index=0, page_key="s1-p1", display_text="Visible one", narration_text="Narration one")
    scene = dict(result["editor_document"]["scene"])
    if slide_layout is not None:
        scene["avatar_layout"] = slide_layout
    result["editor_document"] = {"scene": scene}
    return build_partial_render_manifest(
        project_id=42,
        job_id=77,
        ordered_results=[result],
        playback_assets=_playback_assets(42),
        avatar_options={
            "enabled": True,
            "teacher_id": 9,
            "publisher_avatar_layout": publisher_layout or {"position": "top-right", "size": "medium", "visible": True},
        },
    )


def test_avatar_layout_identity_uses_effective_values_not_override_source():
    inherited = _single_page_avatar_layout_manifest()
    explicit_same = _single_page_avatar_layout_manifest(slide_layout={"position": "top-right"})
    malformed = _single_page_avatar_layout_manifest(slide_layout={"position": "center", "size": "giant", "visible": "maybe"})

    inherited_page = inherited["pages"]["s1-p1"]
    explicit_page = explicit_same["pages"]["s1-p1"]
    malformed_page = malformed["pages"]["s1-p1"]

    assert explicit_page["avatar_display_hash"] == inherited_page["avatar_display_hash"]
    assert explicit_page["avatar_input_hash"] == inherited_page["avatar_input_hash"]
    assert malformed_page["avatar_display_hash"] == inherited_page["avatar_display_hash"]
    assert malformed_page["avatar_input_hash"] == inherited_page["avatar_input_hash"]
    assert explicit_page["narration_text_hash"] == inherited_page["narration_text_hash"]
    assert explicit_page["subtitle_text_hash"] == inherited_page["subtitle_text_hash"]


@pytest.mark.parametrize(
    "slide_layout",
    [
        {"position": "bottom-left"},
        {"size": "large"},
        {"visible": False},
    ],
)
def test_avatar_layout_changes_classify_as_avatar_display_only(slide_layout):
    old_manifest = _single_page_avatar_layout_manifest()
    expected_manifest = _single_page_avatar_layout_manifest(slide_layout=slide_layout)

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=expected_manifest,
    )
    page = report["pages"]["s1-p1"]

    assert page["classification"] == "avatar_display_changed"
    assert page["changed_hashes"] == ["avatar_display_hash"]
    assert expected_manifest["pages"]["s1-p1"]["tts_input_hash"] == old_manifest["pages"]["s1-p1"]["tts_input_hash"]
    assert expected_manifest["pages"]["s1-p1"]["tts_settings_hash"] == old_manifest["pages"]["s1-p1"]["tts_settings_hash"]
    assert expected_manifest["pages"]["s1-p1"]["layout_hash"] == old_manifest["pages"]["s1-p1"]["layout_hash"]
    assert expected_manifest["pages"]["s1-p1"]["narration_text_hash"] == old_manifest["pages"]["s1-p1"]["narration_text_hash"]
    assert expected_manifest["pages"]["s1-p1"]["subtitle_text_hash"] == old_manifest["pages"]["s1-p1"]["subtitle_text_hash"]


def test_avatar_layout_publisher_default_changes_only_inheriting_slides():
    inherited_old = _single_page_avatar_layout_manifest(
        publisher_layout={"position": "top-right", "size": "medium", "visible": True}
    )
    inherited_new = _single_page_avatar_layout_manifest(
        publisher_layout={"position": "top-left", "size": "medium", "visible": True}
    )
    explicit_old = _single_page_avatar_layout_manifest(
        slide_layout={"position": "top-right"},
        publisher_layout={"position": "top-right", "size": "medium", "visible": True},
    )
    explicit_new = _single_page_avatar_layout_manifest(
        slide_layout={"position": "top-right"},
        publisher_layout={"position": "top-left", "size": "medium", "visible": True},
    )

    assert inherited_old["pages"]["s1-p1"]["avatar_display_hash"] != inherited_new["pages"]["s1-p1"]["avatar_display_hash"]
    assert explicit_old["pages"]["s1-p1"]["avatar_display_hash"] == explicit_new["pages"]["s1-p1"]["avatar_display_hash"]
    assert explicit_old["pages"]["s1-p1"]["avatar_input_hash"] == explicit_new["pages"]["s1-p1"]["avatar_input_hash"]


@pytest.mark.parametrize("old_manifest", [None, {"version": "legacy", "pages": []}])
def test_classifier_missing_or_invalid_old_manifest_requires_full(old_manifest):
    expected_manifest = _two_page_manifest()

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=expected_manifest,
    )

    assert report["global_reasons"] == ["unknown_requires_full"]
    assert report["summary"]["unknown_requires_full"] == 2
    assert all(page["requires_full"] for page in report["pages"].values())


def test_classifier_page_membership_changes_are_structural():
    first = _render_result(index=0, page_key="s1-p1", display_text="Visible one", narration_text="Narration one")
    second = _render_result(index=1, page_key="s2-p1", display_text="Visible two", narration_text="Narration two")
    third = _render_result(index=2, page_key="s3-p1", display_text="Visible three", narration_text="Narration three")
    old_manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[first, second],
        playback_assets=_playback_assets(42),
    )
    added_manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[first, second, third],
        playback_assets=_playback_assets(42),
    )
    deleted_manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[first],
        playback_assets=_playback_assets(42),
    )

    added_report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=added_manifest,
    )
    deleted_report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=deleted_manifest,
    )

    assert added_report["pages"]["s3-p1"]["classification"] == "structural_changed"
    assert added_report["pages"]["s3-p1"]["requires_full"] is True
    assert deleted_report["pages"]["s2-p1"]["classification"] == "structural_changed"
    assert deleted_report["pages"]["s2-p1"]["requires_full"] is True


def test_classifier_reordered_pages_are_structural():
    first = _render_result(index=0, page_key="s1-p1", display_text="Visible one", narration_text="Narration one")
    second = _render_result(index=1, page_key="s2-p1", display_text="Visible two", narration_text="Narration two")
    old_manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[first, second],
        playback_assets=_playback_assets(42),
    )
    reordered_manifest = build_partial_render_manifest(
        project_id=42,
        ordered_results=[second, first],
        playback_assets=_playback_assets(42),
    )

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=reordered_manifest,
    )

    assert report["global_reasons"] == ["structural_changed"]
    assert report["pages"]["s1-p1"]["classification"] == "structural_changed"
    assert report["pages"]["s2-p1"]["classification"] == "structural_changed"


def test_classifier_missing_artifacts_do_not_require_full_by_themselves():
    old_manifest = _two_page_manifest()
    expected_manifest = deepcopy(old_manifest)
    expected_manifest["pages"]["s1-p1"]["artifacts"]["tts_audio"] = ""

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=expected_manifest,
    )
    page = report["pages"]["s1-p1"]

    assert page["classification"] == "missing_artifact"
    assert page["reasons"] == ["missing_artifact"]
    assert page["missing_artifacts"] == ["tts_audio"]
    assert page["requires_full"] is False


def test_classifier_orders_multiple_reasons_counts_summary_and_does_not_mutate_inputs():
    old_manifest = _two_page_manifest()
    expected_manifest = deepcopy(old_manifest)
    expected_manifest["pages"]["s1-p1"]["display_text_hash"] = stable_hash("display changed")
    expected_manifest["pages"]["s1-p1"]["layout_hash"] = stable_hash("layout changed")
    expected_manifest["pages"]["s1-p1"]["artifacts"]["slide_image"] = ""
    old_before = deepcopy(old_manifest)
    expected_before = deepcopy(expected_manifest)

    report = classify_partial_render_changes(
        old_manifest=old_manifest,
        expected_manifest=expected_manifest,
    )
    page = report["pages"]["s1-p1"]

    assert page["classification"] == "display_text_changed"
    assert page["reasons"] == ["display_text_changed", "layout_changed", "missing_artifact"]
    assert page["changed_hashes"] == ["display_text_hash", "layout_hash"]
    assert report["summary"]["display_text_changed"] == 1
    assert report["summary"]["unchanged"] == 1
    assert old_manifest == old_before
    assert expected_manifest == expected_before


@pytest.mark.parametrize(
    ("classification", "reasons", "expected_action"),
    [
        ("unchanged", [], "reuse_all"),
        ("display_text_changed", ["display_text_changed"], "recompose_visual_only_future"),
        ("background_changed", ["background_changed"], "recompose_visual_only_future"),
        ("layout_changed", ["layout_changed"], "recompose_visual_only_future"),
        ("narration_text_changed", ["narration_text_changed"], "rerun_tts_avatar_future"),
        ("subtitle_text_changed", ["subtitle_text_changed"], "rerun_tts_avatar_future"),
        ("tts_input_changed", ["tts_input_changed"], "rerun_tts_avatar_future"),
        ("tts_settings_changed", ["tts_settings_changed"], "rerun_tts_avatar_future"),
        ("avatar_input_changed", ["avatar_input_changed"], "rerun_avatar_future"),
        ("avatar_display_changed", ["avatar_display_changed"], "metadata_only_future"),
        ("missing_artifact", ["missing_artifact"], "rerender_page_future"),
        ("structural_changed", ["structural_changed"], "full_rerender_required_future"),
        ("unknown_requires_full", ["unknown_requires_full"], "full_rerender_required_future"),
    ],
)
def test_partial_render_plan_maps_classifications_to_report_only_future_actions(
    classification,
    reasons,
    expected_action,
):
    plan = build_partial_render_plan(
        _classification_result(
            _classification_page(classification=classification, reasons=reasons),
            global_reasons=["unknown_requires_full"] if classification == "unknown_requires_full" else [],
        )
    )
    page = plan["pages"]["s1-p1"]

    assert plan["version"] == 1
    assert plan["mode"] == "report_only"
    assert page["recommended_action"] == expected_action
    assert page["future_only"] is True
    assert page["actual_behavior_changed"] is False
    assert plan["summary"][expected_action] == 1
    assert plan["summary"]["unknown_requires_full"] == (1 if classification == "unknown_requires_full" else 0)


def test_partial_render_plan_multiple_reasons_choose_safest_action_deterministically():
    missing_over_tts = _classification_result(
        _classification_page(
            classification="display_text_changed",
            reasons=[
                "avatar_display_changed",
                "display_text_changed",
                "tts_input_changed",
                "missing_artifact",
            ],
        )
    )
    full_over_missing = _classification_result(
        _classification_page(
            page_key="s2-p1",
            classification="structural_changed",
            reasons=["missing_artifact", "structural_changed", "display_text_changed"],
            index=1,
        )
    )

    missing_plan = build_partial_render_plan(missing_over_tts)
    rebuilt_missing_plan = build_partial_render_plan(missing_over_tts)
    full_plan = build_partial_render_plan(full_over_missing)

    assert missing_plan == rebuilt_missing_plan
    assert missing_plan["pages"]["s1-p1"]["recommended_action"] == "rerender_page_future"
    assert missing_plan["summary"]["rerender_page_future"] == 1
    assert full_plan["pages"]["s2-p1"]["recommended_action"] == "full_rerender_required_future"
    assert full_plan["summary"]["full_rerender_required_future"] == 1


def test_visual_only_recompose_eligibility_allows_only_visual_targets():
    classification = _classification_result(
        _classification_page(
            page_key="s1-p1",
            classification="display_text_changed",
            reasons=["display_text_changed", "layout_changed"],
        ),
        _classification_page(
            page_key="s2-p1",
            classification="background_changed",
            reasons=["background_changed"],
            index=1,
        ),
    )
    plan = build_partial_render_plan(classification)

    report = get_visual_only_recompose_eligibility(
        classification_result=classification,
        plan=plan,
        target_page_keys={"s2-p1", "s1-p1"},
    )

    assert report["eligible"] is True
    assert report["mode"] == "visual_only_recompose"
    assert report["target_page_keys"] == ["s1-p1", "s2-p1"]
    assert report["fallback_reasons"] == []
    assert report["pages"]["s1-p1"]["eligible"] is True
    assert report["pages"]["s2-p1"]["recommended_action"] == "recompose_visual_only_future"


def test_visual_only_recompose_eligibility_rejects_mixed_target_set():
    classification = _classification_result(
        _classification_page(
            page_key="s1-p1",
            classification="display_text_changed",
            reasons=["display_text_changed"],
        ),
        _classification_page(
            page_key="s2-p1",
            classification="tts_settings_changed",
            reasons=["tts_settings_changed"],
            index=1,
        ),
    )
    plan = build_partial_render_plan(classification)

    report = get_visual_only_recompose_eligibility(
        classification_result=classification,
        plan=plan,
        target_page_keys={"s1-p1", "s2-p1"},
    )

    assert report["eligible"] is False
    assert report["pages"]["s1-p1"]["eligible"] is True
    assert report["pages"]["s2-p1"]["eligible"] is False
    assert "target_page_action_not_visual_only" in report["fallback_reasons"]
    assert "target_page_has_non_visual_reason" in report["fallback_reasons"]


@pytest.mark.parametrize(
    "classification",
    [
        "narration_text_changed",
        "subtitle_text_changed",
        "tts_input_changed",
        "tts_settings_changed",
        "avatar_input_changed",
        "avatar_display_changed",
        "missing_artifact",
        "structural_changed",
        "unknown_requires_full",
    ],
)
def test_visual_only_recompose_eligibility_rejects_non_visual_classifications(classification):
    classification_result = _classification_result(
        _classification_page(classification=classification),
        global_reasons=["unknown_requires_full"] if classification == "unknown_requires_full" else [],
    )
    plan = build_partial_render_plan(classification_result)

    report = get_visual_only_recompose_eligibility(
        classification_result=classification_result,
        plan=plan,
        target_page_keys={"s1-p1"},
    )

    assert report["eligible"] is False
    assert report["pages"]["s1-p1"]["eligible"] is False
    assert "target_page_action_not_visual_only" in report["fallback_reasons"]


def test_narration_only_recompose_eligibility_allows_target_tts_only_with_unchanged_non_targets():
    classification = _classification_result(
        _classification_page(
            page_key="s1-p1",
            classification="narration_text_changed",
            reasons=["narration_text_changed", "subtitle_text_changed", "tts_input_changed", "avatar_input_changed"],
        ),
        _classification_page(
            page_key="s2-p1",
            classification="unchanged",
            reasons=[],
            index=1,
        ),
    )
    plan = build_partial_render_plan(classification)

    report = get_narration_only_recompose_eligibility(
        classification_result=classification,
        plan=plan,
        target_page_keys={"s1-p1"},
    )

    assert report["eligible"] is True
    assert report["mode"] == "narration_only_recompose"
    assert report["fallback_reasons"] == []
    assert report["pages"]["s1-p1"]["recommended_action"] == "rerun_tts_avatar_future"
    assert report["pages"]["s2-p1"]["recommended_action"] == "reuse_all"

    mixed_non_target = deepcopy(classification)
    mixed_non_target["pages"]["s2-p1"] = _classification_page(
        page_key="s2-p1",
        classification="tts_settings_changed",
        reasons=["tts_settings_changed"],
        index=1,
    )
    mixed_report = get_narration_only_recompose_eligibility(
        classification_result=mixed_non_target,
        plan=build_partial_render_plan(mixed_non_target),
        target_page_keys={"s1-p1"},
    )

    assert mixed_report["eligible"] is False
    assert mixed_report["pages"]["s2-p1"]["eligible"] is False
    assert "non_target_page_changed" in mixed_report["fallback_reasons"]


def test_pre_render_eligibility_log_payload_is_sanitized_and_page_specific():
    classification = _classification_result(
        _classification_page(
            page_key="s1-p1",
            classification="narration_text_changed",
            reasons=["narration_text_changed", "tts_input_changed"],
        ),
        _classification_page(
            page_key="s2-p1",
            classification="tts_settings_changed",
            reasons=["tts_settings_changed"],
            index=1,
        ),
    )
    decision = {
        "comparison_phase": "pre_render_eligibility",
        "mode": "narration_only_recompose",
        "target_page_keys": ["s1-p1"],
        "classification": classification,
        "plan": build_partial_render_plan(classification),
        "fallback_reasons": ["non_target_page_changed"],
        "artifacts_by_page_key": {
            "s1-p1": {"slide_image_abs_path": "/secret/storage/slide.png"},
        },
    }

    payload = worker_tasks._partial_render_eligibility_log_payload(decision)

    assert payload == {
        "phase": "pre_render_eligibility",
        "mode": "narration_only_recompose",
        "global_reasons": [],
        "target_pages": [
            {
                "page_key": "s1-p1",
                "classification": "narration_text_changed",
                "reasons": ["narration_text_changed", "tts_input_changed"],
                "recommended_action": "rerun_tts_avatar_future",
            }
        ],
        "changed_non_target_pages": [
            {
                "page_key": "s2-p1",
                "classification": "tts_settings_changed",
                "reasons": ["tts_settings_changed"],
                "recommended_action": "rerun_tts_avatar_future",
            }
        ],
        "fallback_reasons": ["non_target_page_changed"],
    }
    assert "secret" not in json.dumps(payload)
    assert "artifact" not in json.dumps(payload)


def test_expected_manifest_builder_uses_new_slide_inputs_and_reuses_artifacts_by_page_key_only():
    previous_playback_assets = {
        "final_segments": [
            {
                "index": 0,
                "page_key": "old-position-key",
                "tts_audio": "42/audio/old-position.mp3",
                "avatar_clip": "42/avatar_segments/old-position.mp4",
                "part_rel_path": "42/parts/old-position.mp4",
                "slide": "42/images/old-position.png",
            },
            {
                "index": 1,
                "page_key": "new-key",
                "tts_audio": "42/audio/new-key.mp3",
                "avatar_clip": "42/avatar_segments/new-key.mp4",
                "part_rel_path": "42/parts/new-key.mp4",
                "slide": "42/images/new-key.png",
            },
        ]
    }

    manifest = build_expected_partial_render_manifest(
        project_id=42,
        job_id=88,
        slides=[
            {
                "index": 0,
                "slide_num": 1,
                "page_key": "new-key",
                "page_id": 500,
                "display_text": "New display",
                "narration_text": "New narration",
                "subtitle_chunks": ["New narration"],
                "duration": 4.0,
                "pause_seconds": 0.5,
                "tts_settings": {"provider_preference": "gtts"},
            }
        ],
        previous_playback_assets=previous_playback_assets,
        avatar_options={"enabled": True, "teacher_id": 9, "default_position": "bottom-left"},
    )

    assert list(manifest["pages"]) == ["new-key"]
    page = manifest["pages"]["new-key"]
    assert page["display_text_hash"] == stable_hash("New display")
    assert page["narration_text_hash"] == stable_hash("New narration")
    assert page["artifacts"] == {
        "tts_audio": "42/audio/new-key.mp3",
        "avatar_clip": "42/avatar_segments/new-key.mp4",
        "composed_segment": "42/parts/new-key.mp4",
        "slide_image": "42/images/new-key.png",
    }


def test_legacy_final_segments_without_page_keys_restore_timing_by_stable_index():
    previous_playback_assets = _playback_assets()
    previous_playback_assets["partial_render_manifest"] = _two_page_manifest()
    for segment in previous_playback_assets["final_segments"]:
        segment.pop("page_key")
    slides = [
        {"index": 0, "page_key": "s1-p1"},
        {"index": 1, "page_key": "s2-p1"},
    ]

    enriched = worker_tasks._slides_with_previous_segment_timing(slides, previous_playback_assets)

    assert [(row["duration"], row["pause_seconds"]) for row in enriched] == [(2.0, 0.25), (3.0, 0.25)]


@pytest.mark.parametrize(
    "slides",
    [
        [{"index": 0, "page_key": "s1-p1"}],
        [
            {"index": 0, "page_key": "s2-p1"},
            {"index": 1, "page_key": "s1-p1"},
        ],
    ],
)
def test_legacy_final_segment_index_fallback_rejects_ambiguous_count_or_order(slides):
    previous_playback_assets = _playback_assets()
    previous_playback_assets["partial_render_manifest"] = _two_page_manifest()
    for segment in previous_playback_assets["final_segments"]:
        segment.pop("page_key")

    enriched = worker_tasks._slides_with_previous_segment_timing(slides, previous_playback_assets)

    assert all("duration" not in row and "pause_seconds" not in row for row in enriched)


def test_legacy_final_segment_index_fallback_requires_explicit_contiguous_indexes():
    previous_playback_assets = _playback_assets()
    previous_playback_assets["partial_render_manifest"] = _two_page_manifest()
    for segment in previous_playback_assets["final_segments"]:
        segment.pop("page_key")
    previous_playback_assets["final_segments"][1].pop("index")
    slides = [
        {"index": 0, "page_key": "s1-p1"},
        {"index": 1, "page_key": "s2-p1"},
    ]

    enriched = worker_tasks._slides_with_previous_segment_timing(slides, previous_playback_assets)

    assert all("duration" not in row and "pause_seconds" not in row for row in enriched)


def _patch_finalize_side_effects(monkeypatch, tmp_path):
    from scripts import ffmpeg_helpers

    def fake_concat_videos(_part_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")

    def fake_generate_srt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nCaption\n", encoding="utf-8")

    def fake_generate_vtt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nCaption\n", encoding="utf-8")

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_vtt_from_cues)
    monkeypatch.setattr(
        worker_tasks,
        "_package_hls_assets_for_playback",
        lambda **_kwargs: worker_tasks._hls_sidecar_payload(enabled=False, packaging_status="not_required"),
    )
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_update_transcript_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "_dispatch_claimed_render_followup_intent",
        lambda *_args, **_kwargs: {"status": "none"},
    )
    monkeypatch.setattr(worker_tasks, "_mark_project_ready_after_successful_render", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_notify_render_completed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "_schedule_lesson_intelligence_after_worker_event",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(worker_tasks, "_schedule_creator_analytics_after_worker_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "_run_auto_video_frame_audit_after_render",
        lambda *_args, **_kwargs: {"enabled": False},
    )


def _finalize_two_page_no_avatar_sidecar(tmp_path, project: Project, job: Job):
    project_root = tmp_path / str(project.id)
    results = []
    for index, (display_text, narration_text) in enumerate(
        [("Visible one", "Narration one"), ("Visible two", "Narration two")]
    ):
        result = _without_avatar(
            _render_result(
                index=index,
                page_key=f"s{index + 1}-p1",
                display_text=display_text,
                narration_text=narration_text,
                project_id=project.id,
            )
        )
        part_path = project_root / "parts" / f"part_{index + 1:03d}.mp4"
        slide_path = project_root / "images" / f"slide_{index + 1:03d}.png"
        audio_path = project_root / "audio" / f"slide_{index + 1:03d}.mp3"
        for path in (part_path, slide_path, audio_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"artifact-{index}-{path.suffix}".encode("utf-8"))
        result.update(
            {
                "part_path": str(part_path),
                "slide_path": str(slide_path),
                "tts_audio_path": str(audio_path),
            }
        )
        results.append(result)

    avatar_options = {"enabled": False, "requested": False}
    worker_tasks.concat_and_finalize.run(
        results,
        str(project.id),
        False,
        avatar_options,
        job.id,
    )
    sidecar_path = project_root / "playback_assets.json"
    return results, json.loads(sidecar_path.read_text(encoding="utf-8")), avatar_options


def _runtime_slides_from_finalized_results(results: list[dict]) -> list[dict]:
    slides = []
    for result in results:
        row = dict(result)
        row["notes_text"] = str(result.get("narration_text") or "")
        row["audio_out"] = str(result.get("tts_audio_path") or "")
        row["part_out"] = str(result.get("part_path") or "")
        row["image_path"] = str(result.get("slide_path") or "")
        for key in ("duration", "pause_seconds", "part_path", "slide_path", "tts_audio_path"):
            row.pop(key, None)
        slides.append(row)
    return slides


def _finalize_two_page_txt_sidecar(
    tmp_path,
    project: Project,
    job: Job,
    *,
    tts_settings: dict[str, Any] | None = None,
    finalized_tts_settings: dict[str, Any] | None = None,
    pause_seconds: float = 0.25,
    narration_texts: tuple[str, str] = ("Narration one", "Narration two"),
    spoken_texts: tuple[str, str] | None = None,
):
    project_root = tmp_path / str(project.id)
    avatar_options = {"enabled": False, "requested": False}
    tts_settings = dict(tts_settings or {"provider_preference": "gtts", "speech_speed": 1.0})
    finalized_tts_settings = dict(finalized_tts_settings or tts_settings)
    source_report = {"renderer": "txt_whiteboard"}
    default_scene = {
        "source_type": "txt",
        "source_background_generated": False,
        "background_mode": "whiteboard",
        "background_fit": "contain",
        "text_scale": 1.0,
    }
    results: list[dict[str, Any]] = []
    slides: list[dict[str, Any]] = []
    for index, narration_text in enumerate(narration_texts):
        page_key = f"s{index + 1}-p1"
        spoken_text = (
            spoken_texts[index]
            if spoken_texts and index < len(spoken_texts)
            else canonicalize_tts_input(
                narration_text,
                tts_settings=tts_settings,
                effective_language="en",
            )["spoken_text"]
        )
        part_path = project_root / "parts" / f"part_{index + 1:03d}.mp4"
        slide_path = project_root / "parts" / f"part_{index + 1:03d}.whiteboard.png"
        audio_path = project_root / "audio" / f"slide_{index + 1:03d}.mp3"
        for path in (part_path, slide_path, audio_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"txt-artifact-{index}-{path.suffix}".encode("utf-8"))

        results.append(
            {
                "index": index,
                "slide_num": index + 1,
                "page_key": page_key,
                "source_slide_index": index,
                "split_index": 0,
                "part_path": str(part_path),
                "duration": 2.0 + index,
                "pause_seconds": pause_seconds,
                "text": narration_text,
                "narration_text": narration_text,
                "original_text": narration_text,
                "display_text": narration_text,
                "spoken_text": spoken_text,
                "tts_normalization_language": "en",
                "tts_normalization_rules_applied": [],
                "tts_provider": "gtts",
                "tts_provider_preference": "gtts",
                "tts_normalization_enabled": True,
                "tts_normalization_mode": "loose",
                "tts_unknown_word_strategy": "keep",
                "tts_applied_overrides": {},
                "tts_fallback_used": False,
                "tts_fallback_reason": "",
                "tts_settings": finalized_tts_settings,
                "tts_preprocessing_warnings": [],
                "slide_path": str(slide_path),
                "tts_audio_path": str(audio_path),
                "subtitle_chunks": [narration_text],
                "whiteboard_mode": True,
                "scene_background_mode": "whiteboard",
                "scene_background_fit": "contain",
                "scene_text_scale": 1.0,
                "editor_document": {"scene": dict(default_scene)},
                "source_render_method": "txt_whiteboard",
                "source_render_warnings": [],
                "source_render_details": [],
                "source_render_dependency_report": source_report,
                "source_background_warnings": [],
                "source_background_details": [],
                "avatar_applied": False,
                "avatar_engine_used": "none",
                "avatar_segment_rel_path": "",
                "avatar_attempted": False,
                "avatar_skipped": False,
                "avatar_failed": False,
                "avatar_status": "none",
            }
        )
        slides.append(
            {
                "index": index,
                "slide_num": index + 1,
                "page_key": page_key,
                "source_slide_index": index,
                "split_index": 0,
                "image_path": "",
                "audio_out": str(audio_path),
                "part_out": str(part_path),
                "notes_text": narration_text,
                "narration_text": narration_text,
                "original_text": narration_text,
                "display_text": narration_text,
                "subtitle_chunks": [narration_text],
                "whiteboard_mode": True,
                "scene_background_mode": "whiteboard",
                "scene_background_fit": "contain",
                "scene_text_scale": 1.0,
                "editor_document": {"scene": dict(default_scene)},
                "source_type": "txt",
                "source_render_method": "txt_whiteboard",
                "source_render_warnings": [],
                "source_render_details": [],
                "source_render_dependency_report": source_report,
                "source_background_warnings": [],
                "source_background_details": [],
            }
        )

    worker_tasks.concat_and_finalize.run(
        results,
        str(project.id),
        False,
        avatar_options,
        job.id,
    )
    sidecar_path = project_root / "playback_assets.json"
    return results, slides, json.loads(sidecar_path.read_text(encoding="utf-8")), avatar_options, tts_settings


def test_visual_only_recompose_reuses_audio_avatar_and_replaces_only_target_part(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    project_dir = tmp_path / "42"
    audio_path = project_dir / "audio" / "slide_001.mp3"
    part_path = project_dir / "parts" / "part_001.mp4"
    other_part_path = project_dir / "parts" / "part_002.mp4"
    image_path = project_dir / "images" / "slide_001.png"
    for path, payload in (
        (audio_path, b"old-audio"),
        (part_path, b"old-part"),
        (other_part_path, b"other-part"),
        (image_path, b"image"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    calls: dict[str, Any] = {}

    def fail_synthesize(*_args, **_kwargs):
        raise AssertionError("visual-only recomposition must not call TTS fallback")

    def fake_create_slide_video(image_arg, audio_arg, output_arg, **kwargs):
        calls["image"] = image_arg
        calls["audio"] = audio_arg
        calls["output"] = output_arg
        calls["duration_sec"] = kwargs.get("duration_sec")
        Path(output_arg).write_bytes(b"new-part")

    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "apply", fail_synthesize)
    monkeypatch.setattr(worker_tasks, "render_avatar_segment", SimpleNamespace(apply=fail_synthesize))
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fake_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 3.0)
    monkeypatch.setattr(
        worker_tasks,
        "_render_visual_only_slide_image",
        lambda _slide, *, part_out: {
            "render_image_path": str(image_path),
            "notes_text_prepared": "Narration",
            "original_text": "New visible",
            "display_text": "New visible",
            "subtitle_chunks": ["Narration"],
            "whiteboard_mode": False,
            "scene_background_mode": "original",
            "source_render_warnings": [],
            "source_render_details": [],
        },
    )

    result = worker_tasks.recompose_visual_only_slide_segment.run(
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "part_out": str(part_path),
            "source_slide_index": 0,
            "split_index": 0,
            "narration_text": "Narration",
            "display_text": "New visible",
            "editor_document": {
                "scene": {
                    "avatar_layout": {
                        "position": "bottom-left",
                        "visible": False,
                    }
                }
            },
        },
        "42",
        "voice",
        0.5,
        "en",
        "service",
        {
            "enabled": False,
            "publisher_avatar_layout": {
                "position": "top-right",
                "size": "large",
                "visible": True,
            },
        },
        {"provider_preference": "gtts"},
        {
            "tts_audio": "42/audio/slide_001.mp3",
            "tts_audio_abs_path": str(audio_path),
            "avatar_clip": "42/avatar_segments/avatar_001.mp4",
        },
    )

    assert calls["audio"] == str(audio_path)
    assert calls["image"] == str(image_path)
    assert calls["duration_sec"] == 3.5
    assert part_path.read_bytes() == b"new-part"
    assert other_part_path.read_bytes() == b"other-part"
    assert result["tts_audio_path"] == str(audio_path)
    assert result["tts_provider"] == "cached"
    assert result["avatar_segment_rel_path"] == "42/avatar_segments/avatar_001.mp4"
    assert result["avatar_engine_used"] == "cached"
    assert result["avatar_layout"] == {
        "position": "bottom-left",
        "size": "large",
        "visible": False,
        "source_level": "slide",
        "sources": {
            "position": "slide",
            "size": "publisher",
            "visible": "slide",
        },
    }
    assert result["effective_avatar_layout"] == {
        "position": "bottom-left",
        "size": "large",
        "visible": False,
    }
    assert result["visual_only_recomposed"] is True
    assert list(part_path.parent.glob("*.visual-recompose.*")) == []


def test_visual_only_recompose_missing_audio_falls_back_without_success_marker(tmp_path, monkeypatch):
    class FakeFallbackResult:
        result = {
            "index": 0,
            "page_key": "s1-p1",
            "part_path": str(tmp_path / "part_001.mp4"),
            "tts_provider": "gtts",
        }

        def failed(self):
            return False

    calls: dict[str, Any] = {}

    def fake_synthesize_apply(*, args):
        calls["args"] = args
        return FakeFallbackResult()

    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "apply", fake_synthesize_apply)

    result = worker_tasks.recompose_visual_only_slide_segment.run(
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "part_out": str(tmp_path / "part_001.mp4"),
            "narration_text": "Narration",
            "display_text": "Visible",
        },
        "42",
        "voice",
        0.5,
        "en",
        "service",
        {"enabled": False},
        {"provider_preference": "gtts"},
        {"tts_audio_abs_path": str(tmp_path / "missing.mp3")},
    )

    assert result["tts_provider"] == "gtts"
    assert "visual_only_recomposed" not in result
    assert calls["args"][1] == "42"


def test_narration_only_recompose_regenerates_tts_and_reuses_cached_slide_image(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers, tts_client

    project_dir = tmp_path / "42"
    image_path = project_dir / "images" / "slide_001.png"
    audio_path = project_dir / "audio" / "slide_001.mp3"
    part_path = project_dir / "parts" / "part_001.mp4"
    for path, payload in (
        (image_path, b"cached-image"),
        (audio_path, b"old-audio"),
        (part_path, b"old-part"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    calls: dict[str, Any] = {}

    def fail_fallback(*_args, **_kwargs):
        raise AssertionError("narration-only recomposition must not call full slide render")

    def fail_visual_render(*_args, **_kwargs):
        raise AssertionError("narration-only recomposition must not render visuals")

    def fake_synthesize(_voice_id, text, output_path, **kwargs):
        calls["tts_text"] = text
        calls["tts_output"] = output_path
        calls["tts_settings"] = kwargs.get("tts_settings")
        Path(output_path).write_bytes(b"new-audio")
        return {
            "spoken_text": text,
            "provider": "fake",
            "provider_preference": "gtts",
            "tts_normalization_language": "en",
        }

    def fake_create_slide_video(image_arg, audio_arg, output_arg, **kwargs):
        calls["image"] = image_arg
        calls["audio"] = audio_arg
        calls["output"] = output_arg
        calls["duration_sec"] = kwargs.get("duration_sec")
        assert Path(audio_arg).read_bytes() == b"new-audio"
        Path(output_arg).write_bytes(b"new-part")

    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "apply", fail_fallback)
    monkeypatch.setattr(worker_tasks, "_render_visual_only_slide_image", fail_visual_render)
    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fake_synthesize)
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fake_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 4.0)
    monkeypatch.setattr(ffmpeg_helpers, "trim_trailing_silence", lambda _path: None)
    monkeypatch.setattr(worker_tasks, "WORKER_TRIM_TRAILING_SILENCE", True)

    result = worker_tasks.recompose_narration_only_slide_segment.run(
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "part_out": str(part_path),
            "audio_out": str(audio_path),
            "source_slide_index": 0,
            "split_index": 0,
            "narration_text": "New narration",
            "display_text": "Visible unchanged",
            "subtitle_chunks": ["New narration"],
        },
        "42",
        "voice",
        0.5,
        "en",
        "service",
        {"enabled": False},
        {"provider_preference": "gtts"},
        {"slide_image_abs_path": str(image_path)},
    )

    assert calls["tts_text"] == "New narration"
    assert calls["image"] == str(image_path)
    assert calls["duration_sec"] == 4.5
    assert audio_path.read_bytes() == b"new-audio"
    assert part_path.read_bytes() == b"new-part"
    assert result["narration_only_recomposed"] is True
    assert result["slide_path"] == str(image_path)
    assert result["tts_audio_path"] == str(audio_path)
    assert result["duration"] == 4.5
    assert result["avatar_status"] == "none"
    assert list(audio_path.parent.glob("*.narration-recompose.*")) == []
    assert list(part_path.parent.glob("*.narration-recompose.*")) == []


def test_narration_only_recompose_failure_keeps_outputs_and_falls_back(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers, tts_client

    project_dir = tmp_path / "42"
    image_path = project_dir / "images" / "slide_001.png"
    audio_path = project_dir / "audio" / "slide_001.mp3"
    part_path = project_dir / "parts" / "part_001.mp4"
    for path, payload in (
        (image_path, b"cached-image"),
        (audio_path, b"old-audio"),
        (part_path, b"old-part"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    class FakeFallbackResult:
        result = {
            "index": 0,
            "page_key": "s1-p1",
            "part_path": str(part_path),
            "tts_provider": "gtts",
        }

        def failed(self):
            return False

    calls: dict[str, Any] = {}

    def fake_synthesize(_voice_id, _text, output_path, **_kwargs):
        Path(output_path).write_bytes(b"temp-audio")
        return {"provider": "fake"}

    def fail_create_slide_video(*_args, **_kwargs):
        raise RuntimeError("compose failed")

    def fake_fallback_apply(*, args):
        calls["fallback_args"] = args
        return FakeFallbackResult()

    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fake_synthesize)
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fail_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 4.0)
    monkeypatch.setattr(ffmpeg_helpers, "trim_trailing_silence", lambda _path: None)
    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "apply", fake_fallback_apply)

    result = worker_tasks.recompose_narration_only_slide_segment.run(
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "part_out": str(part_path),
            "audio_out": str(audio_path),
            "narration_text": "New narration",
            "display_text": "Visible unchanged",
        },
        "42",
        "voice",
        0.5,
        "en",
        "service",
        {"enabled": False},
        {"provider_preference": "gtts"},
        {"slide_image_abs_path": str(image_path)},
    )

    assert result["tts_provider"] == "gtts"
    assert "narration_only_recomposed" not in result
    assert calls["fallback_args"][1] == "42"
    assert audio_path.read_bytes() == b"old-audio"
    assert part_path.read_bytes() == b"old-part"
    assert list(audio_path.parent.glob("*.narration-recompose.*")) == []
    assert list(part_path.parent.glob("*.narration-recompose.*")) == []


def test_narration_only_recompose_promotion_failure_restores_outputs_and_falls_back(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers, tts_client

    project_dir = tmp_path / "42"
    image_path = project_dir / "images" / "slide_001.png"
    audio_path = project_dir / "audio" / "slide_001.mp3"
    part_path = project_dir / "parts" / "part_001.mp4"
    for path, payload in (
        (image_path, b"cached-image"),
        (audio_path, b"old-audio"),
        (part_path, b"old-part"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    class FakeFallbackResult:
        result = {
            "index": 0,
            "page_key": "s1-p1",
            "part_path": str(part_path),
            "tts_provider": "gtts",
        }

        def failed(self):
            return False

    def fake_synthesize(_voice_id, _text, output_path, **_kwargs):
        Path(output_path).write_bytes(b"new-audio")
        return {"provider": "fake"}

    def fake_create_slide_video(_image_arg, _audio_arg, output_arg, **_kwargs):
        Path(output_arg).write_bytes(b"new-part")

    original_replace = Path.replace

    def fail_part_promotion(self, target):
        if self.name.startswith(".part_001.narration-recompose."):
            raise OSError("part promotion failed")
        return original_replace(self, target)

    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fake_synthesize)
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fake_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 4.0)
    monkeypatch.setattr(ffmpeg_helpers, "trim_trailing_silence", lambda _path: None)
    monkeypatch.setattr(Path, "replace", fail_part_promotion)
    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "apply", lambda *, args: FakeFallbackResult())

    result = worker_tasks.recompose_narration_only_slide_segment.run(
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "part_out": str(part_path),
            "audio_out": str(audio_path),
            "narration_text": "New narration",
            "display_text": "Visible unchanged",
        },
        "42",
        "voice",
        0.5,
        "en",
        "service",
        {"enabled": False},
        {"provider_preference": "gtts"},
        {"slide_image_abs_path": str(image_path)},
    )

    assert result["tts_provider"] == "gtts"
    assert "narration_only_recomposed" not in result
    assert audio_path.read_bytes() == b"old-audio"
    assert part_path.read_bytes() == b"old-part"
    assert list(audio_path.parent.glob("*.narration-recompose*")) == []
    assert list(part_path.parent.glob("*.narration-recompose*")) == []


@pytest.mark.parametrize(
    ("changed_result_extra", "expected_skip"),
    [
        ({"visual_only_recomposed": True}, True),
        ({}, False),
    ],
)
def test_merge_visual_recompose_skip_requires_success_marker(monkeypatch, changed_result_extra, expected_skip):
    captured: dict[str, Any] = {}

    def fake_concat_apply(*, args):
        captured["args"] = args
        return SimpleNamespace(result={"status": "ok"})

    monkeypatch.setattr(worker_tasks.concat_and_finalize, "apply", fake_concat_apply)

    changed_result = {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "part_path": "42/parts/part_001.mp4",
        "tts_audio_path": "42/audio/slide_001.mp3",
        **changed_result_extra,
    }
    slide = {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "part_out": "42/parts/part_001.mp4",
        "audio_out": "42/audio/slide_001.mp3",
    }

    result = worker_tasks.merge_and_finalize_segments.run(
        [changed_result],
        "42",
        [slide],
        ["s1-p1"],
        {"enabled": False},
        "job-1",
        True,
    )

    assert result == {"status": "ok"}
    assert captured["args"][-1] is expected_skip


def _dispatch_capture(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_group(signatures):
        captured["header"] = list(signatures)
        return captured["header"]

    class FakePipeline:
        def apply_async(self, **kwargs):
            captured["apply_async_kwargs"] = kwargs
            return SimpleNamespace(id="visual-chord")

    def fake_chord(header, callback):
        captured["chord_header"] = header
        captured["callback"] = callback
        return FakePipeline()

    monkeypatch.setattr(worker_tasks, "group", fake_group)
    monkeypatch.setattr(worker_tasks, "chord", fake_chord)
    return captured


def _patch_process_dispatch_dependencies(monkeypatch, slides, old_sidecar):
    class FakeExportResult:
        result = slides

        def failed(self):
            return False

    monkeypatch.setattr(worker_tasks.export_project, "apply", lambda *_args, **_kwargs: FakeExportResult())
    monkeypatch.setattr(worker_tasks.process_pptx_to_video, "update_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_sync_transcript_pages_from_export", lambda _project_id, rows: list(rows))
    monkeypatch.setattr(worker_tasks, "_schedule_lesson_intelligence_after_worker_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_run_auto_source_moderation_after_transcript_sync", lambda _project_id: {"enabled": False, "block_render": False})
    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", lambda *_args, **_kwargs: {"enabled": False, "block_render": False})
    monkeypatch.setattr(worker_tasks, "_run_auto_ocr_slide_moderation_after_export", lambda *_args, **_kwargs: {"enabled": False, "block_render": False})
    monkeypatch.setattr(
        worker_tasks,
        "_detect_language_from_slides",
        lambda *_args, **_kwargs: {
            "detected_language": "en",
            "resolved_language": "en",
            "source": "test",
            "confidence": 1.0,
        },
    )
    monkeypatch.setattr(worker_tasks, "_write_language_detection_sidecar", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(worker_tasks, "_read_playback_sidecar", lambda _project_id: old_sidecar)


def _touch_render_artifacts(storage_root: Path, project_id: int, count: int) -> None:
    for index in range(count):
        for rel_path in (
            f"{project_id}/parts/part_{index + 1:03d}.mp4",
            f"{project_id}/audio/slide_{index + 1:03d}.mp3",
            f"{project_id}/images/slide_{index + 1:03d}.png",
        ):
            path = storage_root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"artifact-{index}".encode("ascii"))


def _planner_baseline_sidecar(project: Project, job: Job, old_results: list[dict], *, avatar_options: dict | None = None) -> dict:
    sidecar = _old_sidecar_for_results(
        project.id,
        old_results,
        avatar_options=avatar_options or {"enabled": False, "requested": False},
        effective_language="en",
    )
    sidecar.update(
        {
            "job_id": job.id,
            "project_id": project.id,
            "mp4_rel_path": job.result_url,
            "srt_rel_path": job.srt_url,
        }
    )
    sidecar["render_planner_manifest"] = worker_tasks._planner_manifest_from_render_results(
        project_id=project.id,
        slides=old_results,
        voice_id="voice",
        effective_language="en",
        tts_settings={"provider_preference": "gtts"},
        avatar_options=avatar_options or {"enabled": False, "requested": False},
        artifacts_by_page_key=worker_tasks._planner_artifacts_by_page_key(sidecar),
    )
    return sidecar


def test_finalized_planner_manifest_preserves_captured_input_identity(tmp_path, monkeypatch):
    project_id = 42
    job_id = 77
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    _touch_render_artifacts(tmp_path, project_id, 2)

    captured_slides = [
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "source_slide_index": 0,
            "split_index": 0,
            "narration_text": "Narration one",
            "display_text": "Visible one",
            "subtitle_chunks": ["Narration one"],
            "rich_text_html": "Visible one",
            "image_path": "",
            "whiteboard_mode": True,
        },
        {
            "index": 1,
            "slide_num": 2,
            "page_key": "s2-p1",
            "source_slide_index": 1,
            "split_index": 0,
            "narration_text": "Narration two",
            "display_text": "Visible two",
            "subtitle_chunks": ["Narration two"],
            "rich_text_html": "Visible two",
            "image_path": "",
            "whiteboard_mode": True,
        },
    ]
    captured_manifest = worker_tasks._planner_manifest_from_render_results(
        project_id=project_id,
        slides=captured_slides,
        voice_id="voice",
        effective_language="en",
        tts_settings={"provider_preference": "gtts"},
        avatar_options={"enabled": False, "requested": False},
    )
    worker_tasks._write_render_plan(
        project_id,
        job_id,
        {"current_manifest": captured_manifest},
    )

    finalized_sidecar = _playback_assets(project_id)
    artifacts = worker_tasks._planner_artifacts_by_page_key(finalized_sidecar)
    finalized_manifest = worker_tasks._planner_manifest_with_captured_inputs(
        project_id=project_id,
        job_id=job_id,
        artifacts_by_page_key=artifacts,
    )
    assert finalized_manifest is not None
    assert finalized_manifest["pages"]["s1-p1"]["fingerprint"] == captured_manifest["pages"]["s1-p1"]["fingerprint"]
    assert finalized_manifest["pages"]["s1-p1"]["inputs"]["image"]["image_token"] == ""
    assert finalized_manifest["pages"]["s1-p1"]["inputs"]["timing"]["duration_seconds"] is None
    assert finalized_manifest["pages"]["s1-p1"]["inputs"]["transcript"]["rich_text_html"] == "Visible one"
    assert finalized_manifest["pages"]["s1-p1"]["artifacts"]["tts_audio"]["rel_path"] == f"{project_id}/audio/slide_001.mp3"

    current_slides = [dict(slide) for slide in captured_slides]
    current_slides[1] = {
        **current_slides[1],
        "narration_text": "Edited narration two",
        "subtitle_chunks": ["Edited narration two"],
    }
    current_manifest = worker_tasks._planner_manifest_from_render_results(
        project_id=project_id,
        slides=current_slides,
        voice_id="voice",
        effective_language="en",
        tts_settings={"provider_preference": "gtts"},
        avatar_options={"enabled": False, "requested": False},
        artifacts_by_page_key=artifacts,
    )
    plan = worker_tasks.plan_render_dirty_slides(
        previous_manifest=finalized_manifest,
        current_manifest=current_manifest,
        storage_root=tmp_path,
    )
    assert plan["dirty_slides"] == [2]
    assert plan["reusable_slides"] == [1]


@pytest.mark.django_db
def test_auto_render_dispatches_only_dirty_slide_and_records_non_authoritative_plan(tmp_path, monkeypatch):
    owner = _make_user("auto_dirty_dispatch_owner")
    project = Project.objects.create(title="Auto dirty dispatch", user=owner, status="processing")
    baseline_job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    _touch_render_artifacts(tmp_path, project.id, 5)
    old_results = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(5)
    ]
    current_slides = [dict(item) for item in old_results]
    current_slides[1] = {**current_slides[1], "narration_text": "Edited narration 2", "text": "Edited narration 2", "notes_text": "Edited narration 2"}
    old_sidecar = _planner_baseline_sidecar(project, baseline_job, old_results)
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, current_slides, old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="auto",
    )

    assert result["status"] == "dispatched"
    assert result["effective_render_mode"] == "auto"
    assert result["dirty_page_keys"] == ["s2-p1"]
    assert result["reusable_page_keys"] == ["s1-p1", "s3-p1", "s4-p1", "s5-p1"]
    assert [signature.args[0]["page_key"] for signature in captured["header"]] == ["s2-p1"]
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["callback"].task == "worker.tasks.record_dirty_render_dispatch_result"
    plan = worker_tasks._read_render_plan(project.id, job.id)
    assert plan["authoritative_final"] is False
    assert plan["baseline_job_id"] == baseline_job.id


@pytest.mark.django_db
def test_auto_render_dirty_slide_failure_errback_preserves_authoritative_output(tmp_path, monkeypatch):
    owner = _make_user("auto_dirty_failure_errback_owner")
    project = Project.objects.create(title="Auto dirty failure errback", user=owner, status="processing")
    baseline_job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    _touch_render_artifacts(tmp_path, project.id, 3)
    old_results = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(3)
    ]
    current_slides = [dict(item) for item in old_results]
    current_slides[1] = {**current_slides[1], "narration_text": "Edited narration 2", "text": "Edited narration 2", "notes_text": "Edited narration 2"}
    old_sidecar = _planner_baseline_sidecar(project, baseline_job, old_results)
    sidecar_path = tmp_path / str(project.id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(old_sidecar, sort_keys=True), encoding="utf-8")
    sidecar_before = sidecar_path.read_bytes()
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, current_slides, old_sidecar)
    monkeypatch.setattr(worker_tasks, "_notify_render_failed", lambda *_args, **_kwargs: None)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="auto",
    )

    assert result["dirty_page_keys"] == ["s2-p1"]
    assert [signature.args[0]["page_key"] for signature in captured["header"]] == ["s2-p1"]
    dirty_signature = captured["header"][0]
    assert all(not str(key).startswith("_controlled") for key in dirty_signature.args[6])
    link_error = dirty_signature.options["link_error"]
    assert link_error.task == "worker.tasks.mark_project_render_failed"
    assert list(link_error.args) == [str(project.id), job.id]

    slide_failure = RuntimeError("dirty_slide_task_failed:slide_2:page_key=s2-p1")
    errback_result = worker_tasks.mark_project_render_failed.run(
        "slide-task-id",
        slide_failure,
        str(project.id),
        job.id,
    )

    job.refresh_from_db()
    baseline_job.refresh_from_db()
    plan = worker_tasks._read_render_plan(project.id, job.id)
    assert errback_result["status"] == "failed"
    assert errback_result["job_id"] == job.id
    assert job.status == "failed"
    assert job.status != "done"
    assert job.result_url in {"", None}
    assert "page_key=s2-p1" in job.error_message
    assert baseline_job.status == "done"
    assert baseline_job.result_url == f"{project.id}/{project.id}.mp4"
    assert baseline_job.srt_url == f"{project.id}/{project.id}.srt"
    assert sidecar_path.read_bytes() == sidecar_before
    assert plan["authoritative_final"] is False
    assert "dirty_slide_results" not in plan.get("stage_results", {})


@pytest.mark.django_db
def test_auto_render_zero_dirty_dispatches_no_slide_tasks(tmp_path, monkeypatch):
    owner = _make_user("auto_zero_dirty_owner")
    project = Project.objects.create(title="Auto zero dirty", user=owner, status="processing")
    baseline_job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "_notify_render_completed", lambda *_args, **_kwargs: None)
    _touch_render_artifacts(tmp_path, project.id, 2)
    old_results = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(2)
    ]
    old_sidecar = _planner_baseline_sidecar(project, baseline_job, old_results)
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [dict(item) for item in old_results], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="auto",
    )

    job.refresh_from_db()
    assert result["status"] == "zero_dirty_noop"
    assert "header" not in captured
    assert job.status == "done"
    assert job.result_url == baseline_job.result_url
    assert worker_tasks._read_render_plan(project.id, job.id)["stage_results"]["zero_dirty"] is True


@pytest.mark.django_db
def test_auto_render_missing_baseline_falls_back_to_full_dispatch(tmp_path, monkeypatch):
    owner = _make_user("auto_missing_baseline_owner")
    project = Project.objects.create(title="Auto missing baseline", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    slides = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(3)
    ]
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, slides, {})

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="auto",
    )

    assert result["status"] == "dispatched"
    assert result["effective_render_mode"] == "full"
    assert result["fallback_reason"] == "MissingBaseline"
    assert [signature.args[0]["page_key"] for signature in captured["header"]] == ["s1-p1", "s2-p1", "s3-p1"]
    assert captured["callback"].task == "worker.tasks.concat_and_finalize"


@pytest.mark.django_db
def test_auto_render_missing_reusable_artifact_dirties_that_slide(tmp_path, monkeypatch):
    owner = _make_user("auto_missing_artifact_owner")
    project = Project.objects.create(title="Auto missing artifact", user=owner, status="processing")
    baseline_job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    _touch_render_artifacts(tmp_path, project.id, 3)
    missing_audio = tmp_path / f"{project.id}/audio/slide_002.mp3"
    missing_audio.unlink()
    old_results = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(3)
    ]
    old_sidecar = _planner_baseline_sidecar(project, baseline_job, old_results)
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [dict(item) for item in old_results], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="auto",
    )

    assert result["dirty_page_keys"] == ["s2-p1"]
    assert result["reusable_page_keys"] == ["s1-p1", "s3-p1"]
    assert [signature.args[0]["page_key"] for signature in captured["header"]] == ["s2-p1"]


@pytest.mark.django_db
def test_forced_full_render_dispatches_all_slides_even_with_clean_baseline(tmp_path, monkeypatch):
    owner = _make_user("forced_full_dispatch_owner")
    project = Project.objects.create(title="Forced full dispatch", user=owner, status="processing")
    baseline_job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    _touch_render_artifacts(tmp_path, project.id, 2)
    old_results = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(2)
    ]
    old_sidecar = _planner_baseline_sidecar(project, baseline_job, old_results)
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [dict(item) for item in old_results], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="full",
    )

    assert result["effective_render_mode"] == "full"
    assert result["fallback_reason"] == "ForcedFull"
    assert [signature.args[0]["page_key"] for signature in captured["header"]] == ["s1-p1", "s2-p1"]
    assert captured["callback"].task == "worker.tasks.concat_and_finalize"


@pytest.mark.django_db
def test_selected_render_dispatches_selected_slide_and_existing_merge_callback(tmp_path, monkeypatch):
    owner = _make_user("selected_dirty_dispatch_owner")
    project = Project.objects.create(title="Selected dirty dispatch", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    slides = [
        _without_avatar(_render_result(index=i, page_key=f"s{i + 1}-p1", display_text=f"Slide {i + 1}", narration_text=f"Narration {i + 1}", project_id=project.id))
        for i in range(3)
    ]
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, slides, {})

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        ["s2-p1"],
        {"provider_preference": "gtts"},
        job_id=job.id,
        render_mode="selected",
    )

    assert result["effective_render_mode"] == "selected"
    assert result["dirty_page_keys"] == ["s2-p1"]
    assert result["reusable_page_keys"] == ["s1-p1", "s3-p1"]
    assert [signature.args[0]["page_key"] for signature in captured["header"]] == ["s2-p1"]
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"


@pytest.mark.django_db
def test_dirty_dispatch_missing_slide_result_fails_without_promoting_baseline(tmp_path, monkeypatch):
    owner = _make_user("dirty_dispatch_failure_owner")
    project = Project.objects.create(title="Dirty dispatch failure", user=owner, status="processing")
    baseline_job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=50)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "_notify_render_failed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_is_current_render_job", lambda *_args, **_kwargs: True)
    plan = {
        "dirty_page_keys": ["s2-p1"],
        "reusable_page_keys": ["s1-p1"],
        "baseline_job_id": baseline_job.id,
        "baseline_result_url": baseline_job.result_url,
        "baseline_srt_url": baseline_job.srt_url,
        "stage_results": {},
    }

    with pytest.raises(RuntimeError, match="dirty_slide_results_missing"):
        worker_tasks.record_dirty_render_dispatch_result.run([], str(project.id), plan, {"enabled": False}, job.id)

    job.refresh_from_db()
    baseline_job.refresh_from_db()
    assert job.status == "failed"
    assert job.result_url in {"", None}
    assert baseline_job.status == "done"
    assert baseline_job.result_url == f"{project.id}/{project.id}.mp4"


@pytest.mark.django_db
def test_process_full_render_dispatches_enabled_avatar_to_slide_tasks(tmp_path, monkeypatch):
    owner = _make_user("full_render_avatar_dispatch_owner")
    project = Project.objects.create(title="Full render avatar dispatch", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)

    slide = {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "image_path": str(tmp_path / "images" / "slide_001.png"),
        "notes_text": "A lesson slide.",
        "audio_out": str(tmp_path / "audio" / "slide_001.mp3"),
        "part_out": str(tmp_path / "parts" / "part_001.mp4"),
    }
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [slide], old_sidecar={})
    avatar_options = {
        "enabled": True,
        "requested": True,
        "teacher_id": owner.id,
        "source_image_rel_path": "avatars/teacher.png",
        "avatar_source_valid": True,
        "avatar_visible": True,
        "lipsync_engine": "liveportrait+musetalk",
    }

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        avatar_options,
        [],
        {"provider_preference": "gtts"},
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["header"][0].options["queue"] == "avatar"
    assert captured["callback"].options["queue"] == "avatar"
    assert captured["apply_async_kwargs"]["queue"] == "avatar"
    dispatched_avatar_options = captured["header"][0].args[6]
    assert dispatched_avatar_options["enabled"] is True
    assert dispatched_avatar_options["teacher_id"] == owner.id
    assert dispatched_avatar_options["source_image_rel_path"] == "avatars/teacher.png"


def _old_sidecar_for_visual_recompose(
    project_id: int,
    *,
    old_result: dict,
    avatar_options: dict | None = None,
    avatar_payload: dict | None = None,
    effective_language: str = "en",
) -> dict:
    playback_assets = {
        "final_segments": [
            {
                "index": int(old_result.get("index") or 0),
                "page_key": str(old_result.get("page_key") or ""),
                "transcript": str(old_result.get("text") or ""),
                "tts_audio": str(old_result.get("tts_audio_path") or ""),
                "avatar_clip": str(old_result.get("avatar_segment_rel_path") or ""),
                "slide": str(old_result.get("slide_path") or ""),
                "part_rel_path": str(old_result.get("part_path") or ""),
                "duration": float(old_result.get("duration") or 0.0),
                "pause_seconds": float(old_result.get("pause_seconds") or 0.0),
                "source_render_method": str(old_result.get("source_render_method") or ""),
                "source_render_dependency_report": dict(old_result.get("source_render_dependency_report") or {}),
            }
        ],
        "tts_normalization": [
            {
                "index": int(old_result.get("index") or 0),
                "page_key": str(old_result.get("page_key") or ""),
                "tts_normalization_language": effective_language,
                "project_tts_settings": dict(old_result.get("tts_settings") or {}),
            }
        ],
    }
    playback_assets["partial_render_manifest"] = build_partial_render_manifest(
        project_id=project_id,
        job_id=1,
        ordered_results=[old_result],
        playback_assets=playback_assets,
        avatar_options=avatar_options,
        effective_language=effective_language,
    )
    if avatar_payload:
        playback_assets["avatar"] = dict(avatar_payload)
        playback_assets["avatar_status"] = "ready"
        playback_assets["avatar_processing_status"] = "ready"
    return playback_assets


def _old_sidecar_for_results(
    project_id: int,
    old_results: list[dict],
    *,
    avatar_options: dict | None = None,
    effective_language: str = "en",
) -> dict:
    final_segments = []
    tts_normalization = []
    for old_result in old_results:
        final_segments.append(
            {
                "index": int(old_result.get("index") or 0),
                "page_key": str(old_result.get("page_key") or ""),
                "transcript": str(old_result.get("text") or ""),
                "tts_audio": str(old_result.get("tts_audio_path") or ""),
                "avatar_clip": str(old_result.get("avatar_segment_rel_path") or ""),
                "slide": str(old_result.get("slide_path") or ""),
                "part_rel_path": str(old_result.get("part_path") or ""),
                "duration": float(old_result.get("duration") or 0.0),
                "pause_seconds": float(old_result.get("pause_seconds") or 0.0),
                "source_render_method": str(old_result.get("source_render_method") or ""),
                "source_render_dependency_report": dict(old_result.get("source_render_dependency_report") or {}),
            }
        )
        tts_normalization.append(
            {
                "index": int(old_result.get("index") or 0),
                "page_key": str(old_result.get("page_key") or ""),
                "tts_normalization_language": effective_language,
                "project_tts_settings": dict(old_result.get("tts_settings") or {}),
            }
        )
    playback_assets = {
        "final_segments": final_segments,
        "tts_normalization": tts_normalization,
        "slides": [str(result.get("slide_path") or "") for result in old_results],
        "tts_audio": [str(result.get("tts_audio_path") or "") for result in old_results],
        "avatar_clips": [str(result.get("avatar_segment_rel_path") or "") for result in old_results],
    }
    playback_assets["partial_render_manifest"] = build_partial_render_manifest(
        project_id=project_id,
        job_id=1,
        ordered_results=old_results,
        playback_assets=playback_assets,
        avatar_options=avatar_options,
        effective_language=effective_language,
    )
    return playback_assets


def _without_avatar(result: dict) -> dict:
    result.update(
        {
            "avatar_segment_rel_path": "",
            "avatar_attempted": False,
            "avatar_applied": False,
            "avatar_status": "none",
            "avatar_engine_used": "none",
        }
    )
    return result


@pytest.mark.django_db
def test_process_targeted_visual_only_dispatches_recompose_and_merge_callback(tmp_path, monkeypatch):
    owner = _make_user("visual_dispatch_owner")
    project = Project.objects.create(title="Visual dispatch", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    audio_path = tmp_path / str(project.id) / "audio" / "slide_001.mp3"
    part_path = tmp_path / str(project.id) / "parts" / "part_001.mp4"
    image_path = tmp_path / str(project.id) / "images" / "slide_001.png"
    for path in (audio_path, part_path, image_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    old_result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Old visible",
        narration_text="Narration",
        project_id=project.id,
    )
    old_result.pop("page_id", None)
    old_result.update(
        {
            "part_path": f"{project.id}/parts/part_001.mp4",
            "slide_path": f"{project.id}/images/slide_001.png",
            "tts_audio_path": f"{project.id}/audio/slide_001.mp3",
            "tts_settings": {"provider_preference": "gtts"},
            "avatar_segment_rel_path": "",
        }
    )
    current_slide = {
        **old_result,
        "image_path": str(image_path),
        "original_text": "New visible",
        "display_text": "New visible",
        "audio_out": str(audio_path),
        "part_out": str(part_path),
    }
    old_sidecar = _old_sidecar_for_visual_recompose(
        project.id,
        old_result=old_result,
        avatar_options={"enabled": False, "requested": False, "composite_fallback_allowed": False},
    )
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [current_slide], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        ["s1-p1"],
        {"provider_preference": "gtts"},
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.recompose_visual_only_slide_segment"
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"
    assert captured["callback"].args[-1] is True
    assert captured["header"][0].args[-1]["tts_audio_abs_path"] == str(audio_path.resolve())


@pytest.mark.django_db
def test_process_targeted_visual_only_avatar_requires_old_overlay_track(tmp_path, monkeypatch):
    owner = _make_user("visual_avatar_overlay_owner")
    project = Project.objects.create(title="Visual avatar overlay", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    project_root = tmp_path / str(project.id)
    audio_path = project_root / "audio" / "slide_001.mp3"
    part_path = project_root / "parts" / "part_001.mp4"
    image_path = project_root / "images" / "slide_001.png"
    avatar_segment_path = project_root / "avatar_segments" / "avatar_001.mp4"
    for path in (audio_path, part_path, image_path, avatar_segment_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "_avatar_storage_root", lambda: str(tmp_path))

    old_result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Old visible",
        narration_text="Narration",
        project_id=project.id,
    )
    old_result.pop("page_id", None)
    old_result.update(
        {
            "part_path": f"{project.id}/parts/part_001.mp4",
            "slide_path": f"{project.id}/images/slide_001.png",
            "tts_audio_path": f"{project.id}/audio/slide_001.mp3",
            "tts_settings": {"provider_preference": "gtts"},
            "avatar_segment_rel_path": f"{project.id}/avatar_segments/avatar_001.mp4",
            "avatar_applied": True,
        }
    )
    current_slide = {
        **old_result,
        "image_path": str(image_path),
        "original_text": "New visible",
        "display_text": "New visible",
        "audio_out": str(audio_path),
        "part_out": str(part_path),
    }
    old_sidecar = _old_sidecar_for_visual_recompose(
        project.id,
        old_result=old_result,
        avatar_options={"enabled": True, "requested": True, "teacher_id": owner.id},
    )
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [current_slide], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": True, "requested": True, "teacher_id": owner.id},
        ["s1-p1"],
        {"provider_preference": "gtts"},
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"
    assert captured["callback"].args[-1] is False


@pytest.mark.django_db
def test_process_targeted_narration_only_dispatches_recompose_and_merge_callback(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    owner = _make_user("narration_dispatch_owner")
    project = Project.objects.create(title="Narration dispatch", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    project_root = tmp_path / str(project.id)
    audio_one = project_root / "audio" / "slide_001.mp3"
    audio_two = project_root / "audio" / "slide_002.mp3"
    part_one = project_root / "parts" / "part_001.mp4"
    part_two = project_root / "parts" / "part_002.mp4"
    image_one = project_root / "images" / "slide_001.png"
    image_two = project_root / "images" / "slide_002.png"
    for path in (audio_one, audio_two, part_one, part_two, image_one, image_two):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 2.0)

    old_first = _without_avatar(
        _render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible one",
            narration_text="Narration one",
            project_id=project.id,
        )
    )
    old_second = _without_avatar(
        _render_result(
            index=1,
            page_key="s2-p1",
            display_text="Visible two",
            narration_text="Narration two",
            project_id=project.id,
        )
    )
    old_first.update(
        {
            "part_path": f"{project.id}/parts/part_001.mp4",
            "slide_path": f"{project.id}/images/slide_001.png",
            "tts_audio_path": f"{project.id}/audio/slide_001.mp3",
            "tts_settings": {"provider_preference": "gtts"},
        }
    )
    old_second.update(
        {
            "part_path": f"{project.id}/parts/part_002.mp4",
            "slide_path": f"{project.id}/images/slide_002.png",
            "tts_audio_path": f"{project.id}/audio/slide_002.mp3",
            "tts_settings": {"provider_preference": "gtts"},
        }
    )
    current_first = {
        **old_first,
        "text": "Narration one updated",
        "narration_text": "Narration one updated",
        "notes_text": "Narration one updated",
        "spoken_text": "Narration one updated",
        "subtitle_chunks": ["Narration one updated"],
        "audio_out": str(audio_one),
        "part_out": str(part_one),
        "image_path": str(image_one),
    }
    current_second = {
        **old_second,
        "audio_out": str(audio_two),
        "part_out": str(part_two),
        "image_path": str(image_two),
    }
    old_sidecar = _old_sidecar_for_results(
        project.id,
        [old_first, old_second],
        avatar_options={"enabled": False, "requested": False, "composite_fallback_allowed": False},
    )
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [current_first, current_second], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        ["s1-p1"],
        {"provider_preference": "gtts"},
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert len(captured["header"]) == 1
    assert captured["header"][0].task == "worker.tasks.recompose_narration_only_slide_segment"
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"
    assert captured["callback"].args[-1] is False
    assert captured["header"][0].args[-1]["slide_image_abs_path"] == str(image_one.resolve())


@pytest.mark.django_db
def test_process_targeted_narration_only_missing_old_artifact_falls_back(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    owner = _make_user("narration_missing_artifact_owner")
    project = Project.objects.create(title="Narration missing artifact", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    project_root = tmp_path / str(project.id)
    audio_path = project_root / "audio" / "slide_001.mp3"
    part_path = project_root / "parts" / "part_001.mp4"
    image_path = project_root / "images" / "slide_001.png"
    for path in (audio_path, part_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 2.0)

    old_result = _without_avatar(
        _render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible",
            narration_text="Narration",
            project_id=project.id,
        )
    )
    old_result.update(
        {
            "part_path": f"{project.id}/parts/part_001.mp4",
            "slide_path": f"{project.id}/images/slide_001.png",
            "tts_audio_path": f"{project.id}/audio/slide_001.mp3",
            "tts_settings": {"provider_preference": "gtts"},
        }
    )
    current_slide = {
        **old_result,
        "text": "Narration updated",
        "narration_text": "Narration updated",
        "spoken_text": "Narration updated",
        "subtitle_chunks": ["Narration updated"],
        "audio_out": str(audio_path),
        "part_out": str(part_path),
        "image_path": str(image_path),
    }
    old_sidecar = _old_sidecar_for_results(
        project.id,
        [old_result],
        avatar_options={"enabled": False, "requested": False},
    )
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [current_slide], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        ["s1-p1"],
        {"provider_preference": "gtts"},
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"
    assert captured["callback"].args[-1] is False


@pytest.mark.parametrize(
    ("username", "current_extra", "avatar_options"),
    [
        (
            "narration_mixed_visual_owner",
            {"display_text": "Visible updated", "original_text": "Visible updated"},
            {"enabled": False, "requested": False},
        ),
        (
            "narration_structural_owner",
            {"page_id": 99999},
            {"enabled": False, "requested": False},
        ),
        (
            "narration_avatar_deferred_owner",
            {},
            {"enabled": True, "requested": True, "teacher_id": 123},
        ),
    ],
)
@pytest.mark.django_db
def test_process_targeted_narration_only_unsafe_cases_use_existing_render_path(
    tmp_path,
    monkeypatch,
    username,
    current_extra,
    avatar_options,
):
    from scripts import ffmpeg_helpers

    owner = _make_user(username)
    if avatar_options.get("teacher_id") == 123:
        avatar_options = {**avatar_options, "teacher_id": owner.id}
    project = Project.objects.create(title="Narration fallback", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    project_root = tmp_path / str(project.id)
    audio_path = project_root / "audio" / "slide_001.mp3"
    part_path = project_root / "parts" / "part_001.mp4"
    image_path = project_root / "images" / "slide_001.png"
    for path in (audio_path, part_path, image_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 2.0)

    old_result = _without_avatar(
        _render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible",
            narration_text="Narration",
            project_id=project.id,
        )
    )
    old_result.update(
        {
            "part_path": f"{project.id}/parts/part_001.mp4",
            "slide_path": f"{project.id}/images/slide_001.png",
            "tts_audio_path": f"{project.id}/audio/slide_001.mp3",
            "tts_settings": {"provider_preference": "gtts"},
        }
    )
    current_slide = {
        **old_result,
        "text": "Narration updated",
        "narration_text": "Narration updated",
        "notes_text": "Narration updated",
        "spoken_text": "Narration updated",
        "subtitle_chunks": ["Narration updated"],
        "audio_out": str(audio_path),
        "part_out": str(part_path),
        "image_path": str(image_path),
        **current_extra,
    }
    old_sidecar = _old_sidecar_for_results(project.id, [old_result], avatar_options=avatar_options)
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [current_slide], old_sidecar)

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        avatar_options,
        ["s1-p1"],
        {"provider_preference": "gtts"},
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"
    assert captured["callback"].args[-1] is False


@pytest.mark.django_db
def test_process_full_render_does_not_use_visual_only_recompose(tmp_path, monkeypatch):
    owner = _make_user("visual_full_owner")
    project = Project.objects.create(title="Visual full", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    image_path = tmp_path / str(project.id) / "images" / "slide_001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    slide = {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "image_path": str(image_path),
        "notes_text": "Narration",
        "narration_text": "Narration",
        "audio_out": str(tmp_path / str(project.id) / "audio" / "slide_001.mp3"),
        "part_out": str(tmp_path / str(project.id) / "parts" / "part_001.mp4"),
    }
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [slide], {})

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        None,
        None,
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["callback"].task == "worker.tasks.concat_and_finalize"


@pytest.mark.django_db
def test_process_targeted_missing_old_manifest_falls_back_to_existing_render_path(tmp_path, monkeypatch):
    owner = _make_user("visual_missing_manifest_owner")
    project = Project.objects.create(title="Visual missing manifest", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    image_path = tmp_path / str(project.id) / "images" / "slide_001.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    slide = {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "image_path": str(image_path),
        "notes_text": "Narration",
        "narration_text": "Narration",
        "display_text": "New visible",
        "audio_out": str(tmp_path / str(project.id) / "audio" / "slide_001.mp3"),
        "part_out": str(tmp_path / str(project.id) / "parts" / "part_001.mp4"),
    }
    captured = _dispatch_capture(monkeypatch)
    _patch_process_dispatch_dependencies(monkeypatch, [slide], {})

    result = worker_tasks.process_pptx_to_video.run(
        str(project.id),
        str(tmp_path / "lesson.txt"),
        "voice",
        0.25,
        "en",
        "service",
        False,
        {"enabled": False, "requested": False},
        ["s1-p1"],
        None,
        job_id=job.id,
    )

    assert result["status"] == "dispatched"
    assert captured["header"][0].task == "worker.tasks.synthesize_and_render_slide"
    assert captured["callback"].task == "worker.tasks.merge_and_finalize_segments"
    assert captured["callback"].args[-1] is False


@pytest.mark.django_db
def test_concat_visual_recompose_skip_preserves_previous_avatar_overlay(tmp_path, monkeypatch):
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("visual_overlay_reuse_owner")
    project = Project.objects.create(title="Visual overlay reuse", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    project_root = tmp_path / str(project.id)
    avatar_track = project_root / "avatar" / "avatar_track.mp4"
    avatar_segment = project_root / "avatar_segments" / "avatar_001.mp4"
    for path in (avatar_track, avatar_segment):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"avatar")
    previous_sidecar = {
        "avatar": {
            "track_rel_path": f"{project.id}/avatar/avatar_track.mp4",
            "default_position": "top-right",
            "default_size": "medium",
        },
        "avatar_status": "ready",
        "avatar_processing_status": "ready",
        "final_segments": [{"index": 0, "page_key": "s1-p1"}],
    }
    sidecar_path = project_root / "playback_assets.json"
    sidecar_path.write_text(json.dumps(previous_sidecar), encoding="utf-8")
    monkeypatch.setattr(
        worker_tasks,
        "_queue_lesson_avatar_overlay_after_base_render",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("avatar overlay should be reused")),
    )
    result_payload = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="New visible",
        narration_text="Narration",
        project_id=project.id,
    )
    result_payload.update(
        {
            "visual_only_recomposed": True,
            "part_path": str(project_root / "parts" / "part_001.mp4"),
            "slide_path": str(project_root / "images" / "slide_001.png"),
            "tts_audio_path": str(project_root / "audio" / "slide_001.mp3"),
            "avatar_applied": True,
            "avatar_segment_rel_path": f"{project.id}/avatar_segments/avatar_001.mp4",
            "avatar_status": "ready",
        }
    )

    finalize_result = worker_tasks.concat_and_finalize.run(
        [result_payload],
        str(project.id),
        False,
        {"enabled": True, "requested": True, "teacher_id": owner.id},
        job.id,
        True,
    )

    updated_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert finalize_result["background_avatar"]["status"] == "reused"
    assert updated_sidecar["avatar"]["track_rel_path"] == f"{project.id}/avatar/avatar_track.mp4"
    assert updated_sidecar["avatar_processing_status"] == "ready"


@pytest.mark.django_db
def test_finalize_adds_manifest_without_removing_playback_sidecar_fields(tmp_path, monkeypatch):
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("partial_manifest_owner")
    project = Project.objects.create(title="Partial manifest lesson", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Visible sidecar text",
        narration_text="Narration sidecar text",
        project_id=project.id,
    )
    result["part_path"] = str(tmp_path / str(project.id) / "parts" / "part_001.mp4")
    result["slide_path"] = str(tmp_path / str(project.id) / "images" / "slide_001.png")
    result["tts_audio_path"] = str(tmp_path / str(project.id) / "audio" / "slide_001.mp3")

    finalize_result = worker_tasks.concat_and_finalize.run([result], str(project.id), False, None, job.id)

    sidecar_path = tmp_path / str(project.id) / "playback_assets.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    manifest = sidecar["partial_render_manifest"]
    analysis = sidecar["partial_render_analysis"]
    assert sidecar["mp4_rel_path"] == f"{project.id}/{project.id}.mp4"
    assert sidecar["srt_rel_path"] == f"{project.id}/{project.id}.srt"
    assert sidecar["vtt_rel_path"] == f"{project.id}/{project.id}.vtt"
    assert sidecar["final_segments"][0]["page_key"] == "s1-p1"
    assert sidecar["final_segments"][0]["part_rel_path"] == f"{project.id}/parts/part_001.mp4"
    assert finalize_result["playback_assets"]["partial_render_manifest"] == manifest
    assert finalize_result["playback_assets"]["partial_render_analysis"] == analysis
    assert manifest["job_id"] == job.id
    assert manifest["pages"]["s1-p1"]["artifacts"] == {
        "tts_audio": f"{project.id}/audio/slide_001.mp3",
        "avatar_clip": "",
        "composed_segment": f"{project.id}/parts/part_001.mp4",
        "slide_image": f"{project.id}/images/slide_001.png",
    }
    assert analysis["version"] == 1
    assert analysis["mode"] == "report_only"
    assert analysis["generated_from"] == "partial_render_manifest"
    assert analysis["comparison_phase"] == "post_render_finalized"
    assert analysis["comparison_roles"] == {
        "old": "previous_finalized_manifest",
        "expected": "current_finalized_manifest",
    }
    assert analysis["classifier"]["available"] is False
    assert analysis["classifier"]["notes"] == [
        "old_playback_assets_missing",
        "old_manifest_missing_or_invalid",
    ]
    assert analysis["classifier"]["result"]["global_reasons"] == ["unknown_requires_full"]
    assert analysis["classifier"]["result"]["pages"]["s1-p1"]["classification"] == "unknown_requires_full"
    assert analysis["plan"]["pages"]["s1-p1"]["recommended_action"] == "full_rerender_required_future"
    assert analysis["plan"]["pages"]["s1-p1"]["future_only"] is True
    assert analysis["plan"]["pages"]["s1-p1"]["actual_behavior_changed"] is False
    assert analysis["plan"]["summary"]["full_rerender_required_future"] == 1
    assert analysis["plan"]["summary"]["unknown_requires_full"] == 1


@pytest.mark.django_db
def test_real_export_shaped_txt_sidecar_supports_narration_only_decision(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("real_txt_narration_decision_owner")
    project = Project.objects.create(title="Real TXT narration decision", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    _results, slides, sidecar, avatar_options, tts_settings = _finalize_two_page_txt_sidecar(
        tmp_path,
        project,
        job,
    )
    slides[0].update(
        {
            "notes_text": "Narration one updated",
            "narration_text": "Narration one updated",
            "subtitle_chunks": ["Narration one updated"],
        }
    )
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 2.0)

    decision = worker_tasks._build_narration_only_recompose_runtime_decision(
        project_id=project.id,
        job_id=job.id + 1,
        slides=slides,
        rerender_page_keys={"s1-p1"},
        previous_playback_assets=sidecar,
        tts_settings=tts_settings,
        avatar_options=avatar_options,
        effective_language="en",
    )

    assert sidecar["partial_render_manifest"]["pages"]["s1-p1"]["duration"] == 2.0
    assert decision["classification"]["global_reasons"] == []
    assert decision["comparison_phase"] == "pre_render_eligibility"
    assert decision["comparison_roles"] == {
        "old": "previous_finalized_manifest",
        "expected": "current_render_inputs",
    }
    assert "structural_changed" not in decision["classification"]["pages"]["s1-p1"]["reasons"]
    assert decision["classification"]["pages"]["s2-p1"]["classification"] == "unchanged"
    assert decision["eligible"] is True


@pytest.mark.django_db
def test_fresh_project_tts_shape_keeps_unchanged_txt_page_eligible(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("fresh_project_tts_narration_owner")
    project = Project.objects.create(title="Fresh project TTS narration", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    raw_tts_settings = default_project_tts_settings()
    finalized_tts_settings = worker_tasks._summarize_tts_settings(raw_tts_settings)
    _results, slides, sidecar, avatar_options, _tts_settings = _finalize_two_page_txt_sidecar(
        tmp_path,
        project,
        job,
        tts_settings=raw_tts_settings,
        finalized_tts_settings=finalized_tts_settings,
        pause_seconds=0.8,
        narration_texts=(
            "First slide narration for effective TTS-input partial-render smoke.",
            "Second slide narration with TTS-settings terminology.",
        ),
        spoken_texts=(
            "First slide narration for effective T T S-input partial-render smoke.",
            "Second slide narration with T T S-settings terminology.",
        ),
    )
    slides[0].update(
        {
            "notes_text": "First slide narration changed for effective TTS-input partial-render smoke version two.",
            "narration_text": "First slide narration changed for effective TTS-input partial-render smoke version two.",
            "subtitle_chunks": [
                "First slide narration changed for effective TTS-input partial-render smoke version two."
            ],
        }
    )
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 4.416)

    enriched_slides = worker_tasks._slides_with_previous_segment_timing(slides, sidecar)
    expected_manifest = build_expected_partial_render_manifest(
        project_id=project.id,
        job_id=job.id + 1,
        slides=enriched_slides,
        previous_playback_assets=sidecar,
        tts_settings=raw_tts_settings,
        avatar_options=avatar_options,
        effective_language="en",
    )
    decision = worker_tasks._build_narration_only_recompose_runtime_decision(
        project_id=project.id,
        job_id=job.id + 1,
        slides=slides,
        rerender_page_keys={"s1-p1"},
        previous_playback_assets=sidecar,
        tts_settings=raw_tts_settings,
        avatar_options=avatar_options,
        effective_language="en",
    )

    old_page = sidecar["partial_render_manifest"]["pages"]["s2-p1"]
    expected_page = expected_manifest["pages"]["s2-p1"]
    assert old_page["tts_settings_hash"] == expected_page["tts_settings_hash"]
    assert old_page["tts_input_hash"] == expected_page["tts_input_hash"]
    assert old_page["tts_settings_hash"] == (
        "sha256:be86cb0453b1e6efe4fa2f7a7e6453179ecc6889ebb2aa64ff28378070c1df51"
    )
    assert expected_page["pause_seconds"] == 0.8
    assert decision["classification"]["global_reasons"] == []
    assert decision["classification"]["pages"]["s1-p1"]["classification"] == "narration_text_changed"
    assert decision["classification"]["pages"]["s2-p1"]["classification"] == "unchanged"
    assert decision["classification"]["pages"]["s2-p1"]["reasons"] == []
    assert decision["plan"]["pages"]["s2-p1"]["recommended_action"] == "reuse_all"
    assert "non_target_page_changed" not in decision["fallback_reasons"]
    assert decision["eligible"] is True


@pytest.mark.django_db
def test_real_txt_conservative_merge_preserves_prior_metadata_and_slide_image(tmp_path, monkeypatch):
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("real_txt_conservative_merge_owner")
    project = Project.objects.create(title="Real TXT conservative merge", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    results, slides, sidecar, avatar_options, _tts_settings = _finalize_two_page_txt_sidecar(
        tmp_path,
        project,
        job,
    )
    slides[0].update(
        {
            "notes_text": "Narration one updated",
            "narration_text": "Narration one updated",
            "subtitle_chunks": ["Narration one updated"],
        }
    )
    slides[1].update(
        {
            "audio_out": "",
            "part_out": "",
            "duration": "invalid",
            "pause_seconds": "invalid",
        }
    )
    changed_result = {
        **results[0],
        "text": "Narration one updated",
        "narration_text": "Narration one updated",
        "spoken_text": "Narration one updated",
        "subtitle_chunks": ["Narration one updated"],
        "duration": 4.0,
    }
    monkeypatch.setattr(worker_tasks, "_read_playback_sidecar", lambda _project_id: sidecar)
    captured: dict[str, Any] = {}

    def fake_finalize_apply(*, args):
        captured["results"] = args[0]
        return SimpleNamespace(result={"status": "ok"})

    monkeypatch.setattr(worker_tasks.concat_and_finalize, "apply", fake_finalize_apply)

    worker_tasks.merge_and_finalize_segments.run(
        [changed_result],
        str(project.id),
        slides,
        ["s1-p1"],
        avatar_options,
        job.id + 1,
    )

    unchanged = next(item for item in captured["results"] if item["page_key"] == "s2-p1")
    assert unchanged["duration"] == 3.0
    assert unchanged["pause_seconds"] == 0.25
    assert unchanged["part_path"] == results[1]["part_path"]
    assert unchanged["tts_audio_path"] == results[1]["tts_audio_path"]
    assert unchanged["slide_path"] == results[1]["slide_path"]
    assert unchanged["tts_normalization_language"] == "en"
    assert unchanged["tts_settings"]["provider_preference"] == "gtts"
    assert unchanged["source_render_method"] == "txt_whiteboard"
    assert unchanged["source_render_dependency_report"] == {"renderer": "txt_whiteboard"}

    layout_changed = deepcopy(slides)
    layout_changed[1]["scene_text_scale"] = 1.25
    layout_changed[1]["editor_document"]["scene"]["text_scale"] = 1.25
    source_changed = deepcopy(slides)
    source_changed[1]["source_render_method"] = "changed-renderer"
    reordered = [deepcopy(slides[1]), deepcopy(slides[0])]
    for index, slide in enumerate(reordered):
        slide["index"] = index

    for unsafe_slides in (layout_changed, source_changed, reordered):
        reusable = worker_tasks._cached_slide_images_for_unchanged_pages(
            project_id=project.id,
            slides=unsafe_slides,
            previous_playback_assets=sidecar,
            avatar_options=avatar_options,
        )
        assert "s2-p1" not in reusable


@pytest.mark.django_db
def test_real_finalized_sidecar_supports_narration_only_runtime_decision(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("finalized_narration_decision_owner")
    project = Project.objects.create(title="Finalized narration decision", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    results, sidecar, avatar_options = _finalize_two_page_no_avatar_sidecar(tmp_path, project, job)
    slides = _runtime_slides_from_finalized_results(results)
    slides[0].update(
        {
            "text": "Narration one updated",
            "narration_text": "Narration one updated",
            "notes_text": "Narration one updated",
            "spoken_text": "Narration one updated",
            "subtitle_chunks": ["Narration one updated"],
        }
    )
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 2.0)

    enriched = worker_tasks._slides_with_previous_segment_timing(slides, sidecar)
    decision = worker_tasks._build_narration_only_recompose_runtime_decision(
        project_id=project.id,
        job_id=job.id + 1,
        slides=slides,
        rerender_page_keys={"s1-p1"},
        previous_playback_assets=sidecar,
        tts_settings={"provider_preference": "gtts", "speech_speed": 1.05},
        avatar_options=avatar_options,
    )

    assert [segment["page_key"] for segment in sidecar["final_segments"]] == ["s1-p1", "s2-p1"]
    assert [(row["duration"], row["pause_seconds"]) for row in enriched] == [(2.0, 0.25), (3.0, 0.25)]
    assert decision["classification"]["global_reasons"] == []
    assert decision["classification"]["pages"]["s2-p1"]["classification"] == "unchanged"
    assert "structural_changed" not in decision["classification"]["pages"]["s1-p1"]["reasons"]
    assert decision["eligible"] is True

    legacy_sidecar = deepcopy(sidecar)
    for segment in legacy_sidecar["final_segments"]:
        segment.pop("page_key")
    legacy_decision = worker_tasks._build_narration_only_recompose_runtime_decision(
        project_id=project.id,
        job_id=job.id + 2,
        slides=slides,
        rerender_page_keys={"s1-p1"},
        previous_playback_assets=legacy_sidecar,
        tts_settings={"provider_preference": "gtts", "speech_speed": 1.05},
        avatar_options=avatar_options,
    )
    assert legacy_decision["classification"]["global_reasons"] == []
    assert legacy_decision["eligible"] is True


@pytest.mark.django_db
def test_cached_merge_preserves_old_slide_image_only_when_visual_hashes_match(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("cached_slide_image_owner")
    project = Project.objects.create(title="Cached slide image", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    results, sidecar, avatar_options = _finalize_two_page_no_avatar_sidecar(tmp_path, project, job)
    slides = _runtime_slides_from_finalized_results(results)
    slides[0].update(
        {
            "text": "Narration one updated",
            "narration_text": "Narration one updated",
            "notes_text": "Narration one updated",
            "spoken_text": "Narration one updated",
            "subtitle_chunks": ["Narration one updated"],
        }
    )
    slides[1]["image_path"] = ""
    changed_result = {
        **results[0],
        "text": "Narration one updated",
        "narration_text": "Narration one updated",
        "spoken_text": "Narration one updated",
        "subtitle_chunks": ["Narration one updated"],
    }
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 2.0)
    monkeypatch.setattr(worker_tasks, "_read_playback_sidecar", lambda _project_id: sidecar)
    captured: dict[str, Any] = {}

    def fake_finalize_apply(*, args):
        captured["results"] = args[0]
        return SimpleNamespace(result={"status": "ok"})

    monkeypatch.setattr(worker_tasks.concat_and_finalize, "apply", fake_finalize_apply)

    worker_tasks.merge_and_finalize_segments.run(
        [changed_result],
        str(project.id),
        slides,
        ["s1-p1"],
        avatar_options,
        job.id + 1,
    )

    unchanged = next(item for item in captured["results"] if item["page_key"] == "s2-p1")
    assert unchanged["slide_path"] == results[1]["slide_path"]

    source_changed_slides = deepcopy(slides)
    source_changed_slides[1]["source_render_method"] = "changed-renderer"
    reusable = worker_tasks._cached_slide_images_for_unchanged_pages(
        project_id=project.id,
        slides=source_changed_slides,
        previous_playback_assets=sidecar,
        avatar_options=avatar_options,
    )
    assert "s2-p1" not in reusable


@pytest.mark.django_db
def test_finalize_adds_deterministic_partial_render_analysis_from_previous_manifest(tmp_path, monkeypatch):
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("partial_analysis_owner")
    project = Project.objects.create(title="Partial analysis lesson", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    old_result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Old visible text",
        narration_text="Narration analysis text",
        project_id=project.id,
    )
    old_sidecar = {
        "partial_render_manifest": build_partial_render_manifest(
            project_id=project.id,
            job_id=job.id - 1,
            ordered_results=[old_result],
            playback_assets={},
            avatar_options=None,
        )
    }
    sidecar_path = tmp_path / str(project.id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(old_sidecar), encoding="utf-8")

    result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="New visible text",
        narration_text="Narration analysis text",
        project_id=project.id,
    )
    result["part_path"] = str(tmp_path / str(project.id) / "parts" / "part_001.mp4")
    result["slide_path"] = str(tmp_path / str(project.id) / "images" / "slide_001.png")
    result["tts_audio_path"] = str(tmp_path / str(project.id) / "audio" / "slide_001.mp3")

    finalize_result = worker_tasks.concat_and_finalize.run([result], str(project.id), False, None, job.id)

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    analysis = sidecar["partial_render_analysis"]
    rebuilt_analysis = worker_tasks._build_partial_render_analysis_report(
        previous_playback_assets=old_sidecar,
        current_playback_assets=sidecar,
    )
    classifier = analysis["classifier"]
    page_report = classifier["result"]["pages"]["s1-p1"]
    assert finalize_result["playback_assets"]["partial_render_analysis"] == analysis
    assert analysis == rebuilt_analysis
    assert analysis == worker_tasks._build_partial_render_analysis_report(
        previous_playback_assets=old_sidecar,
        current_playback_assets=sidecar,
    )
    assert classifier["available"] is True
    assert classifier["notes"] == []
    assert classifier["result"]["global_reasons"] == []
    assert page_report["classification"] == "display_text_changed"
    assert page_report["reasons"] == ["display_text_changed"]
    assert page_report["requires_full"] is False
    assert analysis["plan"]["pages"]["s1-p1"]["recommended_action"] == "recompose_visual_only_future"
    assert analysis["plan"]["pages"]["s1-p1"]["future_only"] is True
    assert analysis["plan"]["pages"]["s1-p1"]["actual_behavior_changed"] is False
    assert analysis["plan"]["summary"]["recompose_visual_only_future"] == 1


@pytest.mark.django_db
def test_finalize_records_narration_only_recompose_and_shifts_later_subtitle_timestamps(tmp_path, monkeypatch):
    from scripts import ffmpeg_helpers

    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("narration_finalize_owner")
    project = Project.objects.create(title="Narration finalize lesson", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    project_root = tmp_path / str(project.id)
    captured: dict[str, Any] = {"srt_cues": [], "vtt_cues": [], "hls": []}

    def fake_generate_srt_from_cues(cues, output_path):
        captured["srt_cues"] = list(cues)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("srt", encoding="utf-8")

    def fake_generate_vtt_from_cues(cues, output_path):
        captured["vtt_cues"] = list(cues)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("vtt", encoding="utf-8")

    def fake_package_hls_assets_for_playback(**kwargs):
        captured["hls"].append(kwargs["final_video"])
        return worker_tasks._hls_sidecar_payload(enabled=False, packaging_status="test_regenerated")

    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_vtt_from_cues)
    monkeypatch.setattr(worker_tasks, "_package_hls_assets_for_playback", fake_package_hls_assets_for_playback)

    old_first = _without_avatar(
        _render_result(
            index=0,
            page_key="s1-p1",
            display_text="Visible one",
            narration_text="Narration one",
            project_id=project.id,
        )
    )
    old_second = _without_avatar(
        _render_result(
            index=1,
            page_key="s2-p1",
            display_text="Visible two",
            narration_text="Narration two",
            project_id=project.id,
        )
    )
    old_first.update(
        {
            "duration": 2.0,
            "part_path": f"{project.id}/parts/part_001.mp4",
            "slide_path": f"{project.id}/images/slide_001.png",
            "tts_audio_path": f"{project.id}/audio/slide_001.mp3",
        }
    )
    old_second.update(
        {
            "duration": 3.0,
            "part_path": f"{project.id}/parts/part_002.mp4",
            "slide_path": f"{project.id}/images/slide_002.png",
            "tts_audio_path": f"{project.id}/audio/slide_002.mp3",
        }
    )
    old_sidecar = _old_sidecar_for_results(
        project.id,
        [old_first, old_second],
        avatar_options={"enabled": False, "requested": False},
    )
    sidecar_path = project_root / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(old_sidecar), encoding="utf-8")

    for rel_path in (
        "audio/slide_001.mp3",
        "audio/slide_002.mp3",
        "parts/part_001.mp4",
        "parts/part_002.mp4",
        "images/slide_001.png",
        "images/slide_002.png",
    ):
        path = project_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")

    first_result = {
        **old_first,
        "text": "Narration one updated",
        "narration_text": "Narration one updated",
        "spoken_text": "Narration one updated",
        "subtitle_chunks": ["Narration one updated"],
        "duration": 5.0,
        "narration_only_recomposed": True,
        "part_path": str(project_root / "parts" / "part_001.mp4"),
        "slide_path": str(project_root / "images" / "slide_001.png"),
        "tts_audio_path": str(project_root / "audio" / "slide_001.mp3"),
    }
    second_result = {
        **old_second,
        "duration": 3.0,
        "part_path": str(project_root / "parts" / "part_002.mp4"),
        "slide_path": str(project_root / "images" / "slide_002.png"),
        "tts_audio_path": str(project_root / "audio" / "slide_002.mp3"),
    }

    finalize_result = worker_tasks.concat_and_finalize.run(
        [first_result, second_result],
        str(project.id),
        False,
        {"enabled": False, "requested": False},
        job.id,
    )

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    second_cue = next(cue for cue in captured["srt_cues"] if cue["text"] == "Narration two")
    assert finalize_result["result_url"] == f"{project.id}/{project.id}.mp4"
    assert (project_root / f"{project.id}.mp4").is_file()
    assert (project_root / f"{project.id}.srt").is_file()
    assert (project_root / f"{project.id}.vtt").is_file()
    assert captured["hls"] == [str(project_root / f"{project.id}.mp4")]
    assert sidecar["hls"]["packaging_status"] == "test_regenerated"
    assert sidecar["narration_only_recomposed_count"] == 1
    assert sidecar["narration_only_recomposed_pages"] == ["s1-p1"]
    assert sidecar["final_segments"][0]["narration_only_recomposed"] is True
    assert sidecar["final_segments"][1]["narration_only_recomposed"] is False
    assert sidecar["partial_render_analysis"]["narration_only_recomposed_count"] == 1
    assert sidecar["partial_render_analysis"]["narration_only_recomposed_pages"] == ["s1-p1"]
    assert sidecar["timeline"][0]["duration"] == 5.0
    assert sidecar["timeline"][1]["start"] == 5.0
    assert float(second_cue["start"]) >= 5.0
    assert captured["vtt_cues"] == captured["srt_cues"]


@pytest.mark.django_db
def test_partial_render_plan_failure_does_not_fail_finalize(tmp_path, monkeypatch):
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("partial_plan_failure_owner")
    project = Project.objects.create(title="Partial plan failure lesson", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    old_result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Old visible text",
        narration_text="Narration plan text",
        project_id=project.id,
    )
    old_sidecar = {
        "partial_render_manifest": build_partial_render_manifest(
            project_id=project.id,
            job_id=job.id - 1,
            ordered_results=[old_result],
            playback_assets={},
            avatar_options=None,
        )
    }
    sidecar_path = tmp_path / str(project.id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(old_sidecar), encoding="utf-8")

    result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="New visible text",
        narration_text="Narration plan text",
        project_id=project.id,
    )
    part_path = tmp_path / str(project.id) / "parts" / "part_001.mp4"
    result["part_path"] = str(part_path)
    result["slide_path"] = str(tmp_path / str(project.id) / "images" / "slide_001.png")
    result["tts_audio_path"] = str(tmp_path / str(project.id) / "audio" / "slide_001.mp3")

    def fail_plan(_classifier_result):
        raise RuntimeError("plan failed")

    monkeypatch.setattr(worker_tasks, "build_partial_render_plan", fail_plan)

    finalize_result = worker_tasks.concat_and_finalize.run([result], str(project.id), False, None, job.id)

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    job.refresh_from_db()
    analysis = sidecar["partial_render_analysis"]
    assert job.status == "done"
    assert finalize_result["result_url"] == f"{project.id}/{project.id}.mp4"
    assert finalize_result["parts"] == [str(part_path)]
    assert analysis["classifier"]["result"]["pages"]["s1-p1"]["classification"] == "display_text_changed"
    assert analysis["plan"] == {
        "version": 1,
        "mode": "report_only",
        "summary": {},
        "pages": {},
        "notes": ["plan_failed"],
    }


@pytest.mark.django_db
def test_partial_render_analysis_failure_does_not_fail_finalize(tmp_path, monkeypatch):
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    owner = _make_user("partial_analysis_failure_owner")
    project = Project.objects.create(title="Partial analysis failure lesson", user=owner, status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    result = _render_result(
        index=0,
        page_key="s1-p1",
        display_text="Visible failure text",
        narration_text="Narration failure text",
        project_id=project.id,
    )
    part_path = tmp_path / str(project.id) / "parts" / "part_001.mp4"
    result["part_path"] = str(part_path)
    result["slide_path"] = str(tmp_path / str(project.id) / "images" / "slide_001.png")
    result["tts_audio_path"] = str(tmp_path / str(project.id) / "audio" / "slide_001.mp3")

    def fail_analysis(**_kwargs):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(worker_tasks, "_build_partial_render_analysis_report", fail_analysis)

    finalize_result = worker_tasks.concat_and_finalize.run([result], str(project.id), False, None, job.id)

    sidecar = json.loads((tmp_path / str(project.id) / "playback_assets.json").read_text(encoding="utf-8"))
    job.refresh_from_db()
    assert job.status == "done"
    assert finalize_result["result_url"] == f"{project.id}/{project.id}.mp4"
    assert finalize_result["parts"] == [str(part_path)]
    assert sidecar["partial_render_manifest"]["pages"]["s1-p1"]["artifacts"]["composed_segment"] == f"{project.id}/parts/part_001.mp4"
    assert sidecar["partial_render_analysis"] == {
        "version": 1,
        "mode": "report_only",
        "generated_from": "partial_render_manifest",
        "comparison_phase": "post_render_finalized",
        "comparison_roles": {
            "old": "previous_finalized_manifest",
            "expected": "current_finalized_manifest",
        },
        "classifier": {
            "available": False,
            "result": None,
            "notes": ["classification_failed"],
        },
        "plan": {
            "version": 1,
            "mode": "report_only",
            "summary": {},
            "pages": {},
            "notes": ["plan_failed"],
        },
    }
