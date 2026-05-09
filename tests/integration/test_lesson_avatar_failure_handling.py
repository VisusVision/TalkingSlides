import importlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
API_ROOT = SERVICES_ROOT / "api"
WORKER_ROOT = SERVICES_ROOT / "worker"
for path in [SERVICES_ROOT, API_ROOT, WORKER_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

worker_tasks = importlib.import_module("worker.tasks")  # noqa: E402


@pytest.fixture
def patched_slide_dependencies(tmp_path, monkeypatch):
    tts_client = importlib.import_module("scripts.tts_client")
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_tasks, "_record_avatar_render_job", lambda **kwargs: None)

    def fake_synthesize_text(_voice_id, _text, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"audio")

    def fake_create_slide_video(_image_path, _audio_path, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"slide-video")

    def fake_avatar_safe_image(image_path, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image_path, output_path)
        return output_path

    monkeypatch.setattr(tts_client, "synthesize_text", fake_synthesize_text)
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fake_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 1.25)
    monkeypatch.setattr(ffmpeg_helpers, "trim_trailing_silence", lambda _path: None)
    monkeypatch.setattr(worker_tasks, "_render_avatar_safe_slide_image", fake_avatar_safe_image)


def test_slide_render_continues_when_avatar_segment_validation_fails(tmp_path, monkeypatch, patched_slide_dependencies):
    slide_image = tmp_path / "slide.png"
    slide_image.write_bytes(b"png")
    source_image = tmp_path / "avatars" / "teacher.png"
    source_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"avatar")

    class FailedAvatarResult:
        result = RuntimeError(
            "liveportrait_motion_source_selection_bug: all_candidates_failed: video: "
            "canonical_input_failed: avatar_input_face_not_detected"
        )

        def failed(self):
            return True

    monkeypatch.setattr(
        worker_tasks,
        "render_avatar_segment",
        SimpleNamespace(apply=lambda **_kwargs: FailedAvatarResult()),
    )

    result = worker_tasks.synthesize_and_render_slide.run(
        {
            "index": 1,
            "slide_num": 2,
            "image_path": str(slide_image),
            "notes_text": "A lesson slide.",
            "audio_out": str(tmp_path / "audio" / "slide_002.mp3"),
            "part_out": str(tmp_path / "parts" / "part_002.mp4"),
        },
        project_id="129",
        voice_id="voice",
        pause_sec=0.0,
        lang_hint="en",
        tts_mode="service",
        avatar_options={
            "enabled": True,
            "teacher_id": 2,
            "source_image_rel_path": "avatars/teacher.png",
            "lipsync_engine": "liveportrait+musetalk",
        },
    )

    assert Path(result["part_path"]).exists()
    assert result["avatar_attempted"] is True
    assert result["avatar_applied"] is False
    assert result["avatar_failed"] is True
    assert result["avatar_status"] == "avatar_failed"
    assert "avatar_input_face_not_detected" in result["avatar_error"]
    assert "avatar_input_face_not_detected" in result["avatar_warning"]
    assert "avatar_input_face_not_detected" in result["avatar_failure_reason"]
    assert result["avatar_segment_rel_path"] == ""


