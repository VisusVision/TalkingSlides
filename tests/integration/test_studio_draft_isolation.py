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

from core.drafts import clear_project_draft  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "publisher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(owner: User, title: str = "Draft isolated lesson") -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}.mp4")
    return project


def _make_page(project: Project, *, order: int, text: str, page_key: str | None = None) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=order,
        source_slide_index=order,
        split_index=0,
        page_key=page_key or f"s{order + 1}-p1",
        original_text=text,
        narration_text=text,
        rich_text_html=text,
        subtitle_chunks=[text],
        editor_document={
            "version": 1,
            "html": text,
            "paragraphs": [{"index": 0, "text": text}],
            "text": {"narration_customized": False, "display_text_customized": False},
        },
    )


@pytest.mark.django_db
def test_studio_transcript_save_writes_draft_without_mutating_active_page():
    owner = _make_user("draft_save_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active text")

    response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "draft_only": True,
            "pages": [{"id": page.id, "original_text": "Draft display", "narration_text": "Draft narration"}],
        },
        format="json",
    )

    assert response.status_code == 200
    project.refresh_from_db()
    page.refresh_from_db()
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["transcript_pages"][0]["original_text"] == "Draft display"
    assert project.draft_data["transcript_pages"][0]["narration_text"] == "Draft narration"
    assert page.original_text == "Active text"
    assert page.narration_text == "Active text"


@pytest.mark.django_db
def test_studio_transcript_fetch_returns_draft_text_when_present():
    owner = _make_user("draft_fetch_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active text")
    _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"draft_only": True, "pages": [{"id": page.id, "narration_text": "Draft narration"}]},
        format="json",
    )

    response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")

    assert response.status_code == 200
    assert response.data["has_draft"] is True
    assert response.data["draft_metadata"]["dirty"] is True
    assert response.data["pages"][0]["narration_text"] == "Draft narration"


@pytest.mark.django_db
def test_public_catalog_detail_still_returns_active_transcript_text():
    owner = _make_user("draft_public_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Public active text")
    _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"draft_only": True, "pages": [{"id": page.id, "narration_text": "Private draft text"}]},
        format="json",
    )

    response = _client().get(f"/api/v1/catalog/{project.id}/")

    assert response.status_code == 200
    assert response.data["transcript_pages"][0]["narration_text"] == "Public active text"
    assert "draft_data" not in response.data
    assert "draft_metadata" not in response.data


@pytest.mark.django_db
def test_split_and_reorder_actions_mutate_draft_only():
    owner = _make_user("draft_action_owner")
    project = _make_project(owner)
    first = _make_page(project, order=0, text="First")
    second = _make_page(project, order=1, text="Second")

    split_response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "draft_only": True,
            "action": "split_page",
            "page_id": first.id,
            "parts": [{"narration_text": "First A"}, {"narration_text": "First B"}],
        },
        format="json",
    )
    split_page_ids = [page["id"] for page in split_response.data["pages"]]
    reorder_response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "draft_only": True,
            "action": "reorder_pages",
            "page_ids": [split_page_ids[-1], split_page_ids[0], split_page_ids[1]],
        },
        format="json",
    )

    assert split_response.status_code == 200
    assert reorder_response.status_code == 200
    active_pages = list(TranscriptPage.objects.filter(project=project, is_active=True).order_by("order", "id"))
    assert [page.id for page in active_pages] == [first.id, second.id]
    assert [page.order for page in active_pages] == [0, 1]
    project.refresh_from_db()
    assert [page["id"] for page in project.draft_data["transcript_pages"]] == [second.id, first.id, -1]
    assert len(project.draft_data["transcript_pages"]) == 3


@pytest.mark.django_db
def test_project_detail_exposes_draft_marker_to_owner_only():
    owner = _make_user("draft_marker_owner")
    stranger = _make_user("draft_marker_stranger", role="student")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active")
    _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"draft_only": True, "pages": [{"id": page.id, "narration_text": "Draft"}]},
        format="json",
    )

    owner_response = _client(owner).get(f"/api/v1/projects/{project.id}/")
    stranger_response = _client(stranger).get(f"/api/v1/projects/{project.id}/")

    assert owner_response.status_code == 200
    assert owner_response.data["has_draft"] is True
    assert owner_response.data["draft_metadata"]["dirty"] is True
    assert stranger_response.status_code == 403


@pytest.mark.django_db
def test_studio_tts_save_writes_draft_without_mutating_active_project_settings():
    owner = _make_user("draft_tts_owner")
    project = _make_project(owner)
    original_settings = dict(project.tts_settings)

    response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/",
        {
            "draft_only": True,
            "tts_settings": {
                "provider_preference": "gtts",
                "speech_speed": 1.2,
            },
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["has_draft"] is True
    assert response.data["tts_settings"]["provider_preference"] == "gtts"
    project.refresh_from_db()
    assert dict(project.tts_settings) == original_settings
    assert project.draft_data["project"]["tts_settings"]["speech_speed"] == 1.2


@pytest.mark.django_db
def test_clear_project_draft_does_not_change_active_data():
    owner = _make_user("draft_clear_owner")
    project = _make_project(owner)
    page = _make_page(project, order=0, text="Active before clear")
    _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"draft_only": True, "pages": [{"id": page.id, "narration_text": "Draft before clear"}]},
        format="json",
    )

    project.refresh_from_db()
    clear_project_draft(project)
    page.refresh_from_db()
    project.refresh_from_db()

    assert project.draft_data == {}
    assert page.narration_text == "Active before clear"
