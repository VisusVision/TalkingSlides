# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
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

from ai_agents.models import AgentFinding  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.ocr_bridge import OCRTextResult, build_ocr_provider  # noqa: E402
from worker.ai_agents.providers.azure_ocr_provider import AzureOCRProvider  # noqa: E402
from worker.ai_agents.providers.noop_ocr_provider import NoopOCRProvider  # noqa: E402
from worker.ai_agents.schemas import FindingLocation  # noqa: E402


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, headers=None, error=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self._error = error

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self._error is not None:
            raise self._error
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _location() -> FindingLocation:
    return FindingLocation(
        project_id=1,
        asset_type="ocr_text",
        image_path="slide.png",
        slide_order=0,
        field_name="ocr_text",
        ui_anchor="slide-0-ocr",
    )


def _configure_azure(monkeypatch, *, enabled=True, endpoint="https://example.cognitiveservices.azure.com", key="secret"):
    monkeypatch.setattr(settings, "AZURE_OCR_ENABLED", enabled, raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_ENDPOINT", endpoint, raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_KEY", key, raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_API_VERSION", "2024-02-29-preview", raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_MODEL", "prebuilt-read", raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_MAX_IMAGE_BYTES", 1024, raising=False)
    monkeypatch.setattr(settings, "AZURE_OCR_LANG_HINTS", "en,tr,ar", raising=False)


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str) -> Project:
    return Project.objects.create(
        title="Azure OCR lesson",
        user=_make_teacher(username),
        status="processing",
        moderation_status="approved",
    )


def _slide(path: Path | str) -> dict:
    return {
        "index": 0,
        "slide_num": 1,
        "source_slide_index": 0,
        "page_key": "slide-1",
        "image_path": str(path),
    }


def test_azure_provider_disabled_makes_no_network_call(monkeypatch, tmp_path):
    _configure_azure(monkeypatch, enabled=False)
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")

    def fail_post(*_args, **_kwargs):
        raise AssertionError("Azure network call should not run when provider is disabled")

    monkeypatch.setattr("worker.ai_agents.providers.azure_ocr_provider.requests.post", fail_post)

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.text == ""
    assert result.success is False
    assert result.metadata["reason"] == "azure_ocr_disabled"
    assert result.metadata["skipped"] is True


def test_azure_provider_missing_endpoint_or_key_makes_no_network_call(monkeypatch, tmp_path):
    _configure_azure(monkeypatch, enabled=True, endpoint="", key="")
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")

    def fail_post(*_args, **_kwargs):
        raise AssertionError("Azure network call should not run without endpoint/key")

    monkeypatch.setattr("worker.ai_agents.providers.azure_ocr_provider.requests.post", fail_post)

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is False
    assert result.metadata["reason"] == "azure_ocr_missing_config"
    assert result.metadata["endpoint_configured"] is False
    assert result.metadata["key_configured"] is False


def test_azure_provider_oversized_image_is_skipped(monkeypatch, tmp_path):
    _configure_azure(monkeypatch)
    monkeypatch.setattr(settings, "AZURE_OCR_MAX_IMAGE_BYTES", 3, raising=False)
    image_path = tmp_path / "large.png"
    image_path.write_bytes(b"abcd")

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is False
    assert result.metadata["reason"] == "image_too_large"
    assert result.metadata["file_size_bytes"] == 4


def test_azure_provider_successful_response_returns_extracted_text(monkeypatch, tmp_path):
    _configure_azure(monkeypatch)
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            payload={
                "analyzeResult": {
                    "content": "This is extracted text.",
                    "pages": [{"words": [{"content": "This", "confidence": 0.8}, {"content": "text", "confidence": 1.0}]}],
                }
            }
        )

    monkeypatch.setattr("worker.ai_agents.providers.azure_ocr_provider.requests.post", fake_post)

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is True
    assert result.text == "This is extracted text."
    assert result.provider == "azure_ocr"
    assert result.metadata["text_length"] == len("This is extracted text.")
    assert result.metadata["confidence"] == pytest.approx(0.9)
    assert result.metadata["language_hints"] == ["en", "tr", "ar"]
    assert result.metadata["model"] == "prebuilt-read"
    assert result.metadata["api_version"] == "2024-02-29-preview"
    assert "secret" not in str(result.metadata)
    assert calls[0][1]["params"]["locale"] == "en"


