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

from core.models import Job, Playlist, PlaylistItem, Project, SavedPlaylist, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(owner: User, title: str) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


def _make_playlist(owner: User, title: str, *, is_public: bool = True) -> Playlist:
    playlist = Playlist.objects.create(user=owner, title=title, is_public=is_public)
    lesson = _make_project(owner, f"{title} lesson")
    PlaylistItem.objects.create(playlist=playlist, project=lesson, order=0)
    return playlist


@pytest.mark.django_db
def test_authenticated_student_can_save_public_playlist():
    publisher = _make_user("saved_public_publisher", role="publisher")
    student = _make_user("saved_public_student")
    playlist = _make_playlist(publisher, "Public save")

    response = _client(student).post(f"/api/v1/playlists/{playlist.id}/save/")

    assert response.status_code == 200
    assert response.data == {"is_saved": True, "save_count": 1}
    assert SavedPlaylist.objects.filter(user=student, playlist=playlist).exists()


@pytest.mark.django_db
def test_second_save_post_toggles_unsave():
    publisher = _make_user("saved_toggle_publisher", role="publisher")
    student = _make_user("saved_toggle_student")
    playlist = _make_playlist(publisher, "Toggle save")
    client = _client(student)

    first = client.post(f"/api/v1/playlists/{playlist.id}/save/")
    second = client.post(f"/api/v1/playlists/{playlist.id}/save/")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data == {"is_saved": False, "save_count": 0}
    assert not SavedPlaylist.objects.filter(user=student, playlist=playlist).exists()


@pytest.mark.django_db
def test_anonymous_cannot_save_playlist():
    publisher = _make_user("saved_anon_publisher", role="publisher")
    playlist = _make_playlist(publisher, "Anonymous blocked")

    response = _client().post(f"/api/v1/playlists/{playlist.id}/save/")

    assert response.status_code in {401, 403}
    assert SavedPlaylist.objects.count() == 0


@pytest.mark.django_db
def test_student_cannot_save_private_playlist():
    publisher = _make_user("saved_private_publisher", role="publisher")
    student = _make_user("saved_private_student")
    playlist = _make_playlist(publisher, "Private blocked", is_public=False)

    response = _client(student).post(f"/api/v1/playlists/{playlist.id}/save/")

    assert response.status_code == 404
    assert SavedPlaylist.objects.count() == 0


@pytest.mark.django_db
def test_owner_cannot_save_private_playlist_consistently():
    publisher = _make_user("saved_private_owner", role="publisher")
    playlist = _make_playlist(publisher, "Owner private blocked", is_public=False)

    response = _client(publisher).post(f"/api/v1/playlists/{playlist.id}/save/")

    assert response.status_code == 404
    assert SavedPlaylist.objects.count() == 0


@pytest.mark.django_db
def test_saved_playlists_endpoint_returns_only_current_users_saved_playlists():
    publisher = _make_user("saved_list_publisher", role="publisher")
    student = _make_user("saved_list_student")
    other_student = _make_user("saved_list_other")
    first = _make_playlist(publisher, "Saved first")
    second = _make_playlist(publisher, "Saved second")
    other = _make_playlist(publisher, "Other saved")
    SavedPlaylist.objects.create(user=student, playlist=first)
    SavedPlaylist.objects.create(user=student, playlist=second)
    SavedPlaylist.objects.create(user=other_student, playlist=other)

    response = _client(student).get("/api/v1/me/saved-playlists/")

    assert response.status_code == 200
    titles = [row["title"] for row in response.data["results"]]
    assert titles == ["Saved second", "Saved first"]
    assert all(row["is_saved"] is True for row in response.data["results"])


@pytest.mark.django_db
def test_deleted_playlist_removes_saved_records_by_cascade():
    publisher = _make_user("saved_delete_publisher", role="publisher")
    student = _make_user("saved_delete_student")
    playlist = _make_playlist(publisher, "Delete saved")
    SavedPlaylist.objects.create(user=student, playlist=playlist)

    playlist.delete()

    assert SavedPlaylist.objects.count() == 0


@pytest.mark.django_db
def test_playlist_detail_includes_save_state_and_count_for_authenticated_user():
    publisher = _make_user("saved_detail_publisher", role="publisher")
    student = _make_user("saved_detail_student")
    other_student = _make_user("saved_detail_other")
    playlist = _make_playlist(publisher, "Detail saved")
    SavedPlaylist.objects.create(user=student, playlist=playlist)
    SavedPlaylist.objects.create(user=other_student, playlist=playlist)

    response = _client(student).get(f"/api/v1/playlists/{playlist.id}/")

    assert response.status_code == 200
    assert response.data["is_saved"] is True
    assert response.data["save_count"] == 2


@pytest.mark.django_db
def test_public_playlist_detail_does_not_leak_private_playlist():
    publisher = _make_user("saved_private_detail_publisher", role="publisher")
    playlist = _make_playlist(publisher, "Private detail", is_public=False)

    response = _client().get(f"/api/v1/playlists/{playlist.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_save_count_updates_correctly():
    publisher = _make_user("saved_count_publisher", role="publisher")
    first_student = _make_user("saved_count_first")
    second_student = _make_user("saved_count_second")
    playlist = _make_playlist(publisher, "Counted saves")

    first = _client(first_student).post(f"/api/v1/playlists/{playlist.id}/save/")
    second = _client(second_student).post(f"/api/v1/playlists/{playlist.id}/save/")
    third = _client(first_student).post(f"/api/v1/playlists/{playlist.id}/save/")

    assert first.data["save_count"] == 1
    assert second.data["save_count"] == 2
    assert third.data == {"is_saved": False, "save_count": 1}


@pytest.mark.django_db
def test_saved_playlist_admin_registration():
    assert admin.site.is_registered(SavedPlaylist)
