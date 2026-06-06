import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from ai_agents.models import AgentFinding, AgentRun, AdminReviewRequest
from core.models import Project, TranscriptPage, UserProfile
from worker.ai_agents.orchestrator import ModerationOrchestrator
from django.contrib.auth.models import User

@pytest.fixture
def api_client():
    return APIClient()

def _make_user(username: str, is_staff=False) -> User:
    user = User.objects.create_user(username=username, password="pass", is_staff=is_staff)
    UserProfile.objects.create(user=user, role="teacher")
    return user

def _make_project(user: User, title="QA Project") -> Project:
    return Project.objects.create(user=user, title=title, status="ready")

def _add_page(project: Project, text: str) -> TranscriptPage:
    return TranscriptPage.objects.create(project=project, order=0, original_text=text, narration_text=text)

@pytest.mark.django_db
class TestModerationExtraQA:

    # --- A. State Transitions ---

    def test_block_scan_without_request_review_is_revision_required(self):
        user = _make_user("qa_state_1")
        project = _make_project(user)
        _add_page(project, "I will kill you.")  # High confidence block
        
        ModerationOrchestrator().run(project.id)
        project.refresh_from_db()
        
        assert project.moderation_status == "revision_required"
        assert AdminReviewRequest.objects.filter(project=project, status="open").exists()

    def test_approved_project_cannot_create_admin_review_request(self):
        user = _make_user("qa_state_2")
        project = _make_project(user)
        project.moderation_status = "approved"
        project.save()
        
        with pytest.raises(CommandError) as exc:
            call_command("create_moderation_review_request", project_id=project.id, user_id=user.id, message="Test")
        assert "not in a reviewable moderation state" in str(exc.value)

    # --- B. Permissions ---

    def test_anonymous_cannot_access_moderation_summary(self, api_client):
        user = _make_user("qa_perm_1")
        project = _make_project(user)
        url = f"/api/v1/projects/{project.id}/moderation/"
        
        response = api_client.get(url)
        assert response.status_code == 401

    def test_non_owner_cannot_rescan_project(self, api_client):
        owner = _make_user("qa_owner")
        other = _make_user("qa_other")
        project = _make_project(owner)
        url = f"/api/v1/projects/{project.id}/moderation/rescan/"
        
        api_client.force_authenticate(user=other)
        response = api_client.post(url)
        assert response.status_code == 403

    def test_staff_can_rescan_any_project(self, api_client, monkeypatch):
        from ai_agents import views
        monkeypatch.setattr(views, "_dispatch_moderation_task", lambda *args, **kwargs: {"status": "accepted"})
        
        owner = _make_user("qa_owner_2")
        staff = _make_user("qa_staff", is_staff=True)
        project = _make_project(owner)
        url = f"/api/v1/projects/{project.id}/moderation/rescan/"
        
        api_client.force_authenticate(user=staff)
        response = api_client.post(url)
        assert response.status_code == 202

    def test_non_staff_cannot_access_admin_review_endpoints(self, api_client):
        user = _make_user("qa_non_staff")
        url = "/api/v1/admin/moderation/review-requests/"
        
        api_client.force_authenticate(user=user)
        response = api_client.get(url)
        assert response.status_code == 403

    # --- C. Data Safety ---

    def test_publisher_summary_does_not_expose_admin_messages_or_raw_data(self):
        user = _make_user("qa_data_1")
        project = _make_project(user)
        _add_page(project, "This contains fuck.")
        
        ModerationOrchestrator().run(project.id)
        project.refresh_from_db()
        
        summary = project.moderation_summary
        # Check that we don't leak internal fields in the JSON summary
        import json
        dump = json.dumps(summary)
        assert "admin_message" not in dump
        assert "provider_raw" not in dump
        assert "Local rule matched" not in dump  # Part of admin_message

    def test_evidence_excerpts_are_truncated(self):
        user = _make_user("qa_data_2")
        project = _make_project(user)
        long_text = "word " * 100 + "fuck " + "word " * 100
        _add_page(project, long_text)
        
        ModerationOrchestrator().run(project.id)
        
        finding = AgentFinding.objects.filter(run__project=project).first()
        assert len(finding.evidence_excerpt) <= 220

    # --- D. Management Commands ---

    def test_run_moderation_scan_handles_missing_id(self):
        with pytest.raises(CommandError):
            call_command("run_moderation_scan", sync=True)

    def test_create_smoke_project_kind_clean_is_approved(self):
        user = _make_user("qa_cmd_1")
        # create_moderation_smoke_project returns the project or ID depending on implementation, 
        # but we can verify it by looking at the DB
        call_command("create_moderation_smoke_project", kind="clean", user_id=user.id, scan=True)
        
        project = Project.objects.filter(user=user, title__icontains="Smoke").first()
        assert project.moderation_status == "approved"

    def test_create_smoke_project_kind_profanity_is_revision_required(self):
        user = _make_user("qa_cmd_2")
        call_command("create_moderation_smoke_project", kind="profanity", user_id=user.id, scan=True)
        
        project = Project.objects.filter(user=user, title__icontains="Smoke").first()
        # Default behavior for profanity 'fuck' is 'block' (revision_required)
        assert project.moderation_status == "revision_required"

    # --- E. Local Rules ---

    def test_educational_context_downgrades_profanity(self):
        user = _make_user("qa_rules_1")
        project = _make_project(user)
        # "fuck" is block, but "educational" pattern triggers downgrade to review
        _add_page(project, "This educational lesson discusses why the word fuck is used.")
        
        ModerationOrchestrator().run(project.id)
        project.refresh_from_db()
        
        assert project.moderation_status == "needs_admin_review"

    def test_unsafe_content_detected_in_all_fields(self):
        user = _make_user("qa_fields")
        
        # Title
        p1 = _make_project(user, title="I will kill you")
        ModerationOrchestrator().run(p1.id)
        p1.refresh_from_db()
        assert p1.moderation_status == "revision_required"
        
        # Description
        p2 = _make_project(user, title="Safe Title")
        p2.description = "I will kill you"
        p2.save()
        ModerationOrchestrator().run(p2.id)
        p2.refresh_from_db()
        assert p2.moderation_status == "revision_required"
        
        # Narration (already tested mostly, but for completeness)
        p3 = _make_project(user)
        _add_page(p3, "I will kill you")
        ModerationOrchestrator().run(p3.id)
        p3.refresh_from_db()
        assert p3.moderation_status == "revision_required"

    # --- F. Robustness ---

    def test_project_without_pages_can_be_scanned(self):
        user = _make_user("qa_robust_1")
        project = _make_project(user)
        # No pages added
        
        result = ModerationOrchestrator().run(project.id)
        project.refresh_from_db()
        assert result["status"] == "done"
        assert project.moderation_status == "approved"

    def test_empty_fields_do_not_crash(self):
        user = _make_user("qa_robust_2")
        project = Project.objects.create(user=user, title="", description="")
        
        result = ModerationOrchestrator().run(project.id)
        assert result["status"] == "done"

    def test_failed_orchestrator_path_sets_failed_status(self, monkeypatch):
        from worker.ai_agents.orchestrator import TextModerationAgent
        user = _make_user("qa_robust_3")
        project = _make_project(user)
        
        def mock_scan(self, project):
            raise RuntimeError("CRASH")
        
        monkeypatch.setattr(TextModerationAgent, "scan_project", mock_scan)
        
        result = ModerationOrchestrator().run(project.id)
        project.refresh_from_db()
        assert result["status"] == "failed"
        assert project.moderation_status == "failed"
        assert project.moderation_summary["moderation_status"] == "failed"
