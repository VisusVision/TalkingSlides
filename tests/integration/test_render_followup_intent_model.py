import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import Project, RenderFollowUpIntent, UserProfile


pytestmark = pytest.mark.django_db


def _make_project(username: str = "render_followup_owner") -> tuple[User, Project]:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    project = Project.objects.create(title=f"Render follow-up {username}", user=user)
    return user, project


def test_create_pending_targeted_render_followup_intent():
    user, project = _make_project()

    intent = RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        reason="transcript_edit",
        requested_by=user,
    )

    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1"]
    assert intent.reason == "transcript_edit"
    assert intent.requested_by == user
    assert intent.created_at is not None
    assert intent.updated_at is not None


def test_page_keys_default_is_independent_list():
    _, project = _make_project("render_followup_default_list")
    first = RenderFollowUpIntent.objects.create(project=project)
    first.status = RenderFollowUpIntent.STATUS_CLEARED
    first.save(update_fields=["status", "updated_at"])
    second = RenderFollowUpIntent.objects.create(project=project)

    first.page_keys.append("s1-p1")

    assert first.page_keys == ["s1-p1"]
    assert second.page_keys == []


def test_metadata_default_is_independent_dict():
    _, project = _make_project("render_followup_default_dict")
    first = RenderFollowUpIntent.objects.create(project=project)
    first.status = RenderFollowUpIntent.STATUS_CANCELLED
    first.save(update_fields=["status", "updated_at"])
    second = RenderFollowUpIntent.objects.create(project=project)

    first.metadata["source"] = "test"

    assert first.metadata == {"source": "test"}
    assert second.metadata == {}


def test_full_mode_allows_empty_page_keys():
    _, project = _make_project("render_followup_full")

    intent = RenderFollowUpIntent.objects.create(
        project=project,
        mode=RenderFollowUpIntent.MODE_FULL,
        page_keys=[],
        reason="structural_action",
    )

    intent.full_clean()
    assert intent.mode == RenderFollowUpIntent.MODE_FULL
    assert intent.page_keys == []


def test_status_choices_are_validated_by_full_clean():
    _, project = _make_project("render_followup_status_choices")
    intent = RenderFollowUpIntent(project=project, status="not-a-status")

    with pytest.raises(ValidationError):
        intent.full_clean()


def test_project_allows_only_one_active_render_followup_intent():
    _, project = _make_project("render_followup_unique_active")
    RenderFollowUpIntent.objects.create(project=project, status=RenderFollowUpIntent.STATUS_PENDING)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RenderFollowUpIntent.objects.create(
                project=project,
                status=RenderFollowUpIntent.STATUS_CLAIMED,
            )


@pytest.mark.parametrize(
    "terminal_status",
    [RenderFollowUpIntent.STATUS_CLEARED, RenderFollowUpIntent.STATUS_CANCELLED],
)
def test_terminal_intent_status_allows_new_pending_intent(terminal_status):
    _, project = _make_project(f"render_followup_{terminal_status}")
    RenderFollowUpIntent.objects.create(project=project, status=terminal_status)

    pending = RenderFollowUpIntent.objects.create(
        project=project,
        status=RenderFollowUpIntent.STATUS_PENDING,
    )

    assert pending.status == RenderFollowUpIntent.STATUS_PENDING
