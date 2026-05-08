# pyright: reportMissingImports=false

import os
import sys
from io import StringIO
from pathlib import Path

import django
import pytest
from django.core.management import call_command

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
from worker.ai_agents.ocr_bridge import OCRBridge, OCRTextResult  # noqa: E402
from worker.ai_agents.providers.noop_ocr_provider import NoopOCRProvider  # noqa: E402
from worker.ai_agents.schemas import FindingLocation  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str) -> Project:
    return Project.objects.create(
        title="OCR bridge lesson",
        user=_make_teacher(username),
        status="ready",
    )


def test_noop_ocr_provider_returns_empty_text_safely(tmp_path):
    image_path = tmp_path / "missing.png"
    location = FindingLocation(asset_type="ocr_text", image_path=str(image_path))

    result = NoopOCRProvider().extract(str(image_path), location)

    assert isinstance(result, OCRTextResult)
    assert result.text == ""
    assert result.provider == "noop_ocr"
    assert result.success is True
    assert result.error_message == ""
    assert result.metadata["noop"] is True
    assert result.metadata["asset_missing"] is True


def test_ocr_bridge_handles_missing_image_path_without_crash():
    result = OCRBridge().extract(image_path=None)

    assert result.text == ""
    assert result.success is True
    assert result.image_path == ""
    assert result.metadata["asset_missing"] is True


def test_ocr_result_preserves_asset_type_and_slide_order_metadata(tmp_path):
    image_path = tmp_path / "slide.png"

    result = OCRBridge().extract(
        image_path=str(image_path),
        asset_type="slide_image",
        slide_order=3,
        project_id=99,
        ui_anchor="manual-slide-3-ocr",
    )

    assert result.asset_type == "slide_image"
    assert result.slide_order == 3
    assert result.location.asset_type == "slide_image"
    assert result.location.slide_order == 3
    assert result.metadata["location"]["project_id"] == 99
    assert result.metadata["location"]["ui_anchor"] == "manual-slide-3-ocr"


@pytest.mark.django_db
def test_run_ocr_bridge_command_loads_and_report_mode_works(tmp_path):
    out = StringIO()
    image_path = tmp_path / "missing-cover.png"

    call_command("run_ocr_bridge", image_path=str(image_path), asset_type="cover", stdout=out)

    output = out.getvalue()
    assert "Provider: noop_ocr" in output
    assert "Extracted text length: 0" in output
    assert "Asset type: cover" in output


@pytest.mark.django_db
def test_run_ocr_bridge_with_moderate_text_and_empty_ocr_text_does_not_crash(tmp_path):
    project = _make_project("ocr_bridge_moderate_empty_teacher")
    out = StringIO()

    call_command(
        "run_ocr_bridge",
        image_path=str(tmp_path / "missing-slide.png"),
        asset_type="slide_image",
        slide_order=0,
        project_id=project.id,
        moderate_text=True,
        stdout=out,
    )

    output = out.getvalue()
    assert "Moderation decision: skipped_empty_text" in output
    assert "Moderation finding count: 0" in output


def test_ocr_bridge_does_not_require_tesseract_or_external_api(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    monkeypatch.setenv("AI_AGENTS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("AI_AGENTS_OLLAMA_BASE_URL", "http://example.invalid")

    result = OCRBridge().extract(str(tmp_path / "missing.png"))

    assert result.provider == "noop_ocr"
    assert result.text == ""
    assert result.success is True
