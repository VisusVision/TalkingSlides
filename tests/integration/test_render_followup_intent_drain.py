import os
import sys
from pathlib import Path
from types import SimpleNamespace

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

from core.models import Job, Project, RenderFollowUpIntent, UserProfile  # noqa: E402
from core.render_followup_intents import merge_render_followup_intent  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _make_project(username: str) -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title=f"Follow-up drain {username}", user=user, status="processing")


def _prepare_lesson_upload(tmp_path: Path, project: Project) -> None:
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")


class _CapturedWorkerCelery:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def signature(self, name, args=None, kwargs=None):
        captured = self

        class _Signature:
            def apply_async(self, **options):
                captured.sent.append(
                    {
                        "name": name,
                        "args": list(args or []),
                        "kwargs": dict(kwargs or {}),
                        "options": dict(options),
                    }
                )
                return SimpleNamespace(id=f"task-followup-drain-{len(captured.sent)}")

        return _Signature()


class _FailingWorkerCelery:
    def signature(self, name, args=None, kwargs=None):
        class _Signature:
            def apply_async(self, **options):
                raise RuntimeError("broker unavailable")

        return _Signature()


def _capture_worker_dispatch(monkeypatch) -> _CapturedWorkerCelery:
    captured = _CapturedWorkerCelery()
    monkeypatch.setattr(worker_tasks, "app", captured)
    monkeypatch.setattr(worker_tasks, "_get_teacher_avatar_config", lambda *_args, **_kwargs: {"enabled": False})
    monkeypatch.setattr(worker_tasks, "_render_followup_voice_id", lambda *_args, **_kwargs: "voice-followup")
    return captured


def _patch_finalize_side_effects(monkeypatch, tmp_path: Path, *, concat_error: Exception | None = None) -> None:
    from scripts import ffmpeg_helpers

    def fake_concat_videos(_part_paths, final_video):
        if concat_error is not None:
            raise concat_error
        Path(final_video).parent.mkdir(parents=True, exist_ok=True)
        Path(final_video).write_text("video", encoding="utf-8")

    def fake_generate_srt_from_cues(cues, srt_path):
        Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
        Path(srt_path).write_text("\n".join(str(cue.get("text") or "") for cue in cues), encoding="utf-8")

    def fake_generate_vtt_from_cues(cues, vtt_path):
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
    monkeypatch.setattr(worker_tasks, "_notify_render_failed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_schedule_lesson_intelligence_after_worker_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_schedule_creator_analytics_after_worker_event", lambda *_args, **_kwargs: None)


def _render_result(tmp_path: Path, *, page_key: str = "s1-p1", text: str = "Updated caption") -> dict:
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
def test_successful_concat_finalization_dispatches_targeted_followup_intent(tmp_path, monkeypatch):
    project = _make_project("targeted")
    _prepare_lesson_upload(tmp_path, project)
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    intent = RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": job.id, "pause_sec": 1.5, "lang_hint": "tr", "source": "transcript"},
    )
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    captured = _capture_worker_dispatch(monkeypatch)

    result = worker_tasks.concat_and_finalize.run([_render_result(tmp_path)], str(project.id), False, None, job.id)

    job.refresh_from_db()
    intent.refresh_from_db()
    followup_job = Job.objects.get(project=project, job_type="video_export", status="pending")
    assert result["followup_dispatch"]["status"] == "dispatched"
    assert job.status == "done"
    assert intent.status == RenderFollowUpIntent.STATUS_CLEARED
    assert intent.metadata["dispatched_job_id"] == followup_job.id
    assert intent.metadata["celery_task_id"] == "task-followup-drain-1"
    assert followup_job.celery_task_id == "task-followup-drain-1"
    assert len(captured.sent) == 1
    assert captured.sent[0]["kwargs"]["job_id"] == followup_job.id
    assert captured.sent[0]["args"][4] == "tr"
    assert captured.sent[0]["args"][8] == ["s1-p1"]


@pytest.mark.django_db
def test_successful_concat_finalization_dispatches_full_followup_intent(tmp_path, monkeypatch):
    project = _make_project("full")
    _prepare_lesson_upload(tmp_path, project)
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_FULL,
        page_keys=[],
        metadata={"active_job_id": job.id},
    )
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    captured = _capture_worker_dispatch(monkeypatch)

    worker_tasks.concat_and_finalize.run([_render_result(tmp_path)], str(project.id), False, None, job.id)

    assert len(captured.sent) == 1
    assert captured.sent[0]["args"][8] == []


