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

from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _make_project(username: str) -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title=f"Scoped render {username}", user=user, status="processing")


def _patch_finalize_side_effects(monkeypatch, tmp_path):
    from scripts import ffmpeg_helpers

    generated_cues = {"srt": [], "vtt": []}

    def fake_concat_videos(_part_paths, final_video):
        Path(final_video).parent.mkdir(parents=True, exist_ok=True)
        Path(final_video).write_text("video", encoding="utf-8")

    def fake_generate_srt_from_cues(cues, srt_path):
        generated_cues["srt"] = list(cues)
        Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
        Path(srt_path).write_text("\n".join(str(cue.get("text") or "") for cue in cues), encoding="utf-8")

    def fake_generate_vtt_from_cues(cues, vtt_path):
        generated_cues["vtt"] = list(cues)
        Path(vtt_path).parent.mkdir(parents=True, exist_ok=True)
        Path(vtt_path).write_text("\n".join(str(cue.get("text") or "") for cue in cues), encoding="utf-8")

    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_vtt_from_cues)
    monkeypatch.setattr(
        worker_tasks,
        "_package_hls_assets_for_playback",
        lambda **_kwargs: worker_tasks._hls_sidecar_payload(enabled=False, packaging_status="not_required"),
    )
    monkeypatch.setattr(worker_tasks, "_run_auto_video_frame_audit_after_render", lambda *_args, **_kwargs: {"enabled": False})
    monkeypatch.setattr(worker_tasks, "_notify_render_completed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_schedule_lesson_intelligence_after_worker_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_schedule_creator_analytics_after_worker_event", lambda *_args, **_kwargs: None)
    return generated_cues


def _render_result(tmp_path, *, page_key: str = "s1-p1", text: str = "Snapshot caption") -> dict:
    return {
        "index": 0,
        "slide_num": 1,
        "page_key": page_key,
        "source_slide_index": 0,
        "split_index": 0,
        "part_path": str(tmp_path / "part_001.mp4"),
        "duration": 2.0,
        "pause_seconds": 0.0,
        "text": text,
        "original_text": text,
        "display_text": text,
        "slide_path": str(tmp_path / "slide_001.png"),
        "tts_audio_path": str(tmp_path / "slide_001.mp3"),
        "subtitle_chunks": [text],
        "whiteboard_mode": False,
        "avatar_applied": False,
        "avatar_failed": False,
    }


@pytest.mark.django_db
def test_update_job_by_id_updates_only_requested_row():
    project = _make_project("job_scoped_update")
    stale_job = Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    latest_job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)

    updated = worker_tasks._update_job_by_id(
        stale_job.id,
        status="done",
        progress=100,
        result_url=f"{project.id}/{project.id}.mp4",
    )

    assert updated is True
    stale_job.refresh_from_db()
    latest_job.refresh_from_db()
    assert stale_job.status == "done"
    assert stale_job.progress == 100
    assert latest_job.status == "pending"
    assert latest_job.progress == 0


@pytest.mark.django_db
def test_job_scoped_update_prevents_latest_job_overwrite_regression():
    project = _make_project("job_scoped_latest_regression")
    original_job = Job.objects.create(project=project, job_type="video_export", status="running", progress=25)
    newer_job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)

    worker_tasks._update_render_job(project.id, original_job.id, status="failed", progress=100, error_message="old replay")

    original_job.refresh_from_db()
    newer_job.refresh_from_db()
    assert original_job.status == "failed"
    assert original_job.error_message == "old replay"
    assert newer_job.status == "pending"
    assert newer_job.error_message == ""


@pytest.mark.django_db
def test_stale_finalize_guard_marks_stale_job_terminal_without_side_effects(tmp_path, monkeypatch):
    project = _make_project("stale_finalize_guard")
    stale_job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    current_job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("stale finalize must not render or write artifacts")

    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", fail_if_called)
    result = worker_tasks.concat_and_finalize.run(
        [{"index": 0, "part_path": str(tmp_path / "missing.mp4"), "duration": 1.0}],
        str(project.id),
        False,
        None,
        stale_job.id,
    )

    stale_job.refresh_from_db()
    current_job.refresh_from_db()
    assert result["status"] == "stale"
    assert result["skipped"] is True
    assert stale_job.status == "failed"
    assert stale_job.progress == 100
    assert stale_job.error_message == "stale_render_job_skipped"
    assert current_job.status == "pending"
    assert current_job.progress == 0
    assert current_job.error_message == ""
    assert not (tmp_path / str(project.id) / "playback_assets.json").exists()


