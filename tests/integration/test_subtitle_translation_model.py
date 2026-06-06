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
for path in [API_ROOT, SERVICES_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.db import IntegrityError, transaction  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import subtitle_translation as subtitle_translation_module  # noqa: E402
from core import views  # noqa: E402
from core.models import Job, Project, TranslatedSubtitleTrack, TranscriptPage, UserProfile  # noqa: E402
from core.subtitle_translation import (  # noqa: E402
    ApiSubtitleTranslationProvider,
    ArgosSubtitleTranslationProvider,
    AutoFallbackSubtitleTranslationProvider,
    LibreTranslateSubtitleTranslationProvider,
    MockSubtitleTranslationProvider,
    OllamaSubtitleTranslationProvider,
    SubtitleProviderUnavailable,
    SubtitleTranslationError,
    TranslationCue,
    generate_translated_subtitle_track,
    validate_translated_cues,
)
from worker.tasks import generate_translated_subtitle_track_task  # noqa: E402


class _Session:
    session_key = "subtitle-translation-test-session"

    def save(self):
        return None


class _FakeAsyncResult:
    id = "subtitle-task-test-id"


def _capture_subtitle_dispatch(monkeypatch):
    calls: list[dict] = []

    def fake_dispatch(task_name, *, args=None, kwargs=None, queue=None):
        calls.append({"task_name": task_name, "args": args or [], "kwargs": kwargs or {}, "queue": queue})
        return _FakeAsyncResult()

    monkeypatch.setattr(views, "_dispatch_celery_task", fake_dispatch)
    return calls


@pytest.fixture(autouse=True)
def _clear_playback_cache():
    cache.clear()
    yield
    cache.clear()


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _extract_stream_tokens(url: str) -> list[str]:
    marker = "/api/v1/stream/"
    tokens = []
    for part in str(url or "").split(marker)[1:]:
        tokens.append(part.split("/", 1)[0])
    return tokens


def _write_vtt(root: Path, rel_path: str, text: str = "Caption") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{text}\n", encoding="utf-8")


def _make_ready_project_with_transcript(
    *,
    username: str,
    published: bool = False,
    tts_settings: dict | None = None,
):
    teacher = _make_teacher(username)
    project = Project.objects.create(
        title=f"Translated subtitles {username}",
        user=teacher,
        is_published=published,
        moderation_status="approved" if published else "not_scanned",
        tts_settings=tts_settings or {},
    )
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    TranscriptPage.objects.create(
        project=project,
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Original ChatGPT transcript text.",
        narration_text="ChatGPT explains APIs. Teacher keeps display text.",
        subtitle_chunks=["ChatGPT explains APIs.", "Teacher keeps display text."],
        start_seconds=0.0,
        end_seconds=3.2,
        duration_seconds=3.2,
        chunk_timeline=[
            {"chunk_index": 0, "start": 0.0, "end": 1.4, "text": "ChatGPT explains APIs."},
            {"chunk_index": 1, "start": 1.4, "end": 3.2, "text": "Teacher keeps display text."},
        ],
    )
    return teacher, project, job


@pytest.mark.django_db
def test_translated_subtitle_track_model_create_and_unique_language():
    teacher = _make_teacher("translation_model_teacher")
    project = Project.objects.create(title="Translation model", user=teacher)
    job = Job.objects.create(project=project, job_type="video_export", status="done", srt_url=f"{project.id}/{project.id}.srt")

    track = TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="EN",
        language_label="English",
        source_language_code="TR",
        provider="mock",
        status="pending",
    )

    track.refresh_from_db()
    assert track.language_code == "en"
    assert track.source_language_code == "tr"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TranslatedSubtitleTrack.objects.create(project=project, language_code="en", provider="mock")


def test_mock_provider_changes_text_only_and_validation_preserves_timing():
    cues = [
        TranslationCue(page_key="s1-p1", chunk_index=0, start=0.0, end=1.5, text="Merhaba"),
        TranslationCue(page_key="s1-p1", chunk_index=1, start=1.5, end=3.0, text="Dunya"),
    ]

    translated = MockSubtitleTranslationProvider().translate_cues(
        cues,
        source_language="tr",
        target_language="en",
        timeout_seconds=20,
    )
    validate_translated_cues(cues, translated)

    assert [cue.text for cue in translated] == ["[en] Merhaba", "[en] Dunya"]
    assert [(cue.start, cue.end) for cue in translated] == [(0.0, 1.5), (1.5, 3.0)]
    assert [cue.page_key for cue in translated] == ["s1-p1", "s1-p1"]


def test_provider_output_validation_rejects_count_order_and_timing_changes():
    original = [
        TranslationCue(page_key="s1-p1", chunk_index=0, start=0.0, end=1.0, text="A"),
        TranslationCue(page_key="s1-p1", chunk_index=1, start=1.0, end=2.0, text="B"),
    ]

    with pytest.raises(ValueError, match="count"):
        validate_translated_cues(original, original[:1])
    with pytest.raises(ValueError, match="chunk_index"):
        validate_translated_cues(
            original,
            [
                TranslationCue(page_key="s1-p1", chunk_index=1, start=0.0, end=1.0, text="A"),
                original[1],
            ],
        )
    with pytest.raises(ValueError, match="timing"):
        validate_translated_cues(
            original,
            [
                TranslationCue(page_key="s1-p1", chunk_index=0, start=0.0, end=1.5, text="A"),
                original[1],
            ],
        )


def test_auto_provider_skips_missing_api_config_and_uses_mock():
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")]

    with override_settings(
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,mock",
        SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_TRANSLATION_API_PROVIDER="",
        SUBTITLE_TRANSLATION_API_BASE_URL="",
        SUBTITLE_TRANSLATION_API_KEY="",
    ):
        provider = AutoFallbackSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    assert [cue.text for cue in translated] == ["[en] Merhaba"]
    assert provider.last_metadata["provider_used"] == "mock"
    assert provider.last_metadata["provider_chain_attempts"][0]["provider"] == "api"
    assert provider.last_metadata["provider_chain_attempts"][0]["status"] == "skipped"
    assert provider.last_metadata["provider_chain_attempts"][-1]["status"] == "success"


