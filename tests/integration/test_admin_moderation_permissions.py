# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from ai_agents.models import AgentFinding, AgentRun, ModerationAuditEvent  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402


def _make_user(username: str, *, role: str = "student", is_staff: bool = False, is_superuser: bool = False) -> User:
    user = User.objects.create_user(
        username=username,
        password="pass",
        is_staff=is_staff,
        is_superuser=is_superuser,
    )
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_project(owner: User, *, moderation_status: str = "approved", published: bool = True) -> Project:
    project = Project.objects.create(
        title=f"{owner.username} lesson",
        user=owner,
        status="ready",
        moderation_status=moderation_status,
        is_published=published,
    )
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/lesson.mp4")
    return project


def _add_visual_finding(project: Project, *, image_path: str) -> AgentFinding:
    run = AgentRun.objects.create(
        project=project,
        triggered_by=project.user,
        purpose="moderation",
        phase="visual_asset_scan",
        status="done",
        final_decision="block",
    )
    project.last_moderation_run_id = run.id
    project.save(update_fields=["last_moderation_run_id"])
    return AgentFinding.objects.create(
        run=run,
        agent_slug="visual_moderation",
        agent_version="local-rules:v1",
        content_type="image",
        object_type="cover",
        object_id=str(project.id),
        location={"project_id": project.id, "asset_type": "cover", "image_path": image_path},
        category="graphic_content",
        severity="high",
        confidence=0.9,
        decision="block",
        user_message="Replace this visual.",
        admin_message="Unsafe visual.",
        provider="local_image_rules",
    )


@pytest.mark.django_db
def test_publisher_edits_own_lesson_allowed():
    publisher = _make_user("perm_owner", role="publisher")
    project = _make_project(publisher)

    response = _client(publisher).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Math"}, format="json")

    assert response.status_code == 200
    assert response.data["category_name"] == "Math"


@pytest.mark.django_db
def test_publisher_edits_other_lesson_denied():
    owner = _make_user("perm_owner_other", role="publisher")
    other = _make_user("perm_other_publisher", role="publisher")
    project = _make_project(owner)

    response = _client(other).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Science"}, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_student_edits_lesson_denied():
    owner = _make_user("perm_student_owner", role="publisher")
    student = _make_user("perm_student", role="student")
    project = _make_project(owner)

    response = _client(student).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Science"}, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_admin_cannot_edit_other_publisher_lesson_through_studio_update():
    owner = _make_user("perm_staff_owner", role="publisher")
    staff = _make_user("perm_staff", role="student", is_staff=True)
    project = _make_project(owner)

    response = _client(staff).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Staff Edit"}, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_admin_can_review_block_and_approve_through_moderation_endpoint():
    owner = _make_user("perm_mod_owner", role="publisher")
    staff = _make_user("perm_mod_staff", role="student", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review")

    blocked = _client(staff).post(
        f"/api/v1/admin/moderation/projects/{project.id}/action/",
        {"action": "block", "reason": "Manual block after review."},
        format="json",
    )
    project.refresh_from_db()

    assert blocked.status_code == 200
    assert project.moderation_status == "admin_rejected"
    assert project.is_published is False
    assert project.manual_moderation_status == "blocked"
    assert ModerationAuditEvent.objects.filter(project=project, action="block", actor=staff).exists()

    approved = _client(staff).post(
        f"/api/v1/admin/moderation/projects/{project.id}/action/",
        {"action": "approve", "reason": "Educational context approved."},
        format="json",
    )
    project.refresh_from_db()

    assert approved.status_code == 200
    assert project.moderation_status == "admin_approved"
    assert ModerationAuditEvent.objects.filter(project=project, action="approve", actor=staff).exists()


@pytest.mark.django_db
def test_staff_admin_can_read_watch_review_copy():
    owner = _make_user("perm_watch_owner", role="publisher")
    staff = _make_user("perm_watch_staff", role="student", is_staff=True)
    project = _make_project(owner, moderation_status="revision_required", published=False)

    response = _client(staff).get(f"/api/v1/catalog/{project.id}/")

    assert response.status_code == 200
    assert response.data["id"] == project.id


@pytest.mark.django_db
def test_staff_admin_can_read_project_detail_but_not_patch_non_owner():
    owner = _make_user("perm_detail_owner", role="publisher")
    staff = _make_user("perm_detail_staff", role="student", is_staff=True)
    project = _make_project(owner)

    read = _client(staff).get(f"/api/v1/projects/{project.id}/")
    edit = _client(staff).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Nope"}, format="json")

    assert read.status_code == 200
    assert read.data["id"] == project.id
    assert edit.status_code == 403


@pytest.mark.django_db
def test_moderation_preview_is_owner_or_staff_only(tmp_path):
    owner = _make_user("perm_preview_owner", role="publisher")
    other = _make_user("perm_preview_other", role="publisher")
    staff = _make_user("perm_preview_staff", role="student", is_staff=True)
    project = _make_project(owner, moderation_status="needs_admin_review", published=False)
    image = tmp_path / "uploads" / str(project.id) / "unsafe.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"unsafe preview")
    finding = _add_visual_finding(project, image_path=str(image))
    url = f"/api/v1/projects/{project.id}/moderation-preview/{finding.id}/"

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        owner_response = _client(owner).get(url)
        staff_response = _client(staff).get(url)
        other_response = _client(other).get(url)

    assert owner_response.status_code == 200
    assert staff_response.status_code == 200
    assert other_response.status_code == 403


@pytest.mark.django_db
def test_staff_admin_can_read_transcript_for_review_but_not_mutate_it():
    owner = _make_user("perm_transcript_owner", role="publisher")
    staff = _make_user("perm_transcript_staff", role="student", is_staff=True)
    project = _make_project(owner)
    page = project.transcript_pages.create(
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="slide-1",
        original_text="Original",
        narration_text="Narration",
    )

    read = _client(staff).get(f"/api/v1/projects/{project.id}/transcript/")
    edit = _client(staff).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {"pages": [{"id": page.id, "narration_text": "Staff edit"}]},
        format="json",
    )

    assert read.status_code == 200
    assert read.data["pages"][0]["id"] == page.id
    assert edit.status_code == 403


