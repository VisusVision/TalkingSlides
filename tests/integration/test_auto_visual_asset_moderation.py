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

from ai_agents.models import AgentFinding, AgentRun  # noqa: E402
from core import views  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.orchestrator import ModerationOrchestrator  # noqa: E402
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


def _enable_visual(monkeypatch, *, block: bool = False, cover: bool = False, slides: bool = True) -> None:
    monkeypatch.setattr(settings, "VISUAL_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_BLOCK_RENDER_ON_REJECTION", block, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_PHASE", "visual_asset_scan", raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_COVER", cover, raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_SCAN_SLIDES", slides, raising=False)


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
def test_auto_visual_missing_image_does_not_crash_or_block(monkeypatch, tmp_path):
    _enable_visual(monkeypatch, block=True)
    project = _make_project("auto_visual_missing_teacher")
    missing_path = tmp_path / "missing.png"

    result = worker_tasks._run_auto_visual_asset_moderation_after_export(project.id, [_slide(missing_path)])

    assert result["status"] == "done"
    assert result["final_decision"] == "allow"
    assert result["block_render"] is False
    assert AgentFinding.objects.filter(run__project=project).count() == 0


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

    def fake_scan(project_id, export_result, job_id=None):
        calls.append({"project_id": project_id, "export_result": export_result, "job_id": job_id})
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
    assert calls == [{"project_id": project.id, "export_result": [], "job_id": None}]


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

    def fake_scan(project_id, export_result, job_id=None):
        calls.append({"project_id": project_id, "export_result": export_result, "job_id": job_id})
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
    assert len(calls[0]["export_result"]) == 1
    assert calls[0]["export_result"][0]["page_key"] == page.page_key
    assert calls[0]["export_result"][0]["image_path"].endswith(".png")
