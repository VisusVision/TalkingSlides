# pyright: reportMissingImports=false

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
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


@pytest.mark.django_db
def test_job_status_defaults_to_light_schema_without_heavy_fields():
    teacher = _make_teacher("job_schema_teacher")
    project = Project.objects.create(title="Schema project", user=teacher)
    job = Job.objects.create(project=project, job_type="video_export", status="running", progress=42)

    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/jobs/{job.id}/")
    force_authenticate(request, user=teacher)
    response = views.JobStatusView.as_view()(request, project_id=project.id, job_id=job.id)

    assert response.status_code == 200
    assert response.data["response_schema"] == "light_v1"
    assert response.data["status"] == "running"
    assert int(response.data["progress"]) == 42
    assert "checkpoints" in response.data
    assert "transcript_pages" not in response.data
    assert "language_detection" not in response.data


@pytest.mark.django_db
def test_job_status_full_schema_opt_in_keeps_transcript_optional():
    teacher = _make_teacher("job_schema_full_teacher")
    project = Project.objects.create(title="Schema project full", user=teacher)
    job = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0)

    request = APIRequestFactory().get(
        f"/api/v1/projects/{project.id}/jobs/{job.id}/?response_schema=full_v1&include_transcript_pages=1"
    )
    force_authenticate(request, user=teacher)
    response = views.JobStatusView.as_view()(request, project_id=project.id, job_id=job.id)

    assert response.status_code == 200
    assert response.data["response_schema"] == "full_v1"
    assert "transcript_pages" in response.data
