# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.test.utils import override_settings

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
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, JobActionAudit, JobCheckpoint, Project, UserProfile  # noqa: E402
from worker import tasks  # noqa: E402


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


class _CapturedDispatch:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, task_name: str, *, args=None, kwargs=None, queue=None):
        self.calls.append(
            {
                "task_name": task_name,
                "args": list(args or []),
                "kwargs": dict(kwargs or {}),
                "queue": queue,
            }
        )
        return SimpleNamespace(id=f"cleanup-{len(self.calls)}")


@pytest.mark.django_db
def test_job_cancel_endpoint_marks_job_and_dispatches_cleanup(monkeypatch):
    teacher = _make_teacher("cancel_dispatch_teacher")
    project = Project.objects.create(title="Cancel flow", user=teacher, render_profile="balanced")
    job = Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-123")

    captured = _CapturedDispatch()
    monkeypatch.setattr(views, "_dispatch_celery_task", captured)
    monkeypatch.setattr(views, "_resolve_avatar_options_for_project", lambda *_args, **_kwargs: {"enabled": False})

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{job.id}/cancel/",
        {"reason": "manual stop"},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.JobCancelView.as_view()(request, project_id=project.id, job_id=job.id)
    assert response.status_code == 200
    assert bool(response.data.get("cancelled")) is True

    job.refresh_from_db()
    assert job.status == "cancelled"
    assert "__cancelled_by_user__" in str(job.error_message or "")

    assert captured.calls, "cleanup dispatch was not called"
    assert captured.calls[0]["task_name"] == "worker.tasks.cleanup_cancelled_project_artifacts"
    assert int(captured.calls[0]["kwargs"]["project_id"]) == int(project.id)
    assert int(captured.calls[0]["kwargs"]["job_id"]) == int(job.id)
    audit = JobActionAudit.objects.filter(job=job, action="cancel_requested").first()
    assert audit is not None
    assert int(audit.project_id) == int(project.id)
    assert int(audit.actor_id) == int(teacher.id)


@pytest.mark.django_db
def test_job_cancel_endpoint_rate_limit_returns_429(monkeypatch):
    teacher = _make_teacher("cancel_rate_teacher")
    project = Project.objects.create(title="Cancel limit flow", user=teacher, render_profile="balanced")
    job = Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-rl")

    monkeypatch.setattr(views, "JOB_CANCEL_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(views, "_dispatch_celery_task", lambda *args, **kwargs: SimpleNamespace(id="noop"))

    request1 = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{job.id}/cancel/",
        {"reason": "first"},
        format="json",
    )
    force_authenticate(request1, user=teacher)
    resp1 = views.JobCancelView.as_view()(request1, project_id=project.id, job_id=job.id)
    assert resp1.status_code == 200

    # Reset job to running to force another cancel attempt in same minute bucket.
    Job.objects.filter(id=job.id).update(status="running", error_message="")
    request2 = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{job.id}/cancel/",
        {"reason": "second"},
        format="json",
    )
    force_authenticate(request2, user=teacher)
    resp2 = views.JobCancelView.as_view()(request2, project_id=project.id, job_id=job.id)
    assert resp2.status_code == 429
    assert bool(resp2.data.get("rate_limited")) is True
    rejected = JobActionAudit.objects.filter(job=job, action="cancel_rejected").first()
    assert rejected is not None


@pytest.mark.django_db
def test_cancelled_cleanup_task_removes_transient_artifacts_and_writes_checkpoint(tmp_path, monkeypatch):
    teacher = _make_teacher("cancel_cleanup_teacher")
    project = Project.objects.create(title="Cleanup flow", user=teacher, render_profile="balanced")
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="cancelled",
        error_message="__cancelled_by_user__: test",
    )

    storage_root = tmp_path / "storage"
    project_root = storage_root / str(project.id)
    audio_dir = project_root / "audio"
    parts_dir = project_root / "parts"
    avatar_segments_dir = project_root / "avatar_segments"
    for directory in [audio_dir, parts_dir, avatar_segments_dir]:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "tmp.bin").write_bytes(b"x")
    (project_root / "export_manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tasks, "STORAGE_ROOT", str(storage_root))

    with override_settings(STORAGE_ROOT=str(storage_root)):
        result = tasks.cleanup_cancelled_project_artifacts.apply(
            kwargs={"project_id": int(project.id), "job_id": int(job.id)}
        ).result

    assert result["status"] == "done"
    assert not audio_dir.exists()
    assert not parts_dir.exists()
    assert not avatar_segments_dir.exists()
    assert not (project_root / "export_manifest.json").exists()

    checkpoint = JobCheckpoint.objects.filter(job=job, stage_name="cleanup_cancelled").first()
    assert checkpoint is not None
    assert checkpoint.stage_status == "done"
