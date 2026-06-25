# pyright: reportMissingImports=false

import builtins
from io import BytesIO
import os
import sys
from pathlib import Path

import django
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test.utils import override_settings
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
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from ai_agents.models import AgentFinding, AgentRun, PublicationBlockEvent  # noqa: E402
from ai_agents.policies import project_can_publish  # noqa: E402
from ai_agents.serializers import moderation_summary_payload  # noqa: E402
from core import views  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents import schemas  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402
from worker.ai_agents.providers import visual_safety_provider  # noqa: E402
from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, *, title: str = "Auto visual moderation lesson") -> Project:
    return Project.objects.create(
        title=title,
        user=_make_teacher(username),
        status="processing",
    )


def _save_image(path: Path, *, size: tuple[int, int] = (24, 24), mode: str = "RGB") -> Path:
    Image.new(mode, size, color=(24, 80, 140)).save(path)
    return path


def _image_upload(name: str = "image.png") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (24, 24), color=(24, 80, 140)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _slide(path: Path | str, *, index: int = 0, page_key: str = "slide-1") -> dict:
    return {
        "index": index,
        "slide_num": index + 1,
        "source_slide_index": index,
        "page_key": page_key,
        "image_path": str(path),
    }


class _SafeVisualProvider:
    provider_name = "fake_visual_safe"
    agent_slug = "fake_visual_safe"
    agent_version = "test:v1"

    def __init__(self) -> None:
        self.reviewed_paths: list[str] = []

    def review_image(self, image_path, location):
        self.reviewed_paths.append(str(image_path or ""))
        return schemas.AgentResultSchema(
            agent_slug=self.agent_slug,
            agent_version=self.agent_version,
            modality="image",
            provider=self.provider_name,
            decision="allow",
            confidence=0.0,
            findings=[],
            metadata={"skipped": False, "location": location.model_dump(exclude_none=True)},
        )


class _BlockingVisualProvider(_SafeVisualProvider):
    provider_name = "fake_visual_block"
    agent_slug = "fake_visual_block"

    def __init__(self, *, category: str = "sexual", severity: str = "high") -> None:
        super().__init__()
        self.category = category
        self.severity = severity

    def review_image(self, image_path, location):
        self.reviewed_paths.append(str(image_path or ""))
        return schemas.AgentResultSchema(
            agent_slug=self.agent_slug,
            agent_version=self.agent_version,
            modality="image",
            provider=self.provider_name,
            decision="block",
            confidence=0.95,
            findings=[
                schemas.AgentFindingSchema(
                    category=self.category,
                    severity=self.severity,
                    confidence=0.95,
                    decision="block",
                    location=location,
                    user_message="Visual moderation test block.",
                    admin_message="Visual moderation test block.",
                )
            ],
            metadata={"skipped": False, "location": location.model_dump(exclude_none=True)},
        )


def _enable_visual(
    monkeypatch,
    *,
    block: bool = False,
    cover: bool = False,
    slides: bool = True,
    allow_weak: bool = True,
) -> None:
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", block, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_PHASE", "visual_asset_scan", raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", cover, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", slides, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", allow_weak, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)


def _enable_safe_visual_provider(monkeypatch) -> _SafeVisualProvider:
    provider = _SafeVisualProvider()
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(visual_safety_provider, "build_visual_safety_provider", lambda: provider)
    return provider


