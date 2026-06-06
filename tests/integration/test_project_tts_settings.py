import os
import sys
import uuid
import inspect
from pathlib import Path
from types import SimpleNamespace

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
from django.db import connection  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402
from core.models import Project, UserProfile, default_project_tts_settings  # noqa: E402
from core.serializers import ProjectSerializer, canonical_project_tts_settings  # noqa: E402


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def _skip_if_tts_settings_missing():
    if not _table_has_column("core_project", "tts_settings"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")


def _make_teacher(prefix="tts_teacher"):
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"{prefix}_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _patch_project(project, user, payload):
    request = APIRequestFactory().patch(f"/api/v1/projects/{project.id}/", payload, format="json")
    force_authenticate(request, user=user)
    return views.ProjectDetailView.as_view()(request, project_id=project.id)


@pytest.mark.django_db
def test_project_tts_settings_defaults_on_new_project():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(title="Default TTS Settings", user=teacher)

    assert project.tts_settings == default_project_tts_settings()
    assert ProjectSerializer(project).data["tts_settings"] == default_project_tts_settings()


@pytest.mark.django_db
def test_project_serializer_returns_defaults_for_empty_or_partial_settings():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    empty_project = Project.objects.create(title="Empty TTS Settings", user=teacher, tts_settings={})
    partial_project = Project.objects.create(
        title="Partial TTS Settings",
        user=teacher,
        tts_settings={
            "provider_preference": "gtts",
            "overrides": {
                "technical": {
                    "GPU": "jee pee you",
                }
            },
        },
    )

    assert ProjectSerializer(empty_project).data["tts_settings"] == default_project_tts_settings()

    settings = ProjectSerializer(partial_project).data["tts_settings"]
    assert settings["provider_preference"] == "gtts"
    assert settings["normalization_enabled"] is True
    assert settings["normalization_mode"] == "loose"
    assert settings["overrides"]["technical"] == {"GPU": "jee pee you"}
    assert settings["overrides"]["abbreviation"] == {}
    assert settings["overrides"]["mixed_word"] == {}


@pytest.mark.django_db
def test_project_detail_patch_updates_tts_settings_only():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(title="Patch TTS Settings", user=teacher)

    response = _patch_project(
        project,
        teacher,
        {
            "tts_settings": {
                "provider_preference": " GTTS ",
                "normalization_enabled": False,
                "normalization_mode": "STRICT",
                "unknown_word_strategy": "PHONETIC",
                "speech_speed": 1.2,
                "volume_gain_db": -3,
                "pause_seconds": 3.5,
                "overrides": {
                    "technical": {
                        " GPU ": " jee pee you ",
                    }
                },
            }
        },
    )

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.category_id is None
    assert project.avatar_enabled_override is None
    assert project.tts_settings == response.data["tts_settings"]
    assert project.tts_settings["provider_preference"] == "gtts"
    assert project.tts_settings["normalization_enabled"] is False
    assert project.tts_settings["normalization_mode"] == "strict"
    assert project.tts_settings["unknown_word_strategy"] == "phonetic"
    assert project.tts_settings["overrides"]["technical"] == {"GPU": "jee pee you"}


@pytest.mark.django_db
def test_project_detail_patch_merges_partial_tts_settings():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(
        title="Merge TTS Settings",
        user=teacher,
        tts_settings=canonical_project_tts_settings(
            {
                "provider_preference": "xtts_v2",
                "normalization_enabled": False,
                "speech_speed": 0.9,
                "overrides": {
                    "technical": {"CUDA": "ku da"},
                    "abbreviation": {"AI": "ey ay"},
                },
            }
        ),
    )

    response = _patch_project(
        project,
        teacher,
        {
            "tts_settings": {
                "speech_speed": 1.1,
                "overrides": {
                    "mixed_word": {"ChatGPT": "chat gpt"},
                },
            }
        },
    )

    assert response.status_code == 200
    project.refresh_from_db()
    settings = project.tts_settings
    assert settings["provider_preference"] == "xtts_v2"
    assert settings["normalization_enabled"] is False
    assert settings["speech_speed"] == 1.1
    assert settings["overrides"]["technical"] == {"CUDA": "ku da"}
    assert settings["overrides"]["abbreviation"] == {"AI": "ey ay"}
    assert settings["overrides"]["mixed_word"] == {"ChatGPT": "chat gpt"}


@pytest.mark.django_db
def test_project_detail_patch_replaces_supplied_override_category():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(
        title="Replace Override Category",
        user=teacher,
        tts_settings=canonical_project_tts_settings(
            {
                "overrides": {
                    "technical": {"CUDA": "ku da"},
                    "abbreviation": {"AI": "ey ay"},
                }
            }
        ),
    )

    response = _patch_project(project, teacher, {"tts_settings": {"overrides": {"technical": {}}}})

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.tts_settings["overrides"]["technical"] == {}
    assert project.tts_settings["overrides"]["abbreviation"] == {"AI": "ey ay"}
    assert project.tts_settings["overrides"]["mixed_word"] == {}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_patch",
    [
        {"provider_preference": "silent"},
        {"normalization_enabled": "true"},
        {"normalization_mode": "medium"},
        {"unknown_word_strategy": "spell"},
        {"speech_speed": 1.6},
        {"volume_gain_db": -13},
        {"pause_seconds": 11},
        {"overrides": {"other": {}}},
        {"overrides": {"technical": {"": "empty"}}},
        {"overrides": {"technical": {"AI": 123}}},
        {"overrides": {"technical": {"bad\nkey": "value"}}},
        {"overrides": {"technical": {"x" * 121: "value"}}},
        {"overrides": {"technical": {"term": "x" * 201}}},
        {"overrides": {"technical": {f"term{i}": "value" for i in range(201)}}},
    ],
)
def test_project_detail_patch_rejects_invalid_tts_settings(bad_patch):
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(title="Invalid TTS Settings", user=teacher)
    original_settings = project.tts_settings

    response = _patch_project(project, teacher, {"tts_settings": bad_patch})

    assert response.status_code == 400
    project.refresh_from_db()
    assert project.tts_settings == original_settings


