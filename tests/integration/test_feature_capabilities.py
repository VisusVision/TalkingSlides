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

from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import Project, TranscriptPage, UserProfile  # noqa: E402


pytestmark = pytest.mark.django_db


class _FakeAsyncResult:
    id = "feature-capabilities-task"


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_user(username: str, *, role: str = "publisher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _make_project(owner: User, *, published: bool = False) -> Project:
    return Project.objects.create(
        title="Feature capabilities lesson",
        description="Feature flag integration test lesson.",
        user=owner,
        status="ready" if published else "draft",
        is_published=published,
        moderation_status="approved",
    )


def _add_page(project: Project) -> None:
    TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        page_key="p1",
        original_text="A complete lesson transcript for feature capability testing.",
        narration_text="A complete lesson transcript for feature capability testing.",
    )


def _fake_dispatch(calls):
    def dispatch(task_name, *, args=None, kwargs=None, queue=None):
        calls.append({"task_name": task_name, "args": args or [], "kwargs": kwargs or {}, "queue": queue})
        return _FakeAsyncResult()

    return dispatch


@override_settings(
    ENABLE_AVATAR=True,
    ENABLE_INTELLIGENCE=True,
    ENABLE_LOCAL_OLLAMA=True,
    ENABLE_VISUAL_MODERATION=True,
    ENABLE_LOCAL_XTTS=False,
    LESSON_INTELLIGENCE_ENABLED=True,
    ANALYTICS_INTELLIGENCE_ENABLED=True,
)
def test_capabilities_endpoint_returns_configured_flags(monkeypatch):
    for name in ("ENABLE_AVATAR", "ENABLE_INTELLIGENCE", "ENABLE_LOCAL_OLLAMA", "ENABLE_VISUAL_MODERATION", "ENABLE_LOCAL_XTTS"):
        monkeypatch.delenv(name, raising=False)

    response = _client().get("/api/v1/capabilities/")

    assert response.status_code == 200
    features = response.data["features"]
    assert features["avatar"]["enabled"] is True
    assert features["intelligence"]["enabled"] is True
    assert features["local_ollama"]["enabled"] is True
    assert features["visual_moderation"]["enabled"] is True
    assert features["local_tts"]["enabled"] is False
    assert features["local_tts"]["status"] == "fallback"


@override_settings(ENABLE_AVATAR=False)
def test_avatar_disabled_endpoint_returns_clear_message(monkeypatch):
    monkeypatch.setenv("ENABLE_AVATAR", "0")
    user = _make_user("feature_avatar_disabled")

    response = _client(user).post(f"/api/v1/users/{user.id}/avatar/prepare/", {}, format="json")

    assert response.status_code == 403
    assert response.data["status"] == "disabled"
    assert response.data["feature"] == "avatar"
    assert "disabled" in response.data["error"].lower()


@override_settings(
    ENABLE_INTELLIGENCE=False,
    LESSON_INTELLIGENCE_ENABLED=False,
    ANALYTICS_INTELLIGENCE_ENABLED=False,
)
def test_intelligence_disabled_endpoint_returns_clear_message(monkeypatch):
    monkeypatch.setenv("ENABLE_INTELLIGENCE", "0")
    user = _make_user("feature_intelligence_disabled")
    project = _make_project(user)
    _add_page(project)

    response = _client(user).post(f"/api/v1/projects/{project.id}/intelligence/analyze/", {}, format="json")

    assert response.status_code == 403
    assert response.data["enabled"] is False
    assert "disabled" in response.data["error"].lower()


@override_settings(
    ENABLE_INTELLIGENCE=False,
    LESSON_INTELLIGENCE_ENABLED=False,
    ANALYTICS_INTELLIGENCE_ENABLED=False,
)
def test_disabled_intelligence_does_not_enqueue_tasks(monkeypatch):
    monkeypatch.setenv("ENABLE_INTELLIGENCE", "0")
    user = _make_user("feature_intelligence_no_enqueue")
    project = _make_project(user)
    calls = []

    import core.views as views

    monkeypatch.setattr(views, "_dispatch_celery_task", _fake_dispatch(calls))

    assert views._queue_lesson_intelligence_schedule(project.id, reason="test", force=True) is False
    assert views._queue_creator_analytics_intelligence_schedule(user.id, reason="test", force=True) is False
    assert calls == []


@override_settings(ENABLE_AVATAR=False)
def test_disabled_avatar_does_not_enqueue_avatar_task(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_AVATAR", "0")
    user = _make_user("feature_avatar_no_enqueue")
    calls = []

    import core.views as views

    monkeypatch.setattr(views, "_dispatch_celery_task", _fake_dispatch(calls))
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(user).post(
            "/api/v1/projects/",
            {
                "lesson_file": SimpleUploadedFile("lesson.txt", b"Slide one\nSlide two", content_type="text/plain"),
                "title": "Disabled avatar upload",
                "avatar_enabled": "1",
            },
            format="multipart",
        )

    assert response.status_code == 202
    assert response.data["avatar_processing_status"] == "none"
    assert len(calls) == 1
    assert calls[0]["task_name"] == "worker.tasks.process_pptx_to_video"
    avatar_options = calls[0]["args"][7]
    assert avatar_options["enabled"] is False
    assert "disabled" in avatar_options["disabled_reason"].lower()


@override_settings(ENABLE_AVATAR=False)
def test_avatar_disabled_rerender_endpoint_does_not_enqueue(monkeypatch):
    monkeypatch.setenv("ENABLE_AVATAR", "0")
    user = _make_user("feature_avatar_rerender_disabled")
    project = _make_project(user)
    calls = []

    import core.views as views

    monkeypatch.setattr(views, "_dispatch_celery_task", _fake_dispatch(calls))

    response = _client(user).post(f"/api/v1/projects/{project.id}/avatar/rerender/", {}, format="json")

    assert response.status_code == 403
    assert response.data["status"] == "disabled"
    assert calls == []


@override_settings(
    ENABLE_AVATAR=False,
    ENABLE_INTELLIGENCE=False,
    ENABLE_VISUAL_MODERATION=False,
    LESSON_INTELLIGENCE_ENABLED=False,
    ANALYTICS_INTELLIGENCE_ENABLED=False,
)
def test_normal_studio_analytics_and_catalog_apis_work_when_heavy_features_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_AVATAR", "0")
    monkeypatch.setenv("ENABLE_INTELLIGENCE", "0")
    monkeypatch.setenv("ENABLE_VISUAL_MODERATION", "0")
    publisher = _make_user("feature_normal_publisher")
    _make_project(publisher, published=True)

    client = _client(publisher)

    projects_response = client.get("/api/v1/projects/")
    analytics_response = client.get("/api/v1/me/analytics/")
    catalog_response = _client().get("/api/v1/catalog/")

    assert projects_response.status_code == 200
    assert analytics_response.status_code == 200
    assert catalog_response.status_code == 200
