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

from core.models import Job, Notification, Project, PublisherFollow, UserProfile  # noqa: E402
from core.notifications import (  # noqa: E402
    create_notification,
    notify_avatar_completed,
    notify_avatar_failed,
    notify_publisher_posted_lesson,
    notify_render_completed,
    notify_render_failed,
)


def _make_user(username: str, *, role: str = "student") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_lesson(
    owner: User,
    title: str,
    *,
    published: bool = True,
    ready: bool = True,
    moderation_status: str = "approved",
    rendered: bool = True,
) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready" if ready else "draft",
        moderation_status=moderation_status,
        is_published=published,
    )
    if rendered:
        Job.objects.create(
            project=project,
            job_type="video_export",
            status="done",
            progress=100,
            result_url=f"{project.id}/{project.id}.mp4",
        )
    return project


def _make_notification(user: User, **kwargs) -> Notification:
    return Notification.objects.create(
        recipient_user=user,
        event_type=kwargs.get("event_type", Notification.EventType.PUBLISHER_LESSON_RENDER_DONE),
        title=kwargs.get("title", "Notification"),
        body=kwargs.get("body", ""),
        actor_user=kwargs.get("actor_user"),
        project=kwargs.get("project"),
        action_url=kwargs.get("action_url", ""),
        metadata=kwargs.get("metadata", {}),
        is_read=kwargs.get("is_read", False),
    )


@pytest.mark.django_db
def test_unauthenticated_notification_list_denied():
    response = _client().get("/api/v1/me/notifications/")

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_user_only_sees_own_notifications():
    user = _make_user("notify_own_user")
    other = _make_user("notify_own_other")
    own = _make_notification(user, title="Mine")
    _make_notification(other, title="Other")

    response = _client(user).get("/api/v1/me/notifications/")

    assert response.status_code == 200
    rows = response.data["results"]
    assert [row["id"] for row in rows] == [own.id]
    assert rows[0]["title"] == "Mine"


@pytest.mark.django_db
def test_unread_count_only_counts_current_users_unread_notifications():
    user = _make_user("notify_count_user")
    other = _make_user("notify_count_other")
    _make_notification(user, is_read=False)
    _make_notification(user, is_read=True)
    _make_notification(other, is_read=False)

    response = _client(user).get("/api/v1/me/notifications/unread-count/")

    assert response.status_code == 200
    assert response.data["unread_count"] == 1


@pytest.mark.django_db
def test_mark_one_read_works_only_for_owner():
    owner = _make_user("notify_mark_owner")
    other = _make_user("notify_mark_other")
    notification = _make_notification(owner)

    forbidden = _client(other).post(f"/api/v1/me/notifications/{notification.id}/read/")
    assert forbidden.status_code in {403, 404}

    response = _client(owner).post(f"/api/v1/me/notifications/{notification.id}/read/")
    notification.refresh_from_db()

    assert response.status_code == 200
    assert notification.is_read is True
    assert notification.read_at is not None


@pytest.mark.django_db
def test_mark_all_read_only_updates_current_user():
    user = _make_user("notify_all_user")
    other = _make_user("notify_all_other")
    _make_notification(user, is_read=False)
    _make_notification(user, is_read=False)
    other_notification = _make_notification(other, is_read=False)

    response = _client(user).post("/api/v1/me/notifications/mark-all-read/")
    other_notification.refresh_from_db()

    assert response.status_code == 200
    assert response.data["updated"] == 2
    assert Notification.objects.filter(recipient_user=user, is_read=False).count() == 0
    assert other_notification.is_read is False


@pytest.mark.django_db
def test_comment_on_publisher_lesson_creates_publisher_notification():
    publisher = _make_user("notify_comment_publisher", role="publisher")
    student = _make_user("notify_comment_student")
    lesson = _make_lesson(publisher, "Commented Lesson")

    response = _client(student).post(
        f"/api/v1/catalog/{lesson.id}/comments/",
        {"text": "Useful lesson"},
        format="json",
    )

    assert response.status_code == 201
    notification = Notification.objects.get(
        recipient_user=publisher,
        event_type=Notification.EventType.PUBLISHER_COMMENT_ON_LESSON,
    )
    assert notification.actor_user == student
    assert notification.project == lesson
    assert notification.action_url == f"/studio?lesson={lesson.id}"
    assert notification.body == "A viewer commented on Commented Lesson."


@pytest.mark.django_db
def test_self_comment_does_not_notify():
    publisher = _make_user("notify_self_comment_publisher", role="publisher")
    lesson = _make_lesson(publisher, "Self Comment")

    response = _client(publisher).post(
        f"/api/v1/catalog/{lesson.id}/comments/",
        {"text": "My own note"},
        format="json",
    )

    assert response.status_code == 201
    assert Notification.objects.filter(recipient_user=publisher).count() == 0


@pytest.mark.django_db
def test_publish_public_lesson_notifies_followers():
    publisher = _make_user("notify_publish_publisher", role="publisher")
    follower = _make_user("notify_publish_follower")
    lesson = _make_lesson(publisher, "Fresh Public Lesson", published=False)
    PublisherFollow.objects.create(follower=follower, publisher=publisher)

    response = _client(publisher).patch(
        f"/api/v1/projects/{lesson.id}/",
        {"is_published": True},
        format="json",
    )

    assert response.status_code == 200
    notification = Notification.objects.get(
        recipient_user=follower,
        event_type=Notification.EventType.STUDENT_FOLLOWED_PUBLISHER_NEW_LESSON,
    )
    assert notification.project == lesson
    assert notification.action_url == f"/watch?lesson={lesson.id}"
    assert "Fresh Public Lesson" in notification.body


