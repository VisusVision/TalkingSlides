# pyright: reportMissingImports=false

import json
import os
import sys
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from core.serializers import ProjectSerializer, TranscriptPageSerializer  # noqa: E402
from scripts import ffmpeg_helpers, tts_client  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str = "scene_teacher") -> Project:
    return Project.objects.create(
        title=f"Scene project {username}",
        user=_make_teacher(username),
        moderation_status="approved",
    )


def _make_page(project: Project, key: str = "s1-p1", *, whiteboard_mode: bool = False, editor_document=None) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key=key,
        original_text="Original",
        narration_text="Narration",
        rich_text_html="Original",
        editor_document=editor_document or {"version": 1, "paragraphs": [{"index": 0, "text": "Original"}]},
        subtitle_chunks=["Narration"],
        whiteboard_mode=whiteboard_mode,
    )


@pytest.mark.django_db
def test_transcript_sync_stores_original_background_path_from_export(tmp_path, monkeypatch):
    project = _make_project("sync_original_background")
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "split_index": 0,
                "page_key": "s1-p1",
                "image_path": str(slide_path),
                "original_text": "Original",
                "narration_text": "Narration",
                "subtitle_chunks": ["Narration"],
                "whiteboard_mode": False,
            }
        ],
    )

    page = TranscriptPage.objects.get(project=project, page_key="s1-p1")
    scene = page.editor_document["scene"]
    assert scene["background_mode"] == "original"
    assert scene["original_background_path"] == f"{project.id}/images/slide-1.png"

    serialized = TranscriptPageSerializer(page).data
    assert serialized["editor_document"]["scene"]["original_background_url"]
    assert "original_background_path" not in serialized["editor_document"]["scene"]


@pytest.mark.django_db
def test_transcript_sync_preserves_custom_background_settings(tmp_path, monkeypatch):
    project = _make_project("sync_custom_background")
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "custom",
                "custom_background_path": f"uploads/{project.id}/backgrounds/custom.png",
                "background_fit": "cover",
                "text_scale": 1.4,
            },
        },
    )
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [{"index": 0, "source_slide_index": 0, "page_key": page.page_key, "image_path": str(slide_path)}],
    )

    page.refresh_from_db()
    scene = page.editor_document["scene"]
    assert scene["background_mode"] == "custom"
    assert scene["custom_background_path"] == f"uploads/{project.id}/backgrounds/custom.png"
    assert scene["background_fit"] == "cover"
    assert scene["original_background_path"] == f"{project.id}/images/slide-1.png"


@pytest.mark.django_db
def test_whiteboard_mode_backward_compatibility_maps_scene_mode():
    project = _make_project("whiteboard_compat")
    whiteboard_page = _make_page(project, key="whiteboard", whiteboard_mode=True)
    original_page = _make_page(project, key="original", whiteboard_mode=False)

    whiteboard_scene = TranscriptPageSerializer(whiteboard_page).data["editor_document"]["scene"]
    original_scene = TranscriptPageSerializer(original_page).data["editor_document"]["scene"]

    assert whiteboard_scene["background_mode"] == "whiteboard"
    assert original_scene["background_mode"] == "original"


@pytest.mark.django_db
def test_owner_can_patch_scene_settings():
    teacher = _make_teacher("patch_scene_owner")
    project = Project.objects.create(title="Patch scene", user=teacher)
    page = _make_page(project)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {"background_mode": "whiteboard", "background_fit": "cover", "text_scale": 1.5},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 200
    page.refresh_from_db()
    assert page.whiteboard_mode is True
    assert page.editor_document["scene"]["background_mode"] == "whiteboard"
    assert page.editor_document["scene"]["background_fit"] == "cover"
    assert page.editor_document["scene"]["text_scale"] == 1.5

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {"text_scale": 0.75},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 200
    page.refresh_from_db()
    assert page.editor_document["scene"]["text_scale"] == 0.75


