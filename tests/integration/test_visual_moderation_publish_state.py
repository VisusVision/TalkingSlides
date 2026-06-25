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

from django.contrib.auth.models import User  # noqa: E402

from ai_agents.policies import project_can_publish  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _make_project(username: str, *, moderation_status: str = "not_scanned") -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(
        title="Visual moderation publish state",
        user=user,
        status="processing",
        moderation_status=moderation_status,
    )


def _slide(path: Path) -> list[dict]:
    return [{
        "index": 0,
        "slide_num": 1,
        "source_slide_index": 0,
        "page_key": "s1-p1",
        "image_path": str(path),
    }]


def _valid_image(path: Path) -> Path:
    Image.new("RGB", (24, 24), color=(24, 80, 140)).save(path)
    return path


def _enable_local_visual(monkeypatch, *, block_render: bool = True) -> None:
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", block_render, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_PHASE", "visual_asset_scan", raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", False, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)


@pytest.mark.django_db
def test_automatic_visual_allow_updates_project_publish_readiness(monkeypatch, tmp_path):
    _enable_local_visual(monkeypatch)
    project = _make_project("visual_allow_publish_state")
    image_path = _valid_image(tmp_path / "safe.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        _slide(image_path),
    )

    project.refresh_from_db()
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "approved"
    assert project.last_moderation_run_id == result["run_id"]
    assert project.moderation_summary["moderation_status"] == "approved"
    project.status = "ready"
    project.save(update_fields=["status", "updated_at"])
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_automatic_visual_needs_admin_review_does_not_allow_publish(monkeypatch, tmp_path):
    _enable_local_visual(monkeypatch, block_render=False)
    project = _make_project("visual_review_publish_state")
    corrupt_path = tmp_path / "corrupt.png"
    corrupt_path.write_bytes(b"not an image")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        _slide(corrupt_path),
    )

    project.refresh_from_db()
    project.status = "ready"
    project.save(update_fields=["status", "updated_at"])
    assert result["final_decision"] == "needs_admin_review"
    assert project.moderation_status == "needs_admin_review"
    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_automatic_visual_reject_does_not_allow_publish(monkeypatch, tmp_path):
    _enable_local_visual(monkeypatch)
    project = _make_project("visual_reject_publish_state")
    image_path = _valid_image(tmp_path / "blocked.png")

    class BlockingProvider:
        def review_image(self, _image_path, location):
            from worker.ai_agents.schemas import AgentFindingSchema, AgentResultSchema, FindingLocation

            return AgentResultSchema(
                agent_slug="visual_local_rules",
                agent_version="test",
                modality="image",
                provider="local_image_rules",
                decision="block",
                confidence=0.99,
                findings=[AgentFindingSchema(
                    category="graphic_content",
                    severity="high",
                    confidence=0.99,
                    decision="block",
                    user_message="Blocked visual.",
                    location=FindingLocation(**location.model_dump()),
                )],
            )

    monkeypatch.setattr(
        "worker.ai_agents.providers.local_image_rules_provider.LocalImageRulesProvider",
        BlockingProvider,
    )

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        _slide(image_path),
    )

    project.refresh_from_db()
    project.status = "ready"
    project.save(update_fields=["status", "updated_at"])
    assert result["final_decision"] == "block"
    assert result["block_render"] is True
    assert project.moderation_status == "revision_required"
    assert project_can_publish(project) is False


@pytest.mark.django_db
def test_local_rule_only_successful_render_state_is_not_moderation_blocked(monkeypatch, tmp_path):
    _enable_local_visual(monkeypatch)
    project = _make_project("visual_local_render_publish_state")
    image_path = _valid_image(tmp_path / "render-safe.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        _slide(image_path),
    )
    worker_tasks._mark_project_ready_after_successful_render(project.id)

    project.refresh_from_db()
    assert result["status"] == "done"
    assert result["block_render"] is False
    assert project.status == "ready"
    assert project.moderation_status == "approved"
    assert project_can_publish(project) is True


@pytest.mark.django_db
def test_visual_allow_does_not_override_existing_moderation_block(monkeypatch, tmp_path):
    _enable_local_visual(monkeypatch)
    project = _make_project(
        "visual_allow_preserves_source_block",
        moderation_status="revision_required",
    )
    image_path = _valid_image(tmp_path / "safe-after-source-block.png")

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        _slide(image_path),
    )

    project.refresh_from_db()
    assert result["final_decision"] == "allow"
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_visual_scan_error_blocks_render_when_configured(monkeypatch, tmp_path):
    _enable_local_visual(monkeypatch, block_render=True)
    project = _make_project("visual_error_fail_closed")
    image_path = _valid_image(tmp_path / "error.png")

    monkeypatch.setattr(
        worker_tasks,
        "_persist_auto_visual_moderation_results",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("persistence failed")),
    )

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(
        project.id,
        _slide(image_path),
    )

    assert result["status"] == "failed"
    assert result["final_decision"] == "needs_admin_review"
    assert result["block_render"] is True
