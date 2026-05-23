# pyright: reportMissingImports=false

import builtins
import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings

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

from ai_agents.models import AgentFinding, AgentRun  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.ocr_bridge import OCRTextResult  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, *, title: str = "Auto OCR moderation lesson") -> Project:
    return Project.objects.create(
        title=title,
        user=_make_teacher(username),
        status="processing",
        moderation_status="approved",
    )


def _slide(path: Path | str, *, index: int = 0, page_key: str = "slide-1") -> dict:
    return {
        "index": index,
        "slide_num": index + 1,
        "source_slide_index": index,
        "page_key": page_key,
        "image_path": str(path),
    }


def _enable_ocr(monkeypatch, *, block: bool = False, slides: bool = True) -> None:
    monkeypatch.setattr(settings, "OCR_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_BLOCK_RENDER_ON_REJECTION", block, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_PHASE", "ocr_slide_scan", raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_SCAN_SLIDES", slides, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_PROVIDER", "noop", raising=False)


def _patch_ocr_text(monkeypatch, text: str):
    class FakeOCRBridge:
        def __init__(self, provider=None):
            self.provider = provider

        def extract(self, image_path="", location=None, **_kwargs):
            return OCRTextResult(
                text=text,
                location=location,
                provider="fake_ocr",
                success=True,
                error_message="",
                image_path=str(image_path or ""),
                asset_type=location.asset_type if location is not None else "ocr_text",
                slide_order=location.slide_order if location is not None else None,
                metadata={"fake": True},
            )

    monkeypatch.setattr("worker.ai_agents.ocr_bridge.OCRBridge", FakeOCRBridge)


@pytest.mark.django_db
def test_auto_ocr_disabled_does_not_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OCR_MODERATION_AUTO_ENABLED", False, raising=False)
    project = _make_project("auto_ocr_disabled_teacher")
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"placeholder")

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    assert result["status"] == "skipped_disabled"
    assert result["block_render"] is False
    assert AgentRun.objects.filter(project=project, phase="ocr_slide_scan").count() == 0


@pytest.mark.django_db
def test_auto_ocr_enabled_noop_completes_without_findings_or_text_scan(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch)

    def fail_scan_text(*_args, **_kwargs):
        raise AssertionError("Text moderation should not run for empty noop OCR text")

    monkeypatch.setattr("worker.ai_agents.providers.local_rules_provider.LocalRulesProvider.scan_text", fail_scan_text)
    project = _make_project("auto_ocr_noop_teacher")
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"placeholder")

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    project.refresh_from_db()
    run = AgentRun.objects.get(project=project, phase="ocr_slide_scan")
    assert result["status"] == "done"
    assert result["final_decision"] == "needs_admin_review"
    assert result["finding_count"] == 0
    assert result["text_asset_count"] == 0
    assert result["block_render"] is False
    assert run.status == "done"
    assert run.final_decision == "needs_admin_review"
    assert project.moderation_summary["ocr_slide_scan"]["final_decision"] == "needs_admin_review"


@pytest.mark.django_db
def test_auto_ocr_missing_slide_image_requires_admin_review(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch, block=True)
    project = _make_project("auto_ocr_missing_teacher")
    missing_path = tmp_path / "missing.png"

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(missing_path)])

    assert result["status"] == "done"
    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is True
    assert AgentFinding.objects.filter(run__project=project).count() == 0


@pytest.mark.django_db
def test_auto_ocr_empty_text_skips_text_moderation(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch)
    _patch_ocr_text(monkeypatch, "")

    def fail_scan_text(*_args, **_kwargs):
        raise AssertionError("Text moderation should not run for empty OCR text")

    monkeypatch.setattr("worker.ai_agents.providers.local_rules_provider.LocalRulesProvider.scan_text", fail_scan_text)
    project = _make_project("auto_ocr_empty_teacher")
    image_path = tmp_path / "empty.png"
    image_path.write_bytes(b"placeholder")

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    assert result["final_decision"] == "needs_admin_review"
    assert result["finding_count"] == 0
    assert result["text_asset_count"] == 0


@pytest.mark.django_db
def test_auto_ocr_unsafe_text_creates_finding_and_revision_required_status(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch)
    _patch_ocr_text(monkeypatch, "I will kill you tomorrow.")
    project = _make_project("auto_ocr_unsafe_teacher")
    image_path = tmp_path / "unsafe.png"
    image_path.write_bytes(b"placeholder")

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path, index=2, page_key="p3")])

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "block"
    assert result["finding_count"] == 1
    assert result["block_render"] is False
    assert finding.content_type == "ocr"
    assert finding.object_type == "slide_image_ocr"
    assert finding.location["slide_order"] == 2
    assert finding.location["page_key"] == "p3"
    assert finding.location["asset_type"] == "ocr_text"
    assert finding.provider == "ocr_slide_moderation:local_rules"
    assert finding.provider_raw["ocr_text_length"] == len("I will kill you tomorrow.")
    assert project.moderation_status == "revision_required"
    assert project.moderation_summary["ocr_slide_scan"]["finding_count"] == 1
    assert "visual_asset_scan" not in project.moderation_summary


@pytest.mark.django_db
def test_auto_ocr_block_flag_false_continues_on_unsafe_text(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch, block=False)
    _patch_ocr_text(monkeypatch, "I will kill you tomorrow.")
    project = _make_project("auto_ocr_no_block_teacher")
    image_path = tmp_path / "unsafe-no-block.png"
    image_path.write_bytes(b"placeholder")

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    assert result["final_decision"] == "block"
    assert result["block_render"] is False


@pytest.mark.django_db
def test_auto_ocr_block_flag_true_blocks_unsafe_text(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch, block=True)
    _patch_ocr_text(monkeypatch, "I will kill you tomorrow.")
    project = _make_project("auto_ocr_block_teacher")
    image_path = tmp_path / "unsafe-block.png"
    image_path.write_bytes(b"placeholder")

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    assert result["final_decision"] == "block"
    assert result["block_render"] is True


@pytest.mark.django_db
def test_text_moderation_behavior_remains_unchanged():
    project = _make_project("auto_ocr_text_unchanged_teacher")
    project.description = "I will kill you tomorrow."
    project.save(update_fields=["description", "updated_at"])

    result = ModerationOrchestrator().run(project.id, triggered_by_user_id=project.user_id)

    project.refresh_from_db()
    assert result["final_decision"] == "block"
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_auto_ocr_does_not_import_video_frame_sampling(monkeypatch, tmp_path):
    _enable_ocr(monkeypatch)
    project = _make_project("auto_ocr_no_video_teacher")
    image_path = tmp_path / "safe.png"
    image_path.write_bytes(b"placeholder")
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "video_frame_moderation" in str(name):
            raise AssertionError(f"Unexpected video frame import during OCR scan: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = worker_tasks._run_auto_ocr_slide_moderation_after_export(project.id, [_slide(image_path)])

    assert result["final_decision"] == "needs_admin_review"