@pytest.mark.django_db
def test_owner_can_select_source_background_mode(tmp_path):
    teacher = _make_teacher("patch_source_background_owner")
    project = Project.objects.create(title="Patch source background", user=teacher)
    source_path = tmp_path / str(project.id) / "source_backgrounds" / "slide-1.png"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "source_background_path": f"{project.id}/source_backgrounds/slide-1.png",
            },
        },
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {"background_mode": "source_background"},
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 200
    page.refresh_from_db()
    assert page.whiteboard_mode is False
    assert page.editor_document["scene"]["background_mode"] == "source_background"
    response_scene = response.data["page"]["editor_document"]["scene"]
    assert response_scene["background_mode"] == "source_background"
    assert response_scene["has_source_background"] is True
    assert response_scene["source_background_url"]
    assert "source_background_path" not in response_scene

    get_request = APIRequestFactory().get(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/source/"
    )
    force_authenticate(get_request, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        get_response = views.TranscriptPageBackgroundImageView.as_view()(
            get_request,
            project_id=project.id,
            page_id=page.id,
            kind="source",
        )
    assert get_response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("source_type,lesson_name", [("docx", "lesson.docx"), ("pdf", "lesson.pdf"), ("png", "lesson.png")])
def test_non_pptx_source_background_mode_is_rejected(tmp_path, source_type, lesson_name):
    teacher = _make_teacher(f"reject_source_background_{source_type}")
    project = Project.objects.create(title=f"Reject source background {source_type}", user=teacher)
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / lesson_name).write_bytes(b"source")
    source_background_path = tmp_path / str(project.id) / "source_backgrounds" / "slide-1.png"
    source_background_path.parent.mkdir(parents=True, exist_ok=True)
    source_background_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "source_type": source_type,
                "source_background_path": f"{project.id}/source_backgrounds/slide-1.png",
            },
        },
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {"background_mode": "source_background"},
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)
        serialized_scene = TranscriptPageSerializer(page, context={"request": request}).data["editor_document"]["scene"]

    assert response.status_code == 400
    assert response.data["error"] == "Source Background is currently available for PPTX lessons only."
    page.refresh_from_db()
    assert page.editor_document["scene"]["background_mode"] == "original"
    assert serialized_scene["source_type"] == source_type
    assert serialized_scene["source_background_available"] is False
    assert serialized_scene["has_source_background"] is False
    assert serialized_scene["source_background_url"] == ""


@pytest.mark.django_db
def test_custom_mode_without_background_is_rejected():
    teacher = _make_teacher("reject_custom_without_background")
    project = Project.objects.create(title="Reject custom without background", user=teacher)
    page = _make_page(project)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {"background_mode": "custom"},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 400
    assert response.data["error"] == "Upload/select a custom background first."
    page.refresh_from_db()
    assert page.editor_document.get("scene", {}).get("background_mode") != "custom"


@pytest.mark.django_db
def test_non_owner_cannot_patch_scene_settings():
    owner = _make_teacher("patch_scene_forbidden_owner")
    other = _make_teacher("patch_scene_forbidden_other")
    project = Project.objects.create(title="Patch forbidden", user=owner)
    page = _make_page(project)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {"background_mode": "whiteboard"},
        format="json",
    )
    force_authenticate(request, user=other)

    response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_can_upload_custom_page_background(tmp_path):
    teacher = _make_teacher("upload_scene_background")
    project = Project.objects.create(title="Upload background", user=teacher)
    page = _make_page(project)

    upload = SimpleUploadedFile("background.png", PNG_1X1, content_type="image/png")
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/",
        {"background_file": upload},
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.TranscriptPageBackgroundUploadView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 200
    page.refresh_from_db()
    scene = page.editor_document["scene"]
    assert scene["background_mode"] == "custom"
    assert scene["custom_background_path"].startswith(f"uploads/{project.id}/backgrounds/page_{page.id}_")
    assert response.data["page"]["editor_document"]["scene"]["custom_background_url"]
    assert "custom_background_path" not in response.data["page"]["editor_document"]["scene"]
    project.refresh_from_db()
    visual_scan = project.moderation_summary["visual_asset_scan"]
    assert visual_scan["status"] == "needs_rescan"
    assert visual_scan["asset_type"] == "custom_background"
    assert visual_scan["transcript_page_id"] == page.id
    assert response.data["moderation_summary"]["visual_asset_scan"]["status"] == "needs_rescan"
    assert response.data["moderation_summary"]["visual_asset_scan"]["asset_type"] == "custom_background"


@pytest.mark.django_db
def test_apply_all_copies_background_scene_settings_to_active_pages():
    teacher = _make_teacher("apply_all_scene")
    project = Project.objects.create(title="Apply all", user=teacher)
    source = _make_page(
        project,
        key="source",
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "custom",
                "custom_background_path": f"uploads/{project.id}/backgrounds/custom.png",
                "background_fit": "cover",
                "text_scale": 1.25,
            },
        },
    )
    target = _make_page(project, key="target")
    target.order = 1
    target.save(update_fields=["order"])

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/background/apply-all/",
        {"source_page_id": source.id, "background_mode": "custom", "background_fit": "cover", "text_scale": 1.25},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectBackgroundApplyAllView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    target.refresh_from_db()
    assert target.editor_document["scene"]["background_mode"] == "custom"
    assert target.editor_document["scene"]["custom_background_path"] == f"uploads/{project.id}/backgrounds/custom.png"
    assert target.editor_document["scene"]["background_fit"] == "cover"
    assert target.editor_document["scene"]["text_scale"] == 1.25
    assert target.narration_text == "Narration"
    assert target.original_text == "Original"
    response_page = next(item for item in response.data["pages"] if item["id"] == target.id)
    response_scene = response_page["editor_document"]["scene"]
    assert response_scene["background_mode"] == "custom"
    assert response_scene["custom_background_url"]
    assert "custom_background_path" not in response_scene
    assert response.data["moderation_summary"]["visual_asset_scan"]["status"] == "needs_rescan"
    assert response.data["moderation_summary"]["visual_asset_scan"]["asset_type"] == "custom_background"


