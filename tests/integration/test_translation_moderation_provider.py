# pyright: reportMissingImports=false

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

from core.models import Project, TranscriptPage, UserProfile  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402
from worker.ai_agents.providers.translation_provider import (  # noqa: E402
    TranslationModerationProvider,
    TranslationResult,
)
from worker.ai_agents.text_moderation import TextModerationAgent  # noqa: E402


class FakeTranslationProvider:
    provider_name = "fake_translation"

    def __init__(self, result: TranslationResult, *, enabled: bool = True, raises: bool = False):
        self.result = result
        self.enabled = enabled
        self.raises = raises
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return self.enabled

    def translate_text(self, text: str) -> TranslationResult:
        self.calls.append(text)
        if self.raises:
            raise RuntimeError("translator exploded")
        return self.result


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Translation moderation lesson", description: str = "") -> Project:
    return Project.objects.create(
        title=title,
        description=description,
        user=_make_teacher(username),
        status="ready",
    )


def _add_page(project: Project, text: str, page_key: str = "slide-1") -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key=page_key,
        original_text=text,
        narration_text="",
    )


def _translation_result(text: str, *, success: bool = True, provider: str = "fake") -> TranslationResult:
    return TranslationResult(
        success=success,
        translated_text=text,
        provider=provider,
        source_language="auto",
        target_language="en",
        error_message="" if success else "unavailable",
        metadata={},
    )


@pytest.mark.django_db
def test_translation_disabled_does_not_call_provider_and_keeps_original_result():
    fake = FakeTranslationProvider(_translation_result("I will kill you."), enabled=False)
    project = _make_project(
        "translation_disabled_teacher",
        title="Clean lesson",
        description="A calm lesson about photosynthesis.",
    )

    result = TextModerationAgent(translation_provider=fake).scan_project(project)

    assert result.decision == "allow"
    assert result.metadata["translation_enabled"] is False
    assert result.metadata["translation_called"] is False
    assert fake.calls == []


@pytest.mark.django_db
def test_translation_provider_unavailable_keeps_original_allow_result():
    fake = FakeTranslationProvider(_translation_result("", success=False))
    project = _make_project(
        "translation_unavailable_teacher",
        title="Clean lesson",
        description="A calm lesson about ecosystems.",
    )

    result = TextModerationAgent(translation_provider=fake).scan_project(project)

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["translation_called"] is True
    assert fake.calls


def test_invalid_libretranslate_response_returns_failure_without_crash(monkeypatch):
    monkeypatch.setenv("TRANSLATION_MODERATION_ENABLED", "1")
    monkeypatch.setenv("TRANSLATION_MODERATION_PROVIDER", "libretranslate")
    monkeypatch.setenv("TRANSLATION_MODERATION_BASE_URL", "http://libretranslate.test")
    monkeypatch.setattr(
        "worker.ai_agents.providers.translation_provider.requests.post",
        lambda *args, **kwargs: FakeResponse({"unexpected": "shape"}),
    )

    result = TranslationModerationProvider().translate_text("Merhaba")

    assert result.success is False
    assert result.provider == "libretranslate"
    assert result.metadata["reason"] == "translation_failed"


@pytest.mark.django_db
def test_local_block_remains_block_and_translation_is_not_called():
    fake = FakeTranslationProvider(_translation_result("A harmless translated lesson."))
    project = _make_project(
        "translation_local_block_teacher",
        description="I will kill you tomorrow.",
    )

    result = TextModerationAgent(translation_provider=fake).scan_project(project)

    assert result.decision == "block"
    assert fake.calls == []


@pytest.mark.django_db
def test_local_allow_translation_flags_unsafe_as_admin_review():
    fake = FakeTranslationProvider(_translation_result("I will kill you tomorrow."))
    project = _make_project(
        "translation_secondary_signal_teacher",
        title="Foreign language lesson",
        description="Yerel kurallar bu cumleyi temiz goruyor.",
    )

    result = TextModerationAgent(translation_provider=fake).scan_project(project)

    assert result.decision == "needs_admin_review"
    assert result.provider == "local_rules+translation_moderation:fake"
    assert result.findings[0].category == "violence"
    assert result.findings[0].decision == "needs_admin_review"
    assert result.findings[0].confidence < 0.85
    assert "secondary English translation" in result.findings[0].user_message


@pytest.mark.django_db
def test_turkish_educational_content_remains_allow_when_translation_allows():
    fake = FakeTranslationProvider(_translation_result("In Ottoman history, wars and deaths occurred."))
    project = _make_project("translation_turkish_education_teacher", title="Tarih dersi")
    _add_page(project, "Osmanli tarihinde savaslar ve olumler yasanmistir.")

    result = TextModerationAgent(translation_provider=fake).scan_project(project)

    assert result.decision == "allow"
    assert result.findings == []
    assert fake.calls


@pytest.mark.django_db
def test_translation_provider_not_called_for_empty_project_text():
    fake = FakeTranslationProvider(_translation_result("I will kill you."))
    project = _make_project("translation_empty_teacher", title="", description="")

    result = TextModerationAgent(translation_provider=fake).scan_project(project)

    assert result.decision == "allow"
    assert result.metadata["translation_called"] is False
    assert fake.calls == []


def test_libretranslate_provider_uses_local_endpoint_without_paid_api(monkeypatch):
    requests_seen = []

    def fake_post(url, *, json, timeout):
        requests_seen.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(
            {
                "translatedText": "I will kill you tomorrow.",
                "detectedLanguage": {"language": "tr", "confidence": 0.99},
            }
        )

    monkeypatch.setenv("TRANSLATION_MODERATION_ENABLED", "1")
    monkeypatch.setenv("TRANSLATION_MODERATION_PROVIDER", "libretranslate")
    monkeypatch.setenv("TRANSLATION_MODERATION_BASE_URL", "http://libretranslate:5000")
    monkeypatch.setenv("TRANSLATION_MODERATION_TIMEOUT_SECONDS", "3")
    monkeypatch.setattr("worker.ai_agents.providers.translation_provider.requests.post", fake_post)

    result = TranslationModerationProvider().translate_text("Seni oldurecegim.")

    assert result.success is True
    assert result.translated_text == "I will kill you tomorrow."
    assert result.source_language == "tr"
    assert requests_seen == [
        {
            "url": "http://libretranslate:5000/translate",
            "json": {
                "q": "Seni oldurecegim.",
                "source": "auto",
                "target": "en",
                "format": "text",
            },
            "timeout": 3.0,
        }
    ]


@pytest.mark.django_db
def test_orchestrator_persists_translation_secondary_review(monkeypatch):
    fake = FakeTranslationProvider(_translation_result("I will kill you tomorrow."))
    monkeypatch.setattr(
        "worker.ai_agents.text_moderation.TranslationModerationProvider",
        lambda: fake,
    )
    project = _make_project(
        "translation_orchestrator_teacher",
        description="Yerel kurallar bu cumleyi temiz goruyor.",
    )

    result = ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert result["final_decision"] == "needs_admin_review"
    assert project.moderation_status == "needs_admin_review"
    assert fake.calls
