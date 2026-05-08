# pyright: reportMissingImports=false

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

from django.contrib.auth.models import User  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import Job, LessonLike, Project, PublisherFollow, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role, bio=f"{username} bio")
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(
    owner: User,
    title: str,
    *,
    published: bool = True,
    ready: bool = True,
    approved: bool = True,
    rendered: bool = True,
) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready" if ready else "draft",
        moderation_status="approved" if approved else "revision_required",
        is_published=published,
    )
    if rendered:
        Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["publisher", "teacher"])
def test_public_profile_visible_for_publisher_roles(role: str):
    publisher = _make_user(f"profile_visible_{role}", role=role)
    follower = _make_user(f"profile_visible_follower_{role}")
    _make_project(publisher, f"{role} public lesson")
    PublisherFollow.objects.create(follower=follower, publisher=publisher)

    response = _client(follower).get(f"/api/v1/users/{publisher.id}/profile/")

    assert response.status_code == 200
    assert response.data["id"] == publisher.id
    assert response.data["role"] == role
    assert response.data["follower_count"] == 1
    assert response.data["lesson_count"] == 1
    assert response.data["is_following"] is True
    assert response.data["latest_lessons"][0]["title"] == f"{role} public lesson"


@pytest.mark.django_db
def test_public_profile_for_normal_student_returns_404():
    student = _make_user("profile_hidden_student", role="student")

    response = _client().get(f"/api/v1/users/{student.id}/profile/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_publisher_lessons_endpoint_returns_only_public_ready_lessons_to_public():
    publisher = _make_user("profile_lessons_publisher", role="publisher")
    visible = _make_project(publisher, "Visible lesson")
    _make_project(publisher, "Draft lesson", published=False)
    _make_project(publisher, "Rejected lesson", approved=False)
    _make_project(publisher, "Unrendered lesson", rendered=False)

    response = _client().get(f"/api/v1/users/{publisher.id}/lessons/")

    assert response.status_code == 200
    rows = response.data["results"]
    assert [row["id"] for row in rows] == [visible.id]
    assert rows[0]["title"] == "Visible lesson"


@pytest.mark.django_db
def test_publisher_lesson_sorting_by_name_and_date():
    publisher = _make_user("profile_sort_publisher", role="teacher")
    beta = _make_project(publisher, "Beta lesson")
    alpha = _make_project(publisher, "Alpha lesson")

    response = _client().get(f"/api/v1/users/{publisher.id}/lessons/?sort=name&order=asc")

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [alpha.id, beta.id]


@pytest.mark.django_db
def test_owner_can_list_own_private_channel_lessons():
    publisher = _make_user("profile_owner_publisher", role="publisher")
    public_lesson = _make_project(publisher, "Owner public lesson")
    draft_lesson = _make_project(publisher, "Owner draft lesson", published=False, ready=False, rendered=False)

    response = _client(publisher).get(f"/api/v1/users/{publisher.id}/lessons/?sort=name&order=asc")

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [draft_lesson.id, public_lesson.id]


@pytest.mark.django_db
def test_staff_can_list_private_channel_lessons():
    publisher = _make_user("profile_staff_owner", role="publisher")
    staff = _make_user("profile_staff_viewer", is_staff=True)
    private_lesson = _make_project(publisher, "Staff-visible draft", published=False, ready=False, rendered=False)

    response = _client(staff).get(f"/api/v1/users/{publisher.id}/lessons/")

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [private_lesson.id]


@pytest.mark.django_db
def test_public_profile_stats_include_likes():
    publisher = _make_user("profile_stats_publisher", role="publisher")
    student = _make_user("profile_stats_student")
    lesson = _make_project(publisher, "Stats lesson")
    LessonLike.objects.create(user=student, project=lesson)

    response = _client().get(f"/api/v1/users/{publisher.id}/profile/")

    assert response.status_code == 200
    assert response.data["stats"]["total_likes"] == 1