@pytest.mark.django_db
def test_draft_private_unpublished_lesson_does_not_notify_followers():
    publisher = _make_user("notify_draft_publisher", role="publisher")
    follower = _make_user("notify_draft_follower")
    PublisherFollow.objects.create(follower=follower, publisher=publisher)
    draft = _make_lesson(publisher, "Hidden Draft", published=False, ready=False, rendered=False)
    private_not_ready = _make_lesson(publisher, "Hidden Not Ready", published=True, ready=False, rendered=True)

    assert notify_publisher_posted_lesson(draft) == 0
    assert notify_publisher_posted_lesson(private_not_ready) == 0
    assert Notification.objects.filter(recipient_user=follower).count() == 0


@pytest.mark.django_db
def test_lesson_render_success_and_failure_create_owner_notifications():
    publisher = _make_user("notify_render_publisher", role="publisher")
    lesson = _make_lesson(publisher, "Render Status Lesson", published=False, rendered=False)
    done_job = Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100)
    failed_job = Job.objects.create(project=lesson, job_type="video_export", status="failed", progress=100)

    notify_render_completed(lesson, done_job)
    notify_render_completed(lesson, done_job)
    notify_render_failed(lesson, failed_job)
    notify_render_failed(lesson, failed_job)

    assert Notification.objects.filter(
        recipient_user=publisher,
        event_type=Notification.EventType.PUBLISHER_LESSON_RENDER_DONE,
    ).count() == 1
    assert Notification.objects.filter(
        recipient_user=publisher,
        event_type=Notification.EventType.PUBLISHER_LESSON_RENDER_FAILED,
    ).count() == 1


@pytest.mark.django_db
def test_avatar_success_and_failure_create_owner_notifications():
    publisher = _make_user("notify_avatar_publisher", role="publisher")
    lesson = _make_lesson(publisher, "Avatar Status Lesson", published=False)
    done_job = Job.objects.create(project=lesson, job_type="avatar_render", status="done", progress=100)
    failed_job = Job.objects.create(project=lesson, job_type="avatar_render", status="failed", progress=100)

    notify_avatar_completed(lesson, done_job)
    notify_avatar_completed(lesson, done_job)
    notify_avatar_failed(lesson, failed_job)
    notify_avatar_failed(lesson, failed_job)

    assert Notification.objects.filter(
        recipient_user=publisher,
        event_type=Notification.EventType.PUBLISHER_AVATAR_RENDER_DONE,
    ).count() == 1
    assert Notification.objects.filter(
        recipient_user=publisher,
        event_type=Notification.EventType.PUBLISHER_AVATAR_RENDER_FAILED,
    ).count() == 1


@pytest.mark.django_db
def test_notification_api_does_not_expose_raw_storage_paths():
    publisher = _make_user("notify_raw_publisher", role="publisher")
    lesson = _make_lesson(publisher, "Safe Metadata Lesson")
    job = Job.objects.create(
        project=lesson,
        job_type="video_export",
        status="done",
        result_url="storage_local/private/final.mp4",
    )
    create_notification(
        recipient_user=publisher,
        event_type=Notification.EventType.PUBLISHER_LESSON_RENDER_DONE,
        project=lesson,
        job=job,
        title="Safe notification",
        action_url="/media/storage_local/private/final.mp4",
        metadata={
            "project_id": lesson.id,
            "job_id": job.id,
            "storage_path": "storage_local/private/final.mp4",
            "result_url": "storage_local/private/final.mp4",
            "status": "done",
        },
    )

    response = _client(publisher).get("/api/v1/me/notifications/")
    rendered = str(response.data)

    assert response.status_code == 200
    assert "storage_local" not in rendered
    assert "final.mp4" not in rendered
    assert response.data["results"][0]["action_url"] == ""
    assert response.data["results"][0]["metadata"] == {
        "project_id": lesson.id,
        "job_id": job.id,
        "status": "done",
    }


@pytest.mark.django_db
def test_private_draft_lesson_details_are_not_leaked_to_followers():
    publisher = _make_user("notify_private_publisher", role="publisher")
    follower = _make_user("notify_private_follower")
    draft = _make_lesson(publisher, "Secret Draft Title", published=False, ready=False, rendered=False)
    Notification.objects.create(
        recipient_user=follower,
        actor_user=publisher,
        event_type=Notification.EventType.STUDENT_FOLLOWED_PUBLISHER_NEW_LESSON,
        project=draft,
        title="New lesson",
        body="A lesson is available.",
        action_url=f"/watch?lesson={draft.id}",
        metadata={"project_id": draft.id, "lesson_id": draft.id},
    )

    response = _client(follower).get("/api/v1/me/notifications/")
    row = response.data["results"][0]

    assert response.status_code == 200
    assert row["project"] is None
    assert row["action_url"] == ""
    assert row["metadata"] == {}
    assert "Secret Draft Title" not in str(response.data)
