# pyright: reportMissingImports=false

from copy import deepcopy
import json
import os
import sys
from pathlib import Path

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

from core.models import Job, Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.partial_render_manifest import (  # noqa: E402
    build_expected_partial_render_manifest,
    build_partial_render_manifest,
    classify_partial_render_changes,
    canonical_json,
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


def test_hash_helpers_are_stable():
    left = {"b": [2, 1], "a": {"z": True, "m": None}}
    right = {"a": {"m": None, "z": True}, "b": [2, 1]}

    assert canonical_json(left) == canonical_json(right)
    assert stable_hash(left) == stable_hash(right)
    assert normalize_text("  Alpha \r\nBeta  ") == "Alpha\nBeta"
    assert stable_hash(normalize_text("Alpha\r\nBeta")) == stable_hash(normalize_text("Alpha\nBeta"))


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
    assert sidecar["mp4_rel_path"] == f"{project.id}/{project.id}.mp4"
    assert sidecar["srt_rel_path"] == f"{project.id}/{project.id}.srt"
    assert sidecar["vtt_rel_path"] == f"{project.id}/{project.id}.vtt"
    assert sidecar["final_segments"][0]["part_rel_path"] == f"{project.id}/parts/part_001.mp4"
    assert finalize_result["playback_assets"]["partial_render_manifest"] == manifest
    assert manifest["job_id"] == job.id
    assert manifest["pages"]["s1-p1"]["artifacts"] == {
        "tts_audio": f"{project.id}/audio/slide_001.mp3",
        "avatar_clip": "",
        "composed_segment": f"{project.id}/parts/part_001.mp4",
        "slide_image": f"{project.id}/images/slide_001.png",
    }