@pytest.mark.django_db
def test_non_owner_cannot_apply_background_to_all():
    owner = _make_teacher("apply_all_forbidden_owner")
    other = _make_teacher("apply_all_forbidden_other")
    project = Project.objects.create(title="Apply all forbidden", user=owner)
    source = _make_page(project)

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/background/apply-all/",
        {"source_page_id": source.id, "background_mode": "whiteboard"},
        format="json",
    )
    force_authenticate(request, user=other)

    response = views.ProjectBackgroundApplyAllView.as_view()(request, project_id=project.id)

    assert response.status_code == 403


@pytest.mark.django_db
def test_display_text_edit_updates_original_and_narration_when_not_customized():
    teacher = _make_teacher("display_text_sync_teacher")
    project = Project.objects.create(title="Display text sync", user=teacher, moderation_status="approved")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original display",
        narration_text="Original display",
        rich_text_html="Original display",
        editor_document={
            "version": 1,
            "paragraphs": [{"index": 0, "text": "Original display"}],
            "text": {"narration_customized": False, "display_text_customized": False},
        },
        subtitle_chunks=["Original display"],
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"pages": [{"id": page.id, "original_text": "Updated display"}]},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    page.refresh_from_db()
    project.refresh_from_db()
    assert page.original_text == "Updated display"
    assert page.narration_text == "Updated display"
    assert page.subtitle_chunks == ["Updated display"]
    assert page.editor_document["paragraphs"][0]["text"] == "Updated display"
    assert page.editor_document["text"]["display_text_customized"] is True
    assert page.editor_document["text"]["narration_customized"] is False
    assert project.moderation_status == "not_scanned"
    assert project.moderation_summary["editor_text_changed"]["changed_fields"] == ["narration_text", "original_text"]
    assert response.data["moderation_status"] == "not_scanned"
    assert response.data["moderation_summary"]["editor_text_changed"]["changed_fields"] == [
        "narration_text",
        "original_text",
    ]


@pytest.mark.django_db
def test_display_text_edit_preserves_custom_narration():
    teacher = _make_teacher("display_text_custom_narration_teacher")
    project = Project.objects.create(title="Display text custom narration", user=teacher, moderation_status="approved")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original display",
        narration_text="Custom narration",
        rich_text_html="Original display",
        editor_document={
            "version": 1,
            "paragraphs": [{"index": 0, "text": "Original display"}],
            "text": {"narration_customized": True, "display_text_customized": False},
        },
        subtitle_chunks=["Custom narration"],
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"pages": [{"id": page.id, "original_text": "Updated display"}]},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    page.refresh_from_db()
    assert page.original_text == "Updated display"
    assert page.narration_text == "Custom narration"
    assert page.subtitle_chunks == ["Custom narration"]
    assert page.editor_document["text"]["narration_customized"] is True


@pytest.mark.django_db
def test_narration_edit_marks_custom_without_changing_display_text():
    teacher = _make_teacher("narration_custom_teacher")
    project = Project.objects.create(title="Narration custom", user=teacher, moderation_status="approved")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Display only",
        narration_text="Display only",
        rich_text_html="Display only",
        editor_document={
            "version": 1,
            "paragraphs": [{"index": 0, "text": "Display only"}],
            "text": {"narration_customized": False, "display_text_customized": False},
        },
        subtitle_chunks=["Display only"],
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"pages": [{"id": page.id, "narration_text": "Spoken and caption text"}]},
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    page.refresh_from_db()
    project.refresh_from_db()
    assert page.original_text == "Display only"
    assert page.narration_text == "Spoken and caption text"
    assert page.subtitle_chunks == ["Spoken and caption text"]
    assert page.editor_document["paragraphs"][0]["text"] == "Display only"
    assert page.editor_document["text"]["narration_customized"] is True
    assert project.moderation_status == "not_scanned"
    assert project.moderation_summary["editor_text_changed"]["changed_fields"] == ["narration_text"]
    assert response.data["moderation_status"] == "not_scanned"
    assert response.data["moderation_summary"]["editor_text_changed"]["changed_fields"] == ["narration_text"]


