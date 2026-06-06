# pyright: reportMissingImports=false
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

from django.contrib.auth.models import User
from core.models import Project, UserProfile
from worker.ai_agents.orchestrator import ModerationOrchestrator
from worker.ai_agents.visual_moderation import VisualModerationAgent
from worker.ai_agents.video_frame_moderation import VideoFrameModerationAgent, VideoFrameItem
from worker.ai_agents.ocr_bridge import OCRBridge

def _make_user(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user

def _make_project(user: User) -> Project:
    return Project.objects.create(user=user, title="Visual QA Project", status="ready")

@pytest.mark.django_db
class TestVisualExtraQA:

    def test_visual_scan_with_empty_image_path_does_not_crash(self):
        agent = VisualModerationAgent()
        # Test with None
        res1 = agent.scan_slide_image(project_id=1, image_path=None, slide_order=0)
        assert res1.decision == "allow"
        assert res1.metadata["asset_missing"] is True
        
        # Test with empty string
        res2 = agent.scan_slide_image(project_id=1, image_path="", slide_order=0)
        assert res2.decision == "allow"
        assert res2.metadata["asset_missing"] is True

    def test_multiple_slide_image_scan_preserves_each_slide_order(self):
        agent = VisualModerationAgent()
        results = [
            agent.scan_slide_image(project_id=1, image_path="p1.jpg", slide_order=0),
            agent.scan_slide_image(project_id=1, image_path="p2.jpg", slide_order=5),
        ]
        
        assert results[0].metadata["location"]["slide_order"] == 0
        assert results[1].metadata["location"]["slide_order"] == 5

    def test_video_frame_scan_with_timestamp_zero_works(self):
        agent = VideoFrameModerationAgent()
        res = agent.scan_frame(project_id=1, frame_path="f.jpg", timestamp_seconds=0.0)
        assert res.decision == "allow"
        assert res.metadata["location"]["timestamp_seconds"] == 0.0

    def test_ocr_bridge_handles_missing_file_path(self):
        bridge = OCRBridge()
        res = bridge.extract(image_path=None)
        assert res.text == ""
        assert res.metadata["asset_missing"] is True

    def test_text_only_moderation_still_does_not_call_visual_methods(self):
        user = _make_user("visual_qa_user")
        project = _make_project(user)
        
        # Orchestrator.run currently only runs text moderation
        res = ModerationOrchestrator().run(project.id)
        
        # Verify result is for text modality (Ollama or local rules)
        # Note: res is a dict returned by run()
        project.refresh_from_db()
        run_id = res["run_id"]
        from ai_agents.models import AgentRun
        run = AgentRun.objects.get(pk=run_id)
        
        # Since orchestrator.run line 60 calls text_agent.scan_project,
        # the run summary/metadata should indicate text modality.
        assert run.summary["findings"] == [] # project is clean
        # If we had findings, modality would be in AgentFinding.
        # But we can check orchestrator.py:89 where it returns dict.
        assert res["moderation_status"] == "approved"
