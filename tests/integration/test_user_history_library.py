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

from core.models import Job, LessonLike, LessonProgress, Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_public_lesson(owner: User, title: str, *, cover_path: str = "") -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
        cover_image_original=cover_path,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


def _make_private_lesson(owner: User, title: str) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=False,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


@pytest.mark.django_db
def test_history_endpoint_returns_only_current_users_public_progress():
    teacher = _make_user("history_library_teacher", role="teacher")
    student = _make_user("history_library_student")
    other_student = _make_user("history_library_other")
    public_lesson = _make_public_lesson(teacher, "Visible watched lesson", cover_path="uploads/private/cover.png")
    private_lesson = _make_private_lesson(teacher, "Hidden draft lesson")
    other_lesson = _make_public_lesson(teacher, "Other user's watched lesson")

    LessonProgress.objects.create(user=student, project=public_lesson, progress_pct=42)
    LessonProgress.objects.create(user=student, project=private_lesson, progress_pct=80)
    LessonProgress.objects.create(user=other_student, project=other_lesson, progress_pct=55)

    response = _client(student).get("/api/v1/me/history/")

    assert response.status_code == 200
    rows = response.data["results"]
    assert [row["project_id"] for row in rows] == [public_lesson.id]
    assert rows[0]["progress_pct"] == 42
    assert rows[0]["lesson"]["title"] == "Visible watched lesson"
    assert rows[0]["lesson"]["user_progress"] == 42
    assert "uploads/private" not in rows[0]["lesson"]["cover_url"]


@pytest.mark.django_db
def test_liked_lessons_endpoint_returns_only_current_users_public_likes():
    teacher = _make_user("liked_library_teacher", role="teacher")
    student = _make_user("liked_library_student")
    other_student = _make_user("liked_library_other")
    public_lesson = _make_public_lesson(teacher, "Visible liked lesson")
    private_lesson = _make_private_lesson(teacher, "Hidden liked draft")
    other_lesson = _make_public_lesson(teacher, "Other user's liked lesson")

    LessonLike.objects.create(user=student, project=public_lesson)
    LessonLike.objects.create(user=student, project=private_lesson)
    LessonLike.objects.create(user=other_student, project=other_lesson)
    LessonProgress.objects.create(user=student, project=public_lesson, progress_pct=30)

    response = _client(student).get("/api/v1/me/liked-lessons/")

    assert response.status_code == 200
    rows = response.data["results"]
    assert [row["project_id"] for row in rows] == [public_lesson.id]
    assert rows[0]["lesson"]["title"] == "Visible liked lesson"
    assert rows[0]["lesson"]["user_liked"] is True
    assert rows[0]["lesson"]["user_progress"] == 30


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/api/v1/me/history/", "/api/v1/me/liked-lessons/"])
def test_anonymous_cannot_access_user_library_endpoints(url: str):
    response = _client().get(url)

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_history_excludes_unrendered_and_unapproved_lessons():
    teacher = _make_user("history_filter_teacher", role="teacher")
    student = _make_user("history_filter_student")
    unrendered = Project.objects.create(
        title="No render",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    rejected = _make_public_lesson(teacher, "Rejected lesson")
    rejected.moderation_status = "revision_required"
    rejected.save(update_fields=["moderation_status"])

    LessonProgress.objects.create(user=student, project=unrendered, progress_pct=20)
    LessonProgress.objects.create(user=student, project=rejected, progress_pct=20)

    response = _client(student).get("/api/v1/me/history/")

    assert response.status_code == 200
    assert response.data["results"] == []
