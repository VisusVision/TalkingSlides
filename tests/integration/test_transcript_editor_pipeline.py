# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.db import connection

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
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Project, TranscriptPage, Job, UserProfile  # noqa: E402
from core.serializers import canonical_project_tts_settings  # noqa: E402


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


def _make_teacher(username):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _import_worker_tasks():
    from worker import tasks as worker_tasks

    return worker_tasks


@pytest.mark.django_db
def test_transcript_patch_persists_editor_document_and_triggers_rerender(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher = _make_teacher("transcript_teacher_save")
    project_tts_settings = canonical_project_tts_settings(
        {
            "provider_preference": "gtts",
            "overrides": {
                "technical": {"GPU": "jee pee you"},
            },
        }
    )
    project = Project.objects.create(title="Transcript save", user=teacher, tts_settings=project_tts_settings)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original",
        narration_text="Original",
    )

    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")

    sent_tasks = []
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice_test")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(
            send_task=lambda *args, **kwargs: (
                sent_tasks.append((args, kwargs)) or SimpleNamespace(id="task-transcript-1")
            )
        ),
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "trigger_rerender": True,
            "pages": [
                {
                    "id": page.id,
                    "narration_text": "Edited line 1\nEdited line 2",
                    "rich_text_html": "<b>Edited line 1</b><br />Edited line 2",
                    "editor_document": {
                        "version": 1,
                        "paragraphs": [
                            {"index": 0, "text": "Edited line 1"},
                            {"index": 1, "text": "Edited line 2"},
                        ],
                    },
                }
            ],
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert "rerender_job" in response.data
    assert response.data["rerender_job"]["status"] == "pending"

    page.refresh_from_db()
    assert page.narration_text == "Edited line 1\nEdited line 2"
    assert page.editor_document.get("version") == 1
    assert len(page.subtitle_chunks) >= 2

    assert Job.objects.filter(project=project, status="pending").exists()
    assert sent_tasks
    assert sent_tasks[0][0][0] == "worker.tasks.process_pptx_to_video"
    assert sent_tasks[0][1]["args"][2] == "voice_test"
    assert sent_tasks[0][1]["args"][8] == ["s1-p1"]
    assert sent_tasks[0][1]["args"][9] == project_tts_settings


@pytest.mark.django_db
def test_transcript_patch_partial_update_preserves_other_pages_and_original_text():
    _ensure_transcript_table()
    teacher = _make_teacher("transcript_teacher_partial")
    project = Project.objects.create(title="Partial transcript save", user=teacher)
    page_one = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original one",
        narration_text="Original one",
    )
    page_two = TranscriptPage.objects.create(
        project=project,
        order=1,
        source_slide_index=1,
        split_index=0,
        page_key="s2-p1",
        original_text="Original two",
        narration_text="Original two",
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "pages": [
                {
                    "id": page_one.id,
                    "page_key": page_one.page_key,
                    "order": page_one.order,
                    "narration_text": "Edited one",
                    "rich_text_html": "Edited one",
                    "editor_document": {
                        "version": 1,
                        "html": "Edited one",
                        "paragraphs": [{"index": 0, "text": "Edited one"}],
                    },
                }
            ]
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert len(response.data["pages"]) == 2

    page_one.refresh_from_db()
    page_two.refresh_from_db()
    assert page_one.narration_text == "Edited one"
    assert page_one.original_text == "Original one"
    assert page_two.narration_text == "Original two"
    assert page_two.original_text == "Original two"


@pytest.mark.django_db
def test_transcript_rerender_preserves_original_text():
    _ensure_transcript_table()
    worker_tasks = _import_worker_tasks()
    teacher = _make_teacher("transcript_teacher_original_text")
    project = Project.objects.create(title="Original text survives", user=teacher)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original source text",
        narration_text="Edited narration text",
        subtitle_chunks=["Edited narration text"],
    )

    slides = [
        {
            "index": 0,
            "slide_num": 1,
            "source_slide_index": 0,
            "split_index": 0,
            "page_key": "s1-p1",
            "notes_text": "Fresh export fallback text",
            "original_text": "Fresh export fallback text",
            "narration_text": "Fresh export fallback text",
            "subtitle_chunks": ["Fresh export fallback text"],
        }
    ]

    synced = worker_tasks._sync_transcript_pages_from_export(project.id, slides)

    page.refresh_from_db()
    assert page.original_text == "Original source text"
    assert page.narration_text == "Edited narration text"
    assert synced[0]["original_text"] == "Original source text"