def test_auto_provider_falls_back_to_mock_when_allowed():
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Hello")]

    with override_settings(
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,argos,mock",
        SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_TRANSLATION_API_PROVIDER="",
        ARGOS_TRANSLATE_ENABLED=False,
    ):
        provider = AutoFallbackSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    assert translated[0].text == "[en] Hello"
    assert provider.last_metadata["provider_used"] == "mock"
    assert provider.last_metadata["fallback_used"] is True


def test_auto_provider_uses_mocked_libretranslate_success(monkeypatch):
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")]

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"translatedText":"Hello"}'

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://libre.test/translate"
        assert timeout == 3
        return _Response()

    monkeypatch.setattr(subtitle_translation_module, "urlopen", fake_urlopen)

    with override_settings(
        LIBRETRANSLATE_BASE_URL="http://libre.test",
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,libretranslate,mock",
        SUBTITLE_TRANSLATION_API_PROVIDER="",
    ):
        provider = AutoFallbackSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=3)

    assert translated[0].text == "Hello"
    assert provider.last_metadata["provider_used"] == "libretranslate"
    assert provider.last_metadata["fallback_used"] is True


def test_auto_provider_falls_back_from_libretranslate_failure_to_argos(monkeypatch):
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")]

    def fail_libretranslate(self, cues, *, source_language, target_language, timeout_seconds):
        raise SubtitleProviderUnavailable("libre down")

    def fake_argos(self, cues, *, source_language, target_language, timeout_seconds):
        return [
            TranslationCue(
                page_key=cue.page_key,
                chunk_index=cue.chunk_index,
                start=cue.start,
                end=cue.end,
                text=f"argos:{cue.text}",
            )
            for cue in cues
        ]

    monkeypatch.setattr(LibreTranslateSubtitleTranslationProvider, "translate_cues", fail_libretranslate)
    monkeypatch.setattr(ArgosSubtitleTranslationProvider, "translate_cues", fake_argos)

    with override_settings(SUBTITLE_TRANSLATION_PROVIDER_CHAIN="libretranslate,argos,mock"):
        provider = AutoFallbackSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    assert translated[0].text == "argos:Merhaba"
    assert provider.last_metadata["provider_used"] == "argos"
    assert [item["status"] for item in provider.last_metadata["provider_chain_attempts"]] == ["skipped", "success"]


def test_auto_provider_fails_cleanly_when_mock_fallback_disabled():
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Hello")]

    with override_settings(
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,mock",
        SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=False,
        SUBTITLE_TRANSLATION_API_PROVIDER="",
        SUBTITLE_TRANSLATION_API_BASE_URL="",
        SUBTITLE_TRANSLATION_API_KEY="",
    ):
        provider = AutoFallbackSubtitleTranslationProvider()
        with pytest.raises(SubtitleProviderUnavailable, match="no subtitle translation provider available"):
            provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    assert provider.last_metadata["provider_used"] == ""
    assert provider.last_metadata["provider_chain_attempts"][-1]["provider"] == "mock"
    assert provider.last_metadata["provider_chain_attempts"][-1]["status"] == "skipped"


