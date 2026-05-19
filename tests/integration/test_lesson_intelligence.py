import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError

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
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.lesson_intelligence import (  # noqa: E402
    LessonIntelligenceProviderUnavailable,
    PaidLessonIntelligenceProvider,
    analyze_with_provider_chain,
    build_lesson_intelligence_input,
)
from core.models import LessonIntelligenceReport, Project, TranscriptPage, UserProfile  # noqa: E402


pytestmark = pytest.mark.django_db


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_user(username: str, *, role: str = "publisher", is_staff: bool = False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role=role)
    return user


def _make_project(owner: User, title: str = "Gradient Descent Basics") -> Project:
    return Project.objects.create(title=title, description="A lesson about optimization examples.", user=owner, status="draft")


def _add_page(
    project: Project,
    *,
    order: int,
    key: str,
    original: str,
    narration: str = "",
) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=order,
        source_slide_index=order,
        page_key=key,
        original_text=original,
        narration_text=narration or original,
    )


def _lesson_text() -> str:
    return (
        "Introduction: Today we will learn how gradient descent improves a model. "
        "The algorithm starts with a prediction, measures error, and updates parameters. "
        "For example, a simple regression model can adjust its weight after each mistake. "
        "Summary: We recap the update loop and the next step is testing learning rates."
    )


def _turkish_lesson_text() -> str:
    return (
        "Giriş: Bu ders veri analizi konusunu sade bir şekilde anlatır. "
        "Öğrenci, temel kavramları ve neden önemli olduklarını öğrenir. "
        "Konu boyunca açıklık, yapı ve anlatım akışı üzerinde durulur. "
        "Sonuç: Ders sonunda ana fikirler özetlenir ve sonraki adım önerilir."
    )


def _analyze_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/intelligence/analyze/"


def _latest_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/intelligence/"


class _FakeOllamaResponse:
    def __init__(self, payload: dict | str):
        if isinstance(payload, str):
            self.body = payload.encode("utf-8")
        else:
            self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body


class _FakeAsyncResult:
    id = "fake-intelligence-task"


