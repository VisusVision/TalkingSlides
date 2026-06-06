# pyright: reportMissingImports=false
import os
import sys
from pathlib import Path

import django
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
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

from ai_agents.models import AgentFinding, AgentRun
from core.models import Project, UserProfile
from django.contrib.auth.models import User

@pytest.fixture
def test_user():
    user = User.objects.create_user(username="image_qa_user", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user

@pytest.fixture
def test_project(test_user):
    return Project.objects.create(user=test_user, title="Image QA Project", status="ready")

@pytest.fixture
def valid_image(tmp_path):
    path = tmp_path / "valid.jpg"
    img = Image.new("RGB", (100, 100), color="red")
    img.save(path)
    return str(path)

@pytest.fixture
def corrupt_image(tmp_path):
    path = tmp_path / "corrupt.jpg"
    with open(path, "w") as f:
        f.write("not an image")
    return str(path)

@pytest.mark.django_db
class TestLocalImageExtraQA:

    def test_command_report_mode_does_not_persist_agent_run(self, test_project, valid_image):
        initial_run_count = AgentRun.objects.count()
        call_command("run_visual_moderation_scan", project_id=test_project.id, cover_path=valid_image)
        assert AgentRun.objects.count() == initial_run_count

    def test_command_persist_creates_agent_run_and_findings(self, test_project, valid_image):
        call_command("run_visual_moderation_scan", project_id=test_project.id, cover_path=valid_image, persist=True)
        run = AgentRun.objects.filter(project=test_project, phase="visual_manual_scan").latest("id")
        assert run.status == "done"
        assert run.final_decision == "allow"
        # Since it's a valid image, there are no findings, but the run is created.
        assert AgentFinding.objects.filter(run=run).count() == 0

    def test_command_persist_with_corrupt_image_creates_findings(self, test_project, corrupt_image):
        call_command("run_visual_moderation_scan", project_id=test_project.id, cover_path=corrupt_image, persist=True)
        run = AgentRun.objects.filter(project=test_project, phase="visual_manual_scan").latest("id")
        assert run.final_decision == "needs_admin_review"
        assert AgentFinding.objects.filter(run=run, decision="needs_admin_review").exists()

    def test_command_rejects_missing_project_id(self):
        with pytest.raises(CommandError) as exc:
            call_command("run_visual_moderation_scan", project_id=99999)
        assert "not found" in str(exc.value).lower()

    def test_slide_scan_with_slide_order_zero_preserves_zero(self, test_project, valid_image):
        # We'll test this via the provider directly for simplicity
        from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from worker.ai_agents.visual_moderation import VisualModerationAgent
        
        agent = VisualModerationAgent(provider=LocalImageRulesProvider())
        result = agent.scan_slide_image(project_id=test_project.id, image_path=valid_image, slide_order=0)
        assert result.metadata["location"]["slide_order"] == 0

    def test_missing_image_path_returns_allow_with_missing_metadata(self, test_project):
        from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from worker.ai_agents.visual_moderation import VisualModerationAgent
        
        agent = VisualModerationAgent(provider=LocalImageRulesProvider())
        result = agent.scan_cover_image(test_project, image_path="/non/existent/path.jpg")
        assert result.decision == "allow"
        assert result.metadata["missing"] is True

    def test_large_image_triggers_needs_admin_review(self, test_project, tmp_path):
        from worker.ai_agents.providers.local_image_rules_provider import LocalImageRulesProvider
        from worker.ai_agents.visual_moderation import VisualModerationAgent
        
        # Create a large image (exceeding default 12000 limit might be too slow/heavy for CI, 
        # let's use a provider with small limits)
        path = tmp_path / "large.jpg"
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(path)
        
        provider = LocalImageRulesProvider(max_width=50, max_height=50)
        agent = VisualModerationAgent(provider=provider)
        result = agent.scan_cover_image(test_project, image_path=str(path))
        
        assert result.decision == "needs_admin_review"
        assert result.findings[0].category == "graphic_content"
        assert "exceeding configured local safety limits" in result.findings[0].admin_message