def test_api_provider_is_unavailable_without_env_and_does_not_call_http(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("API provider should not call HTTP without full config")

    monkeypatch.setattr(subtitle_translation_module, "urlopen", fail_if_called)

    with override_settings(
        SUBTITLE_TRANSLATION_API_PROVIDER="",
        SUBTITLE_TRANSLATION_API_BASE_URL="",
        SUBTITLE_TRANSLATION_API_KEY="",
    ):
        provider = ApiSubtitleTranslationProvider()
        with pytest.raises(SubtitleProviderUnavailable, match="missing"):
            provider.translate_cues(
                [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Hello")],
                source_language="tr",
                target_language="en",
                timeout_seconds=1,
            )


def test_ollama_provider_unavailable_when_disabled_without_http(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Ollama provider should not call HTTP when disabled")

    monkeypatch.setattr(subtitle_translation_module, "urlopen", fail_if_called)

    with override_settings(OLLAMA_TRANSLATION_ENABLED=False):
        provider = OllamaSubtitleTranslationProvider()
        with pytest.raises(SubtitleProviderUnavailable, match="disabled"):
            provider.translate_cues(
                [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")],
                source_language="tr",
                target_language="en",
                timeout_seconds=1,
            )


def test_ollama_provider_translates_contextual_batch_and_preserves_timing(monkeypatch):
    cues = [
        TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.25, text="Merhaba dunya."),
        TranslationCue(page_key="s1", chunk_index=1, start=1.25, end=2.5, text="Baglam korunur."),
    ]

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            payload = {
                "response": json.dumps(
                    {
                        "translations": [
                            {"cue_id": "s1:0", "text": "Hello world."},
                            {"cue_id": "s1:1", "text": "Context is preserved."},
                        ]
                    }
                )
            }
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://ollama.test/api/generate"
        assert timeout == 4
        body = json.loads(request.data.decode("utf-8"))
        prompt = body["prompt"]
        assert body["model"] == "qwen-test"
        assert '"cue_id": "s1:0"' in prompt
        assert '"cue_id": "s1:1"' in prompt
        assert "Merhaba dunya." in prompt
        assert "Baglam korunur." in prompt
        return _Response()

    monkeypatch.setattr(subtitle_translation_module, "urlopen", fake_urlopen)

    with override_settings(
        OLLAMA_TRANSLATION_ENABLED=True,
        OLLAMA_TRANSLATION_BASE_URL="http://ollama.test",
        OLLAMA_TRANSLATION_MODEL="qwen-test",
        OLLAMA_TRANSLATION_TIMEOUT_SECONDS=4,
        OLLAMA_TRANSLATION_MAX_CUES_PER_BATCH=40,
        OLLAMA_TRANSLATION_MAX_CHARS_PER_BATCH=6000,
    ):
        provider = OllamaSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    validate_translated_cues(cues, translated)
    assert [cue.text for cue in translated] == ["Hello world.", "Context is preserved."]
    assert [(cue.start, cue.end) for cue in translated] == [(0.0, 1.25), (1.25, 2.5)]
    assert provider.last_metadata["provider_used"] == "ollama"
    assert provider.last_metadata["batch_count"] == 1
    assert provider.last_metadata["context_aware"] is True


def test_ollama_provider_retries_strict_prompt_after_wrong_translation_count(monkeypatch):
    cues = [
        TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Birinci cumle."),
        TranslationCue(page_key="s1", chunk_index=1, start=1.0, end=2.0, text="Ikinci cumle."),
    ]
    calls = []

    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body["prompt"])
        if len(calls) == 1:
            return _Response(
                {
                    "response": json.dumps(
                        {
                            "translations": [
                                {"cue_id": "s1:0", "text": "First sentence."},
                            ]
                        }
                    )
                }
            )
        assert "Previous response was invalid" in body["prompt"]
        assert '["s1:0", "s1:1"]' in body["prompt"]
        return _Response(
            {
                "response": json.dumps(
                    {
                        "translations": [
                            {"cue_id": "s1:0", "text": "First sentence."},
                            {"cue_id": "s1:1", "text": "Second sentence."},
                        ]
                    }
                )
            }
        )

    monkeypatch.setattr(subtitle_translation_module, "urlopen", fake_urlopen)

    with override_settings(
        OLLAMA_TRANSLATION_ENABLED=True,
        OLLAMA_TRANSLATION_BASE_URL="http://ollama.test",
        OLLAMA_TRANSLATION_MODEL="qwen-test",
        OLLAMA_TRANSLATION_TIMEOUT_SECONDS=4,
    ):
        provider = OllamaSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    validate_translated_cues(cues, translated)
    assert [cue.text for cue in translated] == ["First sentence.", "Second sentence."]
    assert len(calls) == 2
    assert provider.last_metadata["provider_used"] == "ollama"
    assert provider.last_metadata["retry_count"] == 1


def test_ollama_provider_invalid_json_fails_safely(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"response":"not json"}'

    monkeypatch.setattr(subtitle_translation_module, "urlopen", lambda *_args, **_kwargs: _Response())

    with override_settings(OLLAMA_TRANSLATION_BASE_URL="http://ollama.test"):
        provider = OllamaSubtitleTranslationProvider()
        with pytest.raises(SubtitleProviderUnavailable, match="invalid JSON"):
            provider.translate_cues(
                [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")],
                source_language="tr",
                target_language="en",
                timeout_seconds=1,
            )


def test_ollama_provider_rejects_wrong_cue_order(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            payload = {"response": json.dumps({"translations": [{"cue_id": "wrong:0", "text": "Hello"}]})}
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(subtitle_translation_module, "urlopen", lambda *_args, **_kwargs: _Response())

    with override_settings(OLLAMA_TRANSLATION_BASE_URL="http://ollama.test"):
        provider = OllamaSubtitleTranslationProvider()
        with pytest.raises(ValueError, match="cue_id"):
            provider.translate_cues(
                [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")],
                source_language="tr",
                target_language="en",
                timeout_seconds=1,
            )


def test_auto_provider_uses_ollama_before_fallbacks(monkeypatch):
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")]

    def fake_ollama(self, cues, *, source_language, target_language, timeout_seconds):
        return [
            TranslationCue(
                page_key=cue.page_key,
                chunk_index=cue.chunk_index,
                start=cue.start,
                end=cue.end,
                text=f"ollama:{cue.text}",
            )
            for cue in cues
        ]

    monkeypatch.setattr(OllamaSubtitleTranslationProvider, "translate_cues", fake_ollama)

    with override_settings(
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,ollama,libretranslate,mock",
        SUBTITLE_TRANSLATION_API_PROVIDER="",
    ):
        provider = AutoFallbackSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    assert translated[0].text == "ollama:Merhaba"
    assert provider.last_metadata["provider_used"] == "ollama"
    assert [item["provider"] for item in provider.last_metadata["provider_chain_attempts"]] == ["api", "ollama"]
    assert provider.last_metadata["provider_chain_attempts"][0]["status"] == "skipped"
    assert provider.last_metadata["provider_chain_attempts"][1]["status"] == "success"


def test_auto_provider_falls_back_from_ollama_failure_to_libretranslate(monkeypatch):
    cues = [TranslationCue(page_key="s1", chunk_index=0, start=0.0, end=1.0, text="Merhaba")]

    def fail_ollama(self, cues, *, source_language, target_language, timeout_seconds):
        raise SubtitleProviderUnavailable("ollama down")

    def fake_libre(self, cues, *, source_language, target_language, timeout_seconds):
        return [
            TranslationCue(
                page_key=cue.page_key,
                chunk_index=cue.chunk_index,
                start=cue.start,
                end=cue.end,
                text=f"libre:{cue.text}",
            )
            for cue in cues
        ]

    monkeypatch.setattr(OllamaSubtitleTranslationProvider, "translate_cues", fail_ollama)
    monkeypatch.setattr(LibreTranslateSubtitleTranslationProvider, "translate_cues", fake_libre)

    with override_settings(SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,ollama,libretranslate,mock", SUBTITLE_TRANSLATION_API_PROVIDER=""):
        provider = AutoFallbackSubtitleTranslationProvider()
        translated = provider.translate_cues(cues, source_language="tr", target_language="en", timeout_seconds=1)

    assert translated[0].text == "libre:Merhaba"
    assert provider.last_metadata["provider_used"] == "libretranslate"
    assert [item["provider"] for item in provider.last_metadata["provider_chain_attempts"]] == [
        "api",
        "ollama",
        "libretranslate",
    ]
    assert [item["status"] for item in provider.last_metadata["provider_chain_attempts"]] == [
        "skipped",
        "skipped",
        "success",
    ]


@pytest.mark.django_db
def test_post_translation_disabled_creates_no_track(tmp_path):
    teacher, project, _job = _make_ready_project_with_transcript(username="translation_disabled_post_teacher")
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "en", "language_label": "English", "provider": "mock"},
        format="json",
    )
    force_authenticate(request, user=teacher)
    request.user = teacher
    request.session = _Session()

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_ENABLED=False):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 503
    assert response.data["translation_enabled"] is False
    assert "disabled" in response.data["error"].lower()
    assert TranslatedSubtitleTrack.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_post_translation_rejects_invalid_language_code(tmp_path):
    teacher, project, _job = _make_ready_project_with_transcript(username="translation_invalid_language_teacher")
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "english!!", "language_label": "English", "provider": "mock"},
        format="json",
    )
    force_authenticate(request, user=teacher)
    request.user = teacher
    request.session = _Session()

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_ENABLED=True):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 400
    assert "language_code" in response.data["error"]
    assert TranslatedSubtitleTrack.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_post_for_missing_language_returns_202_and_does_not_call_provider_synchronously(tmp_path, monkeypatch):
    teacher, project, job = _make_ready_project_with_transcript(
        username="translation_generate_teacher",
        published=True,
        tts_settings={"pronunciation_overrides": {"ChatGPT": "chat gpt"}},
    )
    dispatch_calls = _capture_subtitle_dispatch(monkeypatch)

    def fail_if_called_sync(*_args, **_kwargs):
        raise AssertionError("provider should not be called synchronously from POST")

    monkeypatch.setattr(MockSubtitleTranslationProvider, "translate_cues", fail_if_called_sync)

    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "en", "language_label": "English", "provider": "mock"},
        format="json",
    )
    force_authenticate(request, user=teacher)
    request.user = teacher
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="en")
    assert track.status == "processing"
    assert track.job_id == job.id
    assert track.srt_path == ""
    assert track.vtt_path == ""
    assert response.data["track"]["status"] == "processing"
    assert response.data["track"]["vtt_url"] is None
    assert dispatch_calls[0]["task_name"] == "worker.tasks.generate_translated_subtitle_track_task"
    assert dispatch_calls[0]["kwargs"]["project_id"] == project.id
    assert dispatch_calls[0]["kwargs"]["language_code"] == "en"


