# pyright: reportMissingImports=false

import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.db import connection
from django.test.utils import override_settings
from django.utils import timezone

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
from core.models import Job, Project, RenderFollowUpIntent, TranscriptPage, UserProfile  # noqa: E402
from core.render_recovery import build_render_recovery_report  # noqa: E402


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


class _FailingCelery:
    def signature(self, *_args, **_kwargs):
        class _Signature:
            def apply_async(self, **_options):
                raise RuntimeError("broker unavailable")

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


def _patch_transcript_page(teacher, project, page, text: str):
    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"trigger_rerender": True, "pages": [{"id": page.id, "narration_text": text}]},
        format="json",
    )
    force_authenticate(request, user=teacher)
    return views.ProjectTranscriptView.as_view()(request, project_id=project.id)


def _age_job(job: Job, *, hours: int = 4) -> Job:
    old = timezone.now() - timedelta(hours=hours)
    Job.objects.filter(pk=job.pk).update(created_at=old, updated_at=old)
    job.refresh_from_db()
    return job


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
    assert response.data["avatar_processing_status"] == "none"
    assert response.data["avatar_processing_message"] == ""
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []


@pytest.mark.django_db
def test_pending_video_export_without_task_id_still_dedupes_active_render(tmp_path, monkeypatch):
    teacher, project = _make_project("rerender_pending_no_task_active")
    _prepare_lesson_upload(tmp_path, project)
    existing = Job.objects.create(project=project, job_type="video_export", status="pending", progress=0, celery_task_id="")
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_rerender(teacher, project)

    existing.refresh_from_db()
    assert response.status_code == 202
    assert response.data["id"] == existing.id
    assert response.data["status"] == "pending"
    assert response.data["deduped"] is True
    assert response.data["existing_job"] is True
    assert existing.celery_task_id == ""
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
    assert response.data["avatar_processing_status"] == "none"
    assert response.data["avatar_processing_message"] == ""
    assert "deduped" not in response.data
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent[0]["name"] == "worker.tasks.process_pptx_to_video"
    assert captured.sent[0]["kwargs"]["job_id"] == response.data["id"]


@pytest.mark.django_db
def test_project_rerender_dispatch_failure_leaves_reportable_pending_job(tmp_path, monkeypatch):
    teacher, project = _make_project("rerender_dispatch_failure_reportable")
    _prepare_lesson_upload(tmp_path, project)
    monkeypatch.setattr(views, "_celery_app", _FailingCelery())
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice-dedupe")
    monkeypatch.setattr(
        views,
        "_resolve_avatar_options_for_project",
        lambda project, request: {"enabled": False, "teacher_id": int(project.user_id or 0)},
    )

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        with pytest.raises(RuntimeError, match="broker unavailable"):
            _post_rerender(teacher, project)

    job = _age_job(Job.objects.get(project=project, job_type="video_export"), hours=4)
    assert job.status == "pending"
    assert job.celery_task_id == ""

    report = build_render_recovery_report(dry_run=True, max_age_hours=2)
    matches = [
        finding
        for finding in report.findings
        if finding.category == "orphan_recovery_candidate"
        and finding.object_type == "Job"
        and finding.object_id == job.id
    ]
    assert matches
    assert "pending_without_task_id" in matches[0].detail
    assert "dispatch_window_candidate" in matches[0].detail

    job.refresh_from_db()
    assert job.status == "pending"
    assert job.celery_task_id == ""
    assert job.error_message == ""


@pytest.mark.django_db
def test_transcript_triggered_rerender_records_targeted_followup_with_existing_active_job(tmp_path, monkeypatch):
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
    assert response.data["rerender_job"] is None
    assert response.data["rerender_strategy"] == "follow_up"
    assert response.data["follow_up_render_intent"] is True
    assert response.data["queued_follow_up"] is True
    assert response.data["deduped"] is True
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert Job.objects.get(project=project, job_type="video_export").id == existing.id
    assert captured.sent == []

    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1"]
    assert intent.reason == "transcript_text_edit"


@pytest.mark.django_db
def test_transcript_followup_targeted_intent_unions_second_text_edit(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_followup_union")
    first = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="First",
        narration_text="First",
    )
    second = TranscriptPage.objects.create(
        project=project,
        order=1,
        source_slide_index=1,
        split_index=0,
        page_key="s2-p1",
        original_text="Second",
        narration_text="Second",
    )
    _prepare_lesson_upload(tmp_path, project)
    Job.objects.create(project=project, job_type="video_export", status="running", progress=40)
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        for page, text in [(first, "First edit"), (second, "Second edit")]:
            request = APIRequestFactory().patch(
                f"/api/v1/projects/{project.id}/transcript/",
                {"trigger_rerender": True, "pages": [{"id": page.id, "narration_text": text}]},
                format="json",
            )
            force_authenticate(request, user=teacher)
            response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)
            assert response.status_code == 200
            assert response.data["deduped"] is True

    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1", "s2-p1"]
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []


@pytest.mark.django_db
def test_transcript_structural_action_records_full_followup_with_existing_active_job(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_structural_followup")
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Alpha beta",
        narration_text="Alpha beta",
    )
    _prepare_lesson_upload(tmp_path, project)
    Job.objects.create(project=project, job_type="video_export", status="running", progress=40)
    captured = _capture_dispatch(monkeypatch)

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        {
            "action": "split_page",
            "page_id": page.id,
            "parts": [
                {"narration_text": "Alpha"},
                {"narration_text": "beta"},
            ],
            "trigger_rerender": True,
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectTranscriptActionView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["rerender_job"] is None
    assert response.data["rerender_strategy"] == "follow_up"
    assert response.data["follow_up_render_intent"] is True
    assert response.data["deduped"] is True

    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.mode == RenderFollowUpIntent.MODE_FULL
    assert intent.page_keys == []
    assert intent.reason == "transcript_structural_action"
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []


@pytest.mark.django_db
def test_transcript_text_edit_keeps_existing_full_followup_intent(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_followup_full_stays")
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
    Job.objects.create(project=project, job_type="video_export", status="running", progress=40)
    RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_FULL,
        page_keys=[],
        reason="transcript_structural_action",
    )
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
    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.mode == RenderFollowUpIntent.MODE_FULL
    assert intent.page_keys == []
    assert intent.reason == "transcript_text_edit"
    assert response.data["render_follow_up_intent_id"] == intent.id
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []


@pytest.mark.django_db
def test_transcript_triggered_rerender_creates_new_job_when_no_active_job_exists(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_no_active")
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
    assert response.data["rerender_job"]["status"] == "pending"
    assert "deduped" not in response.data
    assert "follow_up_render_intent" not in response.data
    assert RenderFollowUpIntent.objects.filter(project=project).count() == 0
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert len(captured.sent) == 1


@pytest.mark.django_db
def test_repeated_transcript_triggered_rerender_reuses_pending_job_and_records_followup(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_repeated_pending")
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
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        first = _patch_transcript_page(teacher, project, page, "First edit")
        second = _patch_transcript_page(teacher, project, page, "Second edit")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["rerender_job"]["status"] == "pending"
    assert second.data["rerender_job"] is None
    assert second.data["rerender_strategy"] == "follow_up"
    assert second.data["deduped"] is True
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert len(captured.sent) == 1

    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1"]
    assert intent.metadata["active_job_id"] == first.data["rerender_job"]["id"]


@pytest.mark.django_db
def test_transcript_pending_video_export_without_task_id_records_followup_not_duplicate(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_pending_no_task_active")
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
    existing = Job.objects.create(
        project=project,
        job_type="video_export",
        status="pending",
        progress=0,
        celery_task_id="",
    )
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _patch_transcript_page(teacher, project, page, "Edited")

    existing.refresh_from_db()
    assert response.status_code == 200
    assert response.data["rerender_job"] is None
    assert response.data["rerender_strategy"] == "follow_up"
    assert response.data["deduped"] is True
    assert existing.celery_task_id == ""
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert captured.sent == []

    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert intent.metadata["active_job_id"] == existing.id


@pytest.mark.django_db
def test_transcript_triggered_rerender_merges_single_followup_with_existing_active_job(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher, project = _make_project("transcript_existing_active_merge_once")
    first = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="First",
        narration_text="First",
    )
    second = TranscriptPage.objects.create(
        project=project,
        order=1,
        source_slide_index=1,
        split_index=0,
        page_key="s2-p1",
        original_text="Second",
        narration_text="Second",
    )
    _prepare_lesson_upload(tmp_path, project)
    existing = Job.objects.create(project=project, job_type="video_export", status="running", progress=40)
    captured = _capture_dispatch(monkeypatch)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        first_response = _patch_transcript_page(teacher, project, first, "First edit")
        second_response = _patch_transcript_page(teacher, project, second, "Second edit")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.data["render_follow_up_intent_id"] == second_response.data["render_follow_up_intent_id"]
    assert Job.objects.filter(project=project, job_type="video_export").count() == 1
    assert Job.objects.get(project=project, job_type="video_export").id == existing.id
    assert captured.sent == []

    intent = RenderFollowUpIntent.objects.get(project=project)
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1", "s2-p1"]
    assert intent.metadata["active_job_id"] == existing.id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "terminal_status",
    [RenderFollowUpIntent.STATUS_CLEARED, RenderFollowUpIntent.STATUS_CANCELLED],
)
def test_transcript_followup_ignores_terminal_intent_when_creating_pending(
    terminal_status,
    tmp_path,
    monkeypatch,
):
    _ensure_transcript_table()
    teacher, project = _make_project(f"transcript_terminal_followup_{terminal_status}")
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
    Job.objects.create(project=project, job_type="video_export", status="running", progress=40)
    RenderFollowUpIntent.objects.create(
        project=project,
        status=terminal_status,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["old"],
    )
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
    pending = RenderFollowUpIntent.objects.get(project=project, status=RenderFollowUpIntent.STATUS_PENDING)
    assert pending.page_keys == ["s1-p1"]
    assert RenderFollowUpIntent.objects.filter(project=project).count() == 2
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
    assert captured.sent[0]["kwargs"]["job_id"] == response.data["id"]


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
