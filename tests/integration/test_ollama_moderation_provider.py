# pyright: reportMissingImports=false

import json
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

from core.models import Project, UserProfile  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402
from worker.ai_agents.providers.ollama_provider import OllamaProvider  # noqa: E402
from worker.ai_agents.schemas import AgentFindingSchema, FindingLocation  # noqa: E402
from worker.ai_agents.text_moderation import TextModerationAgent  # noqa: E402


class FakeResponse:
    def __init__(self, response_text: str, *, status_code: int = 200):
        self.response_text = response_text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"response": self.response_text}


def _enable_ollama(monkeypatch):
    monkeypatch.setenv("AI_AGENTS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("AI_AGENTS_OLLAMA_BASE_URL", "http://ollama.test")
    monkeypatch.setenv("AI_AGENTS_TEXT_MODEL", "qwen-test")
    monkeypatch.setenv("AI_AGENTS_LLM_TIMEOUT_SECONDS", "0.1")


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Ollama moderation lesson", description: str = "") -> Project:
    return Project.objects.create(
        title=title,
        description=description,
        user=_make_teacher(username),
        status="ready",
    )


@pytest.mark.django_db
def test_ollama_disabled_means_no_network_call(monkeypatch):
    monkeypatch.setenv("AI_AGENTS_LOCAL_LLM_ENABLED", "0")

    def fail_post(*args, **kwargs):
        raise AssertionError("Ollama should not be called when disabled.")

    monkeypatch.setattr("worker.ai_agents.providers.ollama_provider.requests.post", fail_post)
    project = _make_project(
        "ollama_disabled_teacher",
        title="History lesson about war crimes",
        description="This educational history lesson discusses genocide and executions during the war.",
    )

    result = ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert result["status"] == "done"
    assert project.moderation_status == "needs_admin_review"


@pytest.mark.django_db
def test_ollama_unavailable_does_not_block(monkeypatch):
    _enable_ollama(monkeypatch)

    def unavailable(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("worker.ai_agents.providers.ollama_provider.requests.post", unavailable)
    project = _make_project(
        "ollama_unavailable_teacher",
        title="History lesson about war crimes",
        description="This educational history lesson discusses genocide and executions during the war.",
    )

    ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert project.moderation_status == "needs_admin_review"


@pytest.mark.django_db
def test_ollama_invalid_json_does_not_block(monkeypatch):
    _enable_ollama(monkeypatch)
    monkeypatch.setattr(
        "worker.ai_agents.providers.ollama_provider.requests.post",
        lambda *args, **kwargs: FakeResponse("not json"),
    )
    project = _make_project(
        "ollama_invalid_json_teacher",
        title="History lesson about war crimes",
        description="This educational history lesson discusses genocide and executions during the war.",
    )

    ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert project.moderation_status == "needs_admin_review"


def test_valid_ollama_json_response_is_parsed(monkeypatch):
    _enable_ollama(monkeypatch)
    payload = {
        "decision": "needs_admin_review",
        "confidence": 0.66,
        "findings": [
            {
                "category": "violence",
                "severity": "medium",
                "confidence": 0.66,
                "decision": "needs_admin_review",
                "user_message": "This may need human review.",
                "admin_message": "Educational violence context is ambiguous.",
                "evidence_excerpt": "genocide and executions",
            }
        ],
    }
    monkeypatch.setattr(
        "worker.ai_agents.providers.ollama_provider.requests.post",
        lambda *args, **kwargs: FakeResponse(json.dumps(payload)),
    )

    result = OllamaProvider().review_text(
        "This educational history lesson discusses genocide and executions.",
        FindingLocation(project_id=123, field_name="description"),
    )

    assert result.decision == "needs_admin_review"
    assert result.confidence == 0.66
    assert result.provider == "ollama"
    assert result.findings[0].category == "violence"
    assert result.findings[0].location.project_id == 123


@pytest.mark.django_db
def test_high_confidence_local_block_remains_block_even_if_ollama_says_allow(monkeypatch):
    _enable_ollama(monkeypatch)

    def fail_post(*args, **kwargs):
        raise AssertionError("Ollama should not be called for high-confidence local blocks.")

    monkeypatch.setattr("worker.ai_agents.providers.ollama_provider.requests.post", fail_post)
    project = _make_project("ollama_local_block_teacher", description="I will kill you tomorrow.")

    ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_ambiguous_local_result_can_become_needs_admin_review(monkeypatch):
    _enable_ollama(monkeypatch)
    payload = {
        "decision": "needs_admin_review",
        "confidence": 0.7,
        "findings": [
            {
                "category": "unknown",
                "severity": "medium",
                "confidence": 0.7,
                "decision": "needs_admin_review",
                "user_message": "This should be reviewed by an admin.",
                "admin_message": "Ollama saw ambiguous context.",
                "evidence_excerpt": "ambiguous wording",
            }
        ],
    }
    monkeypatch.setattr(
        "worker.ai_agents.providers.ollama_provider.requests.post",
        lambda *args, **kwargs: FakeResponse(json.dumps(payload)),
    )

    class WarningProvider:
        provider_name = "warning_stub"

        def scan_text(self, text, location):
            if not str(text or "").strip():
                return []
            return [
                AgentFindingSchema(
                    category="unknown",
                    severity="low",
                    confidence=0.4,
                    decision="warn",
                    location=location,
                    user_message="Warning.",
                    admin_message="Stub warning.",
                )
            ]

    project = _make_project("ollama_ambiguous_teacher", title="Ambiguous lesson")

    result = TextModerationAgent(provider=WarningProvider()).scan_project(project)

    assert result.decision == "needs_admin_review"
    assert result.metadata["ollama_called"] is True
    assert any(finding.decision == "needs_admin_review" for finding in result.findings)