@pytest.mark.django_db
def test_auto_visual_disabled_does_not_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", False, raising=False)
    project = _make_project("auto_visual_disabled_teacher")
    image_path = _save_image(tmp_path / "slide.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    assert result["status"] == "skipped_disabled"
    assert result["block_render"] is False
    assert AgentRun.objects.filter(project=project, phase="visual_asset_scan").count() == 0


@pytest.mark.django_db
def test_auto_visual_enabled_records_allow_run_for_valid_slide(monkeypatch, tmp_path):
    _enable_visual(monkeypatch)
    project = _make_project("auto_visual_valid_teacher")
    image_path = _save_image(tmp_path / "valid-slide.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    project.refresh_from_db()
    run = AgentRun.objects.get(project=project, phase="visual_asset_scan")
    assert result["status"] == "done"
    assert result["final_decision"] == "allow"
    assert result["finding_count"] == 0
    assert result["block_render"] is False
    assert run.status == "done"
    assert run.final_decision == "allow"
    assert project.moderation_summary["visual_asset_scan"]["final_decision"] == "allow"


@pytest.mark.django_db
def test_unsafe_cover_then_safe_cover_rerun_clears_visual_block(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=False, allow_weak=False)
    project = _make_project("auto_visual_cover_clear")
    project.status = "ready"
    project.moderation_status = "approved"
    project.save(update_fields=["status", "moderation_status", "updated_at"])
    cover_path = tmp_path / "uploads" / str(project.id) / "cover.png"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    _save_image(cover_path)
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])

    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    provider = _BlockingVisualProvider()
    monkeypatch.setattr(visual_safety_provider, "build_visual_safety_provider", lambda: provider)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        blocked = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    assert blocked["final_decision"] == "block"
    assert project.moderation_status == "revision_required"

    safe_provider = _SafeVisualProvider()
    monkeypatch.setattr(visual_safety_provider, "build_visual_safety_provider", lambda: safe_provider)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        allowed = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    assert allowed["final_decision"] == "allow"
    assert project.moderation_status == "approved"
    assert project.moderation_summary["visual_asset_scan"]["final_decision"] == "allow"


@pytest.mark.django_db
def test_old_provider_unavailable_for_previous_asset_does_not_block_current_safe_asset(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=False, allow_weak=False)
    project = _make_project("auto_visual_provider_unavailable_clear")
    project.status = "ready"
    project.moderation_status = "needs_admin_review"
    project.moderation_summary = {
        "moderation_status": "needs_admin_review",
        "message": "Visual moderation requires admin review before publication.",
        "visual_asset_scan": {
            "final_decision": "needs_admin_review",
            "provider_errors": [{"reason": "semantic_visual_provider_unavailable"}],
            "asset_type": "cover",
            "asset_path": f"uploads/{project.id}/old-cover.png",
        },
    }
    project.save(update_fields=["status", "moderation_status", "moderation_summary", "updated_at"])
    old_run = AgentRun.objects.create(
        project=project,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="needs_admin_review",
        summary=project.moderation_summary["visual_asset_scan"],
    )
    AgentFinding.objects.create(
        run=old_run,
        agent_slug="visual_safety_provider_unavailable",
        agent_version="test:v1",
        content_type="image",
        object_type="cover",
        object_id="cover",
        location={"asset_type": "cover", "image_path": f"uploads/{project.id}/old-cover.png"},
        category="provider_unavailable",
        severity="high",
        confidence=1.0,
        decision="needs_admin_review",
        user_message="Old provider unavailable.",
        admin_message="Old provider unavailable.",
        provider="visual_safety_provider_unavailable",
    )
    PublicationBlockEvent.objects.create(
        project=project,
        run=old_run,
        blocked_by="visual_safety_provider_unavailable",
        reason_category="provider_unavailable",
        highest_severity="high",
        message_to_user="Old visual provider unavailable.",
    )
    cover_path = tmp_path / "uploads" / str(project.id) / "safe-cover.png"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    _save_image(cover_path)
    project.cover_image_original = f"uploads/{project.id}/safe-cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(visual_safety_provider, "build_visual_safety_provider", lambda: _SafeVisualProvider())

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    payload = moderation_summary_payload(project)
    assert result["final_decision"] == "allow"
    assert PublicationBlockEvent.objects.filter(project=project, resolved=False).count() == 0
    assert project.moderation_status == "approved"
    assert payload["moderation_status"] == "approved"
    assert payload["can_publish"] is True
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_latest_safe_visual_run_updates_effective_publish_status(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=False, allow_weak=False)
    project = _make_project("auto_visual_effective_status")
    project.status = "ready"
    project.moderation_status = "needs_admin_review"
    project.moderation_summary = {
        "moderation_status": "needs_admin_review",
        "message": "Visual moderation requires admin review before publication.",
        "visual_asset_scan": {"final_decision": "needs_admin_review", "asset_type": "cover"},
    }
    project.save(update_fields=["status", "moderation_status", "moderation_summary", "updated_at"])
    cover_path = tmp_path / "uploads" / str(project.id) / "cover.png"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    _save_image(cover_path)
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(visual_safety_provider, "build_visual_safety_provider", lambda: _SafeVisualProvider())

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    payload = moderation_summary_payload(project)
    assert payload["raw_moderation_status"] == "approved"
    assert payload["moderation_status"] == "approved"
    assert payload["can_publish"] is True
    assert payload["publish_block_reason"] == ""


