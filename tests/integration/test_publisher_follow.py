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

from core.models import Job, Project, PublisherFollow, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student", is_staff: bool = False, is_superuser: bool = False) -> User:
    user = User.objects.create_user(
        username=username,
        password="pass",
        is_staff=is_staff,
        is_superuser=is_superuser,
    )
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_public_lesson(owner: User, title: str) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


@pytest.mark.django_db
def test_student_can_follow_and_unfollow_publisher():
    publisher = _make_user("follow_teacher", role="teacher")
    student = _make_user("follow_student")

    response = _client(student).post(f"/api/v1/users/{publisher.id}/follow/")

    assert response.status_code == 200
    assert response.data["is_following"] is True
    assert response.data["follower_count"] == 1
    assert PublisherFollow.objects.filter(follower=student, publisher=publisher).exists()

    response = _client(student).post(f"/api/v1/users/{publisher.id}/follow/")

    assert response.status_code == 200
    assert response.data["is_following"] is False
    assert response.data["follower_count"] == 0
    assert not PublisherFollow.objects.filter(follower=student, publisher=publisher).exists()


@pytest.mark.django_db
def test_publisher_follow_count_updates_for_multiple_followers():
    publisher = _make_user("follow_count_publisher", role="publisher")
    first = _make_user("follow_count_first")
    second = _make_user("follow_count_second")

    first_response = _client(first).post(f"/api/v1/users/{publisher.id}/follow/")
    second_response = _client(second).post(f"/api/v1/users/{publisher.id}/follow/")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.data["follower_count"] == 2


@pytest.mark.django_db
def test_user_cannot_follow_self():
    publisher = _make_user("follow_self_publisher", role="publisher")

    response = _client(publisher).post(f"/api/v1/users/{publisher.id}/follow/")

    assert response.status_code == 400
    assert not PublisherFollow.objects.filter(follower=publisher, publisher=publisher).exists()


@pytest.mark.django_db
def test_anonymous_cannot_follow_publisher():
    publisher = _make_user("follow_anonymous_publisher", role="publisher")

    response = _client().post(f"/api/v1/users/{publisher.id}/follow/")

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_normal_student_cannot_be_followed_as_publisher():
    student_target = _make_user("follow_target_student", role="student")
    follower = _make_user("follow_student_follower")

    response = _client(follower).post(f"/api/v1/users/{student_target.id}/follow/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_staff_user_can_be_followed_as_publisher():
    staff = _make_user("follow_staff_publisher", role="student", is_staff=True)
    student = _make_user("follow_staff_student")

    response = _client(student).post(f"/api/v1/users/{staff.id}/follow/")

    assert response.status_code == 200
    assert response.data["is_following"] is True


@pytest.mark.django_db
def test_me_following_returns_only_current_user_publishers():
    publisher = _make_user("following_list_publisher", role="publisher")
    other_publisher = _make_user("following_list_other_publisher", role="teacher")
    student = _make_user("following_list_student")
    other_student = _make_user("following_list_other_student")
    _make_public_lesson(publisher, "Latest followed lesson")
    PublisherFollow.objects.create(follower=student, publisher=publisher)
    PublisherFollow.objects.create(follower=other_student, publisher=other_publisher)

    response = _client(student).get("/api/v1/me/following/")

    assert response.status_code == 200
    rows = response.data["results"]
    assert [row["id"] for row in rows] == [publisher.id]
    assert rows[0]["is_following"] is True
    assert rows[0]["latest_lessons"][0]["title"] == "Latest followed lesson"


@pytest.mark.django_db
def test_anonymous_cannot_access_following_library():
    response = _client().get("/api/v1/me/following/")

    assert response.status_code in {401, 403}
