# pyright: reportMissingImports=false

import os
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path

import django
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

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
from worker.ai_agents.video_frame_moderation import (  # noqa: E402
    VideoFrameModerationAgent,
    format_timestamp_label,
    sample_video_frames,
)


FFMPEG = shutil.which("ffmpeg")


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_project(username: str) -> Project:
    return Project.objects.create(
        title="Video frame sampling lesson",
        user=_make_teacher(username),
        status="ready",
    )


def _create_tiny_video(path: Path) -> Path:
    if not FFMPEG:
        pytest.skip("ffmpeg is not available")
    command = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=16x16:d=1",
        "-frames:v",
        "1",
        str(path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        pytest.skip(f"ffmpeg could not create tiny test video: {completed.stderr}")
    return path


def test_missing_video_path_returns_clear_failure_without_crash(tmp_path):
    result = sample_video_frames(
        video_path=tmp_path / "missing.mp4",
        output_dir=tmp_path / "frames",
        max_frames=1,
    )

    assert result.success is False
    assert result.sampled_frames == []
    assert "not found" in result.error_message.lower()


def test_ffmpeg_unavailable_returns_clear_failure(monkeypatch, tmp_path):
    video_path = tmp_path / "placeholder.mp4"
    video_path.write_bytes(b"not a real video")
    monkeypatch.setattr("worker.ai_agents.video_frame_moderation.shutil.which", lambda name: None)

    result = sample_video_frames(
        video_path=video_path,
        output_dir=tmp_path / "frames",
        max_frames=1,
    )

    assert result.success is False
    assert "ffmpeg" in result.error_message.lower()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not available")
def test_invalid_video_file_returns_clear_failure_without_crash(tmp_path):
    invalid_video = tmp_path / "invalid.mp4"
    invalid_video.write_text("not a video")

    result = sample_video_frames(
        video_path=invalid_video,
        output_dir=tmp_path / "frames",
        max_frames=1,
    )

    assert result.success is False
    assert result.sampled_frames == []
    assert result.error_message


def test_sample_video_frames_command_help_loads():
    from ai_agents.management.commands.sample_video_frames import Command

    parser = Command().create_parser("manage.py", "sample_video_frames")

    assert "Sample local video frames" in parser.format_help()


def test_sample_video_frames_command_rejects_missing_required_args():
    with pytest.raises(CommandError):
        call_command("sample_video_frames")


def test_timestamp_label_formatting_works():
    assert format_timestamp_label(0) == "00:00:00"
    assert format_timestamp_label(65.5) == "00:01:05.500"
    assert format_timestamp_label(3661) == "01:01:01"


def test_video_frame_moderation_interface_preserves_timestamp_and_frame_path():
    result = VideoFrameModerationAgent().scan_frame(
        project_id=123,
        frame_path="frame.jpg",
        timestamp_seconds=2.5,
        timestamp_label="00:00:02.500",
        ui_anchor="manual-video-frame-0",
    )

    location = result.metadata["location"]
    assert result.decision == "allow"
    assert result.modality == "video_frame"
    assert location["asset_type"] == "video_frame"
    assert location["frame_path"] == "frame.jpg"
    assert location["timestamp_seconds"] == 2.5
    assert location["timestamp_label"] == "00:00:02.500"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not available")
def test_ffmpeg_available_samples_one_frame_from_tiny_video(tmp_path):
    video_path = _create_tiny_video(tmp_path / "tiny.mp4")

    result = sample_video_frames(
        video_path=video_path,
        output_dir=tmp_path / "frames",
        every_seconds=1,
        max_frames=1,
    )

    assert result.success is True
    assert len(result.sampled_frames) == 1
    assert Path(result.sampled_frames[0].frame_path).is_file()
    assert result.sampled_frames[0].timestamp_seconds == 0.0


@pytest.mark.django_db
def test_report_mode_does_not_persist_agent_run_or_finding(tmp_path):
    initial_run_count = AgentRun.objects.count()
    initial_finding_count = AgentFinding.objects.count()
    out = StringIO()

    call_command(
        "sample_video_frames",
        video_path=str(tmp_path / "missing.mp4"),
        output_dir=str(tmp_path / "frames"),
        max_frames=1,
        stdout=out,
    )

    assert "Success: False" in out.getvalue()
    assert AgentRun.objects.count() == initial_run_count
    assert AgentFinding.objects.count() == initial_finding_count


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not available")
@pytest.mark.django_db
def test_moderate_with_sampled_frame_uses_local_image_provider_safely(tmp_path):
    project = _make_project("video_frame_moderate_teacher")
    video_path = _create_tiny_video(tmp_path / "tiny-moderate.mp4")
    out = StringIO()

    call_command(
        "sample_video_frames",
        video_path=str(video_path),
        output_dir=str(tmp_path / "frames"),
        every_seconds=1,
        max_frames=1,
        moderate=True,
        project_id=project.id,
        stdout=out,
    )

    output = out.getvalue()
    assert "Frame count: 1" in output
    assert "Moderation provider: local_image_rules" in output
    assert "Moderation decision: allow" in output
    assert AgentRun.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_moderate_requires_project_id(tmp_path):
    with pytest.raises(CommandError) as exc:
        call_command(
            "sample_video_frames",
            video_path=str(tmp_path / "missing.mp4"),
            output_dir=str(tmp_path / "frames"),
            moderate=True,
        )

    assert "project-id" in str(exc.value).lower()