@pytest.mark.django_db
def test_transcript_sync_preserves_whiteboard_scene_and_text_content(tmp_path, monkeypatch):
    project = _make_project("sync_whiteboard_text")
    page = _make_page(
        project,
        whiteboard_mode=True,
        editor_document={
            "version": 1,
            "scene": {"background_mode": "whiteboard", "text_scale": 1.45},
            "paragraphs": [{"index": 0, "text": "Edited narration"}],
        },
    )
    page.narration_text = "Edited narration"
    page.rich_text_html = "Edited narration"
    page.save(update_fields=["narration_text", "rich_text_html", "editor_document"])
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [{"index": 0, "source_slide_index": 0, "page_key": page.page_key, "image_path": str(slide_path)}],
    )

    page.refresh_from_db()
    assert page.whiteboard_mode is True
    assert page.narration_text == "Edited narration"
    assert page.rich_text_html == "Edited narration"
    assert page.editor_document["scene"]["background_mode"] == "whiteboard"
    assert page.editor_document["scene"]["original_background_path"] == f"{project.id}/images/slide-1.png"


@pytest.mark.django_db
def test_sync_render_descriptor_includes_background_and_text(tmp_path, monkeypatch):
    project = _make_project("sync_render_descriptor")
    custom_path = tmp_path / "uploads" / str(project.id) / "backgrounds" / "custom.png"
    custom_path.parent.mkdir(parents=True, exist_ok=True)
    custom_path.write_bytes(PNG_1X1)
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "custom",
                "custom_background_path": f"uploads/{project.id}/backgrounds/custom.png",
                "background_fit": "cover",
                "text_scale": 0.75,
            },
            "paragraphs": [{"index": 0, "text": "Display overlay"}],
            "text": {"narration_customized": True, "display_text_customized": True},
        },
    )
    page.original_text = "Display overlay"
    page.narration_text = "Spoken narration"
    page.rich_text_html = "Display overlay"
    page.subtitle_chunks = ["Spoken narration"]
    page.save(update_fields=["original_text", "narration_text", "rich_text_html", "subtitle_chunks", "editor_document"])
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [{"index": 0, "source_slide_index": 0, "page_key": page.page_key, "image_path": str(slide_path)}],
    )

    assert slides[0]["image_path"] == str(custom_path)
    assert slides[0]["scene_background_mode"] == "custom"
    assert slides[0]["display_text"] == "Display overlay"
    assert slides[0]["original_text"] == "Display overlay"
    assert slides[0]["narration_text"] == "Spoken narration"
    assert slides[0]["subtitle_chunks"] == ["Spoken narration"]
    assert slides[0]["scene_text_scale"] == 0.75


@pytest.mark.django_db
def test_sync_render_descriptor_normalizes_legacy_split_text(tmp_path, monkeypatch):
    project = _make_project("sync_legacy_split_text")
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    first = _make_page(project, key="s1-p1", whiteboard_mode=True)
    second = _make_page(project, key="s1-p2", whiteboard_mode=True)
    second.order = 1
    second.source_slide_index = 0
    second.split_index = 1
    first.original_text = "First visual segment\n\nSecond visual segment"
    first.narration_text = "First visual segment"
    first.rich_text_html = "First visual segment<br /><br />Second visual segment"
    first.subtitle_chunks = ["First visual segment", "Second visual segment"]
    second.original_text = ""
    second.narration_text = "Second visual segment"
    second.rich_text_html = ""
    second.subtitle_chunks = ["First visual segment", "Second visual segment"]
    first.save(update_fields=["original_text", "narration_text", "rich_text_html", "subtitle_chunks"])
    second.save(update_fields=["order", "source_slide_index", "split_index", "original_text", "narration_text", "rich_text_html", "subtitle_chunks"])
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "page_key": "s1-p1",
                "image_path": str(slide_path),
                "original_text": "First visual segment\n\nSecond visual segment",
                "display_text": "First visual segment\n\nSecond visual segment",
                "notes_text": "First visual segment\n\nSecond visual segment",
            }
        ],
    )

    assert [slide["page_key"] for slide in slides] == ["s1-p1", "s1-p2"]
    assert [slide["narration_text"] for slide in slides] == ["First visual segment", "Second visual segment"]
    assert [slide["original_text"] for slide in slides] == ["First visual segment", "Second visual segment"]
    assert [slide["display_text"] for slide in slides] == ["First visual segment", "Second visual segment"]
    assert [slide["subtitle_chunks"] for slide in slides] == [["First visual segment"], ["Second visual segment"]]


