# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import override_settings

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
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile  # noqa: E402


def _ensure_transcript_table() -> None:
    table_name = TranscriptPage._meta.db_table
    if table_name in connection.introspection.table_names():
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, table_name)
            }
        missing_fields = [
            field_name
            for field_name in ["is_active", "deleted_at"]
            if TranscriptPage._meta.get_field(field_name).column not in columns
        ]
        if missing_fields:
            with connection.schema_editor() as schema_editor:
                for field_name in missing_fields:
                    schema_editor.add_field(TranscriptPage, TranscriptPage._meta.get_field(field_name))
        return
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(TranscriptPage)


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _prepare_lesson_upload(tmp_path, project: Project) -> Path:
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    lesson_path = upload_dir / "lesson.txt"
    lesson_path.write_text("lesson", encoding="utf-8")
    return lesson_path


class _CapturedCelery:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def signature(self, name, args=None, kwargs=None):
        captured = self

        class _Signature:
            def apply_async(self, **options):
                captured.sent.append(
                    {
                        "name": name,
                        "args": list(args or []),
                        "kwargs": dict(kwargs or {}),
                        "options": dict(options),
                    }
                )
                return SimpleNamespace(id=f"task-queue-routing-{len(captured.sent)}")

        return _Signature()


def _capture_apply_async(monkeypatch) -> _CapturedCelery:
    captured = _CapturedCelery()
    monkeypatch.setattr(views, "_celery_app", captured)
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice-route")
    return captured


def _force_avatar_options(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(
        views,
        "_resolve_avatar_options_for_project",
        lambda project, request: {"enabled": enabled, "teacher_id": int(project.user_id or 0)},
    )


@pytest.mark.django_db
def test_non_avatar_upload_enqueues_render_queue(tmp_path, monkeypatch):
    teacher = _make_teacher("queue_upload_render_teacher")
    captured = _capture_apply_async(monkeypatch)
    _force_avatar_options(monkeypatch, False)
    lesson_file = SimpleUploadedFile("lesson.txt", b"Sample lesson content", content_type="text/plain")

    request = APIRequestFactory().post(
        "/api/v1/projects/",
        {
            "title": "Render queue upload",
            "pause_sec": "0.2",
            "lesson_file": lesson_file,
        },
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_RENDER_QUEUE="render", CELERY_AVATAR_QUEUE="avatar"):
        response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 202
    assert captured.sent[0]["name"] == "worker.tasks.process_pptx_to_video"
    assert captured.sent[0]["options"]["queue"] == "render"
    assert captured.sent[0]["args"][7]["enabled"] is False


@pytest.mark.django_db
def test_avatar_enabled_project_rerender_enqueues_avatar_queue(tmp_path, monkeypatch):
    teacher = _make_teacher("queue_rerender_avatar_teacher")
    project = Project.objects.create(title="Avatar queue rerender", user=teacher, avatar_enabled_override=True)
    _prepare_lesson_upload(tmp_path, project)
    captured = _capture_apply_async(monkeypatch)
    _force_avatar_options(monkeypatch, True)

    request = APIRequestFactory().post(f"/api/v1/projects/{project.id}/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_RENDER_QUEUE="render", CELERY_AVATAR_QUEUE="avatar"):
        response = views.ProjectRerenderView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    assert captured.sent[0]["name"] == "worker.tasks.process_pptx_to_video"
    assert captured.sent[0]["options"]["queue"] == "avatar"
    assert captured.sent[0]["args"][7]["enabled"] is True


@pytest.mark.django_db
def test_transcript_rerender_enqueues_render_queue_when_avatar_disabled(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher = _make_teacher("queue_transcript_render_teacher")
    project = Project.objects.create(title="Transcript render queue", user=teacher)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original",
        narration_text="Original",
    )
    _prepare_lesson_upload(tmp_path, project)
    captured = _capture_apply_async(monkeypatch)
    _force_avatar_options(monkeypatch, False)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "trigger_rerender": True,
            "pages": [{"id": page.id, "narration_text": "Edited"}],
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_RENDER_QUEUE="render", CELERY_AVATAR_QUEUE="avatar"):
        response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert captured.sent[0]["options"]["queue"] == "render"
    assert captured.sent[0]["args"][8] == ["s1-p1"]


@pytest.mark.django_db
@pytest.mark.parametrize("avatar_enabled,expected_queue", [(False, "render"), (True, "avatar")])
def test_structural_transcript_rerender_uses_queue_for_avatar_state(
    avatar_enabled,
    expected_queue,
    tmp_path,
    monkeypatch,
):
    _ensure_transcript_table()
    teacher = _make_teacher(f"queue_structural_{expected_queue}_teacher")
    project = Project.objects.create(title=f"Structural {expected_queue} queue", user=teacher)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original",
        narration_text="Original",
    )
    _prepare_lesson_upload(tmp_path, project)
    captured = _capture_apply_async(monkeypatch)
    _force_avatar_options(monkeypatch, avatar_enabled)

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "action": "split_page",
            "page_id": page.id,
            "parts": [{"narration_text": "First"}, {"narration_text": "Second"}],
            "trigger_rerender": True,
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_RENDER_QUEUE="render", CELERY_AVATAR_QUEUE="avatar"):
        response = views.ProjectTranscriptActionView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["rerender_strategy"] == "full"
    assert captured.sent[0]["options"]["queue"] == expected_queue
    assert captured.sent[0]["args"][8] == []
