# pyright: reportMissingImports=false

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
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402


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
        return SimpleNamespace(id="janitor-task-1")


@pytest.mark.django_db
def test_system_orphan_cleanup_run_requires_admin():
    teacher = User.objects.create_user(username="teacher_cleanup", password="pass")
    request = APIRequestFactory().post("/api/v1/system/orphan-cleanup/run/", {"min_age_hours": 2}, format="json")
    force_authenticate(request, user=teacher)

    response = views.SystemOrphanCleanupRunView.as_view()(request)
    assert response.status_code == 403


@pytest.mark.django_db
def test_system_orphan_cleanup_run_dispatches_task_for_admin(monkeypatch):
    admin = User.objects.create_superuser(username="admin_cleanup", password="pass", email="admin@example.com")
    captured = _CapturedDispatch()
    monkeypatch.setattr(views, "_dispatch_celery_task", captured)

    request = APIRequestFactory().post("/api/v1/system/orphan-cleanup/run/", {"min_age_hours": 4}, format="json")
    force_authenticate(request, user=admin)

    response = views.SystemOrphanCleanupRunView.as_view()(request)
    assert response.status_code == 202
    assert response.data["status"] == "accepted"
    assert response.data["task_id"] == "janitor-task-1"
    assert int(response.data["min_age_hours"]) == 4
    assert captured.calls
    assert captured.calls[0]["task_name"] == "worker.tasks.cleanup_orphan_render_artifacts"
    assert int(captured.calls[0]["kwargs"]["min_age_hours"]) == 4