@pytest.mark.django_db
def test_sync_render_descriptor_uses_source_background_path(tmp_path, monkeypatch):
    project = _make_project("sync_source_background_descriptor")
    source_background_path = tmp_path / str(project.id) / "source_backgrounds" / "slide-1.png"
    source_background_path.parent.mkdir(parents=True, exist_ok=True)
    source_background_path.write_bytes(PNG_1X1)
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "source_background",
                "source_background_path": f"{project.id}/source_backgrounds/slide-1.png",
                "background_fit": "cover",
            },
            "paragraphs": [{"index": 0, "text": "Display overlay"}],
        },
    )
    page.original_text = "Display overlay"
    page.save(update_fields=["original_text", "editor_document"])
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "page_key": page.page_key,
                "source_type": "pptx",
                "image_path": str(slide_path),
                "source_background_path": str(source_background_path),
            }
        ],
    )

    assert slides[0]["image_path"] == str(source_background_path)
    assert slides[0]["scene_background_mode"] == "source_background"
    assert slides[0]["whiteboard_mode"] is False
    assert slides[0]["display_text"] == "Display overlay"


@pytest.mark.django_db
def test_sync_render_descriptor_falls_back_to_whiteboard_when_source_background_missing(tmp_path, monkeypatch):
    project = _make_project("sync_source_background_missing")
    slide_path = tmp_path / str(project.id) / "images" / "slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "source_background",
                "source_background_path": f"{project.id}/source_backgrounds/missing.png",
            },
        },
    )
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "page_key": page.page_key,
                "source_type": "pptx",
                "image_path": str(slide_path),
                "source_background_path": str(tmp_path / str(project.id) / "source_backgrounds" / "missing.png"),
            }
        ],
    )

    assert slides[0]["image_path"] == ""
    assert slides[0]["scene_background_mode"] == "source_background"
    assert slides[0]["whiteboard_mode"] is True
    assert "source_background_missing_fallback_whiteboard" in slides[0]["source_background_warnings"]


@pytest.mark.django_db
def test_sync_render_descriptor_falls_back_when_source_background_unsupported(tmp_path, monkeypatch):
    project = _make_project("sync_source_background_unsupported")
    slide_path = tmp_path / str(project.id) / "images" / "docx-slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "source_background",
                "source_type": "docx",
            },
        },
    )
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "page_key": page.page_key,
                "source_type": "docx",
                "image_path": str(slide_path),
            }
        ],
    )

    assert slides[0]["image_path"] == ""
    assert slides[0]["scene_background_mode"] == "whiteboard"
    assert slides[0]["whiteboard_mode"] is True
    assert "source_background_unsupported_for_source_type" in slides[0]["source_background_warnings"]


@pytest.mark.django_db
def test_sync_render_descriptor_custom_missing_falls_back_to_whiteboard(tmp_path, monkeypatch):
    project = _make_project("sync_custom_missing")
    slide_path = tmp_path / str(project.id) / "images" / "docx-slide-1.png"
    slide_path.parent.mkdir(parents=True, exist_ok=True)
    slide_path.write_bytes(PNG_1X1)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "custom",
                "source_type": "docx",
            },
        },
    )
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    slides = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "source_slide_index": 0,
                "page_key": page.page_key,
                "source_type": "docx",
                "image_path": str(slide_path),
            }
        ],
    )

    assert slides[0]["image_path"] == ""
    assert slides[0]["scene_background_mode"] == "whiteboard"
    assert slides[0]["whiteboard_mode"] is True
    assert "custom_background_missing_fallback_whiteboard" in slides[0]["source_background_warnings"]


def test_scene_overlay_layout_autofits_long_whiteboard_text():
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    image = Image.new("RGB", worker_tasks.SCENE_RENDER_CANVAS_SIZE, color="white")
    draw = ImageDraw.Draw(image)
    long_text = " ".join(["Long display text should fit inside the whiteboard canvas"] * 90)

    layout = worker_tasks._compute_scene_text_overlay_layout(
        draw,
        long_text,
        worker_tasks.SCENE_RENDER_CANVAS_SIZE,
        text_scale=1.0,
        boxed=False,
    )

    assert layout["text_scale"] == 1.0
    assert layout["font_size"] < layout["preferred_font_size"]
    assert layout["box_top"] >= layout["safe_top"]
    assert layout["box_bottom"] <= layout["safe_bottom"]
    assert layout["lines"]


