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

from django.contrib.auth.models import User  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.drafts import promote_project_draft  # noqa: E402
from core.models import Job, Project, TranscriptPage, UserProfile  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


def _make_user(username: str, *, role: str = "publisher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_project(owner: User, title: str = "Draft management lesson") -> Project:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/old-public.mp4",
        srt_url=f"{project.id}/old-public.srt",
    )
    return project


def _make_page(project: Project, *, text: str = "Public safe text", order: int = 0) -> TranscriptPage:
    return TranscriptPage.objects.create(
        project=project,
        order=order,
        source_slide_index=order,
        split_index=0,
        page_key=f"s{order + 1}-p1",
        original_text=text,
        narration_text=text,
        rich_text_html=text,
        subtitle_chunks=[text],
        editor_document={
            "version": 1,
            "html": text,
            "paragraphs": [{"index": 0, "text": text}],
            "text": {"narration_customized": False, "display_text_customized": False},
        },
    )


def _save_draft_text(project: Project, page: TranscriptPage, text: str):
    return _client(project.user).patch(
        f"/api/v1/projects/{project.id}/transcript/",
        {
            "draft_only": True,
            "pages": [{"id": page.id, "original_text": text, "narration_text": text}],
        },
        format="json",
    )


def _discard_draft(project: Project, user: User | None):
    return _client(user).post(f"/api/v1/projects/{project.id}/draft/discard/", {}, format="json")


def _block_draft_with_moderation(project: Project, monkeypatch) -> dict:
    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_AUTO_ENABLED", True, raising=False)
    monkeypatch.setattr("django.conf.settings.SOURCE_MODERATION_BLOCK_RENDER_ON_REJECTION", True, raising=False)
    Job.objects.create(project=project, job_type="video_export", status="running", progress=10)
    result = worker_tasks._run_auto_source_moderation_for_draft(project.id)
    worker_tasks._mark_draft_render_blocked(project.id, result)
    return result


@pytest.mark.django_db
def test_discard_draft_clears_draft_data_and_keeps_active_page_unchanged():
    owner = _make_user("discard_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Active public text")
    assert _save_draft_text(project, page, "Private draft text").status_code == 200

    response = _discard_draft(project, owner)

    project.refresh_from_db()
    page.refresh_from_db()
    assert response.status_code == 200
    assert response.data["discarded"] is True
    assert response.data["has_draft"] is False
    assert project.draft_data == {}
    assert page.narration_text == "Active public text"


@pytest.mark.django_db
def test_discard_draft_makes_studio_fetch_active_text_again():
    owner = _make_user("discard_fetch_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Studio active text")
    _save_draft_text(project, page, "Studio draft text")

    draft_response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")
    discard_response = _discard_draft(project, owner)
    active_response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")

    assert draft_response.data["pages"][0]["narration_text"] == "Studio draft text"
    assert discard_response.data["pages"][0]["narration_text"] == "Studio active text"
    assert active_response.data["has_draft"] is False
    assert active_response.data["pages"][0]["narration_text"] == "Studio active text"


@pytest.mark.django_db
def test_student_and_anonymous_cannot_discard_publisher_draft():
    owner = _make_user("discard_permission_owner")
    student = _make_user("discard_permission_student", role="student")
    project = _make_project(owner)
    page = _make_page(project)
    _save_draft_text(project, page, "Student must not discard this")

    student_response = _discard_draft(project, student)
    anonymous_response = _discard_draft(project, None)

    project.refresh_from_db()
    assert student_response.status_code == 403
    assert anonymous_response.status_code in {401, 403}
    assert project.draft_data["metadata"]["dirty"] is True


@pytest.mark.django_db
def test_unsafe_draft_moderation_failure_keeps_public_active_text_and_studio_draft(monkeypatch):
    owner = _make_user("unsafe_draft_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Safe public text")
    old_job = project.jobs.filter(status="done").first()
    _save_draft_text(project, page, "I will kill you tomorrow.")

    moderation_result = _block_draft_with_moderation(project, monkeypatch)

    project.refresh_from_db()
    page.refresh_from_db()
    old_job.refresh_from_db()
    failed_job = project.jobs.order_by("-created_at", "-id").first()
    public_response = _client().get(f"/api/v1/catalog/{project.id}/")
    studio_response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")

    assert moderation_result["block_render"] is True
    assert page.narration_text == "Safe public text"
    assert old_job.result_url.endswith("/old-public.mp4")
    assert failed_job.status == "failed"
    assert not failed_job.result_url
    assert public_response.status_code == 200
    assert public_response.data["transcript_pages"][0]["narration_text"] == "Safe public text"
    assert "draft_data" not in public_response.data
    assert "draft_metadata" not in public_response.data
    assert studio_response.data["has_draft"] is True
    assert studio_response.data["pages"][0]["narration_text"] == "I will kill you tomorrow."


@pytest.mark.django_db
def test_discard_unsafe_draft_returns_studio_to_public_text(monkeypatch):
    owner = _make_user("unsafe_discard_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Safe version before unsafe draft")
    _save_draft_text(project, page, "I will kill you tomorrow.")
    _block_draft_with_moderation(project, monkeypatch)

    discard_response = _discard_draft(project, owner)
    studio_response = _client(owner).get(f"/api/v1/projects/{project.id}/transcript/")

    project.refresh_from_db()
    page.refresh_from_db()
    assert discard_response.status_code == 200
    assert discard_response.data["has_draft"] is False
    assert discard_response.data["pages"][0]["narration_text"] == "Safe version before unsafe draft"
    assert studio_response.data["pages"][0]["narration_text"] == "Safe version before unsafe draft"
    assert project.draft_data == {}
    assert "draft_moderation" not in project.moderation_summary
    assert page.narration_text == "Safe version before unsafe draft"


@pytest.mark.django_db
def test_successful_safe_draft_promotion_clears_draft():
    owner = _make_user("safe_promote_management_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Old active text")
    _save_draft_text(project, page, "Promoted safe draft")

    result = promote_project_draft(
        project,
        render_outputs={
            "page_timeline": [
                {"page_key": page.page_key, "start": 0, "end": 1, "duration": 1, "chunk_timeline": []}
            ]
        },
    )

    project.refresh_from_db()
    page.refresh_from_db()
    assert result["status"] == "promoted"
    assert project.draft_data == {}
    assert page.narration_text == "Promoted safe draft"


@pytest.mark.django_db
def test_public_catalog_does_not_expose_draft_before_promotion():
    owner = _make_user("public_draft_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Visible public text")
    _save_draft_text(project, page, "Hidden draft text")

    public_response = _client().get(f"/api/v1/catalog/{project.id}/")

    assert public_response.status_code == 200
    assert public_response.data["transcript_pages"][0]["narration_text"] == "Visible public text"
    assert "draft_data" not in public_response.data
    assert "draft_metadata" not in public_response.data
    assert "has_draft" not in public_response.data


@pytest.mark.django_db
def test_save_only_creates_draft_without_mutating_active_rows():
    owner = _make_user("save_only_management_owner")
    project = _make_project(owner)
    page = _make_page(project, text="Active save-only text")

    response = _save_draft_text(project, page, "Saved draft only text")

    project.refresh_from_db()
    page.refresh_from_db()
    assert response.status_code == 200
    assert project.draft_data["metadata"]["dirty"] is True
    assert project.draft_data["transcript_pages"][0]["narration_text"] == "Saved draft only text"
    assert page.narration_text == "Active save-only text"