@pytest.mark.django_db
def test_visual_recheck_changes_run_and_clears_stale_block(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=False, allow_weak=False)
    project = _make_project("auto_visual_recheck_clear")
    project.status = "ready"
    project.moderation_status = "needs_admin_review"
    project.moderation_summary = {
        "moderation_status": "needs_admin_review",
        "message": "Visual moderation requires admin review before publication.",
        "visual_asset_scan": {"final_decision": "needs_admin_review", "asset_type": "cover"},
    }
    project.save(update_fields=["status", "moderation_status", "moderation_summary", "updated_at"])
    cover_path = tmp_path / "uploads" / str(project.id) / "cover.png"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    _save_image(cover_path)
    project.cover_image_original = f"uploads/{project.id}/cover.png"
    project.cover_image_processed = project.cover_image_original
    project.save(update_fields=["cover_image_original", "cover_image_processed", "updated_at"])
    first_run = AgentRun.objects.create(
        project=project,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="needs_admin_review",
    )
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(visual_safety_provider, "build_visual_safety_provider", lambda: _SafeVisualProvider())

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    project.refresh_from_db()
    latest_run = AgentRun.objects.filter(project=project, phase="visual_asset_scan").order_by("-created_at", "-id").first()
    assert result["run_id"] != first_run.id
    assert latest_run.id == result["run_id"]
    assert latest_run.final_decision == "allow"
    assert project.moderation_status == "approved"
    assert moderation_summary_payload(project)["can_publish"] is True


@pytest.mark.django_db
def test_render_completion_does_not_republish_unresolved_moderation_block():
    project = _make_project("auto_visual_render_completion_blocked")
    project.status = "processing"
    project.moderation_status = "needs_admin_review"
    project.is_published = True
    project.save(update_fields=["status", "moderation_status", "is_published"])

    worker_tasks._mark_project_ready_after_successful_render(project.id)

    project.refresh_from_db()
    assert project.status == "ready"
    assert project.is_published is False


@pytest.mark.django_db
def test_weak_local_rules_do_not_semantically_approve_by_default(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, allow_weak=False)
    project = _make_project("auto_visual_provider_required_teacher")
    project.status = "ready"
    project.moderation_status = "approved"
    project.is_published = True
    project.save(update_fields=["status", "moderation_status", "is_published"])
    image_path = _save_image(tmp_path / "valid-provider-required.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project, category="provider_unavailable")
    assert result["final_decision"] == "needs_admin_review"
    assert finding.decision == "needs_admin_review"
    assert finding.location["asset_type"] == "slide_image"
    assert finding.location["slide_order"] == 0
    assert finding.user_message.startswith("We could not complete the visual safety scan")
    assert finding.admin_message.startswith("The semantic visual safety provider did not return")
    assert project.moderation_status == "needs_admin_review"
    assert project.is_published is False
    assert project.moderation_summary["visual_asset_scan"]["provider_errors"][0]["reason"] == "semantic_visual_provider_unavailable"


@pytest.mark.django_db
def test_missing_optional_cover_does_not_create_provider_unavailable(monkeypatch):
    _enable_visual(monkeypatch, cover=True, slides=False, allow_weak=False)
    project = _make_project("auto_visual_missing_optional_cover")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [])

    assert result["status"] == "skipped_no_assets"
    assert AgentRun.objects.filter(project=project, phase="visual_asset_scan").count() == 0
    assert AgentFinding.objects.filter(run__project=project, category="provider_unavailable").count() == 0


