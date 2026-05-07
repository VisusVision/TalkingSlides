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
from core.models import Job, Project, UserProfile  # noqa: E402


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
        return SimpleNamespace(id=f"retry-{len(self.calls)}")


@pytest.mark.django_db
def test_job_retry_dispatches_and_is_idempotent(monkeypatch, tmp_path):
    teacher = _make_teacher("retry_teacher")
    project = Project.objects.create(title="Retry lesson", user=teacher, render_profile="balanced")
    original_job = Job.objects.create(project=project, job_type="video_export", status="failed")

    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")

    captured = _CapturedDispatch()
    monkeypatch.setattr(views, "_dispatch_celery_task", captured)
    monkeypatch.setattr(views, "_resolve_avatar_options_for_project", lambda *_args, **_kwargs: {"enabled": False})

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{original_job.id}/retry/",
        {"request_id": "retry_req_001"},
        format="json",
    )
    force_authenticate(request, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_RENDER_QUEUE="render"):
        response = views.JobRetryView.as_view()(request, project_id=project.id, job_id=original_job.id)
    assert response.status_code == 202
    assert int(response.data["retried_from_job_id"]) == int(original_job.id)
    assert response.data["idempotent_replay"] is False
    assert captured.calls

    replay_request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{original_job.id}/retry/",
        {"request_id": "retry_req_001"},
        format="json",
    )
    force_authenticate(replay_request, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_RENDER_QUEUE="render"):
        replay_response = views.JobRetryView.as_view()(replay_request, project_id=project.id, job_id=original_job.id)
    assert replay_response.status_code == 200
    assert replay_response.data["idempotent_replay"] is True
    assert int(replay_response.data["retried_from_job_id"]) == int(original_job.id)


@pytest.mark.django_db
def test_job_retry_rejects_running_and_done_jobs():
    teacher = _make_teacher("retry_status_teacher")
    project = Project.objects.create(title="Retry status lesson", user=teacher)
    running_job = Job.objects.create(project=project, job_type="video_export", status="running")
    done_job = Job.objects.create(project=project, job_type="video_export", status="done")

    running_request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{running_job.id}/retry/",
        {"request_id": "retry_running"},
        format="json",
    )
    force_authenticate(running_request, user=teacher)
    running_response = views.JobRetryView.as_view()(running_request, project_id=project.id, job_id=running_job.id)
    assert running_response.status_code == 409

    done_request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{done_job.id}/retry/",
        {"request_id": "retry_done"},
        format="json",
    )
    force_authenticate(done_request, user=teacher)
    done_response = views.JobRetryView.as_view()(done_request, project_id=project.id, job_id=done_job.id)
    assert done_response.status_code == 409


@pytest.mark.django_db
def test_job_retry_requires_request_id():
    teacher = _make_teacher("retry_request_id_teacher")
    project = Project.objects.create(title="Retry id lesson", user=teacher)
    original_job = Job.objects.create(project=project, job_type="video_export", status="failed")

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/jobs/{original_job.id}/retry/",
        {},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.JobRetryView.as_view()(request, project_id=project.id, job_id=original_job.id)
    assert response.status_code == 400
