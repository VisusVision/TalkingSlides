# pyright: reportMissingImports=false

import json
import os
import sys
from datetime import timedelta
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
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import (  # noqa: E402
    Category,
    Job,
    LessonComment,
    LessonLike,
    LessonProgress,
    Project,
    UserProfile,
)


def _make_user(
    username: str,
    *,
    role: str = "student",
    is_staff: bool = False,
    is_superuser: bool = False,
) -> User:
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


def _make_project(
    owner: User,
    title: str,
    *,
    category: Category | None = None,
    published: bool = True,
) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        category=category,
        status="ready" if published else "draft",
        moderation_status="approved",
        is_published=published,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}.mp4",
    )
    return project


def _assert_no_viewer_identity_keys(value):
    if isinstance(value, dict):
        assert "user_interest_aggregates" not in value
        assert "user_id" not in value
        assert "username" not in value
        assert "viewer_id" not in value
        for child in value.values():
            _assert_no_viewer_identity_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_viewer_identity_keys(child)


@pytest.mark.django_db
def test_creator_analytics_requires_authentication_and_creator_role():
    student = _make_user("creator_analytics_student", role="student")

    anonymous_response = _client().get("/api/v1/me/analytics/")
    student_response = _client(student).get("/api/v1/me/analytics/")

    assert anonymous_response.status_code in {401, 403}
    assert student_response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["teacher", "publisher"])
def test_creator_roles_can_read_own_analytics(role: str):
    creator = _make_user(f"creator_analytics_{role}", role=role)
    viewer = _make_user(f"creator_analytics_viewer_{role}")
    lesson = _make_project(creator, f"{role} analytics lesson")

    LessonProgress.objects.create(user=viewer, project=lesson, progress_pct=75)
    LessonLike.objects.create(user=viewer, project=lesson)
    LessonComment.objects.create(user=viewer, project=lesson, text="Useful lesson")

    response = _client(creator).get("/api/v1/me/analytics/?range=30")

    assert response.status_code == 200
    assert response.data["summary"]["total_lessons"] == 1
    assert response.data["summary"]["total_views"] == 1
    assert response.data["summary"]["unique_viewers"] == 1
    assert response.data["summary"]["likes"] == 1
    assert response.data["summary"]["comments"] == 1
    assert response.data["tables"]["top_lessons"][0]["title"] == f"{role} analytics lesson"


@pytest.mark.django_db
def test_creator_analytics_is_scoped_to_current_creator():
    owner = _make_user("creator_scope_owner", role="publisher")
    other_creator = _make_user("creator_scope_other", role="publisher")
    viewer = _make_user("creator_scope_viewer")
    own_lesson = _make_project(owner, "Owner lesson")
    other_lesson = _make_project(other_creator, "Other creator lesson")

    LessonProgress.objects.create(user=viewer, project=own_lesson, progress_pct=80)
    LessonLike.objects.create(user=viewer, project=own_lesson)
    LessonComment.objects.create(user=viewer, project=own_lesson, text="Owner comment")
    LessonProgress.objects.create(user=viewer, project=other_lesson, progress_pct=100)
    LessonLike.objects.create(user=viewer, project=other_lesson)
    LessonComment.objects.create(user=viewer, project=other_lesson, text="Other comment")

    response = _client(owner).get("/api/v1/me/analytics/?range=90")

    assert response.status_code == 200
    assert response.data["summary"]["total_lessons"] == 1
    assert response.data["summary"]["total_views"] == 1
    assert response.data["summary"]["likes"] == 1
    assert response.data["summary"]["comments"] == 1
    titles = [row["title"] for row in response.data["tables"]["top_lessons"]]
    assert titles == ["Owner lesson"]
    assert "Other creator lesson" not in json.dumps(response.data)


