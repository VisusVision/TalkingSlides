from __future__ import annotations

import json
import os
import sys
import uuid
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
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import tts_llm_suggestions, views  # noqa: E402
from core.models import Project, TranscriptPage, UserProfile, default_project_tts_settings  # noqa: E402
from tests.integration.schema_skip import skip_if_column_missing  # noqa: E402


def _skip_if_tts_settings_missing() -> None:
    skip_if_column_missing("core_project", "tts_settings")


def _make_user(prefix: str = "tts_l1_user") -> User:
    suffix = uuid.uuid4().hex[:8]
    return User.objects.create_user(username=f"{prefix}_{suffix}", password="pass")


def _make_teacher(prefix: str = "tts_l1_teacher") -> User:
    user = _make_user(prefix)
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _post_suggestions(payload: dict, user: User | None = None):
    request = APIRequestFactory().post(
        "/api/v1/tts/pronunciation-suggestions/",
        payload,
        format="json",
    )
    if user is not None:
        force_authenticate(request, user=user)
    return views.TTSPronunciationSuggestionsView.as_view()(request)


@pytest.mark.django_db
def test_disabled_endpoint_returns_fail_open_without_provider_call(monkeypatch):
    user = _make_user()

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("provider should not be called while suggestions are disabled")

    monkeypatch.setattr(tts_llm_suggestions, "_call_ollama", fail_provider)

    with override_settings(TTS_LLM_SUGGESTIONS_ENABLED=False):
        response = _post_suggestions(
            {
                "language": "tr",
                "terms": ["HyperBeam", "LangChain"],
                "context": "HyperBeam ve LangChain anlatimi.",
            },
            user=user,
        )

    assert response.status_code == 200
    assert response.data["enabled"] is False
    assert response.data["suggestions"] == []
    assert response.data["fallback_used"] is True
    assert response.data["provider"] == ""
    assert "LLM pronunciation suggestions are disabled." in response.data["warnings"]


@pytest.mark.django_db
def test_unauthenticated_request_is_rejected():
    response = _post_suggestions(
        {
            "language": "tr",
            "terms": ["HyperBeam"],
            "context": "HyperBeam anlatimi.",
        }
    )

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_request_caps_terms_and_context(monkeypatch):
    user = _make_user()
    captured: dict[str, str] = {}

    def fake_provider(_config, prompt):
        captured["prompt"] = prompt
        return json.dumps(
            {
                "suggestions": [
                    {
                        "term": "TermOne",
                        "suggested_spoken": "torm van",
                        "category": "mixed_word",
                        "confidence": "medium",
                        "reason": "bounded request",
                    },
                    {
                        "term": "TermTwo",
                        "suggested_spoken": "torm tu",
                        "category": "mixed_word",
                        "confidence": "medium",
                        "reason": "bounded request",
                    },
                ]
            }
        )

    monkeypatch.setattr(tts_llm_suggestions, "_call_ollama", fake_provider)

    with override_settings(
        TTS_LLM_SUGGESTIONS_ENABLED=True,
        TTS_LLM_PROVIDER="ollama",
        TTS_LLM_MAX_TERMS=2,
        TTS_LLM_CONTEXT_MAX_CHARS=8,
    ):
        response = _post_suggestions(
            {
                "language": "tr",
                "terms": ["TermOne", "TermTwo", "TermThree"],
                "context": "abcdefghijk",
            },
            user=user,
        )

    assert response.status_code == 200
    assert response.data["fallback_used"] is False
    assert [item["term"] for item in response.data["suggestions"]] == ["TermOne", "TermTwo"]
    assert "terms_truncated_to_2" in response.data["warnings"]
    assert "context_truncated_to_8" in response.data["warnings"]
    assert "TermThree" not in captured["prompt"]
    assert "abcdefghi" not in captured["prompt"]


@pytest.mark.django_db
def test_malformed_provider_json_fails_safely(monkeypatch):
    user = _make_user()
    monkeypatch.setattr(tts_llm_suggestions, "_call_ollama", lambda *_args, **_kwargs: "not json")

    with override_settings(TTS_LLM_SUGGESTIONS_ENABLED=True, TTS_LLM_PROVIDER="ollama"):
        response = _post_suggestions({"language": "tr", "terms": ["HyperBeam"], "context": ""}, user=user)

    assert response.status_code == 200
    assert response.data["enabled"] is True
    assert response.data["suggestions"] == []
    assert response.data["fallback_used"] is True
    assert "LLM pronunciation suggestion provider returned malformed JSON." in response.data["warnings"]