@pytest.mark.django_db
def test_subtitle_translation_task_marks_track_ready_and_preserves_timing(tmp_path):
    _teacher, project, job = _make_ready_project_with_transcript(
        username="translation_task_success_teacher",
        published=True,
        tts_settings={"pronunciation_overrides": {"ChatGPT": "chat gpt"}},
    )
    track = TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="en",
        language_label="English",
        provider="mock",
        status="processing",
        metadata={"provider_requested": "mock", "generation_mode": "async"},
    )

    result = generate_translated_subtitle_track_task.run(
        project.id,
        "en",
        language_label="English",
        provider="mock",
        storage_root=str(tmp_path),
    )

    track.refresh_from_db()
    assert result["status"] == "ready"
    assert track.status == "ready"
    assert track.id == result["track_id"]
    assert track.cue_count == 2
    assert track.srt_path == f"{project.id}/subtitles/en.srt"
    assert track.vtt_path == f"{project.id}/subtitles/en.vtt"

    srt_text = (tmp_path / track.srt_path).read_text(encoding="utf-8")
    vtt_text = (tmp_path / track.vtt_path).read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,400" in srt_text
    assert "00:00:01,400 --> 00:00:03,200" in srt_text
    assert vtt_text.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.400" in vtt_text
    assert "[en] ChatGPT explains APIs." in srt_text
    assert "[en] ChatGPT explains APIs." in vtt_text
    assert "chat gpt" not in srt_text.lower()
    assert "chat gpt" not in vtt_text.lower()


@pytest.mark.django_db
def test_duplicate_generation_updates_existing_language_track(tmp_path):
    teacher, project, _job = _make_ready_project_with_transcript(username="translation_duplicate_teacher")

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_TIMEOUT_SECONDS=20):
        first = generate_translated_subtitle_track(project.id, "EN", provider="mock", language_label="English")
        second = generate_translated_subtitle_track(project.id, "en", provider="mock", language_label="English Updated")

    assert first.id == second.id
    assert TranslatedSubtitleTrack.objects.filter(project=project, language_code="en").count() == 1
    second.refresh_from_db()
    assert second.language_label == "English Updated"
    assert second.status == "ready"
    assert (tmp_path / second.srt_path).is_file()
    assert (tmp_path / second.vtt_path).is_file()


@pytest.mark.django_db
def test_provider_failure_marks_track_failed_without_traceback(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_failure_teacher")

    class _FailingProvider:
        provider_name = "mock"

        def translate_cues(self, cues, *, source_language, target_language, timeout_seconds):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        subtitle_translation_module,
        "get_subtitle_translation_provider",
        lambda provider_name=None, **_kwargs: _FailingProvider(),
    )

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_TIMEOUT_SECONDS=20):
        with pytest.raises(SubtitleTranslationError, match="provider exploded"):
            generate_translated_subtitle_track(project.id, "en", provider="mock", language_label="English")

    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="en")
    assert track.status == "failed"
    assert "provider exploded" in track.error_message
    assert "Traceback" not in track.error_message
    assert track.srt_path == ""
    assert track.vtt_path == ""


@pytest.mark.django_db
def test_subtitle_translation_task_marks_track_failed_on_provider_failure(tmp_path, monkeypatch):
    _teacher, project, job = _make_ready_project_with_transcript(username="translation_task_failure_teacher")
    TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="en",
        language_label="English",
        provider="mock",
        status="processing",
        metadata={"provider_requested": "mock", "generation_mode": "async"},
    )

    class _FailingProvider:
        provider_name = "mock"

        def translate_cues(self, cues, *, source_language, target_language, timeout_seconds):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        subtitle_translation_module,
        "get_subtitle_translation_provider",
        lambda provider_name=None, **_kwargs: _FailingProvider(),
    )

    result = generate_translated_subtitle_track_task.run(
        project.id,
        "en",
        language_label="English",
        provider="mock",
        storage_root=str(tmp_path),
    )

    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="en")
    assert result["status"] == "failed"
    assert track.status == "failed"
    assert "provider exploded" in track.error_message
    assert "Traceback" not in track.error_message


@pytest.mark.django_db
def test_subtitle_translation_task_marks_track_failed_on_permission_error_and_releases_lock(tmp_path, monkeypatch):
    _teacher, project, job = _make_ready_project_with_transcript(username="translation_task_permission_teacher")
    TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="ar",
        language_label="Arabic",
        provider="mock",
        status="processing",
        metadata={"provider_requested": "mock", "generation_mode": "async"},
    )
    lock_key = views._subtitle_generation_lock_key(project.id, "ar")
    cache.set(lock_key, "1", timeout=300)

    def deny_subtitle_dir(*_args, **_kwargs):
        raise PermissionError("Permission denied: /app/storage_local/secret/subtitles/ar.srt")

    monkeypatch.setattr(subtitle_translation_module, "_ensure_subtitle_output_dir", deny_subtitle_dir)

    result = generate_translated_subtitle_track_task.run(
        project.id,
        "ar",
        language_label="Arabic",
        provider="mock",
        storage_root=str(tmp_path),
        lock_key=lock_key,
    )

    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="ar")
    assert result["status"] == "failed"
    assert track.status == "failed"
    assert "not writable" in track.error_message
    assert "/app/storage_local" not in track.error_message
    assert cache.get(lock_key) is None


