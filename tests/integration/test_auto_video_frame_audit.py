# pyright: reportMissingImports=false

import importlib
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

from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun  # noqa: E402
from ai_agents.policies import project_can_publish  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402
from worker.ai_agents.ocr_bridge import OCRTextResult  # noqa: E402
from worker.ai_agents.video_frame_moderation import SampledVideoFrame, VideoFrameSamplingResult  # noqa: E402


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str, *, status: str = "ready", moderation_status: str = "approved") -> Project:
    return Project.objects.create(
        title=f"Video frame audit {username}",
        user=_make_teacher(username),
        status=status,
        moderation_status=moderation_status,
        is_published=False,
    )


def _enable_audit(monkeypatch, *, visual: bool = True, ocr: bool = False, allow_weak: bool = True) -> None:
    monkeypatch.setattr(settings, "ENABLE_VISUAL_MODERATION", True, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_PHASE", "video_frame_audit", raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_EVERY_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_MAX_FRAMES", 5, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RUN_VISUAL_CHECK", visual, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_RUN_OCR", ocr, raising=False)
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION", False, raising=False)
    monkeypatch.setattr(settings, "OCR_MODERATION_PROVIDER", "noop", raising=False)
    monkeypatch.setattr(settings, "VISUAL_MODERATION_REQUIRE_SEMANTIC_PROVIDER", True, raising=False)
    monkeypatch.setattr(settings, "ALLOW_WEAK_LOCAL_VISUAL_APPROVAL", allow_weak, raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_PROVIDER", "none", raising=False)
    monkeypatch.setattr(settings, "VISUAL_SAFETY_CLASSIFIER_ENABLED", False, raising=False)


def _save_image(path: Path, *, size: tuple[int, int] = (24, 24)) -> Path:
    Image.new("RGB", size, color=(20, 80, 160)).save(path)
    return path


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


def _patch_sampling(monkeypatch, result: VideoFrameSamplingResult) -> None:
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.sample_video_frames", lambda **_kwargs: result)


@pytest.mark.django_db
def test_auto_video_frame_audit_disabled_has_no_effect(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VIDEO_FRAME_AUDIT_AUTO_ENABLED", False, raising=False)
    project = _make_project("disabled")

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, None, tmp_path / "missing.mp4")

    assert result["status"] == "skipped_disabled"
    assert result["block_render"] is False
    assert AgentRun.objects.filter(project=project, phase="video_frame_audit").count() == 0


@pytest.mark.django_db
def test_auto_video_frame_audit_missing_video_fails_open(monkeypatch, tmp_path):
    _enable_audit(monkeypatch)
    project = _make_project("missing_video")

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, None, tmp_path / "missing.mp4")

    project.refresh_from_db()
    run = AgentRun.objects.get(project=project, phase="video_frame_audit")
    assert result["status"] == "failed"
    assert result["block_render"] is False
    assert run.status == "failed"
    assert run.final_decision == "allow"
    assert "not found" in project.moderation_summary["video_frame_audit"]["error_message"].lower()
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_auto_video_frame_audit_sampling_failure_fails_open(monkeypatch, tmp_path):
    _enable_audit(monkeypatch)
    project = _make_project("sampling_failure")
    failure = VideoFrameSamplingResult(
        video_path="bad.mp4",
        output_dir=str(tmp_path / "frames"),
        sampled_frames=[],
        success=False,
        error_message="ffmpeg failed",
        ffmpeg_path="ffmpeg",
    )
    _patch_sampling(monkeypatch, failure)

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 10, tmp_path / "bad.mp4")

    project.refresh_from_db()
    assert result["status"] == "failed"
    assert result["block_render"] is False
    assert project.moderation_summary["video_frame_audit"]["status"] == "failed"
    assert project.moderation_summary["video_frame_audit"]["job_id"] == 10
    assert project.moderation_status == "approved"


@pytest.mark.django_db
def test_auto_video_frame_audit_mocked_frames_create_run_and_summary(monkeypatch, tmp_path):
    _enable_audit(monkeypatch)
    project = _make_project("mocked_frames")
    frame_path = _save_image(tmp_path / "frame.jpg")
    _patch_sampling(monkeypatch, _sampling_success(frame_path))

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 11, tmp_path / "final.mp4")

    project.refresh_from_db()
    run = AgentRun.objects.get(project=project, phase="video_frame_audit")
    assert result["status"] == "done"
    assert result["final_decision"] == "allow"
    assert result["sampled_frame_count"] == 1
    assert run.status == "done"
    assert project.moderation_summary["video_frame_audit"]["sampled_frame_count"] == 1
    assert project.moderation_summary["video_frame_audit"]["run_visual_check"] is True


