# pyright: reportMissingImports=false

import json
import os
import sys
from io import StringIO
from pathlib import Path

import django
import pytest
import requests

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
from django.core.management import call_command  # noqa: E402

from ai_agents.models import AgentFinding  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402
from worker.ai_agents.providers.text_safety_provider import AzureContentSafetyTextProvider  # noqa: E402
from worker.ai_agents.schemas import FindingLocation  # noqa: E402
from worker.ai_agents.text_moderation import TextModerationAgent  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


class _FailingOllamaProvider:
    provider_name = "ollama"

    def is_enabled(self):
        return True

    def review_text(self, *_args, **_kwargs):
        raise AssertionError("Ollama should not run when Azure returns a clear result.")


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Azure text safety lesson", description: str = "") -> Project:
    return Project.objects.create(
        title=title,
        description=description,
        user=_make_teacher(username),
        status="ready",
    )


def _enable_azure_text(settings) -> None:
    settings.TEXT_SAFETY_PROVIDER = "azure_content_safety"
    settings.TEXT_SAFETY_CLASSIFIER_ENABLED = True
    settings.TEXT_SAFETY_TIMEOUT_SECONDS = 20
    settings.TEXT_SAFETY_CATEGORIES = "sexual,violence,self_harm,hate"
    settings.TEXT_SAFETY_BLOCK_SEVERITY = 4
    settings.TEXT_SAFETY_FALLBACK_PROVIDER = "local_rules"
    settings.AZURE_CONTENT_SAFETY_ENABLED = True
    settings.AZURE_CONTENT_SAFETY_ENDPOINT = "https://example.cognitiveservices.azure.com"
    settings.AZURE_CONTENT_SAFETY_KEY = "test-secret-key"
    settings.AZURE_CONTENT_SAFETY_API_VERSION = "2024-09-01"


def _disable_azure_text(settings) -> None:
    settings.TEXT_SAFETY_PROVIDER = "local_rules"
    settings.TEXT_SAFETY_CLASSIFIER_ENABLED = False


def _mock_azure_text(monkeypatch, payload: dict, calls: list | None = None) -> None:
    def fake_post(url, *, params, headers, json, timeout):
        if calls is not None:
            calls.append(
                {
                    "url": url,
                    "params": params,
                    "json": json,
                    "timeout": timeout,
                    "key_configured": bool(headers.get("Ocp-Apim-Subscription-Key")),
                }
            )
        return _FakeResponse(payload)

    monkeypatch.setattr("worker.ai_agents.providers.text_safety_provider.requests.post", fake_post)


@pytest.mark.django_db
def test_azure_text_safe_response_allows_without_ollama(settings, monkeypatch):
    _enable_azure_text(settings)
    calls = []
    _mock_azure_text(
        monkeypatch,
        {
            "categoriesAnalysis": [
                {"category": "Sexual", "severity": 0},
                {"category": "Violence", "severity": 0},
                {"category": "SelfHarm", "severity": 0},
                {"category": "Hate", "severity": 0},
            ]
        },
        calls,
    )
    project = _make_project(
        "azure_text_safe_teacher",
        title="History lesson about war crimes",
        description="This educational history lesson discusses genocide and executions during the war.",
    )

    result = TextModerationAgent(ollama_provider=_FailingOllamaProvider()).scan_project(project)

    assert result.provider == "azure_content_safety"
    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["ollama_called"] is False
    assert calls
    assert all(call["url"].endswith("/contentsafety/text:analyze") for call in calls)
    assert all(call["key_configured"] for call in calls)
    assert "test-secret-key" not in json.dumps(result.metadata)


@pytest.mark.django_db
def test_azure_text_unsafe_response_creates_text_finding_and_blocks(settings, monkeypatch):
    _enable_azure_text(settings)
    _mock_azure_text(monkeypatch, {"categoriesAnalysis": [{"category": "Violence", "severity": 4}]})
    project = _make_project("azure_text_unsafe_teacher", title="", description="Unsafe text fixture.")

    result = ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"
    assert finding.provider == "azure_content_safety"
    assert finding.content_type == "text"
    assert finding.category == "violence"
    assert finding.decision == "block"
    assert project.moderation_summary["findings"][0]["content_type"] == "text"
    assert project.moderation_summary["findings"][0]["source_kind"] == "transcript_text"
    assert "visual_issues" not in project.moderation_summary


