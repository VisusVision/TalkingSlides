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

from core.models import Job, Playlist, PlaylistItem, Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student") -> User:
    user = User.objects.create_user(username=username, password="pass")
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
    rendered: bool = True,
    approved: bool = True,
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
def test_public_channel_playlists_only_include_public_playlists():
    publisher = _make_user("public_playlist_publisher", role="publisher")
    public_playlist = Playlist.objects.create(user=publisher, title="Public playlist", is_public=True)
    private_playlist = Playlist.objects.create(user=publisher, title="Private playlist", is_public=False)
    visible = _make_project(publisher, "Visible lesson")
    PlaylistItem.objects.create(playlist=public_playlist, project=visible, order=0)
    PlaylistItem.objects.create(playlist=private_playlist, project=visible, order=0)

    response = _client().get(f"/api/v1/users/{publisher.id}/playlists/")

    assert response.status_code == 200
    assert [row["title"] for row in response.data["results"]] == ["Public playlist"]
    assert response.data["results"][0]["item_count"] == 1
    assert response.data["results"][0]["items"][0]["project"]["title"] == "Visible lesson"


@pytest.mark.django_db
def test_public_playlist_detail_hides_draft_unpublished_and_unready_items():
    publisher = _make_user("public_detail_publisher", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Filtered public")
    visible = _make_project(publisher, "Visible lesson")
    draft = _make_project(publisher, "Draft lesson", published=False, ready=False, rendered=False)
    unpublished = _make_project(publisher, "Unpublished lesson", published=False, ready=True, rendered=True)
    unrendered = _make_project(publisher, "Unrendered lesson", published=True, ready=True, rendered=False)
    rejected = _make_project(publisher, "Rejected lesson", approved=False)
    for index, project in enumerate([draft, visible, unpublished, unrendered, rejected]):
        PlaylistItem.objects.create(playlist=playlist, project=project, order=index)

    response = _client().get(f"/api/v1/playlists/{playlist.id}/")

    assert response.status_code == 200
    assert response.data["item_count"] == 1
    assert [item["project"]["title"] for item in response.data["items"]] == ["Visible lesson"]


@pytest.mark.django_db
def test_private_playlist_hidden_from_anonymous():
    publisher = _make_user("private_playlist_publisher", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Private playlist", is_public=False)
    visible = _make_project(publisher, "Visible private lesson")
    PlaylistItem.objects.create(playlist=playlist, project=visible, order=0)

    response = _client().get(f"/api/v1/playlists/{playlist.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_owner_can_see_private_playlist_and_draft_items():
    publisher = _make_user("private_owner_publisher", role="publisher")
    playlist = Playlist.objects.create(user=publisher, title="Private owner", is_public=False)
    draft = _make_project(publisher, "Owner draft", published=False, ready=False, rendered=False)
    PlaylistItem.objects.create(playlist=playlist, project=draft, order=0)

    response = _client(publisher).get(f"/api/v1/playlists/{playlist.id}/")

    assert response.status_code == 200
    assert response.data["is_public"] is False
    assert response.data["items"][0]["project"]["title"] == "Owner draft"
