# pyright: reportMissingImports=false

from io import BytesIO
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
from django.test.utils import override_settings  # noqa: E402

from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents import ocr_bridge, schemas  # noqa: E402
from worker.ai_agents.providers import visual_safety_provider  # noqa: E402


def _png_bytes(label: str = "MODERATION TEST: clean marker") -> bytes:
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    image = Image.new("RGB", (320, 120), color=(245, 247, 250))
    draw = ImageDraw.Draw(image)
    draw.text((12, 45), label, fill=(20, 35, 55))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _make_project(tmp_path: Path, username: str = "visual_owner", *, status: str = "approved") -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="publisher")
    project = Project.objects.create(
        title=f"{username} lesson",
        user=user,
        status="ready",
        moderation_status=status,
        is_published=True,
    )
    cover = tmp_path / "uploads" / str(project.id) / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(_png_bytes())
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    return project


class _FakeVisualProvider:
    provider_name = "fake_visual_provider"
    agent_slug = "fake_visual_provider"
    agent_version = "test:v1"

    def __init__(self, *, decision: str = "allow", category: str = "sexual", severity: str = "high"):
        self.decision = decision
        self.category = category
        self.severity = severity

    def review_image(self, image_path, location):
        findings = []
        if self.decision != "allow":
            findings.append(
                schemas.AgentFindingSchema(
                    category=self.category,
                    severity=self.severity,
                    confidence=0.96,
                    decision=self.decision,
                    location=location,
                    user_message="MODERATION TEST: adult content marker",
                    admin_message="Mocked visual safety signal.",
                )
            )
        return schemas.AgentResultSchema(
            agent_slug=self.agent_slug,
            agent_version=self.agent_version,
            modality="image",
            provider=self.provider_name,
            decision=self.decision,
            confidence=0.96 if findings else 0.0,
            findings=findings,
            metadata={"skipped": False},
        )


class _FakeOCRProvider:
    provider_name = "fake_ocr"

    def __init__(self, text: str):
        self.text = text

    def extract(self, image_path, location):
        return ocr_bridge.OCRTextResult(
            text=self.text,
            location=location,
            provider=self.provider_name,
            success=True,
            image_path=str(image_path or ""),
            metadata={"mock": True},
        )


@pytest.mark.django_db
def test_cover_image_with_mocked_adult_signal_blocks_or_requires_review(monkeypatch, tmp_path):
    project = _make_project(tmp_path, "visual_adult")
    monkeypatch.setattr(
        visual_safety_provider,
        "build_visual_safety_provider",
        lambda: _FakeVisualProvider(decision="block", category="sexual", severity="high"),
    )

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_VISUAL_MODERATION=True,
        VISUAL_MODERATION_AUTO_ENABLED=True,
        VISUAL_SAFETY_PROVIDER="azure_content_safety",
        VISUAL_SAFETY_CLASSIFIER_ENABLED=True,
    ):
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    assert result["final_decision"] in {"block", "needs_admin_review"}
    assert project.moderation_status in {"revision_required", "needs_admin_review"}
    assert project.is_published is False


@pytest.mark.django_db
def test_ocr_text_image_with_risky_text_is_flagged(monkeypatch, tmp_path):
    project = _make_project(tmp_path, "visual_ocr")
    slide = tmp_path / "slide.png"
    slide.write_bytes(_png_bytes("MODERATION TEST: self-harm concern marker"))
    monkeypatch.setattr(
        ocr_bridge,
        "build_ocr_provider",
        lambda provider_name=None: _FakeOCRProvider("how to commit suicide"),
    )

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_VISUAL_MODERATION=True,
        OCR_MODERATION_AUTO_ENABLED=True,
        OCR_MODERATION_PROVIDER="azure",
    ):
        result = worker_tasks._run_auto_ocr_slide_moderation_after_export(
            project.id,
            [{"image_path": str(slide), "source_slide_index": 0, "page_key": "p1"}],
        )

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_visual_provider_unavailable_requires_review_not_silent_approval(tmp_path):
    project = _make_project(tmp_path, "visual_unavailable")

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_VISUAL_MODERATION=True,
        VISUAL_MODERATION_AUTO_ENABLED=True,
        VISUAL_SAFETY_PROVIDER="azure_content_safety",
        VISUAL_SAFETY_CLASSIFIER_ENABLED=True,
        AZURE_CONTENT_SAFETY_ENABLED=True,
        AZURE_CONTENT_SAFETY_ENDPOINT="",
        AZURE_CONTENT_SAFETY_KEY="",
    ):
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    assert result["final_decision"] == "needs_admin_review"
    assert project.moderation_status == "needs_admin_review"
    assert project.is_published is False


@pytest.mark.django_db
def test_historical_educational_ocr_context_not_auto_blocked(monkeypatch, tmp_path):
    project = _make_project(tmp_path, "visual_history")
    slide = tmp_path / "history.png"
    slide.write_bytes(_png_bytes("MODERATION TEST: violence marker"))
    monkeypatch.setattr(
        ocr_bridge,
        "build_ocr_provider",
        lambda provider_name=None: _FakeOCRProvider("Historical educational lesson about war and culture."),
    )

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_VISUAL_MODERATION=True,
        OCR_MODERATION_AUTO_ENABLED=True,
        OCR_MODERATION_PROVIDER="azure",
    ):
        result = worker_tasks._run_auto_ocr_slide_moderation_after_export(
            project.id,
            [{"image_path": str(slide), "source_slide_index": 0, "page_key": "history"}],
        )

    project.refresh_from_db()
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_clean_cover_scan_allows_without_blocking(monkeypatch, tmp_path):
    project = _make_project(tmp_path, "visual_clean")
    monkeypatch.setattr(
        visual_safety_provider,
        "build_visual_safety_provider",
        lambda: _FakeVisualProvider(decision="allow"),
    )

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_VISUAL_MODERATION=True,
        VISUAL_MODERATION_AUTO_ENABLED=True,
        VISUAL_SAFETY_PROVIDER="azure_content_safety",
        VISUAL_SAFETY_CLASSIFIER_ENABLED=True,
    ):
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "approved"
    assert project.moderation_summary["visual_asset_scan"]["final_decision"] == "allow"