@pytest.mark.django_db
def test_auto_generation_stores_provider_chain_metadata(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_auto_metadata_teacher")

    def fake_argos(self, cues, *, source_language, target_language, timeout_seconds):
        return [
            TranslationCue(
                page_key=cue.page_key,
                chunk_index=cue.chunk_index,
                start=cue.start,
                end=cue.end,
                text=f"argos:{cue.text}",
            )
            for cue in cues
        ]

    monkeypatch.setattr(ArgosSubtitleTranslationProvider, "translate_cues", fake_argos)

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,argos,mock",
        SUBTITLE_TRANSLATION_API_PROVIDER="",
    ):
        track = generate_translated_subtitle_track(project.id, "en", provider="auto", language_label="English")

    assert track.status == "ready"
    assert track.provider == "argos"
    assert track.metadata["provider_requested"] == "auto"
    assert track.metadata["provider_used"] == "argos"
    assert track.metadata["fallback_used"] is True
    assert track.metadata["source_language_code"] == "original"
    assert track.metadata["target_language_code"] == "en"
    assert [item["provider"] for item in track.metadata["provider_chain_attempts"]] == ["api", "argos"]
    assert (tmp_path / track.vtt_path).read_text(encoding="utf-8").startswith("WEBVTT")


@pytest.mark.django_db
def test_auto_generation_uses_ollama_metadata_when_available(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_ollama_metadata_teacher")

    def fake_ollama(self, cues, *, source_language, target_language, timeout_seconds):
        self.last_metadata = {
            "provider_used": "ollama",
            "batch_count": 1,
            "context_aware": True,
            "model": "qwen-test",
        }
        return [
            TranslationCue(
                page_key=cue.page_key,
                chunk_index=cue.chunk_index,
                start=cue.start,
                end=cue.end,
                text=f"ollama:{cue.text}",
            )
            for cue in cues
        ]

    monkeypatch.setattr(OllamaSubtitleTranslationProvider, "translate_cues", fake_ollama)

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,ollama,mock",
        SUBTITLE_TRANSLATION_API_PROVIDER="",
    ):
        track = generate_translated_subtitle_track(project.id, "en", provider="auto", language_label="English")

    assert track.status == "ready"
    assert track.provider == "ollama"
    assert track.metadata["provider_requested"] == "auto"
    assert track.metadata["provider_used"] == "ollama"
    assert track.metadata["provider_chain_attempts"][-1]["provider"] == "ollama"
    assert track.metadata["fallback_used"] is True
    assert "ollama:ChatGPT explains APIs." in (tmp_path / track.vtt_path).read_text(encoding="utf-8")


@pytest.mark.django_db
def test_auto_generation_fails_cleanly_when_no_provider_available(tmp_path):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_auto_failure_teacher")

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="api,mock",
        SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=False,
        SUBTITLE_TRANSLATION_API_PROVIDER="",
        SUBTITLE_TRANSLATION_API_BASE_URL="",
        SUBTITLE_TRANSLATION_API_KEY="",
    ):
        with pytest.raises(SubtitleTranslationError, match="no subtitle translation provider available"):
            generate_translated_subtitle_track(project.id, "en", provider="auto", language_label="English")

    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="en")
    assert track.status == "failed"
    assert "Traceback" not in track.error_message
    assert track.metadata["provider_requested"] == "auto"
    assert track.metadata["provider_used"] == ""
    assert track.metadata["provider_chain_attempts"][-1]["provider"] == "mock"
    assert track.srt_path == ""
    assert track.vtt_path == ""


@pytest.mark.django_db
def test_direct_argos_unavailable_marks_track_failed_safely(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_argos_unavailable_teacher")
    original_import_module = subtitle_translation_module.importlib.import_module

    def fake_import_module(name, package=None):
        if name == "argostranslate.translate":
            raise ModuleNotFoundError("argostranslate")
        return original_import_module(name, package)

    monkeypatch.setattr(subtitle_translation_module.importlib, "import_module", fake_import_module)

    with override_settings(STORAGE_ROOT=str(tmp_path), ARGOS_TRANSLATE_ENABLED=True):
        with pytest.raises(SubtitleTranslationError, match="Argos Translate package is not installed"):
            generate_translated_subtitle_track(project.id, "en", provider="argos", language_label="English")

    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="en")
    assert track.status == "failed"
    assert "Traceback" not in track.error_message
    assert track.metadata["provider_requested"] == "argos"


@pytest.mark.django_db
def test_direct_libretranslate_error_marks_track_failed_safely(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_libre_error_teacher")

    def fake_urlopen(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(subtitle_translation_module, "urlopen", fake_urlopen)

    with override_settings(STORAGE_ROOT=str(tmp_path), LIBRETRANSLATE_BASE_URL="http://libre.test"):
        with pytest.raises(SubtitleTranslationError, match="LibreTranslate request failed"):
            generate_translated_subtitle_track(project.id, "en", provider="libretranslate", language_label="English")

    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="en")
    assert track.status == "failed"
    assert "Traceback" not in track.error_message
    assert track.metadata["provider_requested"] == "libretranslate"


@pytest.mark.django_db
def test_public_published_lesson_lists_generated_ready_translated_track(tmp_path):
    _teacher, project, _job = _make_ready_project_with_transcript(username="translation_public_generated_teacher", published=True)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        generate_translated_subtitle_track(project.id, "ar", provider="mock", language_label="Arabic")
        _write_vtt(tmp_path, f"{project.id}/{project.id}.vtt", "Original caption")

    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    request.session = _Session()
    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    translated = [track for track in response.data["tracks"] if track["type"] == "translated"][0]
    assert translated["language_code"] == "ar"
    assert translated["status"] == "ready"
    assert translated["vtt_url"].startswith("http://testserver/api/v1/stream/")
    assert f"{project.id}/subtitles/ar.vtt" not in translated["vtt_url"]
    requestable_codes = [language["language_code"] for language in response.data["requestable_languages"]]
    assert requestable_codes == [
        "en",
        "ar",
        "tr",
        "fr",
        "de",
        "es",
        "it",
        "pt",
        "ru",
        "zh",
        "ja",
        "ko",
        "hi",
        "ur",
        "id",
        "fa",
    ]
    requestable_labels = {language["language_code"]: language["language_label"] for language in response.data["requestable_languages"]}
    assert requestable_labels["it"] == "Italian"
    assert requestable_labels["fa"] == "Persian"


@pytest.mark.django_db
def test_subtitle_track_list_exposes_configured_requestable_languages(tmp_path):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_requestable_language_config_teacher",
        published=True,
    )
    _write_vtt(tmp_path, f"{project.id}/{project.id}.vtt", "Original caption")
    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST="it,fa",
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["requestable_languages"] == [
        {"language_code": "it", "language_label": "Italian"},
        {"language_code": "fa", "language_label": "Persian"},
    ]


