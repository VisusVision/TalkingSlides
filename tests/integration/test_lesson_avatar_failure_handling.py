import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
API_ROOT = SERVICES_ROOT / "api"
WORKER_ROOT = SERVICES_ROOT / "worker"
for path in [SERVICES_ROOT, API_ROOT, WORKER_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

worker_tasks = importlib.import_module("worker.tasks")  # noqa: E402

from django.contrib.auth.models import User  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402


class _DummySession(dict):
    session_key = "test-session"

    def save(self):
        self.session_key = self.session_key or "test-session"


def _make_avatar_enabled_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed=f"avatars/{username}/processed.png",
        avatar_source_valid=True,
        avatar_moderation_status="approved",
    )
    return user


def _create_published_ready_avatar_lesson(teacher, *, title: str, avatar_processing_status: str):
    project = Project.objects.create(
        title=title,
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        avatar_enabled_override=True,
        avatar_processing_status=avatar_processing_status,
        avatar_visible=True,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
    )
    return project


@pytest.mark.django_db
def test_avatar_failure_does_not_block_published_lesson():
    teacher = _make_avatar_enabled_teacher("avatar_failed_public_teacher")
    project = _create_published_ready_avatar_lesson(
        teacher,
        title="Published Lesson With Failed Avatar",
        avatar_processing_status="failed",
    )

    factory = APIRequestFactory()
    catalog_response = views.CatalogListView.as_view()(factory.get("/api/v1/catalog/"))

    assert catalog_response.status_code == 200
    assert project.id in {item["id"] for item in catalog_response.data}

    token_request = factory.get(f"/api/v1/projects/{project.id}/playback-token/")
    token_request.session = _DummySession()
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        token_response = views.PlaybackTokenView.as_view()(token_request, project_id=project.id)

    assert token_response.status_code == 200
    assert token_response.data["video_url"]
    assert token_response.data["avatar_processing_status"] == "failed"
    assert token_response.data["avatar_available"] is False
    assert token_response.data["avatar_overlay"]["enabled"] is False

    project.refresh_from_db()
    assert project.status == "ready"
    assert project.is_published is True


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

    def fake_synthesize_text_with_metadata(voice_id, text, output_path, **kwargs):
        fake_synthesize_text(voice_id, text, output_path, **kwargs)
        return {
            "spoken_text": text,
            "provider": "test",
            "provider_preference": "test",
            "tts_normalization_language": kwargs.get("lang") or "en",
            "tts_normalization_rules_applied": [],
        }

    def fake_create_slide_video(_image_path, _audio_path, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"slide-video")

    def fake_avatar_safe_image(image_path, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image_path, output_path)
        return output_path

    monkeypatch.setattr(tts_client, "synthesize_text", fake_synthesize_text)
    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fake_synthesize_text_with_metadata)
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
    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fail_tts)

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


def test_concat_finalize_queues_avatar_after_base_video_without_blocking(tmp_path, monkeypatch):
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    updates: list[dict] = []
    queued: list[dict] = []

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(worker_tasks, "_update_job", lambda _project_id, **kwargs: updates.append(kwargs))
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_update_transcript_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "_queue_lesson_avatar_overlay_after_base_render",
        lambda **kwargs: queued.append(kwargs) or {"status": "queued", "queued": True, "job_id": 77},
    )

    def fake_concat_videos(_part_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"base-video")

    def fake_generate_srt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nSlide\n", encoding="utf-8")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_srt_from_cues)

    part = tmp_path / "129" / "parts" / "part_001.mp4"
    slide = tmp_path / "129" / "images" / "slide_001.png"
    audio = tmp_path / "129" / "audio" / "slide_001.mp3"
    for path in [part, slide, audio]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    result = worker_tasks.concat_and_finalize.run(
        [
            {
                "index": 0,
                "slide_num": 1,
                "page_key": "p1",
                "part_path": str(part),
                "duration": 1.0,
                "pause_seconds": 0.0,
                "text": "Slide",
                "slide_path": str(slide),
                "tts_audio_path": str(audio),
                "subtitle_chunks": ["Slide"],
                "avatar_applied": False,
                "avatar_attempted": False,
                "avatar_skipped": False,
                "avatar_failed": False,
                "avatar_status": "none",
                "avatar_error": "",
                "avatar_failure_reason": "",
                "avatar_motion_validation": {},
                "avatar_segment_rel_path": "",
                "avatar_engine_used": "none",
                "avatar_fallback_chain": [],
            }
        ],
        project_id="129",
        avatar_options={
            "requested": True,
            "enabled": True,
            "teacher_id": 2,
            "source_image_rel_path": "avatars/teacher.png",
            "avatar_source_valid": True,
            "avatar_preview_stale": False,
            "lipsync_engine": "liveportrait+musetalk",
        },
    )

    assert updates[-1]["status"] == "done"
    assert result["background_avatar"]["status"] == "queued"
    assert queued[0]["output_rel_prefix"] == "129"
    assert queued[0]["avatar_options"]["lipsync_engine"] == "liveportrait+musetalk"

    sidecar_payload = json.loads((tmp_path / "129" / "playback_assets.json").read_text(encoding="utf-8"))
    assert sidecar_payload["mp4_rel_path"] == "129/129.mp4"
    assert sidecar_payload["avatar"] is None
    assert sidecar_payload["avatar_status"] == "none"