def test_scene_overlay_layout_autofits_long_custom_text_and_preserves_rtl_alignment():
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    image = Image.new("RGB", worker_tasks.SCENE_RENDER_CANVAS_SIZE, color=(20, 40, 80))
    draw = ImageDraw.Draw(image)
    long_text = " ".join(["Readable custom background overlay text should fit"] * 100)

    layout = worker_tasks._compute_scene_text_overlay_layout(
        draw,
        long_text,
        worker_tasks.SCENE_RENDER_CANVAS_SIZE,
        text_scale=1.0,
        boxed=True,
    )
    assert layout["text_scale"] == 1.0
    assert layout["font_size"] < layout["preferred_font_size"]
    assert layout["box_top"] >= layout["safe_top"]
    assert layout["box_bottom"] <= layout["safe_bottom"]
    assert layout["padding_x"] <= int(worker_tasks.SCENE_RENDER_CANVAS_SIZE[0] * 0.035)

    rtl_layout = worker_tasks._compute_scene_text_overlay_layout(
        draw,
        "مرحبا بكم في الدرس الجديد",
        worker_tasks.SCENE_RENDER_CANVAS_SIZE,
        text_scale=0.75,
        boxed=True,
    )
    assert rtl_layout["text_scale"] == 0.75
    assert rtl_layout["rtl"] is True
    assert rtl_layout["box_bottom"] <= rtl_layout["safe_bottom"]


@pytest.mark.django_db
def test_custom_background_overlay_render_centers_readable_text(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    base_path = tmp_path / "base.png"
    output_path = tmp_path / "overlay.png"
    Image.new("RGB", (3200, 1800), color=(20, 40, 80)).save(base_path)

    rendered = worker_tasks._render_transcript_overlay_image(
        str(base_path),
        "Readable display text for a custom background",
        "",
        str(output_path),
        text_scale=1.0,
        background_fit="cover",
    )

    image = Image.open(rendered).convert("RGB")
    assert image.size == (1600, 900)
    assert image.getpixel((800, 450)) != (20, 40, 80)


@pytest.mark.django_db
def test_custom_background_slide_render_uses_fit_scale_and_overlay_text(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    base_path = tmp_path / "custom-background.png"
    Image.new("RGB", (3200, 1800), color=(20, 40, 80)).save(base_path)
    audio_path = tmp_path / "audio" / "slide_001.mp3"
    part_path = tmp_path / "parts" / "part_001.mp4"

    overlay_calls = []
    create_calls = []
    original_overlay = worker_tasks._render_transcript_overlay_image

    def fake_synthesize_text_with_metadata(_voice_id, text, out_path, **kwargs):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"audio")
        return {
            "spoken_text": text,
            "provider": "test",
            "tts_normalization_language": kwargs.get("lang") or "auto",
        }

    def capture_overlay(base_image_path, display_text, rich_text_html, output_path, *, text_scale=1.0, background_fit="contain"):
        overlay_calls.append(
            {
                "base_image_path": base_image_path,
                "display_text": display_text,
                "rich_text_html": rich_text_html,
                "text_scale": text_scale,
                "background_fit": background_fit,
            }
        )
        return original_overlay(
            base_image_path,
            display_text,
            rich_text_html,
            output_path,
            text_scale=text_scale,
            background_fit=background_fit,
        )

    def fake_create_slide_video(image_path, audio_path_value, out_video_path, **kwargs):
        create_calls.append(
            {
                "image_path": image_path,
                "audio_path": audio_path_value,
                "out_video_path": out_video_path,
                "duration_sec": kwargs.get("duration_sec"),
            }
        )
        Path(out_video_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_video_path).write_bytes(b"video")
        return out_video_path

    monkeypatch.setattr(worker_tasks.synthesize_and_render_slide, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(tts_client, "synthesize_text_with_metadata", fake_synthesize_text_with_metadata)
    monkeypatch.setattr(ffmpeg_helpers, "create_slide_video", fake_create_slide_video)
    monkeypatch.setattr(ffmpeg_helpers, "get_audio_duration", lambda _path: 1.0)
    monkeypatch.setattr(ffmpeg_helpers, "trim_trailing_silence", lambda _path: None)
    monkeypatch.setattr(worker_tasks, "_render_transcript_overlay_image", capture_overlay)

    result = worker_tasks.synthesize_and_render_slide.run(
        {
            "index": 0,
            "slide_num": 1,
            "page_key": "s1-p1",
            "source_slide_index": 0,
            "split_index": 0,
            "image_path": str(base_path),
            "notes_text": "Spoken narration",
            "narration_text": "Spoken narration",
            "original_text": "Visible display overlay",
            "display_text": "Visible display overlay",
            "rich_text_html": "Visible display overlay",
            "subtitle_chunks": ["Spoken narration"],
            "whiteboard_mode": False,
            "scene_background_mode": "custom",
            "custom_background_path": "uploads/1/backgrounds/custom-background.png",
            "scene_background_fit": "cover",
            "scene_text_scale": 1.0,
            "audio_out": str(audio_path),
            "part_out": str(part_path),
        },
        project_id="1",
        voice_id="test-voice",
        pause_sec=0.2,
        lang_hint="auto",
    )

    assert len(overlay_calls) == 1
    assert overlay_calls[0]["base_image_path"] == str(base_path)
    assert overlay_calls[0]["display_text"] == "Visible display overlay"
    assert overlay_calls[0]["rich_text_html"] == "Visible display overlay"
    assert overlay_calls[0]["background_fit"] == "cover"
    assert overlay_calls[0]["text_scale"] == 1.0
    assert len(create_calls) == 1
    assert create_calls[0]["image_path"].endswith(".overlay.png")
    assert Path(create_calls[0]["image_path"]).exists()
    assert result["slide_path"] == create_calls[0]["image_path"]
    assert result["display_text"] == "Visible display overlay"
    assert result["text"] == "Spoken narration"


@pytest.mark.django_db
def test_cover_upload_updates_project_cover_fields(tmp_path):
    teacher = _make_teacher("cover_upload_teacher")
    project = Project.objects.create(title="Cover upload", user=teacher)

    upload = SimpleUploadedFile("cover.png", PNG_1X1, content_type="image/png")
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/cover/",
        {"cover_file": upload},
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectCoverImageView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.cover_image_original.startswith(f"uploads/{project.id}/cover_")
    assert project.cover_image_processed == project.cover_image_original
    assert project.moderation_summary["visual_asset_scan"]["status"] == "needs_rescan"
    assert project.moderation_summary["visual_asset_scan"]["asset_type"] == "cover"
    assert response.data["moderation_summary"]["visual_asset_scan"]["status"] == "needs_rescan"
    assert response.data["moderation_summary"]["visual_asset_scan"]["asset_type"] == "cover"
    assert response.data["cover_url"].endswith(f"/api/v1/projects/{project.id}/cover/")
    assert ProjectSerializer(project, context={"request": request}).data["cover_url"].endswith(f"/api/v1/projects/{project.id}/cover/")


@pytest.mark.django_db
def test_public_catalog_does_not_expose_scene_storage_paths():
    teacher = _make_teacher("public_catalog_scene_paths")
    project = Project.objects.create(
        title="Public path safe",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="public.mp4")
    _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "original_background_path": "1/images/secret-slide.png",
                "custom_background_path": "uploads/1/backgrounds/secret.png",
                "source_background_path": "1/source_backgrounds/secret-clean.png",
            },
        },
    )

    request = APIRequestFactory().get("/api/v1/catalog/")
    response = views.CatalogListView.as_view()(request)

    assert response.status_code == 200
    payload = json.dumps(response.data)
    assert "secret-slide.png" not in payload
    assert "secret-clean.png" not in payload
    assert "custom_background_path" not in payload
    assert "original_background_path" not in payload
    assert "source_background_path" not in payload


