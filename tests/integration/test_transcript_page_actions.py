# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

import pytest  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
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


def _post_action(user, project, payload):
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript/actions/",
        payload,
        format="json",
    )
    force_authenticate(request, user=user)
    return views.ProjectTranscriptActionView.as_view()(request, project_id=project.id)


def _make_page(
    project,
    *,
    order,
    page_key,
    original_text,
    narration_text=None,
    source_slide_index=None,
    split_index=0,
):
    return TranscriptPage.objects.create(
        project=project,
        order=order,
        source_slide_index=order if source_slide_index is None else source_slide_index,
        split_index=split_index,
        page_key=page_key,
        original_text=original_text,
        narration_text=narration_text if narration_text is not None else original_text,
        rich_text_html=narration_text if narration_text is not None else original_text,
        subtitle_chunks=[narration_text if narration_text is not None else original_text],
    )


def _prepare_lesson_upload(tmp_path, project):
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    lesson_path = upload_dir / "lesson.txt"
    lesson_path.write_text("lesson", encoding="utf-8")
    return lesson_path


def _capture_rerender_dispatch(monkeypatch):
    sent_tasks = []
    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice_test")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(
            send_task=lambda *args, **kwargs: (
                sent_tasks.append((args, kwargs)) or SimpleNamespace(id=f"task-action-rerender-{len(sent_tasks) + 1}")
            )
        ),
    )
    return sent_tasks


def _export_slide(page, *, index=None, image_path=None, notes_text=None):
    slide_index = page.order if index is None else index
    return {
        "index": slide_index,
        "slide_num": slide_index + 1,
        "source_slide_index": page.source_slide_index,
        "split_index": page.split_index,
        "page_key": page.page_key,
        "image_path": image_path or f"slide{slide_index + 1}.png",
        "notes_text": notes_text if notes_text is not None else page.original_text,
        "audio_out": f"slide{slide_index + 1}.mp3",
        "part_out": f"slide{slide_index + 1}.mp4",
    }


def _project_and_payload_for_action(teacher, action, *, tts_settings=None):
    project_kwargs = {"title": f"Action {action}", "user": teacher}
    if tts_settings is not None:
        project_kwargs["tts_settings"] = tts_settings
    project = Project.objects.create(**project_kwargs)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="One", narration_text="One")
    page_two = _make_page(project, order=1, page_key="s2-p1", original_text="Two", narration_text="Two")

    if action == "split_page":
        payload = {
            "action": action,
            "page_id": page_one.id,
            "parts": [{"narration_text": "First"}, {"narration_text": "Second"}],
        }
    elif action == "merge_with_next":
        payload = {"action": action, "page_id": page_one.id}
    elif action == "merge_with_previous":
        payload = {"action": action, "page_id": page_two.id}
    elif action == "reorder_pages":
        page_three = _make_page(project, order=2, page_key="s3-p1", original_text="Three", narration_text="Three")
        payload = {"action": action, "page_ids": [page_two.id, page_one.id, page_three.id]}
    elif action == "delete_page":
        payload = {"action": action, "page_id": page_one.id}
    elif action == "restore_page":
        page_one.is_active = False
        page_one.save(update_fields=["is_active", "updated_at"])
        payload = {"action": action, "page_id": page_one.id, "position": "end"}
    else:
        raise AssertionError(f"unsupported action fixture: {action}")

    return project, payload


@pytest.mark.django_db
def test_transcript_action_split_creates_two_pages():
    _ensure_transcript_table()
    teacher = _make_teacher("action_split_teacher")
    project = Project.objects.create(title="Split action", user=teacher)
    page = _make_page(project, order=0, page_key="s1-p1", original_text="Original source")

    response = _post_action(
        teacher,
        project,
        {
            "action": "split_page",
            "page_id": page.id,
            "parts": [
                {"narration_text": "First segment."},
                {"narration_text": "Second segment."},
            ],
        },
    )

    assert response.status_code == 200
    pages = list(TranscriptPage.objects.filter(project=project, is_active=True).order_by("order", "id"))
    assert len(pages) == 2
    assert pages[0].id == page.id
    assert pages[0].page_key == "s1-p1"
    assert pages[0].original_text == "Original source"
    assert pages[0].narration_text == "First segment."
    assert pages[1].page_key.startswith("s1-p1-x")
    assert pages[1].page_key != pages[0].page_key
    assert pages[1].original_text == ""
    assert pages[1].narration_text == "Second segment."
    assert response.data["changed_page_keys"] == [pages[0].page_key, pages[1].page_key]


