import pytest
from django.contrib.auth.models import User

from core.models import Project, RenderFollowUpIntent, UserProfile
from core import render_followup_intents
from core.render_followup_intents import merge_render_followup_intent


pytestmark = pytest.mark.django_db


def _make_project(username: str = "render_followup_merge_owner") -> tuple[User, Project]:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    project = Project.objects.create(title=f"Render follow-up merge {username}", user=user)
    return user, project


def test_merge_helper_creates_pending_targeted_intent_when_none_active():
    user, project = _make_project()

    intent = merge_render_followup_intent(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        reason="transcript_edit",
        requested_by=user,
        metadata={"source": "editor"},
    )

    assert intent.status == RenderFollowUpIntent.STATUS_PENDING
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1"]
    assert intent.reason == "transcript_edit"
    assert intent.requested_by == user
    assert intent.metadata == {"source": "editor"}


def test_merge_helper_unions_targeted_page_keys():
    _, project = _make_project("render_followup_union")
    merge_render_followup_intent(project=project, page_keys=["s1-p1", "s2-p1"])

    intent = merge_render_followup_intent(project=project, page_keys=["s2-p1", "s3-p1"])

    assert RenderFollowUpIntent.objects.filter(project=project).count() == 1
    assert intent.mode == RenderFollowUpIntent.MODE_TARGETED
    assert intent.page_keys == ["s1-p1", "s2-p1", "s3-p1"]


def test_merge_helper_upgrades_targeted_to_full_and_clears_page_keys():
    _, project = _make_project("render_followup_targeted_to_full")
    merge_render_followup_intent(project=project, page_keys=["s1-p1"])

    intent = merge_render_followup_intent(
        project=project,
        mode=RenderFollowUpIntent.MODE_FULL,
        page_keys=["s2-p1"],
        reason="manual_full_render",
    )

    assert intent.mode == RenderFollowUpIntent.MODE_FULL
    assert intent.page_keys == []
    assert intent.reason == "manual_full_render"


def test_merge_helper_keeps_full_when_targeted_arrives_later():
    _, project = _make_project("render_followup_full_stays_full")
    merge_render_followup_intent(project=project, mode=RenderFollowUpIntent.MODE_FULL, reason="manual_full_render")

    intent = merge_render_followup_intent(project=project, page_keys=["s1-p1"], reason="transcript_edit")

    assert intent.mode == RenderFollowUpIntent.MODE_FULL
    assert intent.page_keys == []
    assert intent.reason == "transcript_edit"


def test_merge_helper_treats_structural_reason_as_full():
    _, project = _make_project("render_followup_structural")

    intent = merge_render_followup_intent(
        project=project,
        mode=RenderFollowUpIntent.MODE_TARGETED,
        page_keys=["s1-p1"],
        reason="structural_action",
    )

    assert intent.mode == RenderFollowUpIntent.MODE_FULL
    assert intent.page_keys == []


@pytest.mark.parametrize(
    "terminal_status",
    [RenderFollowUpIntent.STATUS_CLEARED, RenderFollowUpIntent.STATUS_CANCELLED],
)
def test_merge_helper_ignores_terminal_intents_when_creating_pending(terminal_status):
    _, project = _make_project(f"render_followup_merge_{terminal_status}")
    RenderFollowUpIntent.objects.create(project=project, status=terminal_status, page_keys=["old"])

    pending = merge_render_followup_intent(project=project, page_keys=["new"])

    assert pending.status == RenderFollowUpIntent.STATUS_PENDING
    assert pending.page_keys == ["new"]
    assert RenderFollowUpIntent.objects.filter(project=project).count() == 2


def test_merge_helper_updates_requested_by_to_latest_requester():
    first_user, project = _make_project("render_followup_requested_by")
    second_user = User.objects.create_user(username="render_followup_second_requester", password="pass")
    merge_render_followup_intent(project=project, page_keys=["s1-p1"], requested_by=first_user)

    intent = merge_render_followup_intent(project=project, page_keys=["s2-p1"], requested_by=second_user)

    assert intent.requested_by == second_user


def test_merge_helper_merges_metadata_without_overwriting_existing_values():
    _, project = _make_project("render_followup_metadata")
    merge_render_followup_intent(
        project=project,
        page_keys=["s1-p1"],
        metadata={"source": "editor", "count": 1},
    )

    intent = merge_render_followup_intent(
        project=project,
        page_keys=["s2-p1"],
        metadata={"source": "action", "request_id": "abc"},
    )

    assert intent.metadata["source"] == "editor"
    assert intent.metadata["count"] == 1
    assert intent.metadata["request_id"] == "abc"
    assert intent.metadata["merge_conflicts"] == [{"key": "source", "incoming": "action"}]


def test_merge_helper_recovers_when_first_create_hits_unique_race(monkeypatch):
    _, project = _make_project("render_followup_integrity_retry")
    original_merge_once = render_followup_intents._merge_render_followup_intent_once
    calls = {"count": 0}

    def merge_once_with_race(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            RenderFollowUpIntent.objects.create(
                project=project,
                status=RenderFollowUpIntent.STATUS_PENDING,
                page_keys=["race"],
            )
            raise render_followup_intents.IntegrityError("simulated unique race")
        return original_merge_once(**kwargs)

    monkeypatch.setattr(render_followup_intents, "_merge_render_followup_intent_once", merge_once_with_race)

    intent = merge_render_followup_intent(project=project, page_keys=["s1-p1"])

    assert calls["count"] == 2
    assert intent.page_keys == ["race", "s1-p1"]
    assert RenderFollowUpIntent.objects.filter(project=project).count() == 1
