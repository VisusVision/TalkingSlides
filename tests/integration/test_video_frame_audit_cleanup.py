# pyright: reportMissingImports=false

import os
import sys
import time
from io import StringIO
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.core.management import call_command
from PIL import Image

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

from ai_agents.models import AgentFinding, AgentRun  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.video_frame_moderation import SampledVideoFrame, VideoFrameSamplingResult  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str) -> Project:
    return Project.objects.create(
        title=f"Frame cleanup {username}",
        user=_make_teacher(username),
        status="ready",
        moderation_status="approved",
    )


def _frame_base(storage_root: Path) -> Path:
    return storage_root / "moderation" / "video_frames"


def _save_image(path: Path, *, valid: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if valid:
        Image.new("RGB", (24, 24), color=(40, 90, 140)).save(path)
    else:
        path.write_bytes(b"not an image")
    return path


def _sampling_result(frame_path: Path) -> VideoFrameSamplingResult:
    return VideoFrameSamplingResult(
        video_path="final.mp4",
        output_dir=str(frame_path.parent),
        sampled_frames=[
            SampledVideoFrame(
                frame_path=str(frame_path),
                timestamp_seconds=0.0,
                timestamp_label="00:00:00",
            )
        ],
        success=True,
        error_message="",
        ffmpeg_path="ffmpeg",
    )


def _enable_audit(monkeypatch, *, retain: bool = False, cleanup: bool = True) -> None:
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_PHASE", "video_frame_audit", raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_EVERY_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_MAX_FRAMES", 5, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK", True, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RUN_OCR", False, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RETAIN_FRAMES", retain, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_CLEANUP_ON_SUCCESS", cleanup, raising=False)


def test_cleanup_helper_deletes_only_under_video_frame_base(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    inside = _save_image(_frame_base(storage_root) / "1" / "2" / "frame.jpg")
    outside = _save_image(tmp_path / "outside.jpg")

    result = worker_tasks._cleanup_video_frame_audit_files([inside, outside], reason="test")

    assert result["deleted_files"] == 1
    assert result["skipped"] == 1
    assert not inside.exists()
    assert outside.exists()


def test_cleanup_helper_safe_when_path_missing(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))

    result = worker_tasks._cleanup_video_frame_audit_files(_frame_base(storage_root) / "missing.jpg")

    assert result["deleted_files"] == 0
    assert result["skipped"] == 1