@pytest.mark.django_db
def test_transcript_action_split_invalid_payload_is_atomic():
    _ensure_transcript_table()
    teacher = _make_teacher("action_split_invalid_teacher")
    project = Project.objects.create(title="Split invalid", user=teacher)
    page = _make_page(project, order=0, page_key="s1-p1", original_text="Original source")

    response = _post_action(
        teacher,
        project,
        {
            "action": "split_page",
            "page_id": page.id,
            "parts": [{"narration_text": "Only one part."}],
        },
    )

    assert response.status_code == 400
    page.refresh_from_db()
    assert TranscriptPage.objects.filter(project=project).count() == 1
    assert page.page_key == "s1-p1"
    assert page.original_text == "Original source"
    assert page.narration_text == "Original source"


@pytest.mark.django_db
def test_transcript_action_merge_with_next_preserves_source_references():
    _ensure_transcript_table()
    teacher = _make_teacher("action_merge_next_teacher")
    project = Project.objects.create(title="Merge next", user=teacher)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="Original one", narration_text="Narration one")
    page_two = _make_page(project, order=1, page_key="s2-p1", original_text="Original two", narration_text="Narration two")

    response = _post_action(
        teacher,
        project,
        {
            "action": "merge_with_next",
            "page_id": page_one.id,
        },
    )

    assert response.status_code == 200
    page_one.refresh_from_db()
    page_two.refresh_from_db()
    assert page_one.is_active is True
    assert page_two.is_active is False
    assert page_two.deleted_at is not None
    assert page_one.narration_text == "Narration one\n\nNarration two"
    assert page_one.original_text == "Original one\n\nOriginal two"
    assert response.data["changed_page_keys"] == ["s1-p1"]
    assert len(response.data["pages"]) == 1
    assert len(response.data["deleted_pages"]) == 1


@pytest.mark.django_db
def test_transcript_action_merge_with_previous_preserves_source_references():
    _ensure_transcript_table()
    teacher = _make_teacher("action_merge_previous_teacher")
    project = Project.objects.create(title="Merge previous", user=teacher)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="Original one", narration_text="Narration one")
    page_two = _make_page(project, order=1, page_key="s2-p1", original_text="Original two", narration_text="Narration two")

    response = _post_action(
        teacher,
        project,
        {
            "action": "merge_with_previous",
            "page_id": page_two.id,
        },
    )

    assert response.status_code == 200
    page_one.refresh_from_db()
    page_two.refresh_from_db()
    assert page_one.is_active is True
    assert page_two.is_active is False
    assert page_one.narration_text == "Narration one\n\nNarration two"
    assert page_one.original_text == "Original one\n\nOriginal two"
    assert response.data["changed_page_keys"] == ["s1-p1"]


@pytest.mark.django_db
def test_transcript_action_reorder_changes_order_without_losing_ids():
    _ensure_transcript_table()
    teacher = _make_teacher("action_reorder_teacher")
    project = Project.objects.create(title="Reorder action", user=teacher)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="One")
    page_two = _make_page(project, order=1, page_key="s2-p1", original_text="Two")
    page_three = _make_page(project, order=2, page_key="s3-p1", original_text="Three")

    response = _post_action(
        teacher,
        project,
        {
            "action": "reorder_pages",
            "page_ids": [page_three.id, page_one.id, page_two.id],
        },
    )

    assert response.status_code == 200
    assert response.data["changed_page_keys"] == []
    ordered = list(TranscriptPage.objects.filter(project=project, is_active=True).order_by("order"))
    assert [page.id for page in ordered] == [page_three.id, page_one.id, page_two.id]
    assert [page.page_key for page in ordered] == ["s3-p1", "s1-p1", "s2-p1"]
    assert [page.narration_text for page in ordered] == ["Three", "One", "Two"]