@pytest.mark.django_db
def test_scene_patch_persists_highlight_fields(tmp_path):
    teacher = _make_teacher("scene_patch_highlight_fields")
    project = Project.objects.create(title="Highlight patch", user=teacher)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "original_background_path": f"{project.id}/images/slide-1.png",
            },
        },
    )
    source = tmp_path / str(project.id) / "images" / "slide-1.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(PNG_1X1)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/scene/",
        {
            "highlight_enabled": True,
            "highlight_style": "box",
            "highlight_detector": "auto",
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.TranscriptPageSceneView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 200
    page.refresh_from_db()
    scene = page.editor_document["scene"]
    assert scene["highlight_enabled"] is True
    assert scene["highlight_style"] == "box"
    assert scene["highlight_detector"] == "auto"


@pytest.mark.django_db
def test_highlight_preview_requires_owner_auth_and_generates_preview(tmp_path):
    teacher = _make_teacher("highlight_preview_owner")
    outsider = _make_teacher("highlight_preview_outsider")
    project = Project.objects.create(title="Highlight preview", user=teacher)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "original_background_path": f"{project.id}/images/slide-1.png",
            },
        },
    )
    source = tmp_path / str(project.id) / "images" / "slide-1.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(PNG_1X1)

    unauthorized = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/highlight-preview/",
        {"style": "box", "detector": "auto", "draft_only": False},
        format="json",
    )
    force_authenticate(unauthorized, user=outsider)
    with override_settings(STORAGE_ROOT=str(tmp_path), HIGHLIGHT_PREVIEW_ENABLED=True):
        unauthorized_response = views.TranscriptPageHighlightPreviewView.as_view()(
            unauthorized,
            project_id=project.id,
            page_id=page.id,
        )
    assert unauthorized_response.status_code == 403

    authorized = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/highlight-preview/",
        {"style": "box", "detector": "auto", "draft_only": False},
        format="json",
    )
    force_authenticate(authorized, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), HIGHLIGHT_PREVIEW_ENABLED=True):
        response = views.TranscriptPageHighlightPreviewView.as_view()(
            authorized,
            project_id=project.id,
            page_id=page.id,
        )

    assert response.status_code == 200
    assert response.data["preview_image_url"]
    assert response.data["detector_used"] == "auto"
    page.refresh_from_db()
    assert page.editor_document["scene"]["highlight_preview_path"]


