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

from core.drafts import ensure_project_draft_data, mark_draft_moderation_failed, promote_project_draft, save_project_draft_data  # noqa: E402
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
    assert f"/api/v1/projects/{project.id}/cover/" in response.data["cover_url"]
    assert "cover_v=" in response.data["cover_url"]
    assert f"/api/v1/projects/{project.id}/cover/?draft=1" in response.data["draft_cover_url"]
    assert "cover_v=" in response.data["draft_cover_url"]


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
def test_safe_cover_upload_without_draft_flag_waits_for_intentional_promotion(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    def allow_scan(project_id, export_result, **kwargs):
        assert kwargs.get("use_draft") is True
        return {
            "enabled": True,
            "status": "done",
            "project_id": int(project_id),
            "final_decision": "allow",
            "finding_count": 0,
            "findings": [],
            "block_render": False,
            "message": "Visual moderation passed.",
        }

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", allow_scan)
    owner = _make_user("draft_cover_default_safe_owner")
    project = _make_project(owner, tmp_path)
    old_cover = project.cover_image_original
    old_bytes = (tmp_path / old_cover).read_bytes()
    client = _client(owner)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        upload_response = client.post(
            f"/api/v1/projects/{project.id}/cover/",
            {"cover_file": _upload("safe-cover.png", (60, 160, 70))},
            format="multipart",
        )
        project.refresh_from_db()
        cover_after_upload = project.cover_image_original
        draft_cover = project.draft_data["project"]["cover_image_original"]
        public_before = _client().get(f"/api/v1/projects/{project.id}/cover/")
        promote_response = client.post(f"/api/v1/projects/{project.id}/draft/promote/")

    project.refresh_from_db()
    assert upload_response.status_code == 200
    assert "draft=1" in upload_response.data["draft_cover_url"]
    assert "cover_v=" in upload_response.data["draft_cover_url"]
    assert cover_after_upload == old_cover
    assert project.cover_image_original == draft_cover
    assert project.cover_image_original != old_cover
    assert _body(public_before) == old_bytes
    assert promote_response.status_code == 200
    assert f"/api/v1/projects/{project.id}/cover/" in promote_response.data["cover_url"]
    assert "cover_v=" in promote_response.data["cover_url"]


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
def test_cover_only_save_changes_promotes_safe_cover_without_rerender(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    def allow_scan(project_id, export_result, **kwargs):
        assert kwargs.get("use_draft") is True
        return {
            "enabled": True,
            "status": "completed",
            "final_decision": "allow",
            "finding_count": 0,
            "findings": [],
        }

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", allow_scan)
    owner = _make_user("draft_cover_save_owner")
    project = _make_project(owner, tmp_path)
    old_cover = project.cover_image_original
    client = _client(owner)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(client, project, color=(60, 160, 70)).status_code == 200
        project.refresh_from_db()
        draft_cover = project.draft_data["project"]["cover_image_original"]
        response = client.post(f"/api/v1/projects/{project.id}/draft/promote/")

    project.refresh_from_db()
    assert response.status_code == 200
    assert response.data["rerender_strategy"] == "none"
    assert response.data["render_required"] is False
    assert "rerender" in response.data["message"].lower()
    assert project.cover_image_original == draft_cover
    assert project.cover_image_original != old_cover
    assert project.draft_data == {}
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_replacing_blocked_cover_clears_stale_draft_visual_block(tmp_path):
    owner = _make_user("draft_cover_replace_block_owner")
    project = _make_project(owner, tmp_path)
    client = _client(owner)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(client, project, color=(220, 20, 20)).status_code == 200
        project.refresh_from_db()
        mark_draft_moderation_failed(
            project,
            {
                "enabled": True,
                "final_decision": "needs_admin_review",
                "message": "Draft visual moderation requires admin review.",
                "finding_count": 1,
            },
        )
        response = _upload_draft_cover(client, project, color=(60, 160, 70))

    project.refresh_from_db()
    assert response.status_code == 200
    assert "moderation_status" not in project.draft_data["metadata"]
    assert "moderation" not in project.draft_data["metadata"]
    assert "draft_moderation" not in project.moderation_summary
    assert project.moderation_status == "not_scanned"
    assert response.data["draft_metadata"].get("cover_dirty") is True
    assert "moderation_status" not in response.data["draft_metadata"]


@pytest.mark.django_db
def test_blocked_cover_upload_response_reports_review_not_passed(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    def review_scan(project_id, export_result, **kwargs):
        assert kwargs.get("use_draft") is True
        return {
            "enabled": True,
            "status": "done",
            "project_id": int(project_id),
            "phase": "visual_asset_scan",
            "run_id": 456,
            "final_decision": "needs_admin_review",
            "finding_count": 1,
            "findings": [],
            "block_render": True,
            "message": "Visual moderation requires review.",
        }

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", review_scan)
    owner = _make_user("draft_cover_review_message_owner")
    project = _make_project(owner, tmp_path)
    old_cover = project.cover_image_original

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _upload_draft_cover(_client(owner), project, color=(220, 20, 20))

    project.refresh_from_db()
    assert response.status_code == 200
    assert "passed" not in response.data["message"].lower()
    assert "review" in response.data["message"].lower()
    assert response.data["draft_metadata"]["moderation_status"] == "needs_admin_review"
    assert project.cover_image_original == old_cover


@pytest.mark.django_db
def test_safe_cover_replacement_reruns_visual_moderation_and_marks_draft_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))

    def allow_scan(project_id, export_result, **kwargs):
        assert kwargs.get("use_draft") is True
        assert kwargs.get("scan_cover") is True
        return {
            "enabled": True,
            "status": "done",
            "project_id": int(project_id),
            "phase": "visual_asset_scan",
            "run_id": 123,
            "final_decision": "allow",
            "finding_count": 0,
            "findings": [],
            "block_render": False,
            "message": "Visual moderation passed.",
        }

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", allow_scan)
    owner = _make_user("draft_cover_safe_recovery_owner")
    project = _make_project(owner, tmp_path)
    client = _client(owner)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(client, project, color=(220, 20, 20)).status_code == 200
        project.refresh_from_db()
        mark_draft_moderation_failed(
            project,
            {
                "enabled": True,
                "phase": "visual_asset_scan",
                "final_decision": "needs_admin_review",
                "message": "Draft visual moderation requires admin review.",
                "finding_count": 1,
            },
        )
        response = _upload_draft_cover(client, project, color=(60, 160, 70))

    project.refresh_from_db()
    assert response.status_code == 200
    assert response.data["draft_metadata"]["moderation_status"] == "approved"
    assert response.data["draft_metadata"]["moderation"]["finding_count"] == 0
    assert "draft_moderation" not in project.moderation_summary
    assert "visual_asset_scan" not in project.moderation_summary
    assert project.draft_data["metadata"]["moderation_status"] == "approved"


@pytest.mark.django_db
def test_discard_blocked_cover_draft_clears_visual_warning_state(tmp_path):
    owner = _make_user("draft_cover_discard_block_owner")
    project = _make_project(owner, tmp_path)
    old_cover = project.cover_image_original
    client = _client(owner)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(client, project, color=(220, 20, 20)).status_code == 200
        project.refresh_from_db()
        mark_draft_moderation_failed(
            project,
            {
                "enabled": True,
                "final_decision": "needs_admin_review",
                "message": "Draft visual moderation requires admin review.",
                "finding_count": 1,
            },
        )
        response = client.post(f"/api/v1/projects/{project.id}/draft/discard/")

    project.refresh_from_db()
    assert response.status_code == 200
    assert project.cover_image_original == old_cover
    assert project.draft_data == {}
    assert "draft_moderation" not in project.moderation_summary
    assert "draft_visual_asset_scan" not in project.moderation_summary
    assert "visual_asset_scan" not in project.moderation_summary
    assert project.moderation_status not in {"revision_required", "needs_admin_review", "admin_rejected", "block", "blocked"}
    assert response.data["project"]["draft_cover_url"] == ""


@pytest.mark.django_db
def test_unsafe_visual_draft_blocks_promotion_and_keeps_draft_visible(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
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
        public_catalog = _client().get(f"/api/v1/catalog/{project.id}/")
        playback = _client().get(f"/api/v1/projects/{project.id}/playback-token/")

    project.refresh_from_db()
    page.refresh_from_db()
    assert result["block_render"] is True
    assert project.cover_image_original == old_cover
    assert page.editor_document["scene"] == old_scene
    assert project.is_published is False
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["metadata"]["moderation_status"] == "needs_admin_review"
    assert studio_project.status_code == 200
    assert "draft=1" in studio_project.data["draft_cover_url"]
    assert "cover_v=" in studio_project.data["draft_cover_url"]
    assert public_cover.status_code in {403, 404}
    assert public_catalog.status_code == 404
    assert playback.status_code in {403, 404}


@pytest.mark.django_db
def test_cover_only_draft_requires_visual_scan_before_promotion(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    owner = _make_user("draft_cover_scan_owner")
    project = _make_project(owner, tmp_path)
    old_cover = project.cover_image_original
    lesson_file = tmp_path / "uploads" / str(project.id) / "lesson.pptx"
    lesson_file.parent.mkdir(parents=True, exist_ok=True)
    lesson_file.write_bytes(b"placeholder")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(_client(owner), project, color=(200, 40, 60)).status_code == 200
        response = _client(owner).post(f"/api/v1/projects/{project.id}/rerender/")

    project.refresh_from_db()
    assert response.status_code == 400
    assert response.data["rerender_strategy"] == "blocked_by_visual_moderation"
    assert project.cover_image_original == old_cover
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["metadata"]["moderation_status"] == "needs_admin_review"
    assert project.moderation_summary["draft_moderation"]["final_decision"] == "needs_admin_review"


@pytest.mark.django_db
def test_cover_only_save_and_rerender_uses_draft_visual_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    owner = _make_user("draft_cover_transcript_rerender_owner")
    project = _make_project(owner, tmp_path)
    old_cover = project.cover_image_original
    client = _client(owner)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        assert _upload_draft_cover(client, project, color=(200, 40, 60)).status_code == 200
        response = client.patch(
            f"/api/v1/projects/{project.id}/transcript/",
            {"pages": [], "trigger_rerender": True, "draft_only": True},
            format="json",
        )

    project.refresh_from_db()
    assert response.status_code == 400
    assert response.data["rerender_strategy"] == "blocked_by_visual_moderation"
    assert "review" in response.data["message"].lower()
    assert project.cover_image_original == old_cover
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["metadata"]["moderation_status"] == "needs_admin_review"


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