@pytest.mark.django_db
def test_missing_optional_cover_plus_valid_slide_still_scans_slide(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=True, allow_weak=False)
    provider = _enable_safe_visual_provider(monkeypatch)
    project = _make_project("auto_visual_missing_cover_valid_slide")
    slide_path = _save_image(tmp_path / "valid-slide.png", size=(512, 512))

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(slide_path)])

    assert result["status"] == "done"
    assert result["final_decision"] == "allow"
    assert provider.reviewed_paths == [str(slide_path)]
    assert AgentFinding.objects.filter(run__project=project, category="provider_unavailable").count() == 0


@pytest.mark.django_db
def test_valid_512_image_passes_with_safe_visual_provider(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, allow_weak=False)
    provider = _enable_safe_visual_provider(monkeypatch)
    project = _make_project("auto_visual_valid_512_safe_provider")
    image_path = _save_image(tmp_path / "safe-512.png", size=(512, 512))

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    assert result["final_decision"] == "allow"
    assert provider.reviewed_paths == [str(image_path)]
    assert AgentFinding.objects.filter(run__project=project).count() == 0


@pytest.mark.django_db
def test_azure_provider_failure_on_real_asset_requires_review(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, allow_weak=False)
    project = _make_project("auto_visual_azure_failure_real_asset")
    image_path = _save_image(tmp_path / "real-asset.png", size=(512, 512))
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "", raising=False)

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    finding = AgentFinding.objects.get(run__project=project, category="provider_unavailable")
    assert result["final_decision"] == "needs_admin_review"
    assert finding.location["asset_type"] == "slide_image"
    assert finding.location["image_path"] == str(image_path)
    assert finding.evidence_excerpt == "azure_content_safety_missing_config"


@pytest.mark.django_db
def test_invalid_real_asset_uses_quality_finding_not_provider_unavailable(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, allow_weak=False)
    project = _make_project("auto_visual_invalid_quality_not_provider")
    corrupt_path = tmp_path / "corrupt-real.png"
    corrupt_path.write_bytes(b"not a real image")
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "azure_content_safety", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_ENDPOINT", "", raising=False)
    monkeypatch.setattr(settings, "AZURE_CONTENT_SAFETY_KEY", "", raising=False)

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(corrupt_path)])

    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "needs_admin_review"
    assert finding.category == "graphic_content"
    assert finding.provider == "local_image_rules"
    assert finding.provider_raw["error"] in {"UnidentifiedImageError", "OSError"}
    assert AgentFinding.objects.filter(run__project=project, category="provider_unavailable").count() == 0


@pytest.mark.django_db
def test_tiny_real_asset_does_not_create_provider_unavailable(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, allow_weak=False)
    project = _make_project("auto_visual_tiny_not_provider")
    tiny_path = _save_image(tmp_path / "tiny.png", size=(1, 1))

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(tiny_path)])

    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "warn"
    assert finding.category == "graphic_content"
    assert finding.severity == "low"
    assert AgentFinding.objects.filter(run__project=project, category="provider_unavailable").count() == 0


@pytest.mark.django_db
def test_auto_visual_missing_required_slide_fails_closed_without_crash(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, block=True)
    project = _make_project("auto_visual_missing_teacher")
    missing_path = tmp_path / "missing.png"

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(missing_path)])

    assert result["status"] == "done"
    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is True
    finding = AgentFinding.objects.get(run__project=project)
    assert finding.category == "provider_unavailable"
    assert finding.evidence_excerpt == "missing_image_file"


@pytest.mark.django_db
def test_auto_visual_corrupt_image_creates_review_finding_without_crash(monkeypatch, tmp_path):
    _enable_visual(monkeypatch)
    project = _make_project("auto_visual_corrupt_teacher")
    corrupt_path = tmp_path / "corrupt.png"
    corrupt_path.write_bytes(b"not a real image")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(corrupt_path)])

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is False
    assert finding.category == "graphic_content"
    assert finding.decision == "needs_admin_review"
    assert finding.provider_raw["error"] in {"UnidentifiedImageError", "OSError"}
    assert project.moderation_status == "needs_admin_review"
    assert project.moderation_summary["visual_asset_scan"]["finding_count"] == 1