@pytest.mark.django_db
def test_timeout_and_provider_errors_fail_safely(monkeypatch):
    user = _make_user()

    def timeout_provider(*_args, **_kwargs):
        raise TimeoutError("slow provider")

    monkeypatch.setattr(tts_llm_suggestions, "_call_ollama", timeout_provider)
    with override_settings(TTS_LLM_SUGGESTIONS_ENABLED=True, TTS_LLM_PROVIDER="ollama"):
        timeout_response = _post_suggestions(
            {"language": "tr", "terms": ["HyperBeam"], "context": ""},
            user=user,
        )

    assert timeout_response.status_code == 200
    assert timeout_response.data["fallback_used"] is True
    assert "LLM pronunciation suggestion provider timed out." in timeout_response.data["warnings"]

    def failing_provider(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(tts_llm_suggestions, "_call_ollama", failing_provider)
    with override_settings(TTS_LLM_SUGGESTIONS_ENABLED=True, TTS_LLM_PROVIDER="ollama"):
        error_response = _post_suggestions(
            {"language": "tr", "terms": ["HyperBeam"], "context": ""},
            user=user,
        )

    assert error_response.status_code == 200
    assert error_response.data["fallback_used"] is True
    assert "LLM pronunciation suggestion provider unavailable." in error_response.data["warnings"]


@pytest.mark.django_db
def test_successful_provider_response_is_sanitized(monkeypatch):
    user = _make_user()
    long_reason = "English brand or technical term in Turkish text. " * 20
    long_spoken = "haypir " * 40

    def fake_provider(*_args, **_kwargs):
        return json.dumps(
            {
                "suggestions": [
                    {
                        "term": "HyperBeam",
                        "suggested_spoken": "  haypir\nbiim  ",
                        "category": "mixed_word",
                        "confidence": "medium",
                        "reason": long_reason,
                    },
                    {
                        "term": "LangChain",
                        "suggested_spoken": long_spoken,
                        "category": "technical",
                        "confidence": "high",
                        "reason": "Known technical framework.",
                    },
                    {
                        "term": "OtherTerm",
                        "suggested_spoken": "other",
                        "category": "mixed_word",
                        "confidence": "high",
                        "reason": "not requested",
                    },
                    {
                        "term": "BadCategory",
                        "suggested_spoken": "bad",
                        "category": "other",
                        "confidence": "high",
                        "reason": "invalid",
                    },
                    {
                        "term": "Unsafe",
                        "suggested_spoken": "https://example.com",
                        "category": "mixed_word",
                        "confidence": "high",
                        "reason": "unsafe",
                    },
                ]
            }
        )

    monkeypatch.setattr(tts_llm_suggestions, "_call_ollama", fake_provider)

    with override_settings(TTS_LLM_SUGGESTIONS_ENABLED=True, TTS_LLM_PROVIDER="ollama"):
        response = _post_suggestions(
            {
                "language": "tr",
                "terms": ["HyperBeam", "LangChain", "BadCategory", "Unsafe"],
                "context": "HyperBeam ve LangChain anlatimi.",
            },
            user=user,
        )

    assert response.status_code == 200
    assert response.data["fallback_used"] is False
    assert response.data["provider"] == "ollama"
    suggestions = response.data["suggestions"]
    assert [item["term"] for item in suggestions] == ["HyperBeam", "LangChain"]
    assert suggestions[0]["suggested_spoken"] == "haypir biim"
    assert suggestions[0]["category"] == "mixed_word"
    assert suggestions[0]["confidence"] == "medium"
    assert len(suggestions[0]["reason"]) <= 240
    assert len(suggestions[1]["suggested_spoken"]) <= 160
    assert "invalid_provider_suggestions_dropped" in response.data["warnings"]


@pytest.mark.django_db
def test_endpoint_does_not_mutate_project_tts_settings_or_transcript(monkeypatch):
    _skip_if_tts_settings_missing()
    teacher = _make_teacher()
    initial_tts_settings = {
        **default_project_tts_settings(),
        "overrides": {
            "technical": {"GPU": "ci pi yu"},
            "abbreviation": {},
            "mixed_word": {},
        },
    }
    project = Project.objects.create(
        title="No Mutation",
        user=teacher,
        tts_settings=initial_tts_settings,
    )
    page = TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="page-1",
        original_text="HyperBeam anlatimi.",
        narration_text="HyperBeam anlatimi.",
    )

    monkeypatch.setattr(
        tts_llm_suggestions,
        "_call_ollama",
        lambda *_args, **_kwargs: json.dumps(
            {
                "suggestions": [
                    {
                        "term": "HyperBeam",
                        "suggested_spoken": "haypir biim",
                        "category": "mixed_word",
                        "confidence": "medium",
                        "reason": "English brand in Turkish context.",
                    }
                ]
            }
        ),
    )

    with override_settings(TTS_LLM_SUGGESTIONS_ENABLED=True, TTS_LLM_PROVIDER="ollama"):
        response = _post_suggestions(
            {
                "language": "tr",
                "terms": ["HyperBeam"],
                "context": "HyperBeam anlatimi.",
                "project_id": project.id,
            },
            user=teacher,
        )

    assert response.status_code == 200
    assert response.data["suggestions"][0]["suggested_spoken"] == "haypir biim"

    project.refresh_from_db()
    page.refresh_from_db()
    assert project.tts_settings == initial_tts_settings
    assert page.original_text == "HyperBeam anlatimi."
    assert page.narration_text == "HyperBeam anlatimi."


def test_render_and_synthesize_paths_do_not_import_llm_helper():
    tts_service_main = REPO_ROOT / "services" / "tts_service" / "main.py"
    tts_client = REPO_ROOT / "services" / "scripts" / "tts_client.py"

    for path in (tts_service_main, tts_client):
        source = path.read_text(encoding="utf-8").lower()
        assert "tts_llm_suggestions" not in source
        assert "pronunciation-suggestions" not in source
        assert "ollama" not in source