def test_azure_provider_202_polling_response_returns_text(monkeypatch, tmp_path):
    _configure_azure(monkeypatch)
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")
    get_payloads = [
        FakeResponse(payload={"status": "running"}),
        FakeResponse(payload={"status": "succeeded", "analyzeResult": {"content": "Polled text"}}),
    ]

    monkeypatch.setattr(
        "worker.ai_agents.providers.azure_ocr_provider.requests.post",
        lambda *_args, **_kwargs: FakeResponse(status_code=202, headers={"Operation-Location": "https://poll.example/op"}),
    )
    monkeypatch.setattr(
        "worker.ai_agents.providers.azure_ocr_provider.requests.get",
        lambda *_args, **_kwargs: get_payloads.pop(0),
    )
    monkeypatch.setattr("worker.ai_agents.providers.azure_ocr_provider.time.sleep", lambda *_args, **_kwargs: None)

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is True
    assert result.text == "Polled text"
    assert result.metadata["status"] == "succeeded"


def test_azure_provider_timeout_returns_safe_failure(monkeypatch, tmp_path):
    _configure_azure(monkeypatch)
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")

    def timeout_post(*_args, **_kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr("worker.ai_agents.providers.azure_ocr_provider.requests.post", timeout_post)

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is False
    assert result.text == ""
    assert result.metadata["reason"] == "azure_ocr_timeout"


def test_azure_provider_error_response_returns_safe_failure(monkeypatch, tmp_path):
    _configure_azure(monkeypatch)
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(
        "worker.ai_agents.providers.azure_ocr_provider.requests.post",
        lambda *_args, **_kwargs: FakeResponse(status_code=500),
    )

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is False
    assert result.metadata["reason"] == "azure_ocr_request_error"


def test_azure_provider_invalid_json_returns_safe_failure(monkeypatch, tmp_path):
    _configure_azure(monkeypatch)
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(
        "worker.ai_agents.providers.azure_ocr_provider.requests.post",
        lambda *_args, **_kwargs: FakeResponse(payload=ValueError("bad json")),
    )

    result = AzureOCRProvider().extract(str(image_path), _location())

    assert result.success is False
    assert result.metadata["reason"] == "azure_ocr_invalid_response"


def test_ocr_provider_selection_uses_azure_and_unknown_falls_back_to_noop(monkeypatch):
    monkeypatch.setattr(settings, "OCR_MODERATION_PROVIDER", "azure", raising=False)

    assert isinstance(build_ocr_provider(), AzureOCRProvider)
    assert isinstance(build_ocr_provider("unknown-provider"), NoopOCRProvider)


@pytest.mark.django_db
def test_auto_ocr_with_mocked_azure_provider_records_findings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OCR_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_BLOCK_RENDER_ON_REJECTION", False, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_PHASE", "ocr_slide_scan", raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_SCAN_SLIDES", True, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_PROVIDER", "azure", raising=False)
    project = _make_project("azure_ocr_auto_teacher")
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"image")

    def fake_extract(self, image_path="", location=None):
        return OCRTextResult(
            text="I will kill you tomorrow.",
            location=location,
            provider="azure_ocr",
            success=True,
            error_message="",
            image_path=str(image_path or ""),
            asset_type=location.asset_type,
            slide_order=location.slide_order,
            metadata={"text_length": len("I will kill you tomorrow."), "model": "prebuilt-read"},
        )

    monkeypatch.setattr(AzureOCRProvider, "extract", fake_extract)

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "block"
    assert result["finding_count"] == 1
    assert result["block_render"] is False
    assert finding.provider == "ocr_slide_moderation:local_rules"
    assert finding.provider_raw["ocr_provider"] == "azure_ocr"
    assert project.moderation_summary["ocr_slide_scan"]["finding_count"] == 1
    assert project.moderation_status == "approved"


def test_noop_provider_behavior_unchanged(tmp_path):
    image_path = tmp_path / "missing.png"
    result = NoopOCRProvider().extract(str(image_path), _location())

    assert result.provider == "noop_ocr"
    assert result.text == ""
    assert result.success is True
    assert result.metadata["noop"] is True


def test_ocr_settings_default_disabled_noop():
    assert settings.OCR_MODERATION_PROVIDER == "noop"
    assert settings.OCR_MODERATION_AUTO_ENABLED is False
    assert settings.AZURE_OCR_ENABLED is False
    assert settings.AZURE_OCR_KEY == ""