@pytest.mark.django_db
def test_public_request_allowed_language_enqueues_then_reuses_ready_track(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_allowed_teacher",
        published=True,
    )
    dispatch_calls = _capture_subtitle_dispatch(monkeypatch)
    factory = APIRequestFactory()
    request = factory.post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "ar", "language_label": "Ignored Label", "provider": "api"},
        format="json",
        REMOTE_ADDR="203.0.113.10",
    )
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER="auto",
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=5,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    assert response.data.get("already_available") is not True
    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="ar")
    assert track.status == "processing"
    assert track.language_label == "Arabic"
    assert track.metadata["provider_requested"] == "auto"
    assert track.provider == "auto"
    payload = response.data["track"]
    assert payload["language_code"] == "ar"
    assert payload["language_label"] == "Arabic"
    assert payload["status"] == "processing"
    assert payload["vtt_url"] is None
    assert "vtt_path" not in payload
    assert dispatch_calls[0]["task_name"] == "worker.tasks.generate_translated_subtitle_track_task"

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=True,
    ):
        task_result = generate_translated_subtitle_track_task.run(**dispatch_calls[0]["kwargs"])

    track.refresh_from_db()
    assert task_result["status"] == "ready"
    assert track.status == "ready"
    assert track.provider == "mock"
    assert track.vtt_path

    cache.set(views._subtitle_generation_lock_key(project.id, "ar"), "1", timeout=300)
    second_request = factory.post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "ar", "language_label": "Arabic", "provider": "auto"},
        format="json",
        REMOTE_ADDR="203.0.113.10",
    )
    second_request.session = _Session()
    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=0,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        second_response = views.ProjectSubtitleTrackListView.as_view()(second_request, project_id=project.id)

    assert second_response.status_code == 200
    assert second_response.data["already_available"] is True
    assert second_response.data["track"]["id"] == track.id
    assert TranslatedSubtitleTrack.objects.filter(project=project, language_code="ar").count() == 1


@pytest.mark.django_db
def test_public_request_new_default_language_can_be_requested(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_italian_teacher",
        published=True,
    )
    dispatch_calls = _capture_subtitle_dispatch(monkeypatch)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "it", "language_label": "Ignored Public Label", "provider": "mock"},
        format="json",
        REMOTE_ADDR="203.0.113.18",
    )
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER="auto",
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=5,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="it")
    assert track.status == "processing"
    assert track.language_label == "Italian"
    assert response.data["track"]["language_code"] == "it"
    assert response.data["track"]["language_label"] == "Italian"
    assert dispatch_calls[0]["kwargs"]["language_code"] == "it"
    assert dispatch_calls[0]["kwargs"]["language_label"] == "Italian"


@pytest.mark.django_db
def test_public_request_unsupported_language_returns_400(tmp_path):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_unsupported_teacher",
        published=True,
    )
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "pt-br", "language_label": "Portuguese"},
        format="json",
        REMOTE_ADDR="203.0.113.11",
    )
    request.session = _Session()

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_ENABLED=True):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 400
    assert response.data["error"] == "unsupported_language"
    assert TranslatedSubtitleTrack.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_processing_track_returns_202_without_requeue(tmp_path, monkeypatch):
    _teacher, project, job = _make_ready_project_with_transcript(
        username="translation_processing_reuse_teacher",
        published=True,
    )
    dispatch_calls = _capture_subtitle_dispatch(monkeypatch)
    track = TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="ar",
        language_label="Arabic",
        provider="auto",
        status="processing",
        metadata={"provider_requested": "auto", "generation_mode": "async"},
    )
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "ar", "language_label": "Arabic"},
        format="json",
        REMOTE_ADDR="203.0.113.17",
    )
    request.session = _Session()

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_ENABLED=True):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    assert response.data["track"]["id"] == track.id
    assert response.data["track"]["status"] == "processing"
    assert dispatch_calls == []


@pytest.mark.django_db
def test_public_request_anonymous_rate_limit_returns_429(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_anon_rate_teacher",
        published=True,
    )
    _capture_subtitle_dispatch(monkeypatch)
    factory = APIRequestFactory()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=1,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        first = factory.post(
            f"/api/v1/projects/{project.id}/subtitle-tracks/",
            {"language_code": "ar", "language_label": "Arabic"},
            format="json",
            REMOTE_ADDR="203.0.113.12",
        )
        first.session = _Session()
        first_response = views.ProjectSubtitleTrackListView.as_view()(first, project_id=project.id)

        second = factory.post(
            f"/api/v1/projects/{project.id}/subtitle-tracks/",
            {"language_code": "fr", "language_label": "French"},
            format="json",
            REMOTE_ADDR="203.0.113.12",
        )
        second.session = _Session()
        second_response = views.ProjectSubtitleTrackListView.as_view()(second, project_id=project.id)

    assert first_response.status_code == 202
    assert second_response.status_code == 429
    assert second_response.data["error"] == "rate_limited"
    assert "Try again later" in second_response.data["details"]