def test_slide_render_skips_avatar_when_active_source_invalid(tmp_path, monkeypatch, patched_slide_dependencies):
    slide_image = tmp_path / "slide.png"
    slide_image.write_bytes(b"png")
    source_image = tmp_path / "avatars" / "teacher.png"
    source_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"blank-avatar")

    def fail_if_called(**_kwargs):
        raise AssertionError("render_avatar_segment should not run for invalid active avatar source")

    monkeypatch.setattr(worker_tasks, "render_avatar_segment", SimpleNamespace(apply=fail_if_called))

    result = worker_tasks.synthesize_and_render_slide.run(
        {
            "index": 0,
            "slide_num": 1,
            "image_path": str(slide_image),
            "notes_text": "A lesson slide.",
            "audio_out": str(tmp_path / "audio" / "slide_001.mp3"),
            "part_out": str(tmp_path / "parts" / "part_001.mp4"),
        },
        project_id="136",
        voice_id="voice",
        pause_sec=0.0,
        lang_hint="en",
        tts_mode="service",
        avatar_options={
            "enabled": True,
            "teacher_id": 4,
            "source_image_rel_path": "avatars/teacher.png",
            "avatar_source_valid": False,
            "avatar_source_validation_error": "avatar_input_face_not_detected",
            "avatar_source_hash": "invalid-source-hash",
            "lipsync_engine": "liveportrait+musetalk",
        },
    )

    assert Path(result["part_path"]).exists()
    assert result["avatar_attempted"] is False
    assert result["avatar_skipped"] is True
    assert result["avatar_applied"] is False
    assert result["avatar_failed"] is True
    assert result["avatar_status"] == "avatar_source_invalid"
    assert result["avatar_error"] == "avatar_input_face_not_detected"
    assert result["avatar_segment_rel_path"] == ""
    assert result["avatar_motion_validation"]["avatar_source_valid"] is False


def test_slide_render_attaches_avatar_when_segment_succeeds(tmp_path, monkeypatch, patched_slide_dependencies):
    slide_image = tmp_path / "slide.png"
    slide_image.write_bytes(b"png")
    source_image = tmp_path / "avatars" / "teacher.png"
    source_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"avatar")
    avatar_output = tmp_path / "129" / "avatar_segments" / "avatar_001.mp4"

    class SuccessfulAvatarResult:
        result = {
            "output_path": str(avatar_output),
            "engine_used": "liveportrait+musetalk",
            "fallback_chain_used": ["liveportrait", "musetalk"],
            "motion_validation": {"motion_real": True},
        }

        def failed(self):
            return False

    def fake_apply(**_kwargs):
        avatar_output.parent.mkdir(parents=True, exist_ok=True)
        avatar_output.write_bytes(b"avatar-video")
        return SuccessfulAvatarResult()

    monkeypatch.setattr(worker_tasks, "render_avatar_segment", SimpleNamespace(apply=fake_apply))

    result = worker_tasks.synthesize_and_render_slide.run(
        {
            "index": 0,
            "slide_num": 1,
            "image_path": str(slide_image),
            "notes_text": "A lesson slide.",
            "audio_out": str(tmp_path / "audio" / "slide_001.mp3"),
            "part_out": str(tmp_path / "parts" / "part_001.mp4"),
        },
        project_id="129",
        voice_id="voice",
        pause_sec=0.0,
        lang_hint="en",
        tts_mode="service",
        avatar_options={
            "enabled": True,
            "teacher_id": 2,
            "source_image_rel_path": "avatars/teacher.png",
            "avatar_source_valid": True,
            "avatar_preview_stale": False,
            "lipsync_engine": "liveportrait+musetalk",
        },
    )

    assert result["avatar_applied"] is True
    assert result["avatar_failed"] is False
    assert result["avatar_attempted"] is True
    assert result["avatar_status"] == "ready"
    assert result["avatar_error"] == ""
    assert result["avatar_failure_reason"] == ""
    assert result["avatar_segment_rel_path"] == "129/avatar_segments/avatar_001.mp4"
    assert result["avatar_engine_used"] == "liveportrait+musetalk"


