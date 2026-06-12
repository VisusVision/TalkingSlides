# pyright: reportMissingImports=false

import os
import sys
import uuid
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import Job, Project, Slide, UserProfile  # noqa: E402


FAIL_CLOSED_STATUSES = {401, 403, 404, 405}
CROSS_TENANT_DENIED_STATUSES = {403, 404, 405}
OWNER_SLIDE_MUTATION_STATUSES = {200, 202, 204, 405}


def _make_user(role: str = "teacher", *, is_staff: bool = False) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        username=f"router_mutation_{role}_{suffix}",
        password="pass",
        email=f"router_mutation_{role}_{suffix}@example.test",
        is_staff=is_staff,
    )
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project_with_slide_and_job(owner: User, title: str) -> tuple[Project, Slide, Job]:
    project = Project.objects.create(user=owner, title=title)
    slide = Slide.objects.create(
        project=project,
        order=1,
        title=f"{title} slide",
        narration_text=f"{title} narration",
        duration_seconds=12.5,
    )
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"https://cdn.example.test/{uuid.uuid4().hex}.mp4",
    )
    return project, slide, job


def _slide_snapshot(slide_id: int) -> dict[str, object] | None:
    slide = Slide.objects.filter(pk=slide_id).first()
    if slide is None:
        return None
    return {
        "id": slide.id,
        "project_id": slide.project_id,
        "order": slide.order,
        "title": slide.title,
        "narration_text": slide.narration_text,
        "duration_seconds": slide.duration_seconds,
    }


def _job_snapshot(job_id: int) -> dict[str, object] | None:
    job = Job.objects.filter(pk=job_id).first()
    if job is None:
        return None
    return {
        "id": job.id,
        "project_id": job.project_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "result_url": job.result_url,
        "error_message": job.error_message,
    }


def _assert_no_detail_payload(response, object_id: int) -> None:
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        assert data.get("id") != object_id
    elif isinstance(data, list):
        assert all(not isinstance(item, dict) or item.get("id") != object_id for item in data)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "resource", "snapshot"),
    [
        ("patch", "slides", _slide_snapshot),
        ("delete", "slides", _slide_snapshot),
        ("patch", "jobs", _job_snapshot),
        ("delete", "jobs", _job_snapshot),
    ],
)
def test_router_mutations_fail_closed_without_authentication(method: str, resource: str, snapshot):
    owner = _make_user("teacher")
    _project, slide, job = _make_project_with_slide_and_job(owner, "owner lesson")
    target = slide if resource == "slides" else job
    before = snapshot(target.id)

    response = getattr(_client(), method)(
        f"/api/v1/{resource}/{target.id}/",
        {"title": "unauthenticated mutation", "status": "failed"},
        format="json",
    )

    assert response.status_code in FAIL_CLOSED_STATUSES
    _assert_no_detail_payload(response, target.id)
    assert snapshot(target.id) == before


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["patch", "delete"])
def test_teacher_cannot_mutate_another_teachers_slide(method: str):
    actor = _make_user("teacher")
    owner = _make_user("teacher")
    _project, slide, _job = _make_project_with_slide_and_job(owner, "private slide")
    before = _slide_snapshot(slide.id)

    response = getattr(_client(actor), method)(
        f"/api/v1/slides/{slide.id}/",
        {"title": "cross tenant mutation", "narration_text": "changed"},
        format="json",
    )

    assert response.status_code in CROSS_TENANT_DENIED_STATUSES
    _assert_no_detail_payload(response, slide.id)
    assert _slide_snapshot(slide.id) == before


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["patch", "delete"])
def test_teacher_cannot_mutate_another_teachers_job(method: str):
    actor = _make_user("teacher")
    owner = _make_user("teacher")
    _project, _slide, job = _make_project_with_slide_and_job(owner, "private job")
    before = _job_snapshot(job.id)

    response = getattr(_client(actor), method)(
        f"/api/v1/jobs/{job.id}/",
        {"status": "failed", "progress": 0, "error_message": "changed"},
        format="json",
    )

    assert response.status_code in CROSS_TENANT_DENIED_STATUSES
    _assert_no_detail_payload(response, job.id)
    assert _job_snapshot(job.id) == before


@pytest.mark.django_db
def test_slide_owner_patch_is_allowed_or_fails_closed_without_mutation():
    owner = _make_user("teacher")
    _project, slide, _job = _make_project_with_slide_and_job(owner, "owned slide")
    before = _slide_snapshot(slide.id)
    payload = {"title": "owner updated slide", "narration_text": "owner updated narration"}

    response = _client(owner).patch(f"/api/v1/slides/{slide.id}/", payload, format="json")

    assert response.status_code in OWNER_SLIDE_MUTATION_STATUSES
    if response.status_code == 405:
        _assert_no_detail_payload(response, slide.id)
        assert _slide_snapshot(slide.id) == before
    else:
        after = _slide_snapshot(slide.id)
        assert after is not None
        assert after["project_id"] == before["project_id"]
        assert after["title"] == payload["title"]
        assert after["narration_text"] == payload["narration_text"]


@pytest.mark.django_db
def test_slide_owner_delete_is_allowed_or_fails_closed_without_mutation():
    owner = _make_user("teacher")
    _project, slide, _job = _make_project_with_slide_and_job(owner, "owned delete slide")
    before = _slide_snapshot(slide.id)

    response = _client(owner).delete(f"/api/v1/slides/{slide.id}/")

    assert response.status_code in OWNER_SLIDE_MUTATION_STATUSES
    if response.status_code == 405:
        _assert_no_detail_payload(response, slide.id)
        assert _slide_snapshot(slide.id) == before
    else:
        assert _slide_snapshot(slide.id) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "resource", "snapshot"),
    [
        ("patch", "slides", _slide_snapshot),
        ("delete", "slides", _slide_snapshot),
        ("patch", "jobs", _job_snapshot),
        ("delete", "jobs", _job_snapshot),
    ],
)
def test_staff_cannot_mutate_tenant_owned_router_resources(method: str, resource: str, snapshot):
    staff = _make_user("student", is_staff=True)
    owner = _make_user("teacher")
    _project, slide, job = _make_project_with_slide_and_job(owner, "tenant owned")
    target = slide if resource == "slides" else job
    before = snapshot(target.id)

    response = getattr(_client(staff), method)(
        f"/api/v1/{resource}/{target.id}/",
        {"title": "staff mutation", "status": "failed", "progress": 0},
        format="json",
    )

    assert response.status_code in FAIL_CLOSED_STATUSES
    _assert_no_detail_payload(response, target.id)
    assert snapshot(target.id) == before