@pytest.mark.django_db
def test_transcript_action_delete_soft_deletes_page_and_active_timeline_omits_it():
    _ensure_transcript_table()
    teacher = _make_teacher("action_delete_teacher")
    project = Project.objects.create(title="Delete action", user=teacher)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="One")
    page_two = _make_page(project, order=1, page_key="s2-p1", original_text="Two")

    response = _post_action(
        teacher,
        project,
        {
            "action": "delete_page",
            "page_id": page_one.id,
        },
    )

    assert response.status_code == 200
    page_one.refresh_from_db()
    page_two.refresh_from_db()
    assert page_one.is_active is False
    assert page_one.deleted_at is not None
    assert page_one.original_text == "One"
    assert page_two.is_active is True
    assert response.data["changed_page_keys"] == ["s1-p1"]
    assert [page["id"] for page in response.data["pages"]] == [page_two.id]
    assert [page["id"] for page in response.data["deleted_pages"]] == [page_one.id]


@pytest.mark.django_db
def test_transcript_action_restore_page_preserves_identity_and_text():
    _ensure_transcript_table()
    teacher = _make_teacher("action_restore_teacher")
    project = Project.objects.create(title="Restore action", user=teacher)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="One")
    page_two = _make_page(project, order=1, page_key="s2-p1", original_text="Two")
    _post_action(teacher, project, {"action": "delete_page", "page_id": page_one.id})

    response = _post_action(
        teacher,
        project,
        {
            "action": "restore_page",
            "page_id": page_one.id,
            "position": "after",
            "after_page_id": page_two.id,
        },
    )

    assert response.status_code == 200
    page_one.refresh_from_db()
    assert page_one.is_active is True
    assert page_one.deleted_at is None
    assert page_one.page_key == "s1-p1"
    assert page_one.original_text == "One"
    assert page_one.narration_text == "One"
    assert [page["id"] for page in response.data["pages"]] == [page_two.id, page_one.id]
    assert response.data["changed_page_keys"] == ["s1-p1"]


@pytest.mark.django_db
def test_transcript_actions_are_permission_protected():
    _ensure_transcript_table()
    owner = _make_teacher("action_owner_teacher")
    other = _make_teacher("action_other_teacher")
    project = Project.objects.create(title="Forbidden action", user=owner)
    page = _make_page(project, order=0, page_key="s1-p1", original_text="One")

    response = _post_action(
        other,
        project,
        {
            "action": "delete_page",
            "page_id": page.id,
        },
    )

    assert response.status_code == 403
    page.refresh_from_db()
    assert page.is_active is True


