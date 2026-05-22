# pyright: reportMissingImports=false

from io import BytesIO
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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

from core import views as core_views  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402


def _png_bytes() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color=(40, 90, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_png(name: str = "cover.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, _png_bytes(), content_type="image/png")


def _make_user(username: str, *, role: str = "publisher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_project(owner: User, tmp_path: Path) -> Project:
    project = Project.objects.create(
        title=f"{owner.username} partial lesson",
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    cover = tmp_path / "uploads" / str(project.id) / "active.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(_png_bytes())
    project.cover_image_original = f"uploads/{project.id}/active.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/old.mp4")
    return project


def _make_page(project: Project) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="slide-1",
        original_text="Original text",
        narration_text="Original text",
        rich_text_html="Original text",
        subtitle_chunks=["Original text"],
        editor_document={"version": 1, "paragraphs": [{"text": "Original text"}]},
    )


@pytest.mark.django_db
def test_cover_only_update_does_not_create_full_render_job(tmp_path):
    owner = _make_user("partial_cover_owner")
    project = _make_project(owner, tmp_path)
    before = Job.objects.filter(project=project, job_type="video_export").count()

    with override_settings(STORAGE_ROOT=str(tmp_path), VISUAL_MODERATION_AUTO_ENABLED=False):
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/cover/",
            {"cover_file": _upload_png()},
            format="multipart",
        )

    assert response.status_code == 200
    assert response.data["render_required"] is False
    assert Job.objects.filter(project=project, job_type="video_export").count() == before


@pytest.mark.django_db
def test_cover_only_update_creates_cover_moderation_scan_marker(tmp_path):
    owner = _make_user("partial_cover_marker")
    project = _make_project(owner, tmp_path)

    with override_settings(STORAGE_ROOT=str(tmp_path), VISUAL_MODERATION_AUTO_ENABLED=False):
        response = _client(owner).post(
            f"/api/v1/projects/{project.id}/cover/",
            {"cover_file": _upload_png("new-cover.png")},
            format="multipart",
        )

    project.refresh_from_db()
    marker = project.moderation_summary["visual_asset_scan"]
    assert response.status_code == 200
    assert marker["asset_type"] == "cover"
    assert marker["status"] == "needs_rescan"
    assert marker["cover_hash"]


@pytest.mark.django_db
def test_transcript_page_edit_marks_changed_page_for_text_moderation(tmp_path):
    owner = _make_user("partial_text_owner")
    project = _make_project(owner, tmp_path)
    page = _make_page(project)

    response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"pages": [{"id": page.id, "original_text": "Changed text"}]},
        format="json",
    )

    project.refresh_from_db()
    changed = project.moderation_summary["editor_text_changed"]
    assert response.status_code == 200
    assert changed["changed_page_keys"] == ["slide-1"]
    assert changed["content_hash"]


@pytest.mark.django_db
def test_source_upload_triggers_full_pipeline(monkeypatch, tmp_path):
    owner = _make_user("partial_source_owner")
    sent = {}

    def fake_dispatch(task_name, *, args, kwargs=None, queue=None):
        sent["task_name"] = task_name
        sent["args"] = args
        sent["queue"] = queue
        return SimpleNamespace(id="render-task")

    monkeypatch.setattr(core_views, "_dispatch_celery_task", fake_dispatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(owner).post(
            "/api/v1/projects/",
            {"lesson_file": SimpleUploadedFile("lesson.txt", b"hello", content_type="text/plain")},
            format="multipart",
        )

    assert response.status_code == 202
    assert sent["task_name"] == core_views._PROCESS_PROJECT_RENDER_TASK
    assert sent["args"][8] is None
    assert Job.objects.filter(status="pending", job_type="video_export").exists()


@pytest.mark.django_db
def test_metadata_only_edit_does_not_render_video(tmp_path):
    owner = _make_user("partial_metadata_owner")
    project = _make_project(owner, tmp_path)
    before = Job.objects.filter(project=project, job_type="video_export").count()

    response = _client(owner).patch(
        f"/api/v1/projects/{project.id}/",
        {"category_name": "Metadata only"},
        format="json",
    )

    assert response.status_code == 200
    assert Job.objects.filter(project=project, job_type="video_export").count() == before