@pytest.mark.django_db
def test_followup_intent_active_job_mismatch_does_not_dispatch(tmp_path, monkeypatch):
    project = _make_project("mismatch")
    _prepare_lesson_upload(tmp_path, project)
    job = Job.objects.create(project=project, job_type="video_export", status="done", progress=100)
    intent = RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": job.id + 10},
    )
    captured = _capture_worker_dispatch(monkeypatch)

    result = worker_tasks._dispatch_claimed_render_followup_intent(project.id, job.id)

    intent.refresh_from_db()
    assert result["status"] == "none"
    assert intent.status == RenderFollowUpIntent.STATUS_CANCELLED
    assert intent.metadata["cancelled_reason"] == "active_job_id_mismatch"
    assert captured.sent == []
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1


@pytest.mark.django_db
def test_stale_finalize_does_not_dispatch_followup_intent(tmp_path, monkeypatch):
    project = _make_project("stale")
    stale_job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)
    RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": stale_job.id},
    )
    _patch_finalize_side_effects(monkeypatch, tmp_path)
    captured = _capture_worker_dispatch(monkeypatch)

    result = worker_tasks.concat_and_finalize.run([_render_result(tmp_path)], str(project.id), False, None, stale_job.id)

    assert result["status"] == "stale"
    assert captured.sent == []


@pytest.mark.django_db
def test_failed_finalize_does_not_dispatch_followup_intent(tmp_path, monkeypatch):
    project = _make_project("failed")
    _prepare_lesson_upload(tmp_path, project)
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=80)
    intent = RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": job.id},
    )
    _patch_finalize_side_effects(monkeypatch, tmp_path, concat_error=RuntimeError("concat failed"))
    captured = _capture_worker_dispatch(monkeypatch)

    with pytest.raises(RuntimeError):
        worker_tasks.concat_and_finalize.run([_render_result(tmp_path)], str(project.id), False, None, job.id)

    intent.refresh_from_db()
    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert captured.sent == []
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1


@pytest.mark.django_db
def test_followup_dispatch_failure_marks_job_failed_and_cancels_intent(tmp_path, monkeypatch):
    project = _make_project("dispatch_failure")
    _prepare_lesson_upload(tmp_path, project)
    completed_job = Job.objects.create(project=project, job_type="video_export", status="done", progress=100)
    intent = RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": completed_job.id},
    )
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "app", _FailingWorkerCelery())
    monkeypatch.setattr(worker_tasks, "_get_teacher_avatar_config", lambda *_args, **_kwargs: {"enabled": False})

    result = worker_tasks._dispatch_claimed_render_followup_intent(project.id, completed_job.id)

    intent.refresh_from_db()
    failed_job = Job.objects.get(project=project, job_type="video_export", status="failed")
    assert result["status"] == "failed"
    assert result["job_id"] == failed_job.id
    assert failed_job.error_message == "broker unavailable"
    assert intent.status == RenderFollowUpIntent.STATUS_CANCELLED
    assert intent.metadata["dispatch_error"] == "broker unavailable"


@pytest.mark.django_db
def test_two_drain_calls_produce_one_followup_job(tmp_path, monkeypatch):
    project = _make_project("duplicate")
    _prepare_lesson_upload(tmp_path, project)
    job = Job.objects.create(project=project, job_type="video_export", status="done", progress=100)
    RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": job.id},
    )
    captured = _capture_worker_dispatch(monkeypatch)

    first = worker_tasks._dispatch_claimed_render_followup_intent(project.id, job.id)
    second = worker_tasks._dispatch_claimed_render_followup_intent(project.id, job.id)

    assert first["status"] == "dispatched"
    assert second["status"] == "skipped"
    assert len(captured.sent) == 1
    assert Job.objects.filter(project=project, job_type="video_export").count() == 2


@pytest.mark.django_db
def test_new_edit_can_create_pending_intent_while_followup_render_is_active(tmp_path, monkeypatch):
    project = _make_project("new_edit")
    _prepare_lesson_upload(tmp_path, project)
    completed_job = Job.objects.create(project=project, job_type="video_export", status="done", progress=100)
    RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        metadata={"active_job_id": completed_job.id},
    )
    _capture_worker_dispatch(monkeypatch)
    worker_tasks._dispatch_claimed_render_followup_intent(project.id, completed_job.id)
    active_job = Job.objects.filter(project=project, job_type="video_export", status="pending").latest("id")

    pending = merge_render_followup_intent(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s2-p1"],
        reason="transcript_text_edit",
        metadata={"active_job_id": active_job.id, "source": "transcript"},
    )

    assert pending.status == RenderFollowUpIntent.STATUS_PENDING
    assert pending.page_keys == ["s2-p1"]
    assert RenderFollowUpIntent.objects.filter(project=project, status=RenderFollowUpIntent.STATUS_CLEARED).count() == 1
