# pyright: reportMissingImports=false

import json
import os
import sys
from io import StringIO
from pathlib import Path

import django
import pytest
import requests
from django.conf import settings
from PIL import Image

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

from ai_agents.management.commands.moderation_system_status import collect_moderation_system_status  # noqa: E402
from ai_agents.models import AgentFinding, AgentRun  # noqa: E402
from ai_agents.policies import project_can_publish  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.providers.visual_safety_provider import (  # noqa: E402
    AzureContentSafetyVisualProvider,
    build_visual_safety_provider,
)
from worker.ai_agents.schemas import FindingLocation  # noqa: E402
from worker.ai_agents.video_frame_moderation import SampledVideoFrame, VideoFrameSamplingResult  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, *, status: str = "ready", moderation_status: str = "approved") -> Project:
    return Project.objects.create(
        title=f"Visual safety {username}",
        user=_make_teacher(username),
        status=status,
        moderation_status=moderation_status,
        is_published=False,
    )


def _save_image(path: Path, *, size: tuple[int, int] = (24, 24)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(24, 80, 140)).save(path)
    return path


def _location(project_id: int = 1) -> FindingLocation:
    return FindingLocation(project_id=project_id, asset_type="slide_image", image_path="image.png")


def _enable_azure_visual_safety(monkeypatch) -> None:
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_TIMEOUT_SECONDS", 20, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_MAX_IMAGE_BYTES", 10485760, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "https://example.cognitiveservices.azure.com", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "test-secret-key", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_API_VERSION", "2024-09-01", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_CATEGORIES", "sexual,violence,self_harm,hate", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_BLOCK_SEVERITY", 4, raising=False)


def _enable_visual_moderation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_PHASE", "visual_asset_scan", raising=False)


def _enable_video_frame_audit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_PHASE", "video_frame_audit", raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_EVERY_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_MAX_FRAMES", 5, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK", True, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RUN_OCR", False, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION", False, raising=False)


def _mock_azure_response(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        AzureContentSafetyVisualProvider,
        "_submit_image",
        lambda self, *, endpoint, key, image_bytes: payload,
    )


def _sampling_success(frame_path: Path) -> VideoFrameSamplingResult:
    return VideoFrameSamplingResult(
        video_path="final.mp4",
        output_dir=str(frame_path.parent),
        sampled_frames=[
            SampledVideoFrame(
                frame_path=str(frame_path),
                timestamp_seconds=0.0,
                timestamp_label="00:00:00",
            )
        ],
        success=True,
        error_message="",
        ffmpeg_path="ffmpeg",
    )


def test_default_visual_safety_provider_none_makes_no_external_call(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "safe.png")
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)

    def fail_post(*_args, **_kwargs):
        raise AssertionError("Azure should not be called for provider=none")

    monkeypatch.setattr("worker.ai_agents.providers.visual_safety_provider.requests.post", fail_post)

    result = build_visual_safety_provider().review_image(str(image_path), _location())

    assert result.provider == "noop_visual"
    assert result.decision == "allow"
    assert result.findings == []


def test_classifier_disabled_skips_safely(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "safe.png")
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "https://example.test", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "secret", raising=False)

    result = AzureContentSafetyVisualProvider().review_image(str(image_path), _location())

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["skipped"] is True
    assert result.metadata["reason"] == "visual_safety_classifier_disabled"


def test_missing_azure_endpoint_or_key_skips_safely(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "safe.png")
    _enable_azure_visual_safety(monkeypatch)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "", raising=False)

    result = AzureContentSafetyVisualProvider().review_image(str(image_path), _location())

    assert result.decision == "allow"
    assert result.metadata["skipped"] is True
    assert result.metadata["reason"] == "azure_content_safety_missing_config"


def test_oversized_image_skips_safely(monkeypatch, tmp_path):
    image_path = tmp_path / "large.bin"
    image_path.write_bytes(b"x" * 128)
    _enable_azure_visual_safety(monkeypatch)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_MAX_IMAGE_BYTES", 16, raising=False)

    result = AzureContentSafetyVisualProvider().review_image(str(image_path), _location())

    assert result.decision == "allow"
    assert result.metadata["skipped"] is True
    assert result.metadata["reason"] == "image_too_large"
    assert result.metadata["file_size_bytes"] == 128


def test_mocked_azure_safe_result_creates_no_blocking_findings(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "safe.png")
    _enable_azure_visual_safety(monkeypatch)
    monkeypatch.setattr(
        "worker.ai_agents.providers.visual_safety_provider.requests.post",
        lambda *_args, **_kwargs: _FakeResponse(
            {
                "categoriesAnalysis": [
                    {"category": "Sexual", "severity": 0},
                    {"category": "Violence", "severity": 0},
                ]
            }
        ),
    )

    result = AzureContentSafetyVisualProvider().review_image(str(image_path), _location())

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["skipped"] is False
    assert "test-secret-key" not in json.dumps(result.metadata)


def test_mocked_azure_unsafe_result_maps_findings(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "unsafe.png")
    _enable_azure_visual_safety(monkeypatch)
    _mock_azure_response(
        monkeypatch,
        {
            "categoriesAnalysis": [
                {"category": "Violence", "severity": 4},
                {"category": "Sexual", "severity": 6},
            ]
        },
    )

    result = AzureContentSafetyVisualProvider().review_image(str(image_path), _location())

    assert result.decision == "block"
    assert {finding.category for finding in result.findings} == {"violence", "sexual"}
    assert {finding.severity for finding in result.findings} == {"high", "critical"}
    assert all(finding.decision == "block" for finding in result.findings)