@pytest.mark.django_db
def test_transcript_rerender_preserves_other_pages():
    _ensure_transcript_table()
    worker_tasks = _import_worker_tasks()
    teacher = _make_teacher("transcript_teacher_other_pages")
    project = Project.objects.create(title="Other pages survive", user=teacher)
    TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original one",
        narration_text="Edited one",
    )
    page_two = TranscriptPage.objects.create(
        project=project,
        order=1,
        source_slide_index=1,
        split_index=0,
        page_key="s2-p1",
        original_text="Original two",
        narration_text="Original two",
    )

    worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "slide_num": 1,
                "source_slide_index": 0,
                "split_index": 0,
                "page_key": "s1-p1",
                "notes_text": "Fresh one",
                "original_text": "Fresh one",
            }
        ],
    )

    page_two.refresh_from_db()
    assert TranscriptPage.objects.filter(project=project).count() == 2
    assert page_two.original_text == "Original two"
    assert page_two.narration_text == "Original two"


@pytest.mark.django_db
def test_transcript_rerender_uses_edited_narration_for_tts_args():
    _ensure_transcript_table()
    worker_tasks = _import_worker_tasks()
    teacher = _make_teacher("transcript_teacher_tts_args")
    project = Project.objects.create(title="Edited narration render input", user=teacher)
    TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original source",
        narration_text="Edited narration for TTS",
        subtitle_chunks=["Edited narration for TTS"],
    )

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "slide_num": 1,
                "source_slide_index": 0,
                "split_index": 0,
                "page_key": "s1-p1",
                "notes_text": "Fresh source",
                "original_text": "Fresh source",
                "narration_text": "Fresh source",
                "subtitle_chunks": ["Fresh source"],
            }
        ],
    )

    assert synced[0]["narration_text"] == "Edited narration for TTS"
    assert synced[0]["subtitle_chunks"] == ["Edited narration for TTS"]


@pytest.mark.django_db
def test_transcript_rerender_does_not_create_duplicate_project(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher = _make_teacher("transcript_teacher_no_duplicate_project")
    project = Project.objects.create(title="No duplicate project", user=teacher)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original",
        narration_text="Original",
    )

    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")

    sent_tasks = []
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice_test")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(
            send_task=lambda *args, **kwargs: (
                sent_tasks.append((args, kwargs)) or SimpleNamespace(id="task-no-duplicate")
            )
        ),
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "trigger_rerender": True,
            "pages": [
                {
                    "id": page.id,
                    "narration_text": "Edited same project",
                }
            ],
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert Project.objects.count() == 1
    job = Job.objects.get(project=project)
    assert response.data["project_id"] == project.id
    assert response.data["rerender_job"]["project_id"] == project.id
    assert sent_tasks[0][1]["args"][0] == str(project.id)
    assert job.project_id == project.id


@pytest.mark.django_db
def test_transcript_patch_does_not_auto_split_on_double_newline():
    _ensure_transcript_table()
    teacher = _make_teacher("transcript_teacher_split")
    project = Project.objects.create(title="Split newlines", user=teacher)
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original",
        narration_text="Original",
    )

    request = APIRequestFactory().patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "pages": [
                {
                    "id": page.id,
                    "narration_text": "Part one.\n\nPart two.",
                    "rich_text_html": "Part one.<br /><br />Part two.",
                }
            ]
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    response = views.ProjectTranscriptView.as_view()(request, project_id=project.id)
    assert response.status_code == 200

    pages = TranscriptPage.objects.filter(project=project).order_by("order", "id")
    assert pages.count() == 1
    assert pages.first().narration_text.strip().startswith("Part one")
    assert "Part two" in pages.first().narration_text