@pytest.mark.django_db
def test_azure_missing_key_falls_back_to_local_rules_allow(settings):
    _enable_azure_text(settings)
    settings.AZURE_CONTENT_SAFETY_KEY = ""
    project = _make_project("azure_text_missing_key_teacher", description="A calm lesson about cells.")

    result = TextModerationAgent().scan_project(project)

    assert result.decision == "allow"
    assert result.provider == "azure_content_safety+local_rules"
    assert result.findings == []
    assert result.metadata["provider_unavailable"] is True
    assert result.metadata["fallback_provider"] == "local_rules"
    assert result.metadata["provider_errors"][0]["reason"] == "azure_content_safety_missing_config"


@pytest.mark.django_db
def test_azure_timeout_falls_back_to_local_rules(settings, monkeypatch):
    _enable_azure_text(settings)

    def raise_timeout(*_args, **_kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr("worker.ai_agents.providers.text_safety_provider.requests.post", raise_timeout)
    project = _make_project("azure_text_timeout_teacher", description="A calm lesson about ecosystems.")

    result = TextModerationAgent().scan_project(project)

    assert result.decision == "allow"
    assert result.provider == "azure_content_safety+local_rules"
    assert result.metadata["provider_unavailable"] is True
    assert result.metadata["provider_errors"][0]["reason"] == "azure_content_safety_timeout"


@pytest.mark.django_db
def test_local_fallback_catches_unsafe_text_when_azure_unavailable(settings):
    _enable_azure_text(settings)
    settings.AZURE_CONTENT_SAFETY_KEY = ""
    project = _make_project("azure_text_fallback_block_teacher", description="I will kill you tomorrow.")

    result = TextModerationAgent().scan_project(project)

    assert result.decision == "block"
    assert result.provider == "azure_content_safety+local_rules"
    assert result.findings[0].category == "violence"
    assert result.findings[0].decision == "block"
    assert result.metadata["provider_unavailable"] is True


def test_provider_unavailable_metadata_does_not_use_unsafe_wording(settings):
    _enable_azure_text(settings)
    settings.AZURE_CONTENT_SAFETY_KEY = ""

    result = AzureContentSafetyTextProvider().review_text(
        "A calm lesson.",
        location=FindingLocation(project_id=1, field_name="description"),
    )

    rendered = json.dumps(result.model_dump(), sort_keys=True).lower()
    assert result.metadata["provider_error"] is True
    assert result.metadata["reason"] == "azure_content_safety_missing_config"
    assert result.findings == []
    assert "unsafe visual" not in rendered
    assert "visual safety" not in rendered


@pytest.mark.django_db
def test_local_rules_behavior_remains_when_text_provider_is_local(settings, monkeypatch):
    _disable_azure_text(settings)

    def fail_post(*_args, **_kwargs):
        raise AssertionError("Azure text safety should not be called for local_rules provider.")

    monkeypatch.setattr("worker.ai_agents.providers.text_safety_provider.requests.post", fail_post)
    project = _make_project("azure_text_local_rules_teacher", description="I will kill you tomorrow.")

    result = TextModerationAgent().scan_project(project)

    assert result.provider == "local_rules"
    assert result.decision == "block"
    assert result.findings[0].category == "violence"


def test_text_diagnostics_do_not_print_azure_content_safety_key(settings):
    _enable_azure_text(settings)
    settings.AZURE_CONTENT_SAFETY_KEY = "super-secret-key-value"

    stdout = StringIO()
    call_command("moderation_system_status", json=True, stdout=stdout)
    rendered_json = stdout.getvalue()
    stdout = StringIO()
    call_command("moderation_system_status", stdout=stdout)
    rendered_text = stdout.getvalue()

    assert "super-secret-key-value" not in rendered_json
    assert "super-secret-key-value" not in rendered_text