def test_azure_timeout_or_api_error_fails_open(monkeypatch, tmp_path):
    image_path = _save_image(tmp_path / "timeout.png")
    _enable_azure_visual_safety(monkeypatch)

    def raise_timeout(*_args, **_kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr("worker.ai_agents.providers.visual_safety_provider.requests.post", raise_timeout)

    result = AzureContentSafetyVisualProvider().review_image(str(image_path), _location())

    assert result.decision == "allow"
    assert result.findings == []
    assert result.metadata["provider_error"] is True
    assert result.metadata["reason"] == "azure_content_safety_timeout"


@pytest.mark.django_db
def test_visual_asset_moderation_includes_mocked_provider_findings(monkeypatch, tmp_path):
    _enable_visual_moderation(monkeypatch)
    _enable_azure_visual_safety(monkeypatch)
    _mock_azure_response(monkeypatch, {"categoriesAnalysis": [{"category": "Violence", "severity": 4}]})
    project = _make_project("visual_integration", status="processing", moderation_status="approved")
    image_path = _save_image(tmp_path / "slide.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        [{"index": 0, "source_slide_index": 0, "page_key": "s1-p1", "image_path": str(image_path)}],
    )

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project, provider="azure_content_safety")
    assert result["status"] == "done"
    assert result["finding_count"] == 1
    assert result["block_render"] is False
    assert finding.category == "violence"
    assert project.moderation_status == "approved"
    assert "azure_content_safety" in project.moderation_summary["visual_asset_scan"]["providers"]


@pytest.mark.django_db
def test_video_frame_audit_includes_mocked_provider_findings(monkeypatch, tmp_path):
    _enable_video_frame_audit(monkeypatch)
    _enable_azure_visual_safety(monkeypatch)
    _mock_azure_response(monkeypatch, {"categoriesAnalysis": [{"category": "Sexual", "severity": 4}]})
    project = _make_project("frame_integration")
    frame_path = _save_image(tmp_path / "frame.jpg")
    monkeypatch.setattr(
        "worker.ai_agents.video_frame_moderation.sample_video_frames",
        lambda **_kwargs: _sampling_success(frame_path),
    )

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 22, tmp_path / "final.mp4")

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project, provider="azure_content_safety")
    assert result["status"] == "done"
    assert result["finding_count"] == 1
    assert finding.content_type == "video_frame"
    assert finding.category == "sexual"
    assert project.moderation_status == "approved"
    assert "azure_content_safety" in project.moderation_summary["video_frame_audit"]["visual_providers"]


@pytest.mark.django_db
def test_project_moderation_status_unchanged_after_visual_safety_findings(monkeypatch, tmp_path):
    _enable_visual_moderation(monkeypatch)
    _enable_azure_visual_safety(monkeypatch)
    _mock_azure_response(monkeypatch, {"categoriesAnalysis": [{"category": "Violence", "severity": 4}]})
    project = _make_project("status_unchanged", status="processing", moderation_status="approved")
    image_path = _save_image(tmp_path / "slide.png")

    worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [{"image_path": str(image_path)}])

    project.refresh_from_db()
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_visual_publish_gate_blocks_only_when_enabled(settings):
    project = _make_project("visual_gate")
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="block",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_safety_azure_content_safety",
        agent_version="azure-content-safety:v1",
        content_type="image",
        object_type="slide_image",
        object_id="1",
        location={"asset_type": "slide_image", "slide_order": 1},
        category="violence",
        severity="high",
        confidence=0.95,
        decision="block",
        provider="azure_content_safety",
    )

    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = False
    assert project_can_publish(project) is True
    settings.VISUAL_MODERATION_BLOCK_PUBLISH_ON_REJECTION = True
    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_video_frame_publish_gate_blocks_only_when_enabled(settings):
    project = _make_project("frame_gate")
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="video_frame_audit",
        status="done",
        final_decision="block",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_safety_azure_content_safety",
        agent_version="azure-content-safety:v1",
        content_type="video_frame",
        object_type="video_frame",
        object_id="0.000",
        location={"asset_type": "video_frame", "timestamp_seconds": 0.0},
        category="sexual",
        severity="high",
        confidence=0.95,
        decision="block",
        provider="azure_content_safety",
    )

    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = False
    assert project_can_publish(project) is True
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = True
    assert project_can_publish(project) is False


def test_diagnostics_do_not_print_azure_content_safety_key(settings):
    settings.VISUAL_SAFETY_PROVIDER = "azure_content_safety"
    settings.VISUAL_SAFETY_CLASSIFIER_ENABLED = True
    settings.AZURE_CONTENT_SAFETY_ENABLED = True
    settings.AZURE_CONTENT_SAFETY_ENDPOINT = "https://example.cognitiveservices.azure.com"
    settings.AZURE_CONTENT_SAFETY_KEY = "super-secret-key-value"

    status = collect_moderation_system_status()
    rendered_json = json.dumps(status, sort_keys=True)
    stdout = StringIO()
    call_command("moderation_system_status", stdout=stdout)
    rendered_text = stdout.getvalue()

    assert status["visual_ocr_video_providers"]["azure_content_safety_key_configured"] is True
    assert "super-secret-key-value" not in rendered_json
    assert "super-secret-key-value" not in rendered_text
