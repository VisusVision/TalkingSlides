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

from core.models import Category, Job, Playlist, PlaylistItem, Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "publisher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_category(name: str) -> Category:
    return Category.objects.create(name=name)


def _make_project(
    owner: User,
    title: str,
    *,
    category: Category | None = None,
    published: bool = True,
    ready: bool = True,
    rendered: bool = True,
    moderation_status: str = "approved",
) -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        category=category,
        status="ready" if ready else "draft",
        moderation_status=moderation_status,
        is_published=published,
    )
    if rendered:
        Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


@pytest.mark.django_db
def test_public_lesson_in_public_playlist_returns_playlist_context_with_order_and_current_marker():
    publisher = _make_user("watch_playlist_owner")
    playlist = Playlist.objects.create(user=publisher, title="Linear Algebra")
    first = _make_project(publisher, "Vectors")
    current = _make_project(publisher, "Matrices")
    third = _make_project(publisher, "Eigenvalues")
    PlaylistItem.objects.create(playlist=playlist, project=first, order=0)
    PlaylistItem.objects.create(playlist=playlist, project=current, order=1)
    PlaylistItem.objects.create(playlist=playlist, project=third, order=2)

    response = _client().get(f"/api/v1/catalog/{current.id}/playlist-context/")

    assert response.status_code == 200
    assert response.data["mode"] == "playlist"
    assert response.data["playlist"]["title"] == "Linear Algebra"
    assert [item["project"]["id"] for item in response.data["items"]] == [first.id, current.id, third.id]
    assert [item["order"] for item in response.data["items"]] == [0, 1, 2]
    current_rows = [item for item in response.data["items"] if item["is_current"]]
    assert len(current_rows) == 1
    assert current_rows[0]["project"]["id"] == current.id


@pytest.mark.django_db
def test_public_playlist_context_hides_draft_unpublished_and_rejected_items():
    publisher = _make_user("watch_playlist_filtered")
    playlist = Playlist.objects.create(user=publisher, title="Visible sequence")
    current = _make_project(publisher, "Current public")
    draft = _make_project(publisher, "Draft hidden", published=False, ready=False, rendered=True)
    unpublished = _make_project(publisher, "Unpublished hidden", published=False, ready=True, rendered=True)
    rejected = _make_project(publisher, "Rejected hidden", moderation_status="admin_rejected")
    for index, project in enumerate([draft, current, unpublished, rejected]):
        PlaylistItem.objects.create(playlist=playlist, project=project, order=index)

    response = _client().get(f"/api/v1/catalog/{current.id}/playlist-context/")

    assert response.status_code == 200
    assert response.data["mode"] == "playlist"
    assert [item["project"]["title"] for item in response.data["items"]] == ["Current public"]


@pytest.mark.django_db
def test_private_playlist_is_ignored_for_anonymous_context():
    publisher = _make_user("watch_private_playlist")
    private_playlist = Playlist.objects.create(user=publisher, title="Private sequence", is_public=False)
    current = _make_project(publisher, "Public lesson in private playlist")
    sibling = _make_project(publisher, "Public sibling")
    PlaylistItem.objects.create(playlist=private_playlist, project=current, order=0)

    response = _client().get(f"/api/v1/catalog/{current.id}/playlist-context/")

    assert response.status_code == 200
    assert response.data["mode"] == "publisher"
    assert response.data["playlist"] is None
    assert [item["id"] for item in response.data["items"]] == [sibling.id]


@pytest.mark.django_db
def test_publisher_fallback_returns_same_publisher_and_prefers_same_category():
    algebra = _make_category("Algebra")
    geometry = _make_category("Geometry")
    publisher = _make_user("watch_fallback_publisher")
    other_publisher = _make_user("watch_fallback_other")
    current = _make_project(publisher, "Current algebra", category=algebra)
    same_category = _make_project(publisher, "More algebra", category=algebra)
    other_category = _make_project(publisher, "Geometry next", category=geometry)
    _make_project(other_publisher, "Other publisher algebra", category=algebra)

    response = _client().get(f"/api/v1/catalog/{current.id}/playlist-context/")

    assert response.status_code == 200
    assert response.data["mode"] == "publisher"
    ids = [item["id"] for item in response.data["items"]]
    assert ids[0] == same_category.id
    assert other_category.id in ids
    assert current.id not in ids
    assert all(item["teacher_id"] == publisher.id for item in response.data["items"])


@pytest.mark.django_db
def test_owner_can_get_private_rendered_lesson_context():
    publisher = _make_user("watch_private_owner")
    current = _make_project(publisher, "Owner draft current", published=False, ready=False, rendered=True)
    sibling = _make_project(publisher, "Owner draft sibling", published=False, ready=False, rendered=True)

    response = _client(publisher).get(f"/api/v1/catalog/{current.id}/playlist-context/")

    assert response.status_code == 200
    assert response.data["mode"] == "publisher"
    assert [item["id"] for item in response.data["items"]] == [sibling.id]


@pytest.mark.django_db
def test_anonymous_cannot_get_context_for_non_public_lesson():
    publisher = _make_user("watch_private_anonymous")
    draft = _make_project(publisher, "Anonymous hidden draft", published=False, ready=False, rendered=True)

    response = _client().get(f"/api/v1/catalog/{draft.id}/playlist-context/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_public_fallback_does_not_leak_rejected_lessons():
    publisher = _make_user("watch_rejected_filtered")
    current = _make_project(publisher, "Public current")
    visible = _make_project(publisher, "Visible sibling")
    rejected = _make_project(publisher, "Rejected sibling", moderation_status="revision_required")

    response = _client().get(f"/api/v1/catalog/{current.id}/playlist-context/")

    assert response.status_code == 200
    assert response.data["mode"] == "publisher"
    ids = [item["id"] for item in response.data["items"]]
    assert visible.id in ids
    assert rejected.id not in ids