@pytest.mark.django_db
def test_staff_admin_can_read_scene_background_for_review_but_not_delete(tmp_path):
    owner = _make_user("perm_background_owner", role="publisher")
    other = _make_user("perm_background_other", role="publisher")
    staff = _make_user("perm_background_staff", role="student", is_staff=True)
    project = _make_project(owner)
    image = tmp_path / "backgrounds" / "custom.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"custom background")
    page = project.transcript_pages.create(
        order=0,
        source_slide_index=0,
        split_index=0,
        page_key="slide-1",
        original_text="Original",
        narration_text="Narration",
        editor_document={
            "scene": {
                "background_mode": "custom",
                "custom_background_path": "backgrounds/custom.jpg",
            },
        },
    )
    read_url = f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/custom/"
    delete_url = f"/api/v1/projects/{project.id}/transcript-pages/{page.id}/background/"

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        owner_read = _client(owner).get(read_url)
        staff_read = _client(staff).get(read_url)
        other_read = _client(other).get(read_url)
        staff_delete = _client(staff).delete(delete_url)

    assert owner_read.status_code == 200
    assert staff_read.status_code == 200
    assert other_read.status_code == 403
    assert staff_delete.status_code == 403


@pytest.mark.django_db
def test_publisher_cannot_read_other_publishers_project_for_studio_review():
    owner = _make_user("perm_other_read_owner", role="publisher")
    other = _make_user("perm_other_read_publisher", role="publisher")
    project = _make_project(owner)

    response = _client(other).get(f"/api/v1/projects/{project.id}/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_admin_review_gets_lesson_content_when_video_not_ready():
    owner = _make_user("perm_no_video_owner", role="publisher")
    staff = _make_user("perm_no_video_staff", role="student", is_staff=True)
    project = Project.objects.create(
        title="No video lesson",
        user=owner,
        status="draft",
        moderation_status="revision_required",
        is_published=False,
    )

    response = _client(staff).get(f"/api/v1/catalog/{project.id}/")

    assert response.status_code == 200
    assert response.data["id"] == project.id
    assert response.data["video_ready"] is False
    assert response.data["playback_status"] == "video_not_ready"
    assert "transcript_pages" in response.data


@pytest.mark.django_db
def test_superuser_editor_override_default_false_and_explicit_true():
    owner = _make_user("perm_super_owner", role="publisher")
    superuser = _make_user("perm_super", role="student", is_superuser=True)
    project = _make_project(owner)

    denied = _client(superuser).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Denied"}, format="json")
    assert denied.status_code == 403

    with override_settings(STUDIO_SUPERUSER_EDITOR_OVERRIDE_ENABLED=True):
        allowed = _client(superuser).patch(f"/api/v1/projects/{project.id}/", {"category_name": "Allowed"}, format="json")

    assert allowed.status_code == 200
    assert allowed.data["category_name"] == "Allowed"
