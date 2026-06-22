# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest
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

from ai_agents.models import AgentFinding  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _project(username: str) -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title="Visual path test", user=user, status="processing")


def _png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 18), color=(250, 250, 250)).save(path)
    return path


def _slide(path: str, *, whiteboard: bool = False) -> dict:
    return {
        "index": 0,
        "slide_num": 1,
        "source_slide_index": 0,
        "source_slide_num": 1,
        "split_index": 0,
        "page_key": "s1-p1",
        "image_path": path,
        "notes_text": "This is a short render smoke test.",
        "original_text": "This is a short render smoke test.",
        "narration_text": "This is a short render smoke test.",
        "subtitle_chunks": ["This is a short render smoke test."],
        "source_type": "txt" if whiteboard else "pptx",
        "whiteboard_mode": whiteboard,
    }


def _enable_visual(monkeypatch, *, cover: bool = True, block: bool = True) -> None:
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", block, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_PHASE", "visual_asset_scan", raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", cover, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)


@pytest.mark.django_db
def test_generated_whiteboard_slide_preserves_existing_moderation_path(tmp_path):
    project = _project("visual_path_whiteboard")
    generated_slide = _png(tmp_path / "slide-1.png")

    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [_slide(str(generated_slide), whiteboard=True)],
    )
    assets = worker_tasks._visual_slide_assets_from_export(synced)

    assert synced[0]["image_path"] == ""
    assert synced[0]["moderation_image_path"] == str(generated_slide)
    assert assets[0]["image_path"] == str(generated_slide)
    assert Path(assets[0]["image_path"]).is_file()


@pytest.mark.django_db
def test_absent_optional_cover_is_not_submitted_as_missing_asset(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True)
    project = _project("visual_path_no_cover")
    generated_slide = _png(tmp_path / "slide.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        [_slide(str(generated_slide))],
    )

    assert result["final_decision"] == "allow"
    assert result["block_render"] is False
    assert result["scanned_asset_count"] == 1
    assert not AgentFinding.objects.filter(run__project=project, object_type="cover").exists()


@pytest.mark.django_db
def test_missing_required_slide_image_fails_closed(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=False, block=True)
    project = _project("visual_path_missing_slide")
    missing_slide = tmp_path / "missing-slide.png"

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        [_slide(str(missing_slide))],
    )

    finding = AgentFinding.objects.get(run__project=project)
    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is True
    assert finding.category == "provider_unavailable"
    assert finding.evidence_excerpt == "missing_image_file"
    assert finding.location["asset_type"] == "slide_image"


@pytest.mark.django_db
def test_no_avatar_no_cover_safe_generated_slide_passes_visual_gate(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, cover=True, block=True)
    project = _project("visual_path_pipeline_progress")
    project.avatar_enabled_override = False
    project.save(update_fields=["avatar_enabled_override"])
    generated_slide = _png(tmp_path / "generated-slide.png")
    synced = worker_tasks._sync_transcript_pages_from_export(
        project.id,
        [_slide(str(generated_slide), whiteboard=True)],
    )

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, synced)

    assert result["status"] == "done"
    assert result["final_decision"] == "allow"
    assert result["finding_count"] == 0
    assert result["block_render"] is False
