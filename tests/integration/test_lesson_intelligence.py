import json
import os
import sys
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.lesson_intelligence import (  # noqa: E402
    LessonIntelligenceProviderUnavailable,
    PaidLessonIntelligenceProvider,
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


def _analyze_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/intelligence/analyze/"


def _latest_url(project: Project) -> str:
    return f"/api/v1/projects/{project.id}/intelligence/"


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
)
def test_provider_chain_falls_back_when_ollama_unavailable():
    owner = _make_user("li_ollama_owner")
    project = _make_project(owner)
    _add_page(project, order=0, key="p1", original=_lesson_text())

    response = _client(owner).post(_analyze_url(project), {}, format="json")

    assert response.status_code == 200
    assert response.data["provider"] == "heuristic"
    assert response.data["fallback_used"] is True
    attempts = response.data["metadata"]["provider_chain_attempts"]
    assert attempts[0]["provider"] == "ollama"
    assert attempts[0]["status"] in {"skipped", "failed"}


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