@pytest.mark.django_db
def test_staff_admin_stats_still_load_and_creator_endpoint_stays_scoped():
    staff = _make_user("creator_stats_staff", role="teacher", is_staff=True)
    other_creator = _make_user("creator_stats_other", role="publisher")
    viewer = _make_user("creator_stats_viewer")
    own_lesson = _make_project(staff, "Staff own lesson")
    other_lesson = _make_project(other_creator, "Platform lesson")

    LessonProgress.objects.create(user=viewer, project=own_lesson, progress_pct=90)
    LessonProgress.objects.create(user=staff, project=other_lesson, progress_pct=90)

    admin_response = _client(staff).get("/api/v1/admin/stats/")
    creator_response = _client(staff).get("/api/v1/me/analytics/?range=90")

    assert admin_response.status_code == 200
    assert "summary" in admin_response.data
    assert creator_response.status_code == 200
    assert creator_response.data["summary"]["total_lessons"] == 1
    titles = [row["title"] for row in creator_response.data["tables"]["top_lessons"]]
    assert titles == ["Staff own lesson"]


@pytest.mark.django_db
def test_creator_endpoint_does_not_expose_viewer_identity_details():
    creator = _make_user("creator_privacy_owner", role="teacher")
    viewer = _make_user("creator_privacy_viewer")
    lesson = _make_project(creator, "Privacy lesson")
    LessonProgress.objects.create(user=viewer, project=lesson, progress_pct=60)
    LessonLike.objects.create(user=viewer, project=lesson)

    response = _client(creator).get("/api/v1/me/analytics/?range=30")

    assert response.status_code == 200
    _assert_no_viewer_identity_keys(response.data)
    assert "creator_privacy_viewer" not in json.dumps(response.data)


@pytest.mark.django_db
def test_empty_creator_analytics_returns_zeroes_and_empty_rows():
    creator = _make_user("creator_empty_owner", role="publisher")

    response = _client(creator).get("/api/v1/me/analytics/")

    assert response.status_code == 200
    assert response.data["summary"]["total_lessons"] == 0
    assert response.data["summary"]["total_views"] == 0
    assert response.data["summary"]["unique_viewers"] == 0
    assert response.data["summary"]["engagement_events"] == 0
    assert response.data["charts"]["category_popularity"] == []
    assert response.data["tables"]["top_lessons"] == []
    assert response.data["recent_activity"] == []


@pytest.mark.django_db
def test_creator_analytics_honors_category_and_range_filters():
    creator = _make_user("creator_filter_owner", role="publisher")
    recent_viewer = _make_user("creator_filter_recent_viewer")
    old_viewer = _make_user("creator_filter_old_viewer")
    cat_a = Category.objects.create(name="Analytics Category A", slug="analytics-category-a")
    cat_b = Category.objects.create(name="Analytics Category B", slug="analytics-category-b")
    recent_a = _make_project(creator, "Recent A lesson", category=cat_a)
    old_a = _make_project(creator, "Old A lesson", category=cat_a)
    recent_b = _make_project(creator, "Recent B lesson", category=cat_b)

    LessonProgress.objects.create(user=recent_viewer, project=recent_a, progress_pct=80)
    old_progress = LessonProgress.objects.create(user=old_viewer, project=old_a, progress_pct=100)
    LessonProgress.objects.create(user=recent_viewer, project=recent_b, progress_pct=50)
    LessonProgress.objects.filter(pk=old_progress.pk).update(
        updated_at=timezone.now() - timedelta(days=40)
    )

    response = _client(creator).get(
        f"/api/v1/me/analytics/?range=7&category={cat_a.slug}"
    )

    assert response.status_code == 200
    assert response.data["summary"]["total_lessons"] == 2
    assert response.data["summary"]["total_views"] == 1
    assert response.data["filters"]["category"] == cat_a.slug
    assert {row["category_slug"] for row in response.data["tables"]["top_lessons"]} == {cat_a.slug}
    assert "Recent B lesson" not in json.dumps(response.data)