def _fake_dispatch(calls):
    def dispatch(task_name, *, args=None, kwargs=None, queue=None):
        calls.append({"task_name": task_name, "args": args or [], "kwargs": kwargs or {}, "queue": queue})
        return _FakeAsyncResult()

    return dispatch


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_unauthenticated_denied():
    owner = _make_user("li_anon_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    response = _client().post(_analyze_url(project), {}, format="json")

    assert response.status_code in {401, 403}


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_non_owner_denied():
    owner = _make_user("li_owner")
    other = _make_user("li_other")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    response = _client(other).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 403


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_owner_can_analyze_draft_lesson():
    owner = _make_user("li_draft_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original="Old active transcript.")
    project.draft_data = {
        "metadata": {"dirty": True},
        "transcript_pages": [
            {
                "id": 1,
                "order": 0,
                "page_key": "draft-p1",
                "original_text": "Draft opening with objective.",
                "narration_text": _lesson_text(),
            }
        ],
    }
    project.save(update_fields=["draft_data", "updated_at"])

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is False
    assert "gradient descent" in response.data["summary"].lower()


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_staff_can_analyze():
    owner = _make_user("li_staff_owner")
    staff = _make_user("li_staff", is_staff=True)
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    response = _client(staff).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_empty_transcript_returns_clear_error():
    owner = _make_user("li_empty_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original="", narration="")

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 400
    assert "empty" in response.data["error"].lower()


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_heuristic_response_stable_and_complete():
    owner = _make_user("li_stable_owner")
    project = _make_project(owner)
    _add_page(
        project,
        order=0,
        key="p1",
        original="- Objective\n- Algorithm\n- Example",
        narration="Algorithm parameters and loss function.",
    )

    first = _client(owner).post(_analyze_url(project), {}, format="json")
    second = _client(owner).post(_analyze_url(project), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    for payload in (first.data, second.data):
        assert payload["summary"]
        assert payload["complexity"]["level"] in {"beginner", "intermediate", "advanced"}
        assert isinstance(payload["complexity"]["score"], int)
        assert payload["clarity_warnings"]
        assert payload["expanded_narration_suggestions"]
        suggestion = payload["expanded_narration_suggestions"][0]
        assert {
            "page_number",
            "page_key",
            "type",
            "title",
            "advice",
            "draft_narration",
            "copy_text",
            "generated_by",
            "ai_generated",
        }.issubset(suggestion.keys())
        assert suggestion["draft_narration"]
        assert suggestion["copy_text"] == suggestion["draft_narration"]
        assert suggestion["draft_narration"] != suggestion["advice"]
        assert suggestion["draft_narration"] != suggestion["title"]
        assert payload["suggested_tags"]
    assert first.data["summary"] == second.data["summary"]
    assert first.data["complexity"] == second.data["complexity"]


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_no_raw_storage_paths_exposed():
    owner = _make_user("li_paths_owner")
    project = _make_project(owner)
    page = _add_page(project, order=0, key="p1", original=_lesson_text())
    page.editor_document = {
        "scene": {
            "original_background_url": "/api/v1/media/storage_local/secret-slide.png",
            "custom_background_url": "C:/private/generated-media.png",
        }
    }
    page.save(update_fields=["editor_document", "updated_at"])

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    serialized = json.dumps(response.data)
    assert response.status_code == 200
    assert "storage_local" not in serialized
    assert "secret-slide" not in serialized
    assert "C:/private" not in serialized


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_get_returns_latest_report():
    owner = _make_user("li_latest_owner")
    project = _make_project(owner)
    page = _add_page(project, order=0, key="p1", original=_lesson_text())
    first = _client(owner).post(_analyze_url(project), {}, format="json")
    page.narration_text = _lesson_text() + " Finally, learners compare two learning-rate scenarios."
    page.save(update_fields=["narration_text", "updated_at"])
    second = _client(owner).post(_analyze_url(project), {}, format="json")

    latest = _client(owner).get(_latest_url(project))

    assert first.status_code == 200
    assert second.status_code == 200
    assert latest.status_code == 200
    assert latest.data["id"] == second.data["id"]
    assert latest.data["source_hash"] == second.data["source_hash"]
    assert latest.data["report_source_hash"] == second.data["source_hash"]
    assert latest.data["current_source_hash"] == second.data["source_hash"]
    assert latest.data["is_stale"] is False

    page.narration_text = _lesson_text() + " Finally, learners compare three learning-rate scenarios."
    page.save(update_fields=["narration_text", "updated_at"])
    stale = _client(owner).get(_latest_url(project))

    assert stale.status_code == 200
    assert stale.data["id"] == second.data["id"]
    assert stale.data["report_source_hash"] == second.data["source_hash"]
    assert stale.data["current_source_hash"] != second.data["source_hash"]
    assert stale.data["is_stale"] is True


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_get_without_report_exposes_current_hash_and_stale():
    owner = _make_user("li_empty_report_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    latest = _client(owner).get(_latest_url(project))

    assert latest.status_code == 200
    assert latest.data["status"] == "empty"
    assert latest.data["report_source_hash"] == ""
    assert latest.data["current_source_hash"]
    assert latest.data["is_stale"] is True


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_external_provider_not_called_by_default(monkeypatch):
    owner = _make_user("li_no_external_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    called = {"ollama": False, "paid": False}

    def fail_ollama(*args, **kwargs):
        called["ollama"] = True
        raise AssertionError("ollama should not be called")

    def fail_paid(*args, **kwargs):
        called["paid"] = True
        raise AssertionError("paid provider should not be called")

    monkeypatch.setattr("core.lesson_intelligence.OllamaLessonIntelligenceProvider.analyze_lesson", fail_ollama)
    monkeypatch.setattr("core.lesson_intelligence.PaidLessonIntelligenceProvider.analyze_lesson", fail_paid)

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert called == {"ollama": False, "paid": False}


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_source_hash_changes_when_transcript_changes():
    owner = _make_user("li_hash_owner")
    project = _make_project(owner)
    page = _add_page(project, order=0, key="p1", original=_lesson_text())

    first = _client(owner).post(_analyze_url(project), {}, format="json")
    page.narration_text = _lesson_text() + " Add an example about validation loss."
    page.save(update_fields=["narration_text", "updated_at"])
    second = _client(owner).post(_analyze_url(project), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["source_hash"] != second.data["source_hash"]


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_LESSON_INTELLIGENCE_BASE_URL="http://127.0.0.1:9",
    LESSON_INTELLIGENCE_TIMEOUT_SECONDS=0.5,
    INTELLIGENCE_CELERY_QUEUE="intelligence-test",
)
def test_post_analyze_returns_heuristic_and_queues_ollama_enhancement(monkeypatch):
    owner = _make_user("li_ollama_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is True
    assert response.data["enhancement_available"] is True
    assert response.data["enhancement_pending"] is True
    assert response.data["enhancement_status"] == "pending"
    assert response.data["enhancement_provider"] == "ollama"
    attempts = response.data["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] == "queued"
    assert dispatch_calls[0]["task_name"] == "worker.tasks.enhance_lesson_intelligence_report"
    assert dispatch_calls[0]["queue"] == "intelligence-test"

    report = LessonIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["queue"] == "intelligence-test"
    assert enhancement["task_id"] == "fake-intelligence-task"
    assert enhancement["queued_at"]

    latest = _client(owner).get(_latest_url(project))

    assert latest.status_code == 200
    assert latest.data["id"] == response.data["id"]
    assert latest.data["enhancement_pending"] is True
    assert latest.data["enhancement_status"] == "pending"


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_duplicate_lesson_analyze_does_not_enqueue_duplicate_for_same_source(monkeypatch):
    owner = _make_user("li_duplicate_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(owner).post(_analyze_url(project), {}, format="json")
    second = _client(owner).post(_analyze_url(project), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert len(dispatch_calls) == 1
    assert second.data["enhancement_pending"] is True


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_force_lesson_analyze_requeues_fresh_pending_enhancement(monkeypatch):
    owner = _make_user("li_force_requeue_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(owner).post(_analyze_url(project), {}, format="json")
    second = _client(owner).post(_analyze_url(project), {"force": True}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data["id"] != first.data["id"]
    assert len(dispatch_calls) == 2
    old_report = LessonIntelligenceReport.objects.get(pk=first.data["id"])
    assert old_report.metadata["progressive_enhancement"]["status"] == "failed"
    assert "superseded" in old_report.metadata["progressive_enhancement"]["error"]


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    INTELLIGENCE_ENHANCEMENT_STALE_SECONDS=60,
)
def test_stale_lesson_pending_enhancement_is_marked_failed(monkeypatch):
    owner = _make_user("li_stale_pending_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(owner).post(_analyze_url(project), {}, format="json")
    report = LessonIntelligenceReport.objects.get(pk=response.data["id"])
    metadata = dict(report.metadata)
    enhancement = dict(metadata["progressive_enhancement"])
    enhancement["queued_at"] = (timezone.now() - timedelta(seconds=120)).isoformat()
    metadata["progressive_enhancement"] = enhancement
    report.metadata = metadata
    report.save(update_fields=["metadata", "updated_at"])

    latest = _client(owner).get(_latest_url(project))

    assert latest.status_code == 200
    assert latest.data["id"] == response.data["id"]
    assert latest.data["enhancement_pending"] is False
    assert latest.data["enhancement_status"] == "failed"
    assert "stale timeout" in latest.data["enhancement_error_safe"]
    report.refresh_from_db()
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["failed_at"]
    assert enhancement["stale"] is True


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    INTELLIGENCE_ENHANCEMENT_STALE_SECONDS=60,
)
def test_stale_lesson_pending_reanalyze_enqueues_again(monkeypatch):
    owner = _make_user("li_stale_requeue_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(owner).post(_analyze_url(project), {}, format="json")
    old_report = LessonIntelligenceReport.objects.get(pk=first.data["id"])
    metadata = dict(old_report.metadata)
    enhancement = dict(metadata["progressive_enhancement"])
    enhancement["queued_at"] = (timezone.now() - timedelta(seconds=120)).isoformat()
    metadata["progressive_enhancement"] = enhancement
    old_report.metadata = metadata
    old_report.save(update_fields=["metadata", "updated_at"])

    second = _client(owner).post(_analyze_url(project), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.data["id"] != first.data["id"]
    assert second.data["enhancement_pending"] is True
    assert len(dispatch_calls) == 2
    old_report.refresh_from_db()
    assert old_report.metadata["progressive_enhancement"]["status"] == "failed"


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_lesson_stale_source_hash_allows_new_enhancement(monkeypatch):
    owner = _make_user("li_stale_new_owner")
    project = _make_project(owner)
    page = _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    first = _client(owner).post(_analyze_url(project), {}, format="json")
    page.narration_text = _lesson_text() + " Learners compare four update strategies."
    page.save(update_fields=["narration_text", "updated_at"])
    second = _client(owner).post(_analyze_url(project), {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data["id"] != second.data["id"]
    assert first.data["source_hash"] != second.data["source_hash"]
    assert len(dispatch_calls) == 2


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
)
def test_lesson_enhancement_task_start_sets_running(monkeypatch):
    owner = _make_user("li_task_running_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    from worker.tasks import _mark_lesson_intelligence_enhancement  # noqa: E402

    _mark_lesson_intelligence_enhancement(response.data["id"], "running", task_id="task-started")
    report = LessonIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]

    assert enhancement["status"] == "running"
    assert enhancement["task_id"] == "task-started"
    assert enhancement["started_at"]
    assert report.metadata["provider_chain_attempts"][0]["status"] == "running"


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_LESSON_INTELLIGENCE_BASE_URL="http://secret-ollama.local:11434",
    LESSON_INTELLIGENCE_TIMEOUT_SECONDS=120,
    INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS=20,
    LESSON_INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS=20,
)
def test_ollama_timeout_uses_sync_cap_and_falls_back(monkeypatch):
    owner = _make_user("li_ollama_cap_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    captured = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        raise URLError("timed out")

    monkeypatch.setattr("core.lesson_intelligence.urlopen", fake_urlopen)

    lesson_input = build_lesson_intelligence_input(project)
    analysis = analyze_with_provider_chain(lesson_input, chain=["ollama", "heuristic"])

    assert captured["timeout"] == 20
    assert analysis["provider"] == "heuristic"
    assert analysis["fallback_used"] is True
    attempts = analysis["metadata"]["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] in {"skipped", "failed"}
    serialized = json.dumps(analysis)
    assert "secret-ollama.local" not in serialized


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_LESSON_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    LESSON_INTELLIGENCE_TIMEOUT_SECONDS=8,
    INTELLIGENCE_SYNC_PROVIDER_TIMEOUT_CAP_SECONDS=20,
    INTELLIGENCE_BACKGROUND_PROVIDER_TIMEOUT_SECONDS=120,
)
def test_background_success_updates_report_to_ollama(monkeypatch):
    owner = _make_user("li_ollama_success_owner")
    project = _make_project(owner)
    _add_page(
        project,
        order=0,
        key="p1",
        original="- Objective\n- Example",
        narration="A short narration.",
    )
    captured = {}
    provider_payload = {
        "provider": "ollama",
        "lesson_summary": "Ollama generated lesson summary.",
        "short_description": "Ollama generated short description.",
        "complexity_level": "intermediate",
        "complexity_score": 61,
        "complexity_reasons": ["Uses model terminology."],
        "clarity_warnings": [{"type": "short_narration", "message": "Narration needs more explanation."}],
        "page_suggestions": [{"page_number": 1, "page_key": "p1", "type": "example", "suggestion": "Add an example."}],
        "expanded_narration_suggestions": [
            {
                "page_number": 1,
                "page_key": "p1",
                "type": "short_narration",
                "title": "Expand narration",
                "advice": "This slide needs more teaching context.",
                "draft_narration": "In this part, we explain the objective and connect it to a concrete example.",
                "copy_text": "In this part, we explain the objective and connect it to a concrete example.",
                "generated_by": "ollama",
                "ai_generated": True,
            }
        ],
        "suggested_tags": ["optimization"],
        "limitations": [],
    }

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["body"] = request.data.decode("utf-8")
        return _FakeOllamaResponse({"response": json.dumps(provider_payload)})

    monkeypatch.setattr("core.lesson_intelligence.urlopen", fake_urlopen)
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["enhancement_pending"] is True

    from worker.tasks import enhance_lesson_intelligence_report  # noqa: E402

    task_result = enhance_lesson_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(owner).get(_latest_url(project))

    assert task_result["status"] == "done"
    assert captured["timeout"] == 120
    assert latest.data["provider"] == "ollama"
    assert latest.data["fallback_used"] is False
    assert latest.data["summary"] == "Ollama generated lesson summary."
    assert latest.data["enhancement_pending"] is False
    assert latest.data["enhancement_status"] == "done"
    report = LessonIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["started_at"]
    assert enhancement["finished_at"]
    suggestion = latest.data["expanded_narration_suggestions"][0]
    assert suggestion["draft_narration"].startswith("In this part")
    assert suggestion["draft_narration"] != suggestion["advice"]
    assert latest.data["provider_chain_attempts"][0]["status"] == "success"
    assert "draft_narration" in captured["body"]


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_LESSON_INTELLIGENCE_BASE_URL="http://ollama.test:11434",
    LESSON_INTELLIGENCE_TIMEOUT_SECONDS=8,
)
def test_invalid_ollama_json_falls_back_to_heuristic(monkeypatch):
    owner = _make_user("li_ollama_invalid_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())
    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    def fake_urlopen(request, timeout):
        return _FakeOllamaResponse({"response": "not-json"})

    monkeypatch.setattr("core.lesson_intelligence.urlopen", fake_urlopen)

    response = _client(owner).post(_analyze_url(project), {}, format="json")
    from worker.tasks import enhance_lesson_intelligence_report  # noqa: E402

    task_result = enhance_lesson_intelligence_report.run(response.data["id"], response.data["source_hash"])
    latest = _client(owner).get(_latest_url(project))

    assert response.status_code == 200
    assert task_result["status"] == "failed"
    assert latest.data["provider"] == "heuristic"
    assert latest.data["fallback_used"] is True
    assert latest.data["enhancement_pending"] is False
    assert latest.data["enhancement_status"] == "failed"
    report = LessonIntelligenceReport.objects.get(pk=response.data["id"])
    enhancement = report.metadata["progressive_enhancement"]
    assert enhancement["started_at"]
    assert enhancement["finished_at"]
    assert enhancement["failed_at"]
    assert "ollama.test" not in json.dumps(latest.data)
    attempts = latest.data["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] == "failed"


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="openai,heuristic",
    LESSON_INTELLIGENCE_ALLOW_EXTERNAL=False,
)
def test_paid_provider_disabled_unless_external_allowed():
    owner = _make_user("li_paid_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is True
    attempts = response.data["metadata"]["provider_chain_attempts"]
    assert attempts[0]["provider"] == "openai"
    assert attempts[0]["status"] == "skipped"
    assert "disabled" in attempts[0]["error"]

    with pytest.raises(LessonIntelligenceProviderUnavailable, match="disabled"):
        PaidLessonIntelligenceProvider("openai").analyze_lesson({})

    with override_settings(LESSON_INTELLIGENCE_ALLOW_EXTERNAL=True):
        with pytest.raises(LessonIntelligenceProviderUnavailable, match="not implemented"):
            PaidLessonIntelligenceProvider("openai").analyze_lesson({})

    assert LessonIntelligenceReport.objects.filter(project=project, provider="heuristic").exists()


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_turkish_transcript_returns_turkish_user_facing_output():
    owner = _make_user("li_tr_owner")
    project = _make_project(owner, title="Veri Analizi Dersi")
    _add_page(project, order=0, key="p1", original=_turkish_lesson_text())

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert response.data["detected_language"] == "tr"
    assert response.data["output_language"] == "tr"
    assert "Bu ders" in response.data["summary"]
    serialized = json.dumps(response.data, ensure_ascii=False)
    assert "Belirgin örnek" in serialized or "Öneriler" in serialized or "danışma amaçlıdır" in serialized


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_turkish_expanded_narration_contains_turkish_draft_text():
    owner = _make_user("li_tr_draft_owner")
    project = _make_project(owner, title="Veri Analizi")
    _add_page(
        project,
        order=0,
        key="p1",
        original="- Veri\n- \u00d6rnek\n- Sonu\u00e7",
        narration="Bu ders k\u0131sad\u0131r.",
    )

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["output_language"] == "tr"
    suggestion = response.data["expanded_narration_suggestions"][0]
    assert suggestion["draft_narration"].startswith("Bu ")
    assert "madde" in suggestion["draft_narration"]
    assert suggestion["draft_narration"] != suggestion["advice"]
    assert suggestion["copy_text"] == suggestion["draft_narration"]


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_turkish_complexity_display_label_is_localized():
    owner = _make_user("li_tr_label_owner")
    project = _make_project(owner, title="Algoritma Dersi")
    _add_page(
        project,
        order=0,
        key="p1",
        original="Bu ders algoritma, veri yapısı, optimizasyon ve model doğrulama konularını anlatır.",
    )

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["complexity"]["level"] in {"beginner", "intermediate", "advanced"}
    assert response.data["complexity"]["display_label"] in {"başlangıç", "orta", "ileri"}


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_english_transcript_remains_english():
    owner = _make_user("li_en_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["output_language"] == "en"
    assert response.data["summary"].startswith("Gradient Descent Basics: This lesson covers")
    assert response.data["complexity"]["display_label"] in {"beginner", "intermediate", "advanced"}


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_output_language_override_can_force_english_for_turkish_input():
    owner = _make_user("li_tr_force_en_owner")
    project = _make_project(owner, title="Türkçe Ders")
    _add_page(project, order=0, key="p1", original=_turkish_lesson_text())

    response = _client(owner).post(_analyze_url(project), {"output_language": "en"}, format="json")

    assert response.status_code == 200
    assert response.data["detected_language"] == "tr"
    assert response.data["output_language"] == "en"
    assert "This lesson covers" in response.data["summary"]
    assert response.data["complexity"]["display_label"] in {"beginner", "intermediate", "advanced"}


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic",
    LESSON_INTELLIGENCE_MAX_INPUT_CHARS=900,
)
def test_long_lesson_does_not_fail_and_reports_truncation_limitation():
    owner = _make_user("li_long_owner")
    project = _make_project(owner, title="Long Systems Lesson")
    long_text = (
        "Introduction: this lesson explains architecture, database design, validation, optimization, and examples. "
        * 45
    )
    for index in range(8):
        _add_page(project, order=index, key=f"p{index}", original=long_text)

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert response.data["metadata"]["input_truncated"] is True
    assert any("shortened" in str(item).lower() for item in response.data["limitations"])


@override_settings(
    LESSON_INTELLIGENCE_ENABLED=True,
    LESSON_INTELLIGENCE_PROVIDER_CHAIN="ollama,heuristic",
    OLLAMA_LESSON_INTELLIGENCE_BASE_URL="http://127.0.0.1:9",
    LESSON_INTELLIGENCE_TIMEOUT_SECONDS=0.5,
)
def test_ollama_fallback_preserves_turkish_output_language(monkeypatch):
    owner = _make_user("li_tr_ollama_owner")
    project = _make_project(owner, title="Türkçe Fallback Dersi")
    _add_page(project, order=0, key="p1", original=_turkish_lesson_text())

    dispatch_calls = []
    monkeypatch.setattr("core.views._dispatch_celery_task", _fake_dispatch(dispatch_calls))

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is True
    assert response.data["output_language"] == "tr"
    assert "Bu ders" in response.data["summary"]


@override_settings(LESSON_INTELLIGENCE_ENABLED=True, LESSON_INTELLIGENCE_PROVIDER_CHAIN="heuristic")
def test_lesson_language_json_keys_remain_stable():
    owner = _make_user("li_json_keys_owner")
    project = _make_project(owner, title="Türkçe Anahtar Testi")
    _add_page(project, order=0, key="p1", original=_turkish_lesson_text())

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert {"level", "display_label", "score", "reasons"}.issubset(response.data["complexity"].keys())
    assert "clarity_warnings" in response.data
    assert "page_suggestions" in response.data
    assert "expanded_narration_suggestions" in response.data