@pytest.mark.django_db
def test_transcript_action_rerender_strategy_full_when_job_queued(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher = _make_teacher("action_rerender_strategy_teacher")
    project, payload = _project_and_payload_for_action(teacher, "split_page")
    page_count_before = Project.objects.count()
    _prepare_lesson_upload(tmp_path, project)
    sent_tasks = _capture_rerender_dispatch(monkeypatch)
    payload["trigger_rerender"] = True

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_action(teacher, project, payload)

    assert response.status_code == 200
    assert Project.objects.count() == page_count_before
    job = Job.objects.get(project=project)
    assert response.data["project_id"] == project.id
    assert response.data["rerender_strategy"] == "full"
    assert response.data["rerender_job"]["project_id"] == project.id
    assert sent_tasks[0][1]["args"][0] == str(project.id)
    assert sent_tasks[0][1]["args"][8] == []
    assert job.project_id == project.id


@pytest.mark.django_db
def test_transcript_action_rerender_strategy_none_without_trigger():
    _ensure_transcript_table()
    teacher = _make_teacher("action_rerender_none_teacher")
    project, payload = _project_and_payload_for_action(teacher, "delete_page")

    response = _post_action(teacher, project, payload)

    assert response.status_code == 200
    assert response.data["rerender_strategy"] == "none"
    assert response.data["rerender_job"] is None
    assert Job.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_transcript_action_rerender_passes_project_tts_settings(tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher = _make_teacher("action_rerender_tts_teacher")
    project_tts_settings = canonical_project_tts_settings(
        {
            "provider_preference": "gtts",
            "normalization_enabled": False,
            "normalization_mode": "strict",
            "unknown_word_strategy": "phonetic",
            "speech_speed": 1.15,
            "volume_gain_db": -2,
            "pause_seconds": 3.25,
            "overrides": {
                "technical": {"GPU": "jee pee you"},
                "abbreviation": {"AI": "ey ay"},
            },
        }
    )
    project, payload = _project_and_payload_for_action(
        teacher,
        "merge_with_next",
        tts_settings=project_tts_settings,
    )
    _prepare_lesson_upload(tmp_path, project)
    sent_tasks = _capture_rerender_dispatch(monkeypatch)
    payload["trigger_rerender"] = True

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_action(teacher, project, payload)

    assert response.status_code == 200
    assert response.data["rerender_strategy"] == "full"
    assert sent_tasks[0][1]["args"][8] == []
    assert sent_tasks[0][1]["args"][9] == project_tts_settings


@pytest.mark.django_db
@pytest.mark.parametrize(
    "action",
    [
        "split_page",
        "merge_with_next",
        "merge_with_previous",
        "reorder_pages",
        "delete_page",
        "restore_page",
    ],
)
def test_transcript_structural_actions_trigger_full_same_project_rerender(action, tmp_path, monkeypatch):
    _ensure_transcript_table()
    teacher = _make_teacher(f"action_full_{action}")
    project_tts_settings = canonical_project_tts_settings({"provider_preference": "gtts"})
    project, payload = _project_and_payload_for_action(teacher, action, tts_settings=project_tts_settings)
    _prepare_lesson_upload(tmp_path, project)
    sent_tasks = _capture_rerender_dispatch(monkeypatch)
    payload["trigger_rerender"] = True
    page_count_before = Project.objects.count()

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _post_action(teacher, project, payload)

    assert response.status_code == 200
    assert response.data["project_id"] == project.id
    assert response.data["rerender_strategy"] == "full"
    assert response.data["rerender_job"]["project_id"] == project.id
    assert isinstance(response.data["changed_page_keys"], list)
    assert Project.objects.count() == page_count_before
    assert sent_tasks[0][1]["args"][0] == str(project.id)
    assert sent_tasks[0][1]["args"][8] == []
    assert sent_tasks[0][1]["args"][9] == project_tts_settings
    assert Job.objects.get(project=project).project_id == project.id


@pytest.mark.django_db
def test_worker_sync_respects_reordered_active_transcript_order():
    _ensure_transcript_table()
    from worker import tasks as worker_tasks

    teacher = _make_teacher("action_worker_reorder_teacher")
    project = Project.objects.create(title="Worker reorder", user=teacher)
    page_one = _make_page(project, order=1, page_key="s1-p1", original_text="One", source_slide_index=0)
    page_two = _make_page(project, order=2, page_key="s2-p1", original_text="Two", source_slide_index=1)
    page_three = _make_page(project, order=0, page_key="s3-p1", original_text="Three", source_slide_index=2)

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            _export_slide(page_one, index=0),
            _export_slide(page_two, index=1),
            _export_slide(page_three, index=2),
        ],
    )

    assert [item["page_key"] for item in synced] == ["s3-p1", "s1-p1", "s2-p1"]
    assert [item["narration_text"] for item in synced] == ["Three", "One", "Two"]
    assert [item["index"] for item in synced] == [0, 1, 2]


