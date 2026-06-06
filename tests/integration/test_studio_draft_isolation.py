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
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.drafts import clear_project_draft  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


def _make_page(
    project: Project,
    *,
    order: int,
    text: str,
    page_key: str | None = None,
    editor_document: dict | None = None,
    whiteboard_mode: bool = False,
) -> TranscriptPage:
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
        editor_document=editor_document or {
            "version": 1,
            "html": text,
            "paragraphs": [{"index": 0, "text": text}],
            "text": {"narration_customized": False, "display_text_customized": False},
        },
        whiteboard_mode=whiteboard_mode,
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
    split_pages = split_response.data["pages"]
    assert [page["page_key"] for page in split_pages[:2]] == ["s1-p1", "s1-p2"]
    assert [page["original_text"] for page in split_pages[:2]] == ["First A", "First B"]
    assert [page["narration_text"] for page in split_pages[:2]] == ["First A", "First B"]
    assert [page["subtitle_chunks"] for page in split_pages[:2]] == [["First A"], ["First B"]]
    assert split_pages[0]["editor_document"]["paragraphs"][0]["text"] == "First A"
    assert split_pages[1]["editor_document"]["paragraphs"][0]["text"] == "First B"
    assert reorder_response.status_code == 200
    active_pages = list(TranscriptPage.objects.filter(project=project, is_active=True).order_by("order", "id"))
    assert [page.id for page in active_pages] == [first.id, second.id]
    assert [page.order for page in active_pages] == [0, 1]
    project.refresh_from_db()
    assert [page["id"] for page in project.draft_data["transcript_pages"]] == [second.id, first.id, -1]
    assert len(project.draft_data["transcript_pages"]) == 3


@pytest.mark.django_db
def test_split_draft_page_inherits_visual_scene_and_remains_editable(tmp_path):
    owner = _make_user("draft_split_visual_owner")
    project = _make_project(owner)
    parent_scene = {
        "background_mode": "source_background",
        "background_fit": "cover",
        "text_scale": 1.35,
        "source_type": "pptx",
        "original_background_path": f"{project.id}/images/slide-1.png",
        "custom_background_path": f"uploads/{project.id}/backgrounds/custom.png",
        "source_background_path": f"{project.id}/source_backgrounds/slide-1.png",
        "overlay_layout": {"padding": 44, "safe_area": {"top": 12, "bottom": 18}},
        "font": {"size": 34, "align": "center", "family": "Inter"},
    }
    page = _make_page(
        project,
        order=0,
        text="First visual part\n\nSecond visual part",
        editor_document={
            "version": 1,
            "html": "First visual part<br><br>Second visual part",
            "paragraphs": [{"index": 0, "text": "First visual part\n\nSecond visual part"}],
            "scene": parent_scene,
        },
    )

    split_response = _client(owner).post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "draft_only": True,
            "action": "split_page",
            "page_id": page.id,
            "parts": [{"narration_text": "First visual part"}, {"narration_text": "Second visual part"}],
        },
        format="json",
    )

    assert split_response.status_code == 200
    first_split, second_split = split_response.data["pages"][:2]
    assert first_split["original_text"] == "First visual part"
    assert second_split["original_text"] == "Second visual part"
    assert int(second_split["id"]) < 0
    second_scene = second_split["editor_document"]["scene"]
    assert second_scene["background_mode"] == "source_background"
    assert second_scene["background_fit"] == "cover"
    assert second_scene["text_scale"] == 1.35
    assert second_scene["overlay_layout"] == parent_scene["overlay_layout"]
    assert second_scene["font"] == parent_scene["font"]
    assert second_scene["original_background_url"].endswith("?draft=1")
    assert second_scene["custom_background_url"].endswith("?draft=1")
    assert second_scene["source_background_url"].endswith("?draft=1")
    assert "original_background_path" not in second_scene
    assert "custom_background_path" not in second_scene
    assert "source_background_path" not in second_scene

    scene_response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{second_split['id']}/scene/",
        {"draft_only": True, "text_scale": 1.8, "background_fit": "stretch"},
        format="json",
    )

    assert scene_response.status_code == 200
    project.refresh_from_db()
    first_draft, second_draft = project.draft_data["transcript_pages"][:2]
    first_scene = first_draft["editor_document"]["scene"]
    edited_second_scene = second_draft["editor_document"]["scene"]
    assert first_scene["text_scale"] == 1.35
    assert first_scene["background_fit"] == "cover"
    assert edited_second_scene["text_scale"] == 1.8
    assert edited_second_scene["background_fit"] == "stretch"
    assert edited_second_scene["source_background_path"] == parent_scene["source_background_path"]
    assert edited_second_scene["overlay_layout"] == parent_scene["overlay_layout"]

    upload = SimpleUploadedFile("replacement.png", PNG_1X1, content_type="image/png")
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        upload_response = _client(owner).post(
            f"/api/v1/projects/{project.id}/transcript-pages/{second_split['id']}/background/",
            {
                "draft_only": "1",
                "background_file": upload,
                "background_fit": "contain",
                "text_scale": "1.1",
            },
            format="multipart",
        )

    assert upload_response.status_code == 200
    project.refresh_from_db()
    first_scene = project.draft_data["transcript_pages"][0]["editor_document"]["scene"]
    uploaded_second_scene = project.draft_data["transcript_pages"][1]["editor_document"]["scene"]
    assert first_scene["custom_background_path"] == parent_scene["custom_background_path"]
    assert uploaded_second_scene["background_mode"] == "custom"
    assert uploaded_second_scene["custom_background_path"].startswith(f"uploads/{project.id}/backgrounds/page_-1_")
    assert uploaded_second_scene["text_scale"] == 1.1
    assert uploaded_second_scene["overlay_layout"] == parent_scene["overlay_layout"]
    assert upload_response.data["page"]["editor_document"]["scene"]["custom_background_url"].endswith("?draft=1")


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