@pytest.mark.django_db
def test_public_request_authenticated_rate_limit_uses_user_key(tmp_path, monkeypatch):
    teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_auth_rate_owner",
        published=True,
    )
    _capture_subtitle_dispatch(monkeypatch)
    viewer_one = User.objects.create_user(username="subtitle_viewer_one", password="pass")
    viewer_two = User.objects.create_user(username="subtitle_viewer_two", password="pass")
    factory = APIRequestFactory()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_PER_HOUR=1,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        first = factory.post(
            f"/api/v1/projects/{project.id}/subtitle-tracks/",
            {"language_code": "ar", "language_label": "Arabic"},
            format="json",
            REMOTE_ADDR="203.0.113.13",
        )
        force_authenticate(first, user=viewer_one)
        first.user = viewer_one
        first.session = _Session()
        first_response = views.ProjectSubtitleTrackListView.as_view()(first, project_id=project.id)

        second = factory.post(
            f"/api/v1/projects/{project.id}/subtitle-tracks/",
            {"language_code": "fr", "language_label": "French"},
            format="json",
            REMOTE_ADDR="203.0.113.13",
        )
        force_authenticate(second, user=viewer_one)
        second.user = viewer_one
        second.session = _Session()
        second_response = views.ProjectSubtitleTrackListView.as_view()(second, project_id=project.id)

        third = factory.post(
            f"/api/v1/projects/{project.id}/subtitle-tracks/",
            {"language_code": "de", "language_label": "German"},
            format="json",
            REMOTE_ADDR="203.0.113.13",
        )
        force_authenticate(third, user=viewer_two)
        third.user = viewer_two
        third.session = _Session()
        third_response = views.ProjectSubtitleTrackListView.as_view()(third, project_id=project.id)

    assert teacher.id != viewer_one.id
    assert first_response.status_code == 202
    assert second_response.status_code == 429
    assert second_response.data["error"] == "rate_limited"
    assert third_response.status_code == 202


@pytest.mark.django_db
def test_duplicate_public_generation_lock_returns_409(tmp_path):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_lock_teacher",
        published=True,
    )
    cache.set(views._subtitle_generation_lock_key(project.id, "ar"), "1", timeout=300)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "ar", "language_label": "Arabic"},
        format="json",
        REMOTE_ADDR="203.0.113.14",
    )
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=5,
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 409
    assert response.data["error"] == "generation_in_progress"
    assert TranslatedSubtitleTrack.objects.filter(project=project, language_code="ar").count() == 0


@pytest.mark.django_db
def test_public_production_request_does_not_return_mock_when_disabled(tmp_path, monkeypatch):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_public_request_mock_blocked_teacher",
        published=True,
    )
    dispatch_calls = _capture_subtitle_dispatch(monkeypatch)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "ar", "language_label": "Arabic"},
        format="json",
        REMOTE_ADDR="203.0.113.15",
    )
    request.session = _Session()

    with override_settings(
        DEBUG=False,
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER="auto",
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=False,
        SUBTITLE_PUBLIC_REQUEST_RATE_LIMIT_ANON_PER_HOUR=5,
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="ar")
    assert track.status == "processing"

    with override_settings(
        DEBUG=False,
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_TRANSLATION_PROVIDER="auto",
        SUBTITLE_TRANSLATION_PROVIDER_CHAIN="mock",
        SUBTITLE_TRANSLATION_ALLOW_MOCK_FALLBACK=True,
        SUBTITLE_PRODUCTION_ALLOW_MOCK_FALLBACK=False,
    ):
        task_result = generate_translated_subtitle_track_task.run(**dispatch_calls[0]["kwargs"])

    track.refresh_from_db()
    assert task_result["status"] == "failed"
    assert track.status == "failed"
    assert track.vtt_path == ""
    assert "mock:skipped" in track.error_message.lower()


@pytest.mark.django_db
def test_anonymous_draft_cannot_request_translated_track(tmp_path):
    _teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_draft_request_teacher",
        published=False,
    )
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "ar", "language_label": "Arabic", "provider": "auto"},
        format="json",
        REMOTE_ADDR="203.0.113.16",
    )
    request.session = _Session()

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_ENABLED=True):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 404
    assert TranslatedSubtitleTrack.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_owner_can_request_private_draft_language_without_public_allowlist(tmp_path, monkeypatch):
    teacher, project, _job = _make_ready_project_with_transcript(
        username="translation_owner_private_allowlist_teacher",
        published=False,
    )
    dispatch_calls = _capture_subtitle_dispatch(monkeypatch)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/subtitle-tracks/",
        {"language_code": "pt-br", "language_label": "Portuguese", "provider": "mock"},
        format="json",
    )
    force_authenticate(request, user=teacher)
    request.user = teacher
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=True,
        SUBTITLE_PUBLIC_REQUEST_LANGUAGE_ALLOWLIST="en,ar",
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 202
    track = TranslatedSubtitleTrack.objects.get(project=project, language_code="pt-br")
    assert track.status == "processing"
    assert response.data["track"]["vtt_url"] is None
    assert "vtt_path" not in response.data["track"]

    with override_settings(STORAGE_ROOT=str(tmp_path), SUBTITLE_TRANSLATION_ENABLED=True):
        generate_translated_subtitle_track_task.run(**dispatch_calls[0]["kwargs"])

    track.refresh_from_db()
    assert track.status == "ready"
    assert track.vtt_path


@pytest.mark.django_db
def test_anonymous_draft_generated_track_cannot_list_or_stream(tmp_path):
    teacher, project, job = _make_ready_project_with_transcript(username="translation_draft_generated_teacher", published=False)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        track = generate_translated_subtitle_track(project.id, "en", provider="mock", language_label="English")

    factory = APIRequestFactory()
    anonymous_request = factory.get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    anonymous_request.session = _Session()
    assert views.ProjectSubtitleTrackListView.as_view()(anonymous_request, project_id=project.id).status_code == 404

    owner_request = factory.get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    force_authenticate(owner_request, user=teacher)
    owner_request.user = teacher
    owner_request.session = _Session()
    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        owner_response = views.ProjectSubtitleTrackListView.as_view()(owner_request, project_id=project.id)

    assert owner_response.status_code == 200
    translated = [item for item in owner_response.data["tracks"] if item["type"] == "translated"][0]
    token = _extract_stream_tokens(translated["vtt_url"])[0]
    token_job_id, file_type, rel_path, grant_id, bind_key = views.validate_media_token(token)
    assert token_job_id == job.id
    assert file_type == "vtt"
    assert rel_path == track.vtt_path
    assert grant_id
    assert bind_key

    grantless_token = views.generate_media_token(job.id, "vtt", rel_path=track.vtt_path)
    grantless_response = views.MediaStreamView.as_view()(
        factory.get(f"/api/v1/stream/{grantless_token}/"),
        token=grantless_token,
    )
    assert grantless_response.status_code == 403