def test_slide_render_still_fails_for_core_tts_failure(tmp_path, monkeypatch, patched_slide_dependencies):
    tts_client = importlib.import_module("scripts.tts_client")
    slide_image = tmp_path / "slide.png"
    slide_image.write_bytes(b"png")

    def fail_tts(*_args, **_kwargs):
        raise RuntimeError("tts_service_failed")

    monkeypatch.setattr(tts_client, "synthesize_text", fail_tts)

    with pytest.raises(RuntimeError, match="tts_service_failed"):
        worker_tasks.synthesize_and_render_slide.run(
            {
                "index": 0,
                "slide_num": 1,
                "image_path": str(slide_image),
                "notes_text": "A lesson slide.",
                "audio_out": str(tmp_path / "audio" / "slide_001.mp3"),
                "part_out": str(tmp_path / "parts" / "part_001.mp4"),
            },
            project_id="129",
            voice_id="voice",
            pause_sec=0.0,
            lang_hint="en",
            tts_mode="service",
            avatar_options={"enabled": True, "teacher_id": 2, "source_image_rel_path": "avatars/teacher.png"},
        )

    assert not (tmp_path / "parts" / "part_001.mp4").exists()


def test_concat_finalize_records_avatar_warning_without_failure(tmp_path, monkeypatch):
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    updates: list[dict] = []

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(worker_tasks, "_update_job", lambda _project_id, **kwargs: updates.append(kwargs))
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_update_transcript_timeline", lambda *_args, **_kwargs: None)

    def fake_concat_videos(_part_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"final-video")

    def fake_generate_srt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nSlide\n", encoding="utf-8")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)

    part = tmp_path / "129" / "parts" / "part_002.mp4"
    slide = tmp_path / "129" / "images" / "slide_002.png"
    audio = tmp_path / "129" / "audio" / "slide_002.mp3"
    for path in [part, slide, audio]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    result = worker_tasks.concat_and_finalize.run(
        [
            {
                "index": 1,
                "slide_num": 2,
                "page_key": "p2",
                "part_path": str(part),
                "duration": 1.25,
                "pause_seconds": 0.0,
                "text": "Slide",
                "slide_path": str(slide),
                "tts_audio_path": str(audio),
                "subtitle_chunks": ["Slide"],
                "avatar_applied": False,
                "avatar_attempted": False,
                "avatar_skipped": True,
                "avatar_failed": True,
                "avatar_status": "avatar_source_invalid",
                "avatar_error": "avatar_input_face_not_detected",
                "avatar_failure_reason": "avatar_input_face_not_detected",
                "avatar_motion_validation": {"failure_reason": "whole_frame_drift"},
                "avatar_segment_rel_path": "",
                "avatar_engine_used": "none",
                "avatar_fallback_chain": [],
            }
        ],
        project_id="129",
    )

    assert result["avatar_status"] == "avatar_source_invalid"
    assert result["avatar_failures"][0]["status"] == "avatar_source_invalid"
    assert result["avatar_failures"][0]["reason"] == "avatar_input_face_not_detected"
    assert updates[-1]["status"] == "done"
    assert "avatar_source_invalid:slide 2: avatar_input_face_not_detected" in updates[-1]["error_message"]

    sidecar = tmp_path / "129" / "playback_assets.json"
    assert sidecar.exists()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["avatar_clips"] == [""]
    assert sidecar_payload["avatar_slide_metadata"][0]["avatar_attempted"] is False
    assert sidecar_payload["avatar_slide_metadata"][0]["avatar_skipped"] is True
    assert sidecar_payload["avatar_slide_metadata"][0]["avatar_failed"] is True
    assert sidecar_payload["avatar_slide_metadata"][0]["avatar_status"] == "avatar_source_invalid"
    assert sidecar_payload["avatar_slide_metadata"][0]["avatar_error"] == "avatar_input_face_not_detected"
    assert sidecar_payload["final_segments"][0]["avatar_clip"] == ""


def test_render_chord_errback_marks_job_failed(monkeypatch):
    updates: list[dict] = []
    monkeypatch.setattr(worker_tasks, "_update_job", lambda _project_id, **kwargs: updates.append(kwargs))

    result = worker_tasks.mark_project_render_failed.run(
        "slide-task-id",
        RuntimeError("tts_service_failed"),
        "129",
    )

    assert result["status"] == "failed"
    assert updates[-1]["status"] == "failed"
    assert updates[-1]["progress"] == 100
    assert "tts_service_failed" in updates[-1]["error_message"]
