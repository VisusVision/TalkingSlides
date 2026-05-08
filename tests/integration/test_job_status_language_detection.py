# pyright: reportMissingImports=false

import json
import os
import sys
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, Project, User, UserProfile  # noqa: E402


def _make_user(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass1234")
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = "teacher"
    profile.save(update_fields=["role"])
    return user


@pytest.mark.django_db
def test_job_status_returns_language_detection_fallback_when_sidecar_missing(tmp_path):
    user = _make_user("language_test_user")
    project = Project.objects.create(title="Language test", user=user)
    job = Job.objects.create(project=project, job_type="video_export", status="pending")
    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/jobs/{job.id}/")
    force_authenticate(request, user=user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.JobStatusView.as_view()(request, project_id=project.id, job_id=job.id)

    assert response.status_code == 200
    payload = response.data["language_detection"]
    assert payload["resolved_language"] == "en"
    assert payload["fallback_used"] is True
    assert payload["detector"] == "placeholder_v1"


@pytest.mark.django_db
def test_job_status_returns_language_detection_sidecar_payload(tmp_path):
    user = _make_user("language_sidecar_user")
    project = Project.objects.create(title="Language sidecar", user=user)
    job = Job.objects.create(project=project, job_type="video_export", status="running")

    sidecar_dir = tmp_path / str(project.id)
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar = sidecar_dir / "language_detection.json"
    sidecar_payload = {
        "detected_language": "en",
        "resolved_language": "en",
        "source": "text_heuristic",
        "confidence": 0.84,
        "fallback_used": False,
        "supported_languages": ["en"],
        "detector": "placeholder_v1",
    }
    sidecar.write_text(json.dumps(sidecar_payload), encoding="utf-8")

    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/jobs/{job.id}/")
    force_authenticate(request, user=user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.JobStatusView.as_view()(request, project_id=project.id, job_id=job.id)

    assert response.status_code == 200
    assert response.data["language_detection"] == sidecar_payload
