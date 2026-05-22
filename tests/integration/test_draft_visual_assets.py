# pyright: reportMissingImports=false

from io import BytesIO
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

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.drafts import ensure_project_draft_data, promote_project_draft, save_project_draft_data  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _png_bytes(color=(24, 80, 140)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(name: str, color=(24, 80, 140)) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, _png_bytes(color), content_type="image/png")


def _body(response) -> bytes:
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content)
    return response.content


def _make_user(username: str, *, role: str = "publisher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(owner: User, tmp_path: Path, *, title: str = "Draft visual lesson") -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    cover_path = tmp_path / "uploads" / str(project.id) / "active-cover.png"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(_png_bytes((10, 20, 30)))
    project.cover_image_original = f"uploads/{project.id}/active-cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/old.mp4")
    return project


def _make_page(project: Project, *, background_path: str = "") -> TranscriptPage:
    scene = {"background_mode": "original", "background_fit": "contain", "text_scale": 1.0}
    if background_path:
        scene["custom_background_path"] = background_path
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Public text",
        narration_text="Public text",
        rich_text_html="Public text",
        subtitle_chunks=["Public text"],
        editor_document={"version": 1, "scene": scene, "paragraphs": [{"index": 0, "text": "Public text"}]},
    )


def _upload_draft_cover(client: APIClient, project: Project, *, color=(90, 30, 20)):
    return client.post(
        f"/api/v1/projects/{project.id}/cover/",
        {"cover_file": _upload("draft-cover.png", color), "draft_only": "true"},
        format="multipart",
    )


def _upload_draft_background(client: APIClient, project: Project, page: TranscriptPage, *, color=(20, 90, 30)):
    return client.post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/",
        {"background_file": _upload("draft-background.png", color), "draft_only": "true"},
        format="multipart",
    )


@pytest.mark.django_db
def test_studio_cover_upload_creates_draft_without_changing_active_cover(tmp_path):
    owner = _make_user("draft_cover_owner")
    project = _make_project(owner, tmp_path)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _upload_draft_cover(_client(owner), project)

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.cover_image_original == f"uploads/{project.id}/active-cover.png"
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["metadata"]["cover_dirty"] is True
    assert project.draft_data["project"]["cover_image_original"].startswith(f"uploads/{project.id}/cover_")
    assert response.data["cover_url"].endswith(f"/api/v1/projects/{project.id}/cover/")
    assert response.data["draft_cover_url"].endswith(f"/api/v1/projects/{project.id}/cover/?draft=1")


@pytest.mark.django_db
def test_public_cover_serving_keeps_active_cover_before_promotion(tmp_path):
    owner = _make_user("draft_cover_public_owner")
    project = _make_project(owner, tmp_path)
    old_bytes = (tmp_path / project.cover_image_original).read_bytes()

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        upload_response = _upload_draft_cover(_client(owner), project, color=(200, 40, 60))
        public_response = _client().get(f"/api/v1/projects/{project.id}/cover/")
        draft_response = _client(owner).get(f"/api/v1/projects/{project.id}/cover/?draft=1")

    assert upload_response.status_code == 200
    assert public_response.status_code == 200
    assert draft_response.status_code == 200
    assert _body(public_response) == old_bytes
    assert _body(draft_response) != old_bytes