def test_cleanup_helper_does_not_delete_outside_allowed_base(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    outside = _save_image(tmp_path / "keep.jpg")

    result = worker_tasks._cleanup_video_frame_audit_files(outside)

    assert result["deleted_files"] == 0
    assert result["skipped"] == 1
    assert outside.exists()


@pytest.mark.django_db
def test_default_retain_false_cleans_up_sampled_frames_after_success(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    _enable_audit(monkeypatch, retain=False, cleanup=True)
    project = _make_project("default_cleanup")
    frame = _save_image(_frame_base(storage_root) / str(project.id) / "42" / "frame.jpg")
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.sample_video_frames", lambda **_kwargs: _sampling_result(frame))

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 42, tmp_path / "final.mp4")

    assert result["status"] == "done"
    assert result["cleanup"]["deleted_files"] >= 1
    assert not frame.exists()
    assert AgentRun.objects.filter(project=project, phase="video_frame_audit").exists()


@pytest.mark.django_db
def test_retain_true_keeps_sampled_frames(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    _enable_audit(monkeypatch, retain=True, cleanup=True)
    project = _make_project("retain_frames")
    frame = _save_image(_frame_base(storage_root) / str(project.id) / "43" / "frame.jpg")
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.sample_video_frames", lambda **_kwargs: _sampling_result(frame))

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 43, tmp_path / "final.mp4")

    assert result["status"] == "done"
    assert result["cleanup"]["enabled"] is False
    assert result["cleanup"]["reason"] == "retained"
    assert frame.exists()


def test_cleanup_command_dry_run_deletes_nothing_and_reports_candidates(settings, tmp_path):
    storage_root = tmp_path / "storage"
    settings.STORAGE_ROOT = str(storage_root)
    old_file = _save_image(_frame_base(storage_root) / "1" / "1" / "old.jpg")
    _make_old(old_file, days=10)
    out = StringIO()

    call_command("cleanup_video_frame_audit_files", days=7, dry_run=True, stdout=out)

    output = out.getvalue()
    assert "Dry run: True" in output
    assert "Candidate files: 1" in output
    assert "Deleted files: 0" in output
    assert old_file.exists()


def test_cleanup_command_deletes_old_files_older_than_retention(settings, tmp_path):
    storage_root = tmp_path / "storage"
    settings.STORAGE_ROOT = str(storage_root)
    old_file = _save_image(_frame_base(storage_root) / "2" / "2" / "old.jpg")
    new_file = _save_image(_frame_base(storage_root) / "2" / "2" / "new.jpg")
    _make_old(old_file, days=10)

    out = StringIO()
    call_command("cleanup_video_frame_audit_files", days=7, stdout=out)

    output = out.getvalue()
    assert "Deleted files: 1" in output
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_command_all_deletes_all_under_base(settings, tmp_path):
    storage_root = tmp_path / "storage"
    settings.STORAGE_ROOT = str(storage_root)
    first = _save_image(_frame_base(storage_root) / "3" / "a" / "one.jpg")
    second = _save_image(_frame_base(storage_root) / "4" / "b" / "two.jpg")

    out = StringIO()
    call_command("cleanup_video_frame_audit_files", all=True, stdout=out)

    assert "All: True" in out.getvalue()
    assert not first.exists()
    assert not second.exists()
    assert list(_frame_base(storage_root).glob("*")) == []


@pytest.mark.django_db
def test_cleanup_failure_does_not_fail_audit(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    _enable_audit(monkeypatch, retain=False, cleanup=True)
    project = _make_project("cleanup_failure")
    frame = _save_image(_frame_base(storage_root) / str(project.id) / "44" / "frame.jpg")
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.sample_video_frames", lambda **_kwargs: _sampling_result(frame))

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(worker_tasks, "_cleanup_video_frame_audit_files", fail_cleanup)

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 44, tmp_path / "final.mp4")

    assert result["status"] == "done"
    assert result["cleanup"]["reason"] == "cleanup_failed"
    assert AgentRun.objects.filter(project=project, phase="video_frame_audit").exists()


@pytest.mark.django_db
def test_audit_failure_after_sampling_cleans_up_frames(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    _enable_audit(monkeypatch, retain=False, cleanup=True)
    project = _make_project("post_sample_failure")
    frame = _save_image(_frame_base(storage_root) / str(project.id) / "46" / "frame.jpg")
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.sample_video_frames", lambda **_kwargs: _sampling_result(frame))

    def fail_scan(*_args, **_kwargs):
        raise RuntimeError("visual scan failed")

    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.VideoFrameModerationAgent.scan_frame", fail_scan)

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 46, tmp_path / "final.mp4")

    assert result["status"] == "failed"
    assert result["cleanup"]["deleted_files"] >= 1
    assert not frame.exists()
    assert AgentRun.objects.filter(project=project, phase="video_frame_audit", status="failed").exists()


@pytest.mark.django_db
def test_agent_run_and_finding_metadata_remain_after_frame_cleanup(monkeypatch, tmp_path):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(storage_root))
    _enable_audit(monkeypatch, retain=False, cleanup=True)
    project = _make_project("metadata_retained")
    frame = _save_image(_frame_base(storage_root) / str(project.id) / "45" / "bad.jpg", valid=False)
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.sample_video_frames", lambda **_kwargs: _sampling_result(frame))

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 45, tmp_path / "final.mp4")

    finding = AgentFinding.objects.get(run__project=project, run__phase="video_frame_audit")
    assert result["status"] == "done"
    assert not frame.exists()
    assert finding.location["frame_path"] == str(frame)
    assert finding.provider_raw["error"] in {"UnidentifiedImageError", "OSError"}


def _make_old(path: Path, *, days: int) -> None:
    old_time = time.time() - days * 24 * 60 * 60
    os.utime(path, (old_time, old_time))
    try:
        os.utime(path.parent, (old_time, old_time))
    except OSError:
        pass
