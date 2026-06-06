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

from django.contrib import admin  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import Job, Playlist, PlaylistItem, Project, UserProfile  # noqa: E402


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


def _make_project(owner: User, title: str, *, published: bool = False, ready: bool = False, rendered: bool = False) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready" if ready else "draft",
        moderation_status="approved",
        is_published=published,
    )
    if rendered:
        Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


@pytest.mark.django_db
def test_publisher_can_create_playlist():
    publisher = _make_user("playlist_publisher", role="publisher")

    response = _client(publisher).post(
        "/api/v1/playlists/",
        {"title": "Launch sequence", "description": "Start here", "is_public": True},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["title"] == "Launch sequence"
    assert response.data["user"] == publisher.id
    assert Playlist.objects.filter(user=publisher, title="Launch sequence").exists()


@pytest.mark.django_db
def test_teacher_can_create_playlist():
    teacher = _make_user("playlist_teacher", role="teacher")

    response = _client(teacher).post("/api/v1/playlists/", {"title": "Teacher sequence"}, format="json")

    assert response.status_code == 201
    assert response.data["title"] == "Teacher sequence"


@pytest.mark.django_db
def test_student_cannot_create_playlist():
    student = _make_user("playlist_student", role="student")

    response = _client(student).post("/api/v1/playlists/", {"title": "Nope"}, format="json")

    assert response.status_code == 403
    assert not Playlist.objects.filter(title="Nope").exists()


@pytest.mark.django_db
def test_anonymous_cannot_create_playlist():
    response = _client().post("/api/v1/playlists/", {"title": "No auth"}, format="json")

    assert response.status_code in {401, 403}
    assert not Playlist.objects.filter(title="No auth").exists()


@pytest.mark.django_db
def test_publisher_can_add_own_draft_lesson():
    publisher = _make_user("playlist_draft_owner", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Draft-friendly")
    draft = _make_project(publisher, "Draft lesson")

    response = _client(publisher).post(f"/api/v1/playlists/{playlist.id}/items/", {"project_id": draft.id}, format="json")

    assert response.status_code == 201
    assert PlaylistItem.objects.filter(playlist=playlist, project=draft).exists()
    assert response.data["items"][0]["project"]["title"] == "Draft lesson"


@pytest.mark.django_db
def test_publisher_can_add_own_published_lesson():
    publisher = _make_user("playlist_public_owner", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Published-friendly")
    published = _make_project(publisher, "Published lesson", published=True, ready=True, rendered=True)

    response = _client(publisher).post(f"/api/v1/playlists/{playlist.id}/items/", {"project_id": published.id}, format="json")

    assert response.status_code == 201
    assert PlaylistItem.objects.filter(playlist=playlist, project=published).exists()


@pytest.mark.django_db
def test_publisher_cannot_add_another_publishers_lesson():
    owner = _make_user("playlist_owner", role="publisher")
    other = _make_user("playlist_other_owner", role="publisher")
    playlist = Playlist.objects.create(user=owner, title="Mine")
    other_project = _make_project(other, "Other lesson")

    response = _client(owner).post(f"/api/v1/playlists/{playlist.id}/items/", {"project_id": other_project.id}, format="json")

    assert response.status_code == 403
    assert not PlaylistItem.objects.filter(playlist=playlist, project=other_project).exists()


@pytest.mark.django_db
def test_duplicate_playlist_item_is_idempotent():
    publisher = _make_user("playlist_duplicate_owner", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="No duplicates")
    project = _make_project(publisher, "One item")

    first = _client(publisher).post(f"/api/v1/playlists/{playlist.id}/items/", {"project_id": project.id}, format="json")
    second = _client(publisher).post(f"/api/v1/playlists/{playlist.id}/items/", {"project_id": project.id}, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert PlaylistItem.objects.filter(playlist=playlist, project=project).count() == 1


@pytest.mark.django_db
def test_reorder_updates_item_order():
    publisher = _make_user("playlist_reorder_owner", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Ordered")
    first = _make_project(publisher, "First")
    second = _make_project(publisher, "Second")
    third = _make_project(publisher, "Third")
    PlaylistItem.objects.create(playlist=playlist, project=first, order=0)
    PlaylistItem.objects.create(playlist=playlist, project=second, order=1)
    PlaylistItem.objects.create(playlist=playlist, project=third, order=2)

    response = _client(publisher).patch(
        f"/api/v1/playlists/{playlist.id}/items/reorder/",
        {"project_ids": [third.id, first.id, second.id]},
        format="json",
    )

    assert response.status_code == 200
    assert list(PlaylistItem.objects.filter(playlist=playlist).order_by("order").values_list("project_id", flat=True)) == [
        third.id,
        first.id,
        second.id,
    ]


@pytest.mark.django_db
def test_delete_playlist_removes_items():
    publisher = _make_user("playlist_delete_owner", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Delete me")
    project = _make_project(publisher, "Delete child")
    PlaylistItem.objects.create(playlist=playlist, project=project, order=0)

    response = _client(publisher).delete(f"/api/v1/playlists/{playlist.id}/")

    assert response.status_code == 204
    assert not Playlist.objects.filter(id=playlist.id).exists()
    assert not PlaylistItem.objects.filter(playlist_id=playlist.id).exists()


@pytest.mark.django_db
def test_owner_can_see_own_draft_item_in_management_endpoint():
    publisher = _make_user("playlist_management_owner", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Management")
    draft = _make_project(publisher, "Management draft")
    PlaylistItem.objects.create(playlist=playlist, project=draft, order=0)

    response = _client(publisher).get("/api/v1/playlists/")

    assert response.status_code == 200
    assert response.data["results"][0]["items"][0]["project"]["title"] == "Management draft"
    assert response.data["results"][0]["items"][0]["project"]["is_published"] is False


@pytest.mark.django_db
def test_playlist_admin_registration():
    assert admin.site.is_registered(Playlist)
    assert admin.site.is_registered(PlaylistItem)