@pytest.mark.django_db
def test_highlight_preview_invalid_style_and_flag_disable(tmp_path):
    teacher = _make_teacher("highlight_preview_invalid")
    project = Project.objects.create(title="Highlight invalid", user=teacher)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "original_background_path": f"{project.id}/images/slide-1.png",
            },
        },
    )
    source = tmp_path / str(project.id) / "images" / "slide-1.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(PNG_1X1)

    disabled_req = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/highlight-preview/",
        {"style": "box", "detector": "auto", "draft_only": False},
        format="json",
    )
    force_authenticate(disabled_req, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), HIGHLIGHT_PREVIEW_ENABLED=False):
        disabled_response = views.TranscriptPageHighlightPreviewView.as_view()(
            disabled_req,
            project_id=project.id,
            page_id=page.id,
        )
    assert disabled_response.status_code == 400

    invalid_req = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/highlight-preview/",
        {"style": "invalid", "detector": "auto", "draft_only": False},
        format="json",
    )
    force_authenticate(invalid_req, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), HIGHLIGHT_PREVIEW_ENABLED=True):
        invalid_response = views.TranscriptPageHighlightPreviewView.as_view()(
            invalid_req,
            project_id=project.id,
            page_id=page.id,
        )
    assert invalid_response.status_code == 400


@pytest.mark.django_db
def test_highlight_preview_style_switch_creates_fresh_artifact_and_updates_spec(tmp_path):
    teacher = _make_teacher("highlight_preview_switch")
    project = Project.objects.create(title="Highlight switch", user=teacher)
    page = _make_page(
        project,
        editor_document={
            "version": 1,
            "scene": {
                "background_mode": "original",
                "original_background_path": f"{project.id}/images/slide-1.png",
            },
        },
    )
    source = tmp_path / str(project.id) / "images" / "slide-1.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(PNG_1X1)

    box_req = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/highlight-preview/",
        {"style": "box", "detector": "auto", "draft_only": False},
        format="json",
    )
    force_authenticate(box_req, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), HIGHLIGHT_PREVIEW_ENABLED=True):
        box_response = views.TranscriptPageHighlightPreviewView.as_view()(box_req, project_id=project.id, page_id=page.id)
    assert box_response.status_code == 200
    assert box_response.data["engine_version"] == "highlight-v1"
    first_url = box_response.data["preview_image_url"]
    assert first_url
    page.refresh_from_db()
    first_path = page.editor_document.get("scene", {}).get("highlight_preview_path", "")

    bold_req = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/highlight-preview/",
        {"style": "bold", "detector": "auto", "draft_only": False},
        format="json",
    )
    force_authenticate(bold_req, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path), HIGHLIGHT_PREVIEW_ENABLED=True):
        bold_response = views.TranscriptPageHighlightPreviewView.as_view()(bold_req, project_id=project.id, page_id=page.id)
    assert bold_response.status_code == 200
    second_url = bold_response.data["preview_image_url"]
    assert second_url
    assert second_url == first_url

    page.refresh_from_db()
    scene = page.editor_document["scene"]
    assert scene["highlight_style"] == "bold"
    assert scene["highlight"]["style"] == "bold"
    assert scene["highlight"]["detector"] == "auto"
    assert scene["highlight"]["target"] == "block"
    assert scene["highlight"]["version"] == "v1"
    assert scene["highlight_preview_path"]
    assert scene["highlight_preview_path"] != first_path

    first_file = tmp_path / first_path
    second_file = tmp_path / scene["highlight_preview_path"]
    assert first_path
    assert str(second_file).endswith(".png")


@pytest.mark.django_db
def test_highlight_engine_box_and_bold_renderers(tmp_path):
    from worker.highlight_engine import apply_highlight

    source = tmp_path / "src.png"
    output_box = tmp_path / "out_box.png"
    output_bold = tmp_path / "out_bold.png"

    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (320, 180), color=(240, 240, 240)).save(source)

    box_result = apply_highlight(
        image_path=str(source),
        text="Preview this highlighted term",
        style="box",
        detector="auto",
        output_path=str(output_box),
        timeout_sec=5.0,
    )
    bold_result = apply_highlight(
        image_path=str(source),
        text="Preview this highlighted term",
        style="bold",
        detector="auto",
        output_path=str(output_bold),
        timeout_sec=5.0,
    )

    assert box_result["fallback_used"] is False
    assert bold_result["fallback_used"] is False
    assert "renderer_used" in box_result
    assert "renderer_used" in bold_result
    assert box_result["detector_used"] == "auto"
    assert bold_result["detector_used"] == "auto"