@pytest.mark.django_db
def test_worker_sync_delete_excludes_inactive_page_from_render_sequence():
    _ensure_transcript_table()
    from worker import tasks as worker_tasks

    teacher = _make_teacher("action_worker_inactive_teacher")
    project = Project.objects.create(title="Worker inactive skip", user=teacher)
    active_page = _make_page(project, order=0, page_key="s1-p1", original_text="One")
    inactive_page = _make_page(project, order=1, page_key="s2-p1", original_text="Two")
    inactive_page.is_active = False
    inactive_page.save(update_fields=["is_active"])

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            {
                "index": 0,
                "slide_num": 1,
                "source_slide_index": 0,
                "split_index": 0,
                "page_key": active_page.page_key,
                "image_path": "slide1.png",
                "notes_text": "Fresh one",
                "audio_out": "slide1.mp3",
                "part_out": "slide1.mp4",
            },
            {
                "index": 1,
                "slide_num": 2,
                "source_slide_index": 1,
                "split_index": 0,
                "page_key": inactive_page.page_key,
                "image_path": "slide2.png",
                "notes_text": "Fresh two",
                "audio_out": "slide2.mp3",
                "part_out": "slide2.mp4",
            },
        ],
    )

    inactive_page.refresh_from_db()
    assert inactive_page.is_active is False
    assert [item["page_key"] for item in synced] == [active_page.page_key]


@pytest.mark.django_db
def test_worker_sync_split_includes_new_split_page():
    _ensure_transcript_table()
    from worker import tasks as worker_tasks

    teacher = _make_teacher("action_worker_split_teacher")
    project = Project.objects.create(title="Worker split", user=teacher)
    base_page = _make_page(
        project,
        order=0,
        page_key="s1-p1",
        original_text="Original source",
        narration_text="Base narration",
        source_slide_index=0,
        split_index=0,
    )
    split_page = _make_page(
        project,
        order=1,
        page_key="s1-p1-x1",
        original_text="",
        narration_text="Split narration",
        source_slide_index=0,
        split_index=1,
    )

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [_export_slide(base_page, index=0, image_path="slide1.png", notes_text="Fresh source")],
    )

    assert [item["page_key"] for item in synced] == [base_page.page_key, split_page.page_key]
    assert synced[1]["narration_text"] == "Split narration"
    assert synced[1]["image_path"] == "slide1.png"
    assert synced[1]["source_slide_index"] == 0
    assert synced[1]["split_index"] == 1


@pytest.mark.django_db
def test_worker_sync_merge_excludes_soft_deleted_merged_page():
    _ensure_transcript_table()
    from worker import tasks as worker_tasks

    teacher = _make_teacher("action_worker_merge_teacher")
    project = Project.objects.create(title="Worker merge", user=teacher)
    survivor = _make_page(
        project,
        order=0,
        page_key="s1-p1",
        original_text="Original one\n\nOriginal two",
        narration_text="Narration one\n\nNarration two",
        source_slide_index=0,
    )
    merged_away = _make_page(
        project,
        order=1,
        page_key="s2-p1",
        original_text="Original two",
        narration_text="Narration two",
        source_slide_index=1,
    )
    merged_away.is_active = False
    merged_away.save(update_fields=["is_active", "updated_at"])

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [_export_slide(survivor, index=0), _export_slide(merged_away, index=1)],
    )

    assert [item["page_key"] for item in synced] == [survivor.page_key]
    assert synced[0]["narration_text"] == "Narration one\n\nNarration two"


@pytest.mark.django_db
def test_worker_sync_restore_includes_restored_page_at_position():
    _ensure_transcript_table()
    from worker import tasks as worker_tasks

    teacher = _make_teacher("action_worker_restore_teacher")
    project = Project.objects.create(title="Worker restore", user=teacher)
    page_one = _make_page(project, order=0, page_key="s1-p1", original_text="One", source_slide_index=0)
    restored_page = _make_page(project, order=1, page_key="s2-p1", original_text="Two", source_slide_index=1)
    page_three = _make_page(project, order=2, page_key="s3-p1", original_text="Three", source_slide_index=2)
    restored_page.is_active = False
    restored_page.order = 99
    restored_page.save(update_fields=["is_active", "order", "updated_at"])
    restored_page.is_active = True
    restored_page.order = 1
    restored_page.save(update_fields=["is_active", "order", "updated_at"])

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [
            _export_slide(page_one, index=0),
            _export_slide(restored_page, index=1),
            _export_slide(page_three, index=2),
        ],
    )

    assert [item["page_key"] for item in synced] == ["s1-p1", "s2-p1", "s3-p1"]
    assert synced[1]["narration_text"] == "Two"