@pytest.mark.django_db
def test_track_list_includes_original_and_ready_translated_track_with_tokenized_vtt(tmp_path):
    teacher = _make_teacher("translation_public_teacher")
    project = Project.objects.create(
        title="Published translated captions",
        user=teacher,
        is_published=True,
        moderation_status="approved",
    )
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    original_vtt = f"{project.id}/{project.id}.vtt"
    translated_vtt = f"{project.id}/subtitles/en.vtt"
    _write_vtt(tmp_path, original_vtt, "Original caption")
    _write_vtt(tmp_path, translated_vtt, "English caption")
    TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="en",
        language_label="English",
        source_language_code="tr",
        provider="mock",
        status="ready",
        vtt_path=translated_vtt,
        cue_count=1,
    )

    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=False,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["translation_enabled"] is False
    tracks = response.data["tracks"]
    assert tracks[0]["type"] == "original"
    assert tracks[0]["language_code"] == "original"
    assert tracks[0]["is_original"] is True
    assert tracks[0]["vtt_url"].startswith("http://testserver/api/v1/stream/")
    translated = [track for track in tracks if track["type"] == "translated"][0]
    assert translated["language_code"] == "en"
    assert translated["is_original"] is False
    assert translated["vtt_url"].startswith("http://testserver/api/v1/stream/")
    assert translated_vtt not in translated["vtt_url"]
    assert "vtt_path" not in translated

    token = _extract_stream_tokens(translated["vtt_url"])[0]
    token_job_id, file_type, rel_path, _grant_id, _bind_key = views.validate_media_token(token)
    assert token_job_id == job.id
    assert file_type == "vtt"
    assert rel_path == translated_vtt


@pytest.mark.django_db
def test_published_playable_track_list_ignores_stale_project_status(tmp_path):
    teacher = _make_teacher("translation_public_draft_status_teacher")
    project = Project.objects.create(
        title="Published playable translated captions",
        user=teacher,
        status="draft",
        moderation_status="approved",
        is_published=True,
    )
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    original_vtt = f"{project.id}/{project.id}.vtt"
    translated_vtt = f"{project.id}/subtitles/en.vtt"
    _write_vtt(tmp_path, original_vtt, "Original caption")
    _write_vtt(tmp_path, translated_vtt, "English caption")
    TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="en",
        language_label="English",
        source_language_code="tr",
        provider="mock",
        status="ready",
        vtt_path=translated_vtt,
        cue_count=1,
    )

    factory = APIRequestFactory()
    request = factory.get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    tracks = response.data["tracks"]
    assert [(track["language_code"], track["language_label"]) for track in tracks] == [
        ("original", "Original"),
        ("en", "English"),
    ]
    assert all(track["vtt_url"].startswith("http://testserver/api/v1/stream/") for track in tracks)
    assert all("vtt_path" not in track for track in tracks)

    translated = tracks[1]
    token = _extract_stream_tokens(translated["vtt_url"])[0]
    token_job_id, file_type, rel_path, grant_id, bind_key = views.validate_media_token(token)
    assert token_job_id == job.id
    assert file_type == "vtt"
    assert rel_path == translated_vtt
    assert grant_id is None
    assert bind_key is None

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        stream_response = views.MediaStreamView.as_view()(factory.get(f"/api/v1/stream/{token}/"), token=token)

    assert stream_response.status_code == 200


@pytest.mark.django_db
def test_anonymous_cannot_see_draft_translated_track_but_owner_can(tmp_path):
    teacher = _make_teacher("translation_draft_teacher")
    project = Project.objects.create(title="Draft translated captions", user=teacher, is_published=False)
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    original_vtt = f"{project.id}/{project.id}.vtt"
    translated_vtt = f"{project.id}/subtitles/ar.vtt"
    _write_vtt(tmp_path, original_vtt, "Original caption")
    _write_vtt(tmp_path, translated_vtt, "Arabic caption")
    TranslatedSubtitleTrack.objects.create(
        project=project,
        job=job,
        language_code="ar",
        language_label="Arabic",
        source_language_code="tr",
        provider="mock",
        status="ready",
        vtt_path=translated_vtt,
        cue_count=1,
    )

    factory = APIRequestFactory()
    anonymous_request = factory.get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    anonymous_request.session = _Session()
    assert views.ProjectSubtitleTrackListView.as_view()(anonymous_request, project_id=project.id).status_code == 404

    owner_request = factory.get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    force_authenticate(owner_request, user=teacher)
    owner_request.user = teacher
    owner_request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        owner_response = views.ProjectSubtitleTrackListView.as_view()(owner_request, project_id=project.id)

    assert owner_response.status_code == 200
    translated = [track for track in owner_response.data["tracks"] if track["type"] == "translated"][0]
    assert translated["vtt_url"].startswith("http://testserver/api/v1/stream/")
    assert translated_vtt not in translated["vtt_url"]

    token = _extract_stream_tokens(translated["vtt_url"])[0]
    token_job_id, file_type, rel_path, grant_id, bind_key = views.validate_media_token(token)
    assert token_job_id == job.id
    assert file_type == "vtt"
    assert rel_path == translated_vtt
    assert grant_id
    assert bind_key

    grantless_token = views.generate_media_token(job.id, "vtt", rel_path=translated_vtt)
    grantless_response = views.MediaStreamView.as_view()(
        factory.get(f"/api/v1/stream/{grantless_token}/"),
        token=grantless_token,
    )
    assert grantless_response.status_code == 403


@pytest.mark.django_db
def test_disabled_translation_config_does_not_break_original_track_listing(tmp_path):
    teacher = _make_teacher("translation_disabled_teacher")
    project = Project.objects.create(
        title="Original only",
        user=teacher,
        is_published=True,
        moderation_status="approved",
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    _write_vtt(tmp_path, f"{project.id}/{project.id}.vtt", "Original caption")

    request = APIRequestFactory().get(f"/api/v1/projects/{project.id}/subtitle-tracks/")
    request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        SUBTITLE_TRANSLATION_ENABLED=False,
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.ProjectSubtitleTrackListView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["translation_enabled"] is False
    assert [track["type"] for track in response.data["tracks"]] == ["original"]
    assert response.data["tracks"][0]["has_vtt"] is True