@pytest.mark.django_db
def test_public_catalog_does_not_expose_draft_cover_or_background(tmp_path):
    owner = _make_user("draft_visual_catalog_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(_client(owner), project).status_code == 200
        assert _upload_draft_background(_client(owner), project, page).status_code == 200
        response = _client().get(f"/api/v1/catalog/{project.id}/")

    payload = json.dumps(response.data)
    assert response.status_code == 200
    assert "draft_cover_url" not in payload
    assert "draft_background_dirty" not in payload
    assert "draft=1" not in payload
    assert "custom_background_path" not in payload


@pytest.mark.django_db
def test_discard_draft_removes_draft_cover_and_keeps_active_cover(tmp_path):
    owner = _make_user("draft_cover_discard_owner")
    project = _make_project(owner, tmp_path)
    active_cover = project.cover_image_original

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(_client(owner), project).status_code == 200
        response = _client(owner).post(f"/api/v1/projects/{project.id}/draft/discard/")

    project.refresh_from_db()
    assert response.status_code == 200
    assert project.draft_data == {}
    assert project.cover_image_original == active_cover
    assert response.data["project"]["draft_cover_url"] == ""


@pytest.mark.django_db
def test_background_upload_creates_draft_scene_without_changing_active_page(tmp_path):
    owner = _make_user("draft_background_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _upload_draft_background(_client(owner), project, page)

    assert response.status_code == 200
    page.refresh_from_db()
    project.refresh_from_db()
    assert "custom_background_path" not in page.editor_document["scene"]
    draft_scene = project.draft_data["transcript_pages"][0]["editor_document"]["scene"]
    assert draft_scene["background_mode"] == "custom"
    assert draft_scene["custom_background_path"].startswith(f"uploads/{project.id}/backgrounds/page_{page.id}_")
    response_scene = response.data["page"]["editor_document"]["scene"]
    assert response_scene["custom_background_url"].endswith("draft=1")
    assert response.data["page"]["draft_background_dirty"] is True


@pytest.mark.django_db
def test_discard_draft_returns_studio_to_active_background(tmp_path):
    owner = _make_user("draft_background_discard_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_background(_client(owner), project, page).status_code == 200
        discard_response = _client(owner).post(f"/api/v1/projects/{project.id}/draft/discard/")
        studio_response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")

    assert discard_response.status_code == 200
    assert studio_response.status_code == 200
    scene = studio_response.data["pages"][0]["editor_document"]["scene"]
    assert scene["background_mode"] == "original"
    assert scene["custom_background_url"] == ""
    assert "draft_background_dirty" not in studio_response.data["pages"][0] or studio_response.data["pages"][0]["draft_background_dirty"] is False


@pytest.mark.django_db
def test_safe_draft_promotion_updates_cover_and_background_and_clears_draft(tmp_path):
    owner = _make_user("draft_visual_promote_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(_client(owner), project, color=(60, 160, 70)).status_code == 200
        assert _upload_draft_background(_client(owner), project, page, color=(70, 60, 160)).status_code == 200

    project.refresh_from_db()
    draft_cover = project.draft_data["project"]["cover_image_original"]
    draft_background = project.draft_data["transcript_pages"][0]["editor_document"]["scene"]["custom_background_path"]

    result = promote_project_draft(project)

    project.refresh_from_db()
    page.refresh_from_db()
    assert result["status"] == "promoted"
    assert project.draft_data == {}
    assert project.cover_image_original == draft_cover
    assert project.cover_image_processed == draft_cover
    assert page.editor_document["scene"]["background_mode"] == "custom"
    assert page.editor_document["scene"]["custom_background_path"] == draft_background


@pytest.mark.django_db
def test_unsafe_visual_draft_blocks_promotion_and_keeps_draft_visible(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    owner = _make_user("draft_visual_block_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)
    old_cover = project.cover_image_original
    old_scene = dict(page.editor_document["scene"])
    corrupt_rel = f"uploads/{project.id}/blocked-cover.png"
    corrupt_path = tmp_path / corrupt_rel
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_bytes(b"not an image")

    draft_data = ensure_project_draft_data(project)
    draft_data["project"]["cover_image_original"] = corrupt_rel
    draft_data["project"]["cover_image_processed"] = corrupt_rel
    draft_data["metadata"]["cover_dirty"] = True
    save_project_draft_data(project, draft_data, dirty=True)
    Job.objects.create(project=project, job_type="video_export", status="running", progress=10)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [], use_draft=True)
        worker_tasks._mark_draft_render_blocked(project.id, result)
        studio_project = _client(owner).get(f"/api/v1/projects/{project.id}/")
        public_cover = _client().get(f"/api/v1/projects/{project.id}/cover/")

    project.refresh_from_db()
    page.refresh_from_db()
    assert result["block_render"] is True
    assert project.cover_image_original == old_cover
    assert page.editor_document["scene"] == old_scene
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["metadata"]["moderation_status"] == "needs_admin_review"
    assert studio_project.status_code == 200
    assert studio_project.data["draft_cover_url"].endswith("?draft=1")
    assert public_cover.status_code == 404


@pytest.mark.django_db
def test_apply_all_background_updates_draft_pages_only(tmp_path):
    owner = _make_user("draft_background_apply_all_owner")
    project = _make_project(owner, tmp_path)
    source = _make_page(project)
    target = TranscriptPage.objects.create(
        project=project,
        order=1,
        source_slide_index=1,
        split_index=0,
        page_key="s2-p1",
        original_text="Second",
        narration_text="Second",
        rich_text_html="Second",
        subtitle_chunks=["Second"],
        editor_document={"version": 1, "scene": {"background_mode": "original"}},
    )

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_background(_client(owner), project, source).status_code == 200
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/background/apply-all/",
            {
                "source_page_id": source.id,
                "background_mode": "custom",
                "background_fit": "cover",
                "text_scale": 1.25,
                "draft_only": True,
            },
            format="json",
        )

    assert response.status_code == 200
    target.refresh_from_db()
    project.refresh_from_db()
    assert target.editor_document["scene"]["background_mode"] == "original"
    draft_target = next(page for page in project.draft_data["transcript_pages"] if page["id"] == target.id)
    assert draft_target["editor_document"]["scene"]["background_mode"] == "custom"
    assert draft_target["editor_document"]["scene"]["background_fit"] == "cover"
    assert draft_target["editor_document"]["scene"]["text_scale"] == 1.25
