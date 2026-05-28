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


def _make_project(username: str) -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title=f"Scoped render {username}", user=user, status="processing")


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
def test_stale_finalize_guard_skips_side_effects(tmp_path, monkeypatch):
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
    assert stale_job.status == "running"
    assert current_job.status == "pending"
    assert not (tmp_path / str(project.id) / "playback_assets.json").exists()


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