@pytest.mark.django_db
def test_project_detail_patch_invalid_category_does_not_persist_tts_settings():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(title="Atomic Invalid Category", user=teacher)
    original_settings = project.tts_settings

    response = _patch_project(
        project,
        teacher,
        {
            "category_id": "not-an-integer",
            "tts_settings": {
                "provider_preference": "gtts",
                "speech_speed": 1.2,
            },
        },
    )

    assert response.status_code == 400
    project.refresh_from_db()
    assert project.tts_settings == original_settings


@pytest.mark.django_db
def test_project_detail_patch_invalid_category_name_does_not_persist_tts_settings():
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project = Project.objects.create(title="Atomic Invalid Category Name", user=teacher)
    original_settings = project.tts_settings

    response = _patch_project(
        project,
        teacher,
        {
            "category_name": "x" * 201,
            "tts_settings": {
                "provider_preference": "gtts",
                "speech_speed": 1.2,
            },
        },
    )

    assert response.status_code == 400
    project.refresh_from_db()
    assert project.tts_settings == original_settings


@pytest.mark.django_db
def test_project_detail_patch_rejects_tts_settings_for_forbidden_user():
    _skip_if_tts_settings_missing()

    owner = _make_teacher("owner")
    other = _make_teacher("other")
    project = Project.objects.create(title="Forbidden TTS Settings", user=owner)

    response = _patch_project(project, other, {"tts_settings": {"provider_preference": "gtts"}})
    assert response.status_code == 403
    project.refresh_from_db()
    assert project.tts_settings == default_project_tts_settings()


@pytest.mark.django_db
def test_project_upload_existing_flow_compatibility(tmp_path, monkeypatch):
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    lesson_file = SimpleUploadedFile("lesson.txt", b"Sample lesson content", content_type="text/plain")
    sent = {}

    def fake_send_task(name, args=None, kwargs=None):
        sent["name"] = name
        sent["args"] = args or []
        sent["kwargs"] = kwargs or {}
        return SimpleNamespace(id="task-tts-settings-upload")

    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice-phase2")
    monkeypatch.setattr(views, "_celery_app", SimpleNamespace(send_task=fake_send_task))

    request = APIRequestFactory().post(
        "/api/v1/projects/",
        {
            "title": "Upload Compatibility",
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
    assert sent["name"] == "worker.tasks.process_pptx_to_video"
    assert len(sent["args"]) == 10
    assert sent["args"][8] is None
    assert sent["args"][9] == default_project_tts_settings()

    project = Project.objects.latest("id")
    assert project.tts_settings == default_project_tts_settings()


@pytest.mark.django_db
def test_project_upload_rejects_inline_tts_settings(tmp_path, monkeypatch):
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    lesson_file = SimpleUploadedFile("lesson.txt", b"Sample lesson content", content_type="text/plain")
    monkeypatch.setattr(
        views,
        "_celery_app",
        SimpleNamespace(send_task=lambda *_args, **_kwargs: SimpleNamespace(id="should-not-dispatch")),
    )

    request = APIRequestFactory().post(
        "/api/v1/projects/",
        {
            "title": "Rejected Inline Settings",
            "pause_sec": "0.2",
            "lesson_file": lesson_file,
            "tts_settings": '{"provider_preference": "gtts"}',
        },
        format="multipart",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectUploadView.as_view()(request)

    assert response.status_code == 400
    assert "PATCH /api/v1/projects/<id>/" in response.data["error"]


@pytest.mark.django_db
def test_project_rerender_enqueues_celery_with_project_tts_settings(tmp_path, monkeypatch):
    _skip_if_tts_settings_missing()

    teacher = _make_teacher()
    project_tts_settings = canonical_project_tts_settings(
        {
            "provider_preference": "gtts",
            "normalization_mode": "strict",
            "overrides": {
                "technical": {"GPU": "jee pee you"},
            },
        }
    )
    project = Project.objects.create(
        title="Rerender TTS Settings",
        user=teacher,
        tts_settings=project_tts_settings,
    )
    upload_dir = tmp_path / "uploads" / str(project.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "lesson.txt").write_text("lesson", encoding="utf-8")
    sent = {}

    def fake_send_task(name, args=None, kwargs=None):
        sent["name"] = name
        sent["args"] = args or []
        sent["kwargs"] = kwargs or {}
        return SimpleNamespace(id="task-tts-settings-rerender")

    monkeypatch.setattr(views, "_get_voice_id", lambda *_args, **_kwargs: "voice-rerender")
    monkeypatch.setattr(views, "_celery_app", SimpleNamespace(send_task=fake_send_task))

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/rerender/",
        {
            "pause_sec": "0.2",
            "lang_hint": "en",
        },
        format="json",
    )
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectRerenderView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    assert sent["name"] == "worker.tasks.process_pptx_to_video"
    assert len(sent["args"]) == 10
    assert sent["args"][8] is None
    assert sent["args"][9] == project_tts_settings


def test_process_pptx_to_video_accepts_missing_tts_settings():
    from worker import tasks as worker_tasks

    signature = inspect.signature(worker_tasks.process_pptx_to_video.run)

    assert signature.parameters["rerender_page_keys"].default is None
    assert signature.parameters["tts_settings"].default is None