@pytest.mark.django_db
def test_video_frame_audit_requires_semantic_provider_by_default(monkeypatch, tmp_path):
    _enable_audit(monkeypatch, allow_weak=False)
    project = _make_project("provider_required")
    frame_path = _save_image(tmp_path / "frame.jpg")
    _patch_sampling(monkeypatch, _sampling_success(frame_path))

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 111, tmp_path / "final.mp4")

    finding = AgentFinding.objects.get(run__project=project, run__phase="video_frame_audit", category="provider_unavailable")
    assert result["status"] == "done"
    assert result["final_decision"] == "needs_admin_review"
    assert finding.decision == "needs_admin_review"
    assert finding.location["asset_type"] == "video_frame"
    assert finding.location["timestamp_seconds"] == 0.0
    assert finding.location["frame_path"] == str(frame_path)
    assert finding.user_message.startswith("We could not complete the visual safety scan")


@pytest.mark.django_db
def test_auto_video_frame_audit_local_image_finding_is_persisted(monkeypatch, tmp_path):
    _enable_audit(monkeypatch)
    project = _make_project("corrupt_frame")
    frame_path = tmp_path / "corrupt.jpg"
    frame_path.write_bytes(b"not an image")
    _patch_sampling(monkeypatch, _sampling_success(frame_path))

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 12, tmp_path / "final.mp4")

    finding = AgentFinding.objects.get(run__project=project, run__phase="video_frame_audit")
    assert result["final_decision"] == "needs_admin_review"
    assert finding.content_type == "video_frame"
    assert finding.object_type == "video_frame"
    assert finding.location["asset_type"] == "video_frame"
    assert finding.location["frame_path"] == str(frame_path)
    assert finding.location["timestamp_seconds"] == 0.0
    assert finding.provider == "local_image_rules"


@pytest.mark.django_db
def test_auto_video_frame_audit_ocr_disabled_by_default(monkeypatch, tmp_path):
    _enable_audit(monkeypatch, ocr=False)
    project = _make_project("ocr_disabled")
    frame_path = _save_image(tmp_path / "safe.jpg")
    _patch_sampling(monkeypatch, _sampling_success(frame_path))

    def fail_extract(*_args, **_kwargs):
        raise AssertionError("OCRBridge.extract should not be called when frame OCR is disabled")

    monkeypatch.setattr("worker.ai_agents.ocr_bridge.OCRBridge.extract", fail_extract)

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 13, tmp_path / "final.mp4")

    assert result["status"] == "done"
    assert result["ocr_frame_count"] == 0
    assert result["finding_count"] == 0


@pytest.mark.django_db
def test_auto_video_frame_audit_ocr_enabled_records_text_finding(monkeypatch, tmp_path):
    _enable_audit(monkeypatch, visual=False, ocr=True)
    project = _make_project("ocr_enabled")
    frame_path = _save_image(tmp_path / "ocr-frame.jpg")
    _patch_sampling(monkeypatch, _sampling_success(frame_path))

    def fake_extract(self, image_path="", location=None, **_kwargs):
        return OCRTextResult(
            text="I will kill you tomorrow.",
            location=location,
            provider="fake_frame_ocr",
            success=True,
            error_message="",
            image_path=str(image_path or ""),
            asset_type=location.asset_type,
            metadata={"text_length": len("I will kill you tomorrow.")},
        )

    monkeypatch.setattr("worker.ai_agents.ocr_bridge.OCRBridge.extract", fake_extract)

    result = worker_tasks._run_auto_video_frame_audit_after_render(project.id, 14, tmp_path / "final.mp4")

    project.refresh_from_db()
    finding = AgentFinding.objects.get(run__project=project, run__phase="video_frame_audit")
    assert result["final_decision"] == "block"
    assert result["finding_count"] == 1
    assert result["ocr_frame_count"] == 1
    assert finding.content_type == "ocr"
    assert finding.object_type == "video_frame_ocr"
    assert finding.location["frame_path"] == str(frame_path)
    assert finding.provider == "video_frame_ocr:local_rules"
    assert finding.provider_raw["ocr_provider"] == "fake_frame_ocr"
    assert project.moderation_status == "revision_required"


