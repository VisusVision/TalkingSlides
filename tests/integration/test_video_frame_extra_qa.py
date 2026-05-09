# pyright: reportMissingImports=false
import os
import sys
from pathlib import Path
from unittest.mock import patch

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

from ai_agents.models import AgentRun
from core.models import Project, UserProfile
from django.contrib.auth.models import User
from worker.ai_agents.video_frame_moderation import sample_video_frames, format_timestamp_label

@pytest.fixture
def test_user():
    user = User.objects.create_user(username="video_qa_user", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user

@pytest.fixture
def test_project(test_user):
    return Project.objects.create(user=test_user, title="Video QA Project", status="ready")

@pytest.mark.django_db
class TestVideoFrameExtraQA:

    def test_sample_video_frames_rejects_every_seconds_zero(self, tmp_path):
        # We need a file to exist so it doesn't fail on missing file first
        dummy_video = tmp_path / "video.mp4"
        dummy_video.touch()
        
        res = sample_video_frames(video_path=dummy_video, output_dir=tmp_path, every_seconds=0)
        assert res.success is False
        assert "every_seconds must be greater than 0" in res.error_message

    def test_sample_video_frames_rejects_max_frames_zero(self, tmp_path):
        dummy_video = tmp_path / "video.mp4"
        dummy_video.touch()
        
        res = sample_video_frames(video_path=dummy_video, output_dir=tmp_path, max_frames=0)
        assert res.success is False
        assert "max_frames must be greater than 0" in res.error_message

    def test_command_moderate_requires_project_id(self, tmp_path):
        with pytest.raises(CommandError) as exc:
            call_command("sample_video_frames", video_path="any.mp4", output_dir=str(tmp_path), moderate=True)
        assert "--project-id is required" in str(exc.value)

    def test_output_dir_creation(self, tmp_path):
        dummy_video = tmp_path / "video.mp4"
        dummy_video.touch()
        nested_dir = tmp_path / "nested" / "frames"
        
        # This will fail on FFmpeg if not present, but we check dir creation before FFmpeg call
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = Exception("Stop early")
                try:
                    sample_video_frames(video_path=dummy_video, output_dir=nested_dir)
                except Exception:
                    pass
        
        assert nested_dir.exists()

    def test_missing_ffmpeg_behavior(self, tmp_path):
        dummy_video = tmp_path / "video.mp4"
        dummy_video.touch()
        
        with patch("shutil.which", return_value=None):
            res = sample_video_frames(video_path=dummy_video, output_dir=tmp_path)
            assert res.success is False
            assert "ffmpeg executable was not found" in res.error_message

    def test_format_timestamp_label_various_values(self):
        assert format_timestamp_label(0) == "00:00:00"
        assert format_timestamp_label(1.5) == "00:00:01.500"
        assert format_timestamp_label(3661) == "01:01:01"

    def test_command_report_mode_does_not_persist(self, test_project, tmp_path):
        initial_run_count = AgentRun.objects.count()
        # Even if it fails due to missing video, it should not create a run
        call_command("sample_video_frames", video_path="missing.mp4", output_dir=str(tmp_path))
        assert AgentRun.objects.count() == initial_run_count
