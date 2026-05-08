import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402
from core import views  # noqa: E402
from core.models import Category, Job, Project, UserProfile  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def _make_teacher(username_prefix="teacher"):
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"{username_prefix}_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


@pytest.mark.django_db
def test_project_upload_creates_new_category_and_attaches_to_project(tmp_path, monkeypatch):
    if not _table_has_column("core_project", "cover_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    factory = APIRequestFactory()
    teacher = _make_teacher("upload_category")

    lesson_file = SimpleUploadedFile(
        "lesson.txt",
        b"Sample lesson content",
        content_type="text/plain",
    )

    monkeypatch.setattr(views, "_resolve_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(send_task=lambda *_args, **_kwargs: SimpleNamespace(id="task-123")),
    )

    request = factory.post(
        "/api/v1/projects/",
        {
            "title": "Linear Algebra 101",
            "category": "Mathematics",
            "pause_sec": "0.2",
            "lang_hint": "en",
            "lesson_file": lesson_file,
        },
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 202

    created_category = Category.objects.get(name="Mathematics")
    project = Project.objects.latest("id")
    assert project.category_id == created_category.id


@pytest.mark.django_db
def test_project_upload_reuses_existing_category_case_insensitive(tmp_path, monkeypatch):
    if not _table_has_column("core_project", "cover_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    factory = APIRequestFactory()
    teacher = _make_teacher("upload_existing_category")
    category_name = f"Physics-{uuid.uuid4().hex[:6]}"
    existing = Category.objects.create(name=category_name)

    lesson_file = SimpleUploadedFile(
        "lesson.docx",
        b"PK\x03\x04fake",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    monkeypatch.setattr(views, "_resolve_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(send_task=lambda *_args, **_kwargs: SimpleNamespace(id="task-456")),
    )

    request = factory.post(
        "/api/v1/projects/",
        {
            "title": "Kinematics",
            "category": category_name.lower(),
            "pause_sec": "0.2",
            "lang_hint": "auto",
            "lesson_file": lesson_file,
        },
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 202

    project = Project.objects.latest("id")
    assert project.category_id == existing.id
    assert Category.objects.filter(name__iexact=category_name).count() == 1


@pytest.mark.django_db
def test_project_upload_dispatches_avatar_options_with_composite_fallback_flag(tmp_path, monkeypatch):
    if not _table_has_column("core_project", "cover_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    if not _table_has_column("core_userprofile", "avatar_lipsync_engine"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    factory = APIRequestFactory()
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_upload_{suffix}", password="pass")
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
    profile.role = "teacher"
    profile.avatar_enabled = True
    profile.avatar_consent_confirmed = True
    profile.avatar_image_processed = f"avatars/{user.id}/hash/processed.png"
    profile.avatar_lipsync_engine = "liveportrait+musetalk"
    profile.save(
        update_fields=[
            "role",
            "avatar_enabled",
            "avatar_consent_confirmed",
            "avatar_image_processed",
            "avatar_lipsync_engine",
        ]
    )

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_LESSON", "1")
    monkeypatch.setenv("AVATAR_ENABLE_COMPOSITE_FALLBACK", "1")

    sent = {}

    def fake_send_task(name, kwargs=None, args=None):
        sent["name"] = name
        sent["kwargs"] = kwargs or {}
        sent["args"] = args or []
        return SimpleNamespace(id="task-avatar-options-1")

    lesson_file = SimpleUploadedFile(
        "lesson.txt",
        b"Composite avatar payload",
        content_type="text/plain",
    )

    monkeypatch.setattr(views, "_resolve_user", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice-abc")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(send_task=fake_send_task),
    )

    request = factory.post(
        "/api/v1/projects/",
        {
            "title": "Composite dispatch",
            "pause_sec": "0.2",
            "lang_hint": "en",
            "avatar_enabled": "1",
            "lesson_file": lesson_file,
        },
        format="multipart",
    )
    force_authenticate(request, user=user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 202
    assert sent.get("name") == "worker.tasks.process_pptx_to_video"
    assert len(sent.get("args") or []) >= 8

    avatar_options = sent["args"][7]
    assert "composite_fallback_allowed" in avatar_options
    assert isinstance(avatar_options["composite_fallback_allowed"], bool)
    assert avatar_options["composite_fallback_allowed"] is True


@pytest.mark.django_db
def test_project_upload_persists_cover_image_paths(tmp_path, monkeypatch):
    if not _table_has_column("core_project", "cover_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    factory = APIRequestFactory()
    teacher = _make_teacher("upload_cover")

    lesson_file = SimpleUploadedFile(
        "lesson.txt",
        b"Cover upload payload",
        content_type="text/plain",
    )
    cover_file = SimpleUploadedFile(
        "cover.png",
        b"fake-png-bytes",
        content_type="image/png",
    )

    monkeypatch.setattr(views, "_resolve_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(send_task=lambda *_args, **_kwargs: SimpleNamespace(id="task-cover-123")),
    )

    request = factory.post(
        "/api/v1/projects/",
        {
            "title": "Cover enabled upload",
            "pause_sec": "0.2",
            "lang_hint": "en",
            "lesson_file": lesson_file,
            "cover_file": cover_file,
        },
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 202

    project = Project.objects.latest("id")
    assert project.cover_image_original
    assert project.cover_image_processed
    assert project.cover_image_processed == project.cover_image_original
    assert project.cover_image_original.startswith(f"uploads/{project.id}/cover")
    assert (tmp_path / project.cover_image_original).exists()


@pytest.mark.django_db
def test_catalog_exposes_cover_url_and_project_cover_endpoint_streams_file(tmp_path):
    if not _table_has_column("core_project", "cover_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    factory = APIRequestFactory()
    cover_rel_path = f"uploads/cover-test-{uuid.uuid4().hex[:6]}/cover.png"
    project = Project.objects.create(
        title="Public cover lesson",
        cover_image_original=cover_rel_path,
        cover_image_processed=cover_rel_path,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=project, job_type="video_export", status="done")

    cover_path = tmp_path / cover_rel_path
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(b"fake-cover-bytes")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        catalog_request = factory.get("/api/v1/catalog/")
        catalog_response = views.CatalogListView.as_view()(catalog_request)

        assert catalog_response.status_code == 200
        lesson = next(item for item in list(catalog_response.data) if item["id"] == project.id)
        assert lesson["cover_url"].endswith(f"/api/v1/projects/{project.id}/cover/")

        cover_request = factory.get(f"/api/v1/projects/{project.id}/cover/")
        cover_response = views.ProjectCoverImageView.as_view()(cover_request, project_id=project.id)

    assert cover_response.status_code == 200
    assert cover_response["Cache-Control"] == "public, max-age=300"
    assert str(cover_response["Content-Type"]).startswith("image/")
