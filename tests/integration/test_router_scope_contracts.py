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


def _make_user(role: str = "student", *, is_staff: bool = False) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        username=f"router_scope_{role}_{suffix}",
        password="pass",
        email=f"router_scope_{role}_{suffix}@example.test",
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
    )
    job = Job.objects.create(project=project, job_type="video_export", status="done", progress=100)
    return project, slide, job


def _ids(response) -> set[int]:
    payload = response.data
    if isinstance(payload, dict) and "results" in payload:
        payload = payload["results"]
    return {int(item["id"]) for item in payload}


def _assert_detail_denied_without_payload(response, object_id: int) -> None:
    assert response.status_code in {403, 404}
    data = getattr(response, "data", None)
    assert not isinstance(data, dict) or data.get("id") != object_id


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/v1/users/", "/api/v1/slides/", "/api/v1/jobs/"])
def test_router_scoped_resources_fail_closed_without_authentication(path: str):
    response = _client().get(path)

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_student_user_scope_is_self_only_and_project_resources_are_hidden():
    student = _make_user("student")
    teacher = _make_user("teacher")
    _project, teacher_slide, teacher_job = _make_project_with_slide_and_job(teacher, "teacher private")

    client = _client(student)

    users_response = client.get("/api/v1/users/")
    user_detail_response = client.get(f"/api/v1/users/{teacher.id}/")
    slides_response = client.get("/api/v1/slides/")
    slide_detail_response = client.get(f"/api/v1/slides/{teacher_slide.id}/")
    jobs_response = client.get("/api/v1/jobs/")
    job_detail_response = client.get(f"/api/v1/jobs/{teacher_job.id}/")

    assert users_response.status_code == 200
    assert _ids(users_response) == {student.id}
    _assert_detail_denied_without_payload(user_detail_response, teacher.id)

    assert slides_response.status_code == 200
    assert _ids(slides_response) == set()
    _assert_detail_denied_without_payload(slide_detail_response, teacher_slide.id)

    assert jobs_response.status_code == 200
    assert _ids(jobs_response) == set()
    _assert_detail_denied_without_payload(job_detail_response, teacher_job.id)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["teacher", "publisher"])
def test_teacher_and_publisher_resource_scope_is_owner_only(role: str):
    owner = _make_user(role)
    other = _make_user("teacher")
    _owner_project, owner_slide, owner_job = _make_project_with_slide_and_job(owner, f"{role} owned")
    _other_project, other_slide, other_job = _make_project_with_slide_and_job(other, "other owned")

    client = _client(owner)

    slides_response = client.get("/api/v1/slides/")
    own_slide_detail_response = client.get(f"/api/v1/slides/{owner_slide.id}/")
    other_slide_detail_response = client.get(f"/api/v1/slides/{other_slide.id}/")
    jobs_response = client.get("/api/v1/jobs/")
    own_job_detail_response = client.get(f"/api/v1/jobs/{owner_job.id}/")
    other_job_detail_response = client.get(f"/api/v1/jobs/{other_job.id}/")

    assert slides_response.status_code == 200
    assert _ids(slides_response) == {owner_slide.id}
    assert own_slide_detail_response.status_code == 200
    _assert_detail_denied_without_payload(other_slide_detail_response, other_slide.id)

    assert jobs_response.status_code == 200
    assert _ids(jobs_response) == {owner_job.id}
    assert own_job_detail_response.status_code == 200
    _assert_detail_denied_without_payload(other_job_detail_response, other_job.id)


@pytest.mark.django_db
def test_staff_can_see_all_router_scoped_resources():
    student = _make_user("student")
    teacher = _make_user("teacher")
    publisher = _make_user("publisher")
    staff = _make_user("student", is_staff=True)
    _teacher_project, teacher_slide, teacher_job = _make_project_with_slide_and_job(teacher, "teacher owned")
    _publisher_project, publisher_slide, publisher_job = _make_project_with_slide_and_job(publisher, "publisher owned")

    client = _client(staff)

    users_response = client.get("/api/v1/users/")
    slides_response = client.get("/api/v1/slides/")
    jobs_response = client.get("/api/v1/jobs/")
    teacher_slide_detail_response = client.get(f"/api/v1/slides/{teacher_slide.id}/")
    publisher_job_detail_response = client.get(f"/api/v1/jobs/{publisher_job.id}/")

    assert users_response.status_code == 200
    assert {student.id, teacher.id, publisher.id, staff.id}.issubset(_ids(users_response))

    assert slides_response.status_code == 200
    assert {teacher_slide.id, publisher_slide.id}.issubset(_ids(slides_response))
    assert teacher_slide_detail_response.status_code == 200

    assert jobs_response.status_code == 200
    assert {teacher_job.id, publisher_job.id}.issubset(_ids(jobs_response))
    assert publisher_job_detail_response.status_code == 200
