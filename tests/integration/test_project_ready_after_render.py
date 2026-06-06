# pyright: reportMissingImports=false

import importlib
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


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, *, status: str = "processing") -> Project:
    return Project.objects.create(
        title=f"Render ready {username}",
        user=_make_teacher(username),
        status=status,
        moderation_status="approved",
        is_published=False,
    )


def _patch_lightweight_finalizer(monkeypatch, tmp_path: Path, *, fail_concat: bool = False) -> None:
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_update_transcript_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "build_subtitle_cues_from_transcript_pages",
        lambda *_args, **_kwargs: [{"start": 0.0, "end": 1.0, "text": "Slide", "chunk_index": 0}],
    )

    def fake_concat_videos(_part_paths, output_path):
        if fail_concat:
            raise RuntimeError("concat failed")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"mp4")

    def fake_generate_srt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nSlide\n", encoding="utf-8")

    def fake_generate_vtt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSlide\n", encoding="utf-8")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_vtt_from_cues)


def _render_result(project: Project, tmp_path: Path) -> dict:
    part_path = tmp_path / str(project.id) / "parts" / "part_001.mp4"
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"part")
    return {
        "index": 0,
        "slide_num": 1,
        "page_key": "p1",
        "part_path": str(part_path),
        "duration": 1.0,
        "pause_seconds": 0.0,
        "text": "Slide",
        "original_text": "Slide",
        "spoken_text": "Slide",
        "subtitle_chunks": ["Slide"],
        "slide_path": "",
        "tts_audio_path": "",
        "avatar_applied": False,
        "avatar_attempted": False,
        "avatar_skipped": False,
        "avatar_failed": False,
        "avatar_status": "none",
        "avatar_error": "",
        "avatar_failure_reason": "",
        "avatar_segment_rel_path": "",
        "avatar_engine_used": "none",
        "avatar_fallback_chain": [],
        "avatar_motion_validation": {},
    }


@pytest.mark.django_db
def test_successful_render_finalization_marks_project_ready(tmp_path, monkeypatch):
    project = _make_project("ready_after_success", status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running")
    _patch_lightweight_finalizer(monkeypatch, tmp_path)

    result = worker_tasks.concat_and_finalize.run([_render_result(project, tmp_path)], str(project.id))

    project.refresh_from_db()
    job.refresh_from_db()
    assert project.status == "ready"
    assert job.status == "done"
    assert result["result_url"] == f"{project.id}/{project.id}.mp4"


@pytest.mark.django_db
def test_failed_render_finalization_does_not_mark_project_ready(tmp_path, monkeypatch):
    project = _make_project("ready_after_failure", status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running")
    _patch_lightweight_finalizer(monkeypatch, tmp_path, fail_concat=True)

    with pytest.raises(RuntimeError, match="concat failed"):
        worker_tasks.concat_and_finalize.run([_render_result(project, tmp_path)], str(project.id))

    project.refresh_from_db()
    job.refresh_from_db()
    assert project.status == "processing"
    assert job.status == "failed"