@pytest.mark.django_db
def test_auto_video_frame_audit_moves_project_to_review(monkeypatch, tmp_path):
    _enable_audit(monkeypatch)
    project = _make_project("status_unchanged", moderation_status="approved")
    frame_path = tmp_path / "bad.jpg"
    frame_path.write_bytes(b"bad image")
    _patch_sampling(monkeypatch, _sampling_success(frame_path))

    worker_tasks._run_auto_video_frame_audit_after_render(project.id, 15, tmp_path / "final.mp4")

    project.refresh_from_db()
    assert project.moderation_status == "needs_admin_review"
    assert AdminReviewRequest.objects.filter(project=project, status="open").exists()


def _patch_lightweight_finalizer(monkeypatch, tmp_path: Path) -> None:
    ffmpeg_helpers = importlib.import_module("scripts.ffmpeg_helpers")
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_tasks, "DRM_STREAMING_ENABLED", False)
    monkeypatch.setattr(worker_tasks, "_sync_lesson_segments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_tasks, "_update_transcript_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_tasks,
        "build_subtitle_cues_from_transcript_pages",
        lambda *_args, **_kwargs: [{"start": 0.0, "end": 1.0, "text": "Slide", "chunk_index": 0}],
    )

    def fake_concat_videos(_part_paths, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"mp4")

    def fake_generate_srt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nSlide\n", encoding="utf-8")

    def fake_generate_vtt_from_cues(_cues, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSlide\n", encoding="utf-8")

    monkeypatch.setattr(ffmpeg_helpers, "concat_videos", fake_concat_videos)
    monkeypatch.setattr(ffmpeg_helpers, "generate_srt_from_cues", fake_generate_srt_from_cues)
    monkeypatch.setattr(ffmpeg_helpers, "generate_vtt_from_cues", fake_generate_vtt_from_cues)


def _render_result(project: Project, tmp_path: Path) -> dict:
    part_path = tmp_path / str(project.id) / "parts" / "part_001.mp4"
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"part")
    return {
        "index": 0,
        "slide_num": 1,
        "page_key": "p1",
        "part_path": str(part_path),
        "duration": 1.0,
        "pause_seconds": 0.0,
        "text": "Slide",
        "original_text": "Slide",
        "spoken_text": "Slide",
        "subtitle_chunks": ["Slide"],
        "slide_path": "",
        "tts_audio_path": "",
        "avatar_applied": False,
        "avatar_attempted": False,
        "avatar_skipped": False,
        "avatar_failed": False,
        "avatar_status": "none",
        "avatar_error": "",
        "avatar_failure_reason": "",
        "avatar_segment_rel_path": "",
        "avatar_engine_used": "none",
        "avatar_fallback_chain": [],
        "avatar_motion_validation": {},
    }


@pytest.mark.django_db
def test_render_job_success_not_converted_to_failure_by_audit_exception(monkeypatch, tmp_path):
    project = _make_project("render_success", status="processing")
    job = Job.objects.create(project=project, job_type="video_export", status="running")
    _patch_lightweight_finalizer(monkeypatch, tmp_path)

    def raise_audit(*_args, **_kwargs):
        raise RuntimeError("audit exploded")

    monkeypatch.setattr(worker_tasks, "_run_auto_video_frame_audit_after_render", raise_audit)

    result = worker_tasks.concat_and_finalize.run([_render_result(project, tmp_path)], str(project.id))

    project.refresh_from_db()
    job.refresh_from_db()
    assert project.status == "ready"
    assert job.status == "done"
    assert result["result_url"] == f"{project.id}/{project.id}.mp4"


@pytest.mark.django_db
def test_video_frame_audit_findings_block_publish_even_when_legacy_gate_flag_is_off(settings):
    settings.VIDEO_FRAME_AUDIT_BLOCK_PUBLISH_ON_REJECTION = False
    project = _make_project("publish_unchanged", status="ready", moderation_status="approved")
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="video_frame_audit",
        status="done",
        final_decision="needs_admin_review",
    )
    AgentFinding.objects.create(
        run=run,
        agent_slug="visual_moderation_local_image_rules",
        agent_version="local-image-rules:v1",
        content_type="video_frame",
        object_type="video_frame",
        object_id="0.000",
        location={"asset_type": "video_frame", "timestamp_seconds": 0.0, "frame_path": "frame.jpg"},
        category="graphic_content",
        severity="critical",
        confidence=0.9,
        decision="needs_admin_review",
        user_message="Frame should be reviewed.",
        provider="local_image_rules",
    )

    assert project_can_publish(project) is False