def _patch_avatar_overlay_task_lightweight(tmp_path, monkeypatch):
    core_models = importlib.import_module("core.models")
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    job_updates: list[dict] = []
    project_updates: list[dict] = []
    profile_updates: list[dict] = []

    class FakeQuerySet:
        def __init__(self, row=None, updates=None):
            self.row = row
            self.updates = updates

        def filter(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        def values(self, *_args):
            return self

        def first(self):
            return self.row

        def update(self, **kwargs):
            if self.updates is not None:
                self.updates.append(kwargs)
            return 1

    class FakeJobObjects:
        def filter(self, **_kwargs):
            return FakeQuerySet(row={"id": 55}, updates=job_updates)

    class FakeProjectObjects:
        def filter(self, **_kwargs):
            return FakeQuerySet(row={"avatar_last_job_id": "77"}, updates=project_updates)

    class FakeUserProfileObjects:
        def filter(self, **_kwargs):
            return FakeQuerySet(updates=profile_updates)

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "_avatar_feature_enabled", lambda: True)
    monkeypatch.setattr(worker_tasks.render_lesson_avatar_overlay, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_tasks, "_avatar_job_is_current", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(worker_tasks, "_record_avatar_render_job", lambda **_kwargs: None)
    monkeypatch.setattr(core_models, "Job", SimpleNamespace(objects=FakeJobObjects()))
    monkeypatch.setattr(core_models, "Project", SimpleNamespace(objects=FakeProjectObjects()))
    monkeypatch.setattr(core_models, "UserProfile", SimpleNamespace(objects=FakeUserProfileObjects()))

    def fake_concat_videos(_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"avatar-track")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    return {"job": job_updates, "project": project_updates, "profile": profile_updates}


def test_avatar_handoff_manifest_helpers_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    payload = {
        "schema_version": 1,
        "project_id": 129,
        "base_job_id": 55,
        "avatar_job_id": 77,
        "created_at": "2026-05-10T00:00:00+00:00",
        "ordered_results": [{"index": 0, "tts_audio_path": "129/audio/slide_001.mp3"}],
        "avatar_settings": {"lipsync_engine": "liveportrait+musetalk"},
        "status": "created",
    }

    manifest_path = worker_tasks._write_avatar_handoff_manifest("129", "55", payload)

    assert Path(manifest_path) == tmp_path / "projects" / "129" / "renders" / "55" / "avatar_handoff.json"
    assert worker_tasks._read_avatar_handoff_manifest(manifest_path) == payload


def test_avatar_overlay_queue_writes_manifest_and_sends_path_only(tmp_path, monkeypatch):
    core_models = importlib.import_module("core.models")
    captured: dict = {}
    state_updates: list[dict] = []
    job_updates: list[dict] = []
    project_updates: list[dict] = []
    created_jobs: list[dict] = []

    class FakeAsyncResult:
        id = "celery-avatar-123"

    class FakeQuerySet:
        def __init__(self, row=None, updates=None):
            self.row = row
            self.updates = updates

        def filter(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        def values(self, *_args):
            return self

        def first(self):
            return self.row

        def update(self, **kwargs):
            if self.updates is not None:
                self.updates.append(kwargs)
            return 1

    class FakeJobObjects:
        def create(self, **kwargs):
            created_jobs.append(kwargs)
            return SimpleNamespace(id=77)

        def filter(self, **_kwargs):
            return FakeQuerySet(row={"id": 55}, updates=job_updates)

    class FakeProjectObjects:
        def filter(self, **_kwargs):
            return FakeQuerySet(updates=project_updates)

    def fake_apply_async(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeAsyncResult()

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "_avatar_feature_enabled", lambda: True)
    monkeypatch.setattr(worker_tasks, "_mark_project_avatar_state", lambda *_args, **kwargs: state_updates.append(kwargs))
    monkeypatch.setattr(core_models, "Job", SimpleNamespace(objects=FakeJobObjects()))
    monkeypatch.setattr(core_models, "Project", SimpleNamespace(objects=FakeProjectObjects()))
    monkeypatch.setattr(worker_tasks.render_lesson_avatar_overlay, "apply_async", fake_apply_async)

    ordered_results = [{"index": 0, "slide_num": 1, "tts_audio_path": "129/audio/slide_001.mp3"}]
    result = worker_tasks._queue_lesson_avatar_overlay_after_base_render(
        project_id="129",
        ordered_results=ordered_results,
        avatar_options={
            "requested": True,
            "enabled": True,
            "teacher_id": 2,
            "base_job_id": 55,
            "avatar_source_hash": "source-hash",
            "lipsync_engine": "liveportrait+musetalk",
        },
        output_rel_prefix="129",
    )

    assert result["status"] == "queued"
    assert created_jobs[0]["job_type"] == "avatar_render"
    task_kwargs = captured["kwargs"]["kwargs"]
    assert "render_results" not in task_kwargs
    assert "avatar_options" not in task_kwargs
    assert task_kwargs["handoff_manifest_path"] == result["handoff_manifest_path"]
    assert captured["kwargs"].get("args") in (None, [])
    manifest = json.loads(Path(result["handoff_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["base_job_id"] == 55
    assert manifest["avatar_job_id"] == 77
    assert manifest["ordered_results"] == ordered_results
    assert manifest["avatar_settings"]["lipsync_engine"] == "liveportrait+musetalk"
    assert state_updates[-1]["status"] == "queued"


def test_render_lesson_avatar_overlay_reads_handoff_manifest_path(tmp_path, monkeypatch):
    _patch_avatar_overlay_task_lightweight(tmp_path, monkeypatch)
    audio = tmp_path / "129" / "audio" / "slide_001.mp3"
    slide = tmp_path / "129" / "images" / "slide_001.png"
    source = tmp_path / "avatars" / "teacher.png"
    for path in [audio, slide, source]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (tmp_path / "129").mkdir(parents=True, exist_ok=True)
    (tmp_path / "129" / "playback_assets.json").write_text(
        json.dumps({"mp4_rel_path": "129/129.mp4", "final_segments": [{"index": 0}]}),
        encoding="utf-8",
    )

    class SuccessfulAvatarResult:
        result = {
            "output_path": str(tmp_path / "129" / "avatar_segments" / "avatar_001.mp4"),
            "engine_used": "liveportrait+musetalk",
            "fallback_chain_used": ["liveportrait", "musetalk"],
            "motion_validation": {"motion_real": True},
        }

        def failed(self):
            return False

    def fake_apply(**kwargs):
        output_path = Path(kwargs["kwargs"]["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"avatar-segment")
        return SuccessfulAvatarResult()

    monkeypatch.setattr(worker_tasks, "render_avatar_segment", SimpleNamespace(apply=fake_apply))
    manifest_path = worker_tasks._write_avatar_handoff_manifest(
        "129",
        "55",
        {
            "schema_version": 1,
            "project_id": 129,
            "base_job_id": 55,
            "avatar_job_id": 77,
            "created_at": "2026-05-10T00:00:00+00:00",
            "ordered_results": [
                {
                    "index": 0,
                    "slide_num": 1,
                    "page_key": "p1",
                    "text": "Slide",
                    "tts_audio_path": str(audio),
                    "slide_path": str(slide),
                    "duration": 1.0,
                }
            ],
            "avatar_settings": {
                "teacher_id": 2,
                "source_image_rel_path": "avatars/teacher.png",
                "avatar_source_valid": True,
                "avatar_preview_stale": False,
                "lipsync_engine": "liveportrait+musetalk",
            },
            "render_metadata": {"output_rel_prefix": "129"},
            "status": "created",
        },
    )

    result = worker_tasks.render_lesson_avatar_overlay.run(
        project_id=129,
        teacher_id=2,
        handoff_manifest_path=manifest_path,
        output_rel_prefix="129",
        avatar_job_id=77,
        base_job_id=55,
    )

    assert result["status"] == "ready"
    assert result["avatar_track_rel_path"] == "129/avatar/avatar_track_fast.mp4"
    assert result["avatar_fast_track_rel_path"] == "129/avatar/avatar_track_fast.mp4"
    sidecar_payload = json.loads((tmp_path / "129" / "playback_assets.json").read_text(encoding="utf-8"))
    assert sidecar_payload["avatar"]["track_rel_path"] == "129/avatar/avatar_track_fast.mp4"
    assert sidecar_payload["avatar"]["quality"] == "fast"
    assert sidecar_payload["avatar"]["enhanced_pending"] is False
    assert sidecar_payload["final_segments"][0]["avatar_clip"] == "129/avatar_segments/avatar_001.mp4"


def _run_progressive_avatar_overlay(tmp_path, monkeypatch, *, freshness_checks):
    _patch_avatar_overlay_task_lightweight(tmp_path, monkeypatch)
    canonical_adapters = importlib.import_module("avatar.canonical_adapters")
    avatar_pipeline = importlib.import_module("avatar.pipeline")

    audio = tmp_path / "129" / "audio" / "slide_001.mp3"
    slide = tmp_path / "129" / "images" / "slide_001.png"
    source = tmp_path / "avatars" / "teacher.png"
    for path in [audio, slide, source]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (tmp_path / "129").mkdir(parents=True, exist_ok=True)
    sidecar_path = tmp_path / "129" / "playback_assets.json"
    sidecar_path.write_text(
        json.dumps({"mp4_rel_path": "129/129.mp4", "final_segments": [{"index": 0}]}),
        encoding="utf-8",
    )

    class SuccessfulAvatarResult:
        result = {
            "output_path": str(tmp_path / "129" / "avatar_segments" / "avatar_001.mp4"),
            "engine_used": "liveportrait+musetalk",
            "fallback_chain_used": ["liveportrait", "musetalk"],
            "motion_validation": {"motion_real": True},
        }

        def failed(self):
            return False

    def fake_apply(**kwargs):
        output_path = Path(kwargs["kwargs"]["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"avatar-segment")
        return SuccessfulAvatarResult()

    project_state_updates = []
    current_project_avatar_state = {}
    completion_notifications = []

    def fake_mark_project_avatar_state(_project_id, **kwargs):
        project_state_updates.append(kwargs)
        current_project_avatar_state.update(kwargs)

    def fake_run_restoration(*, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"restored-segment")
        if len(freshness_checks) >= 3 and not freshness_checks[2]:
            current_project_avatar_state.update(
                {
                    "status": "queued",
                    "message": "Newer avatar job queued.",
                    "job_id": 88,
                    "output_path": "129/avatar/newer-avatar-track.mp4",
                }
            )
        return SimpleNamespace(success=True, error="")

    checks = iter(freshness_checks)

    def fake_is_current(*_args, **_kwargs):
        try:
            return bool(next(checks))
        except StopIteration:
            return bool(freshness_checks[-1])

    monkeypatch.setenv("AVATAR_PROGRESSIVE_RESTORATION_ENABLED", "1")
    monkeypatch.setattr(worker_tasks, "_avatar_job_is_current", fake_is_current)
    monkeypatch.setattr(worker_tasks, "_mark_project_avatar_state", fake_mark_project_avatar_state)
    monkeypatch.setattr(worker_tasks, "_notify_avatar_completed", lambda *args, **kwargs: completion_notifications.append((args, kwargs)))
    monkeypatch.setattr(worker_tasks, "render_avatar_segment", SimpleNamespace(apply=fake_apply))
    monkeypatch.setattr(canonical_adapters, "run_restoration", fake_run_restoration)
    monkeypatch.setattr(avatar_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: None)

    manifest_path = worker_tasks._write_avatar_handoff_manifest(
        "129",
        "55",
        {
            "schema_version": 1,
            "project_id": 129,
            "base_job_id": 55,
            "avatar_job_id": 77,
            "created_at": "2026-05-10T00:00:00+00:00",
            "ordered_results": [
                {
                    "index": 0,
                    "slide_num": 1,
                    "page_key": "p1",
                    "text": "Slide",
                    "tts_audio_path": str(audio),
                    "slide_path": str(slide),
                    "duration": 1.0,
                }
            ],
            "avatar_settings": {
                "teacher_id": 2,
                "source_image_rel_path": "avatars/teacher.png",
                "avatar_source_valid": True,
                "avatar_preview_stale": False,
                "lipsync_engine": "liveportrait+musetalk",
                "restoration_enabled": True,
                "avatar_runtime_settings": {
                    "motion_preset": "natural",
                    "restoration_enabled": True,
                    "liveportrait_enabled": True,
                },
            },
            "render_metadata": {"output_rel_prefix": "129"},
            "status": "created",
        },
    )

    result = worker_tasks.render_lesson_avatar_overlay.run(
        project_id=129,
        teacher_id=2,
        handoff_manifest_path=manifest_path,
        output_rel_prefix="129",
        avatar_job_id=77,
        base_job_id=55,
    )

    sidecar_payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    return result, sidecar_payload, project_state_updates, current_project_avatar_state, completion_notifications


def test_stale_lesson_avatar_restoration_does_not_publish_final_state(tmp_path, monkeypatch):
    result, sidecar_payload, project_state_updates, current_project_avatar_state, completion_notifications = _run_progressive_avatar_overlay(
        tmp_path,
        monkeypatch,
        freshness_checks=[True, True, False],
    )

    assert result["status"] == "stale"
    assert sidecar_payload["avatar"]["track_rel_path"] == "129/avatar/avatar_track_fast.mp4"
    assert sidecar_payload["avatar"]["quality"] == "fast"
    assert sidecar_payload["avatar"]["enhanced_pending"] is True
    assert sidecar_payload["avatar"].get("track_restored_rel_path", "") == ""
    assert sidecar_payload["avatar_restoration_status"] == "restoring"
    assert [update["message"] for update in project_state_updates] == [
        "Avatar is still processing and will be added when ready.",
        "Avatar ready. Enhanced avatar restoration is still processing.",
    ]
    assert current_project_avatar_state["job_id"] == 88
    assert current_project_avatar_state["output_path"] == "129/avatar/newer-avatar-track.mp4"
    assert completion_notifications == []


def test_current_lesson_avatar_restoration_publishes_final_state(tmp_path, monkeypatch):
    result, sidecar_payload, project_state_updates, _current_project_avatar_state, completion_notifications = _run_progressive_avatar_overlay(
        tmp_path,
        monkeypatch,
        freshness_checks=[True, True, True, True],
    )

    assert result["status"] == "ready"
    assert result["avatar_track_rel_path"] == "129/avatar/avatar_track_restored.mp4"
    assert sidecar_payload["avatar"]["track_rel_path"] == "129/avatar/avatar_track_restored.mp4"
    assert sidecar_payload["avatar"]["quality"] == "restored"
    assert sidecar_payload["avatar"]["enhanced_available"] is True
    assert sidecar_payload["avatar"]["enhanced_pending"] is False
    assert sidecar_payload["avatar_restoration_status"] == "restored"
    assert project_state_updates[-1]["message"] == "Enhanced avatar ready."
    assert completion_notifications


def test_render_lesson_avatar_overlay_accepts_legacy_ordered_results(tmp_path, monkeypatch):
    state_updates: list[dict] = []
    _patch_avatar_overlay_task_lightweight(tmp_path, monkeypatch)
    monkeypatch.setattr(worker_tasks, "_mark_project_avatar_state", lambda *_args, **kwargs: state_updates.append(kwargs))

    result = worker_tasks.render_lesson_avatar_overlay.run(
        project_id=129,
        teacher_id=2,
        render_results=[{"index": 0, "slide_num": 1, "tts_audio_path": str(tmp_path / "missing.mp3")}],
        avatar_options={
            "teacher_id": 2,
            "avatar_source_valid": False,
            "avatar_source_validation_error": "avatar_input_face_not_detected",
            "lipsync_engine": "liveportrait+musetalk",
        },
        output_rel_prefix="129",
        avatar_job_id=77,
    )

    assert result["status"] == "failed"
    assert result["avatar_failures"][0]["status"] == "avatar_source_invalid"
    assert state_updates[-1]["status"] == "failed"


def test_missing_avatar_handoff_manifest_fails_avatar_only(tmp_path, monkeypatch):
    state_updates: list[dict] = []
    _patch_avatar_overlay_task_lightweight(tmp_path, monkeypatch)
    monkeypatch.setattr(worker_tasks, "_mark_project_avatar_state", lambda *_args, **kwargs: state_updates.append(kwargs))
    sidecar_path = tmp_path / "129" / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_text(json.dumps({"mp4_rel_path": "129/129.mp4", "avatar": {"track_rel_path": "old.mp4"}}), encoding="utf-8")

    result = worker_tasks.render_lesson_avatar_overlay.run(
        project_id=129,
        teacher_id=2,
        handoff_manifest_path=str(tmp_path / "missing" / "avatar_handoff.json"),
        output_rel_prefix="129",
        avatar_job_id=77,
        base_job_id=55,
    )

    assert result["status"] == "failed"
    assert result["avatar_failures"][0]["status"] == "avatar_handoff_unavailable"
    assert state_updates[-1]["status"] == "failed"
    sidecar_payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_payload["mp4_rel_path"] == "129/129.mp4"
    assert sidecar_payload["avatar"] is None
    assert sidecar_payload["avatar_status"] == "failed"


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