@pytest.mark.django_db
def test_auto_visual_large_image_creates_review_finding(monkeypatch, tmp_path):
    _enable_visual(monkeypatch)

    class TinyLimitProvider(LocalImageRulesProvider):
        def __init__(self):
            super().__init__(max_width=10, max_height=10, max_pixels=100)

    monkeypatch.setattr(
        "worker.ai_agents.providers.local_image_rules_provider.LocalImageRulesProvider",
        TinyLimitProvider,
    )
    project = _make_project("auto_visual_large_teacher")
    image_path = _save_image(tmp_path / "large.png", size=(20, 20))

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "needs_admin_review"
    assert finding.severity == "medium"
    assert finding.provider_raw["width"] == 20
    assert finding.provider_raw["height"] == 20


@pytest.mark.django_db
def test_auto_visual_block_flag_false_continues_on_review_finding(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, block=False)
    project = _make_project("auto_visual_no_block_teacher")
    corrupt_path = tmp_path / "review.png"
    corrupt_path.write_bytes(b"bad image")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(corrupt_path)])

    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is False


@pytest.mark.django_db
def test_auto_visual_block_flag_true_blocks_review_finding(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, block=True)
    project = _make_project("auto_visual_block_teacher")
    corrupt_path = tmp_path / "blocked.png"
    corrupt_path.write_bytes(b"bad image")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(corrupt_path)])

    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is True


