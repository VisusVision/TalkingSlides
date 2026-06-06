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

from core.models import Project, Slide, UserProfile  # noqa: E402
from worker.ai_agents.ocr_bridge import OCRBridge  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402
from worker.ai_agents.providers.noop_visual_provider import NoopVisualProvider  # noqa: E402
from worker.ai_agents.schemas import AgentResultSchema, FindingLocation  # noqa: E402
from worker.ai_agents.video_frame_moderation import VideoFrameModerationAgent  # noqa: E402
from worker.ai_agents.visual_moderation import VisualModerationAgent  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, title: str = "Visual moderation lesson") -> Project:
    return Project.objects.create(
        title=title,
        user=_make_teacher(username),
        status="ready",
    )


def test_noop_visual_provider_allows_missing_image_without_findings():
    provider = NoopVisualProvider()

    result = provider.review_image(
        "",
        FindingLocation(project_id=123, asset_type="cover", image_path=""),
    )

    assert isinstance(result, AgentResultSchema)
    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["asset_missing"] is True
    assert result.metadata["location"]["asset_type"] == "cover"


@pytest.mark.django_db
def test_cover_image_scan_returns_agent_result_schema():
    project = _make_project("visual_cover_teacher")

    result = VisualModerationAgent().scan_cover_image(project)

    assert isinstance(result, AgentResultSchema)
    assert result.modality == "image"
    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["location"]["project_id"] == project.id
    assert result.metadata["location"]["asset_type"] == "cover"


@pytest.mark.django_db
def test_slide_image_scan_interface_includes_slide_order_location():
    project = _make_project("visual_slide_teacher")
    slide = Slide.objects.create(project=project, order=2, narration_text="Slide narration")

    result = VisualModerationAgent().scan_slide_images(project)

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["scanned_asset_count"] == 1

    single_result = VisualModerationAgent().scan_slide_image(
        project_id=project.id,
        image_path=str(slide.image_file or ""),
        slide_order=2,
        page_key="slide-3",
        ui_anchor=f"slide-{slide.id}-image",
    )
    location = single_result.metadata["location"]
    assert location["asset_type"] == "slide_image"
    assert location["slide_order"] == 2
    assert location["page_key"] == "slide-3"
    assert location["ui_anchor"] == f"slide-{slide.id}-image"


@pytest.mark.django_db
def test_video_frame_scan_interface_includes_timestamp_location():
    project = _make_project("visual_frame_teacher")

    result = VideoFrameModerationAgent().scan_frame(
        project_id=project.id,
        frame_path="",
        timestamp_seconds=12.5,
        timestamp_label="00:12",
        slide_order=0,
        ui_anchor="video-frame-12",
    )

    location = result.metadata["location"]
    assert result.modality == "video_frame"
    assert result.decision == "allow"
    assert result.findings == []
    assert location["asset_type"] == "video_frame"
    assert location["timestamp_seconds"] == 12.5
    assert location["timestamp_label"] == "00:12"
    assert location["ui_anchor"] == "video-frame-12"


def test_ocr_bridge_placeholder_returns_empty_text_without_crashing():
    bridge = OCRBridge()

    text = bridge.extract_text("missing-slide.png")
    result = bridge.extract("missing-slide.png")

    assert text == ""
    assert result.text == ""
    assert result.location.asset_type == "ocr_text"
    assert result.metadata["noop"] is True


@pytest.mark.django_db
def test_orchestrator_visual_methods_are_optional_and_noop():
    project = _make_project("visual_orchestrator_teacher")

    result = ModerationOrchestrator().scan_project_visual_assets(project)

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["noop"] is True


def test_visual_interfaces_do_not_require_external_api_or_image_library(monkeypatch):
    monkeypatch.setenv("AI_AGENTS_LOCAL_LLM_ENABLED", "1")
    monkeypatch.setenv("AI_AGENTS_OLLAMA_BASE_URL", "http://example.invalid")

    result = NoopVisualProvider().review_image(
        "missing-image.png",
        FindingLocation(project_id=456, asset_type="slide_image", image_path="missing-image.png"),
    )

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["noop"] is True