@pytest.mark.django_db
def test_finalize_captions_use_render_snapshot_when_transcript_changes_after_render_start(tmp_path, monkeypatch):
    project = _make_project("caption_snapshot_full")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Snapshot caption",
        narration_text="Snapshot caption",
        subtitle_chunks=["Snapshot caption"],
    )
    result = _render_result(tmp_path, text="Snapshot caption")
    page.narration_text = "Edited after render started"
    page.original_text = "Edited after render started"
    page.subtitle_chunks = ["Edited after render started"]
    page.save(update_fields=["narration_text", "original_text", "subtitle_chunks", "updated_at"])
    generated = _patch_finalize_side_effects(monkeypatch, tmp_path)
    monkeypatch.setattr(
        worker_tasks,
        "build_subtitle_cues_from_transcript_pages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("captions must not reread transcript rows")),
    )

    finalize_result = worker_tasks.concat_and_finalize.run([result], str(project.id), False, None, job.id)

    job.refresh_from_db()
    assert job.status == "done"
    assert finalize_result["job_id"] == job.id
    assert [cue["text"] for cue in generated["srt"]] == ["Snapshot caption"]
    assert all("Edited after render started" not in cue["text"] for cue in generated["srt"])


@pytest.mark.django_db
def test_targeted_finalize_captions_use_merged_render_snapshot_not_latest_transcript(tmp_path, monkeypatch):
    project = _make_project("caption_snapshot_targeted")
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Targeted snapshot",
        narration_text="Targeted snapshot",
        subtitle_chunks=["Targeted snapshot"],
    )
    slide_snapshot = {
        "index": 0,
        "slide_num": 1,
        "page_key": "s1-p1",
        "source_slide_index": 0,
        "split_index": 0,
        "part_out": str(tmp_path / "part_001.mp4"),
        "audio_out": str(tmp_path / "slide_001.mp3"),
        "pause_seconds": 0.0,
        "narration_text": "Targeted snapshot",
        "original_text": "Targeted snapshot",
        "display_text": "Targeted snapshot",
        "subtitle_chunks": ["Targeted snapshot"],
    }
    page.narration_text = "Edited after targeted render started"
    page.original_text = "Edited after targeted render started"
    page.subtitle_chunks = ["Edited after targeted render started"]
    page.save(update_fields=["narration_text", "original_text", "subtitle_chunks", "updated_at"])
    generated = _patch_finalize_side_effects(monkeypatch, tmp_path)
    monkeypatch.setattr(worker_tasks, "build_subtitle_cues_from_transcript_pages", lambda *_args, **_kwargs: [])

    finalize_result = worker_tasks.merge_and_finalize_segments.run(
        [],
        str(project.id),
        [slide_snapshot],
        ["missing-key"],
        None,
        job.id,
    )

    job.refresh_from_db()
    assert job.status == "done"
    assert finalize_result["job_id"] == job.id
    assert [cue["text"] for cue in generated["srt"]] == ["Targeted snapshot"]
    assert all("Edited after targeted render started" not in cue["text"] for cue in generated["srt"])


def test_write_json_sidecar_replaces_atomically_and_cleans_temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    target = tmp_path / "42" / "playback_assets.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"old": true}', encoding="utf-8")

    path = worker_tasks._write_json_sidecar("42", "playback_assets.json", {"new": True})

    assert Path(path) == target
    assert target.read_text(encoding="utf-8") == '{\n  "new": true\n}'
    assert list(target.parent.glob(".playback_assets.json.*.tmp")) == []


def test_write_avatar_handoff_manifest_replaces_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    target = tmp_path / "projects" / "42" / "renders" / "99" / "avatar_handoff.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"old": true}', encoding="utf-8")

    path = worker_tasks._write_avatar_handoff_manifest("42", "99", {"schema_version": 1, "project_id": 42})

    assert Path(path) == target
    assert target.read_text(encoding="utf-8") == '{\n  "schema_version": 1,\n  "project_id": 42\n}'
    assert list(target.parent.glob(".avatar_handoff.json.*.tmp")) == []