@pytest.mark.django_db
def test_text_moderation_behavior_remains_unchanged():
    project = _make_project("auto_visual_text_unchanged_teacher")
    project.description = "I will kill you tomorrow."
    project.save(update_fields=["description", "updated_at"])

    result = ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_auto_visual_does_not_import_ocr_video_or_external_providers(monkeypatch, tmp_path):
    _enable_visual(monkeypatch)
    project = _make_project("auto_visual_no_ocr_video_teacher")
    image_path = _save_image(tmp_path / "safe.png")
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        blocked = (
            "ocr_bridge",
            "video_frame_moderation",
            "ollama_provider",
            "translation_provider",
        )
        if any(token in str(name) for token in blocked):
            raise AssertionError(f"Unexpected provider import during visual scan: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(image_path)])

    assert result["final_decision"] == "allow"


@pytest.mark.django_db
def test_cover_upload_triggers_auto_visual_scan_when_enabled(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=False)
    project = _make_project("auto_visual_cover_upload")
    calls = []

    def fake_scan(project_id, export_result, job_id=None, **kwargs):
        calls.append({"project_id": project_id, "export_result": export_result, "job_id": job_id, **kwargs})
        return {"enabled": True, "status": "done", "final_decision": "allow", "finding_count": 0}

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", fake_scan)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/cover/",
        {"cover_file": _image_upload("cover.png")},
        format="multipart",
    )
    force_authenticate(request, user=project.user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectCoverImageView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    project.refresh_from_db()
    assert project.cover_image_original == ""
    assert project.cover_image_processed == ""
    assert project.draft_data["project"]["cover_image_original"].startswith(f"uploads/{project.id}/cover_")
    assert project.draft_data["metadata"]["cover_dirty"] is True
    assert calls == [
        {
            "project_id": project.id,
            "export_result": [],
            "job_id": None,
            "scan_cover": True,
            "scan_slides": False,
            "use_draft": True,
        }
    ]


@pytest.mark.django_db
def test_published_cover_upload_unpublishes_when_visual_scan_requires_review(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, slides=False)
    project = _make_project("auto_visual_cover_unpublish")
    old_cover = tmp_path / "uploads" / str(project.id) / "active-cover.png"
    old_cover.parent.mkdir(parents=True, exist_ok=True)
    _save_image(old_cover)
    old_cover_rel = f"uploads/{project.id}/active-cover.png"
    project.cover_image_original = old_cover_rel
    project.cover_image_processed = old_cover_rel
    project.status = "ready"
    project.moderation_status = "approved"
    project.is_published = True
    project.save(update_fields=["cover_image_original", "cover_image_processed", "status", "moderation_status", "is_published"])
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/lesson.mp4")

    def fake_scan(project_id, export_result, job_id=None):
        locked = Project.objects.get(pk=project_id)
        locked.moderation_status = "needs_admin_review"
        locked.moderation_summary = {"visual_asset_scan": {"final_decision": "needs_admin_review"}}
        locked.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])
        return {"enabled": True, "status": "done", "final_decision": "needs_admin_review", "finding_count": 1}

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", fake_scan)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/cover/",
        {"cover_file": _image_upload("unsafe-cover.png")},
        format="multipart",
    )
    force_authenticate(request, user=project.user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectCoverImageView.as_view()(request, project_id=project.id)

    project.refresh_from_db()
    assert response.status_code == 200
    assert project.moderation_status == "needs_admin_review"
    assert project.is_published is False
    assert project.cover_image_original == old_cover_rel
    assert project.cover_image_processed == old_cover_rel
    assert project.draft_data["project"]["cover_image_original"].startswith(f"uploads/{project.id}/cover_")


@pytest.mark.django_db
def test_custom_background_upload_triggers_auto_visual_scan_when_enabled(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=False, slides=True)
    project = _make_project("auto_visual_background_upload")
    page = project.transcript_pages.create(
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Display",
        narration_text="Narration",
        editor_document={"version": 1},
    )
    calls = []

    def fake_scan(project_id, export_result, job_id=None, **kwargs):
        calls.append({"project_id": project_id, "export_result": export_result, "job_id": job_id, **kwargs})
        return {"enabled": True, "status": "done", "final_decision": "allow", "finding_count": 0}

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", fake_scan)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/",
        {"background_file": _image_upload("background.png")},
        format="multipart",
    )
    force_authenticate(request, user=project.user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.TranscriptPageBackgroundUploadView.as_view()(request, project_id=project.id, page_id=page.id)

    assert response.status_code == 200
    assert calls[0]["project_id"] == project.id
    assert calls[0]["job_id"] is None
    assert calls[0]["scan_cover"] is False
    assert calls[0]["scan_slides"] is True
    assert len(calls[0]["export_result"]) == 1
    assert calls[0]["export_result"][0]["page_key"] == page.page_key
    assert calls[0]["export_result"][0]["transcript_page_id"] == page.id
    assert calls[0]["export_result"][0]["image_path"].endswith(".png")
    assert calls[0]["export_result"][0]["asset_type"] == "custom_background"
    assert calls[0]["export_result"][0]["ui_anchor"] == f"custom-background-{page.id}"


@pytest.mark.django_db
def test_published_background_upload_unpublishes_when_visual_scan_requires_review(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=False, slides=True)
    project = _make_project("auto_visual_background_unpublish")
    project.status = "ready"
    project.moderation_status = "approved"
    project.is_published = True
    project.save(update_fields=["status", "moderation_status", "is_published"])
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/lesson.mp4")
    page = project.transcript_pages.create(
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="s1-p1",
        original_text="Display",
        narration_text="Narration",
        editor_document={"version": 1},
    )

    def fake_scan(project_id, export_result, job_id=None):
        locked = Project.objects.get(pk=project_id)
        locked.moderation_status = "needs_admin_review"
        locked.moderation_summary = {"visual_asset_scan": {"final_decision": "needs_admin_review"}}
        locked.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])
        return {"enabled": True, "status": "done", "final_decision": "needs_admin_review", "finding_count": 1}

    monkeypatch.setattr(worker_tasks, "_run_auto_visual_asset_moderation_after_export", fake_scan)
    request = APIRequestFactory().post(
        f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/",
        {"background_file": _image_upload("unsafe-background.png")},
        format="multipart",
    )
    force_authenticate(request, user=project.user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.TranscriptPageBackgroundUploadView.as_view()(request, project_id=project.id, page_id=page.id)

    project.refresh_from_db()
    assert response.status_code == 200
    assert project.moderation_status == "needs_admin_review"
    assert project.is_published is False
