# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
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
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402


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


def _make_project(username: str) -> tuple[User, Project]:
    teacher = _make_teacher(username)
    project = Project.objects.create(title=f"Active dedupe {username}", user=teacher)
    return teacher, project


def _prepare_lesson_upload(tmp_path, project: Project) -> None:
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")


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
                return SimpleNamespace(id=f"task-active-dedupe-{len(captured.sent)}")

        return _Signature()


def _capture_dispatch(monkeypatch) -> _CapturedCelery:
    captured = _CapturedCelery()
    monkeypatch.setattr(views, "_celery_app", captured)
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice-dedupe")
    monkeypatch.setattr(
        views,
        "_resolve_avatar_options_for_project",
        lambda project, request: {"enabled": False, "teacher_id": int(project.user_id or 0)},
    )
    return captured


def _post_rerender(teacher, project):
    request = APIRequestFactory().post(f"/api/v1/projects/{project.id}/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)
    return views.ProjectRerenderView.as_view()(request, project_id=project.id)


@pytest.mark.django_db
@pytest.mark.parametrize("job_status", ["pending", "running"])
def test_project_rerender_returns_existing_active_job_without_creating_duplicate(
    job_status,
    tmp_path,
    monkeypatch,
):
    teacher, project = _make_project(f"rerender_existing_{job_status}")
    _prepare_lesson_upload(tmp_path, project)
    existing = Job.objects.create(project=project, job_type="video_export", status=job_status, progress=20)
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_rerender(teacher, project)

    assert response.status_code == 202
    assert response.data["id"] == existing.id
    assert response.data["status"] == job_status
    assert response.data["deduped"] is True
    assert response.data["existing_job"] is True
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []


@pytest.mark.django_db
def test_project_rerender_creates_new_job_when_no_active_job_exists(tmp_path, monkeypatch):
    teacher, project = _make_project("rerender_no_active")
    _prepare_lesson_upload(tmp_path, project)
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_rerender(teacher, project)

    assert response.status_code == 202
    assert response.data["status"] == "pending"
    assert "deduped" not in response.data
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent[0]["name"] == "worker.tasks.process_pptx_to_video"


@pytest.mark.django_db
def test_transcript_triggered_rerender_reuses_existing_active_job(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_existing_active")
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
    existing = Job.objects.create(project=project, job_type="video_export", status="running", progress=40)
    captured = _capture_dispatch(monkeypatch)

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"trigger_rerender": True, "pages": [{"id": page.id, "narration_text": "Edited"}]},
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["rerender_job"]["id"] == existing.id
    assert response.data["rerender_job"]["deduped"] is True
    assert response.data["rerender_job"]["existing_job"] is True
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []


@pytest.mark.django_db
@pytest.mark.parametrize("old_status", ["done", "failed"])
def test_project_rerender_ignores_terminal_old_jobs(tmp_path, monkeypatch, old_status):
    teacher, project = _make_project(f"rerender_terminal_{old_status}")
    _prepare_lesson_upload(tmp_path, project)
    Job.objects.create(project=project, job_type="video_export", status=old_status, progress=100)
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_rerender(teacher, project)

    assert response.status_code == 202
    assert response.data["status"] == "pending"
    assert "deduped" not in response.data
    assert Job.objects.filter(project=project, job_type="video_export").count() == 2
    assert len(captured.sent) == 1


@pytest.mark.django_db
def test_two_fast_rerender_calls_reuse_first_pending_job(tmp_path, monkeypatch):
    teacher, project = _make_project("rerender_two_fast_calls")
    _prepare_lesson_upload(tmp_path, project)
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        first = _post_rerender(teacher, project)
        second = _post_rerender(teacher, project)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["id"] == second.data["id"]
    assert second.data["deduped"] is True
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert len(captured.sent) == 1
