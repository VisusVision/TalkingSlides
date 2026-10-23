"""Owner-private visual choices: revision safety, history and pipeline isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from rest_framework.test import APIClient

from core.drafts import build_draft_from_active_project, promote_project_draft, save_project_draft_data
from core.models import Job, Project, TranscriptPage, VisualDesignSuggestion, VisualDraftDecision, VisualDraftSelection
from core.visual_design_source import build_studio_visual_source
from core.visual_suggestions import generate_suggestion

pytestmark = pytest.mark.django_db


@pytest.fixture
def visual_lesson():
    owner = User.objects.create_user(username="selection-owner")
    project = Project.objects.create(user=owner, title="Private visual test", status="ready", is_published=True, moderation_status="approved")
    page = TranscriptPage.objects.create(project=project, page_key="source-0-split-1", order=0, source_slide_index=0, split_index=1,
        original_text="Ölçüm & kayıt", narration_text="Protected teacher narration")
    Job.objects.create(project=project, status="done", job_type="video_export", result_url="published.mp4")
    source = build_studio_visual_source(project, page.page_key)
    candidate, _ = generate_suggestion(project, owner, source)
    client = APIClient(); client.force_authenticate(owner)
    return owner, project, page, source, candidate, client


def url(project):
    return f"/api/v1/projects/{project.pk}/visual-draft/"


def payload(page, source, candidate, action="select", version=0):
    data = {"page_key": page.page_key, "expected_source_hash": source.source_hash, "expected_version": version, "action": action}
    if action != "restore": data["candidate_id"] = str(candidate.pk)
    return data


def post(lesson, action="select", version=0, **changes):
    _, project, page, source, candidate, client = lesson
    return client.post(url(project), {**payload(page, source, candidate, action, version), **changes}, format="json")


def test_select_reload_and_source_hash_stability_without_any_lesson_mutation(visual_lesson):
    owner, project, page, source, candidate, client = visual_lesson
    before = {"projects": list(Project.objects.values()), "pages": list(TranscriptPage.objects.values()), "jobs": list(Job.objects.values()), "candidates": list(VisualDesignSuggestion.objects.values())}
    with patch("core.views._celery_app.send_task", side_effect=AssertionError("No render dispatch")), patch("core.drafts.promote_project_draft", side_effect=AssertionError("No promotion")):
        result = post(visual_lesson)
    assert result.status_code == 200, result.data
    assert result.data["selection"]["state"] == "selected"
    assert result.data["selection"]["contains_text"] is True
    assert result.data["selection"]["is_stale"] is False
    assert result.data["selection"]["version"] == 1
    assert result.data["selection"]["design_versions"]["renderer"] == "svg-educational.v2"
    assert result.data["applied"] is False
    project.refresh_from_db()
    assert build_studio_visual_source(project, page.page_key).source_hash == source.source_hash
    assert {"projects": list(Project.objects.values()), "pages": list(TranscriptPage.objects.values()), "jobs": list(Job.objects.values()), "candidates": list(VisualDesignSuggestion.objects.values())} == before
    reopened = APIClient(); reopened.force_authenticate(owner)
    fetched = reopened.get(url(project), {"page_key": page.page_key})
    assert fetched.data["selection"] == result.data["selection"]
    assert fetched.data["selected_candidate"]["id"] == str(candidate.pk)
    assert fetched["Cache-Control"] == "private, no-store"
    assert "Authorization" in fetched["Vary"]


def test_select_reject_restore_and_idempotent_history(visual_lesson):
    assert post(visual_lesson).status_code == 200
    for version in (0, 1):
        result = post(visual_lesson, version=version)
        assert result.status_code == 200 and result.data["reused"] is True
    assert VisualDraftDecision.objects.count() == 1
    rejected = post(visual_lesson, action="reject", version=1)
    assert rejected.status_code == 200
    assert rejected.data["selection"]["state"] == "original"
    assert str(visual_lesson[4].pk) in rejected.data["rejected_ids"]
    assert post(visual_lesson, action="reject", version=1).data["reused"] is True
    assert post(visual_lesson, version=0).status_code == 409
    selected = post(visual_lesson, version=2)
    assert selected.data["rejected_ids"] == []  # Deliberate re-selection supersedes rejection.
    restored = post(visual_lesson, action="restore", version=3)
    assert restored.status_code == 200
    assert restored.data["selection"]["candidate_id"] is None
    assert restored.data["selection"]["contains_text"] is False
    assert post(visual_lesson, action="restore", version=3).data["reused"] is True
    assert [x["action"] for x in restored.data["history"]] == ["restore", "select", "reject", "select"]
    assert VisualDesignSuggestion.objects.count() == 1


def test_owner_only_and_cross_project_or_page_candidates(visual_lesson):
    owner, project, page, source, candidate, _ = visual_lesson
    outsider = User.objects.create_user(username="outsider", is_staff=True, is_superuser=True)
    other_project = Project.objects.create(user=outsider, title="Foreign")
    other_page = TranscriptPage.objects.create(project=other_project, page_key=page.page_key, original_text="Other", narration_text="Other")
    other, _ = generate_suggestion(other_project, outsider, build_studio_visual_source(other_project, other_page.page_key))
    for user, expected in [(None, 401), (outsider, 404)]:
        client = APIClient()
        if user: client.force_authenticate(user)
        assert client.get(url(project), {"page_key": page.page_key}).status_code == expected
        assert client.post(url(project), payload(page, source, candidate), format="json").status_code == expected
    assert post(visual_lesson, candidate_id=str(other.pk)).status_code == 404
    VisualDesignSuggestion.objects.filter(pk=candidate.pk).update(page_key="different-page")
    assert post(visual_lesson).status_code == 404
    assert VisualDraftSelection.objects.count() == 0


@pytest.mark.parametrize("change", ["display", "narration", "scene", "source", "split", "key", "delete", "restore", "draft", "merge"])
def test_actual_saved_source_changes_invalidate_selected_candidate(visual_lesson, change):
    _, project, page, source, _, client = visual_lesson
    assert post(visual_lesson).status_code == 200
    if change in ("draft", "merge"):
        draft = build_draft_from_active_project(project)
        if change == "draft": draft["transcript_pages"][0]["original_text"] = "Edited private source"
        else: draft["transcript_pages"] = []
        save_project_draft_data(project, draft)
    else:
        if change == "display": page.original_text = "New text"
        if change == "narration": page.narration_text = "New narration"
        if change == "scene": page.editor_document = {"scene": {"background": "white"}}
        if change == "source": page.source_slide_index = 2
        if change == "split": page.split_index = 2
        if change == "key": page.page_key = "new-key"
        if change in ("delete", "restore"): page.is_active = False
        page.save()
        if change == "restore": page.is_active = True; page.save()
    fetched = client.get(url(project), {"page_key": "source-0-split-1"})
    assert fetched.data["selection"]["is_stale"] is True
    assert post(visual_lesson, version=1).status_code == 409


def test_saved_private_source_and_global_promotion_are_isolated(visual_lesson):
    owner, project, page, _, _, client = visual_lesson
    draft = build_draft_from_active_project(project)
    draft["project"]["title"] = "Saved ordinary draft"
    save_project_draft_data(project, draft)
    source = build_studio_visual_source(project, page.page_key)
    candidate, _ = generate_suggestion(project, owner, source)
    before = deepcopy(project.draft_data)
    result = client.post(url(project), payload(page, source, candidate), format="json")
    assert result.status_code == 200
    project.refresh_from_db()
    assert project.draft_data == before
    assert build_studio_visual_source(project, page.page_key).source_hash == source.source_hash
    promote_project_draft(project)
    page.refresh_from_db()
    assert page.editor_document == {} and page.narration_text == "Protected teacher narration"
    assert VisualDraftSelection.objects.get().candidate_id == candidate.pk
    assert client.get(url(project), {"page_key": page.page_key}).data["selection"]["is_stale"] is True


@pytest.mark.parametrize("change", ["svg", "preview_hash", "plan_hash", "plan", "status", "version", "source_revision", "preview_status"])
def test_reject_tampered_or_unsupported_candidate(visual_lesson, change):
    candidate = visual_lesson[4]
    updates = {}
    if change in ("svg", "preview_status"):
        preview = deepcopy(candidate.preview_payload)
        if change == "svg": preview["svg"] = preview["svg"].replace("</svg>", "<script>evil()</script></svg>")
        else: preview["status"] = "invalid"
        updates["preview_payload"] = preview
    elif change in ("preview_hash", "plan_hash"): updates[change] = "sha256:" + "b" * 64
    elif change == "plan": updates["plan_payload"] = {**candidate.plan_payload, "required_labels": ["Fabricated"]}
    elif change == "status": updates["status"] = "unsupported"
    elif change == "source_revision": updates["source_revision"] = "sha256:" + "b" * 64
    else: updates["schema_version"] = "visual-suggestion.v999"
    VisualDesignSuggestion.objects.filter(pk=candidate.pk).update(**updates)
    assert post(visual_lesson).status_code == 409
    assert VisualDraftSelection.objects.count() == 0
    assert VisualDraftDecision.objects.count() == 0


@pytest.mark.parametrize("changes", [{"svg": "<svg/>"}, {"expected_version": True}, {"expected_version": "0"}, {"expected_source_hash": "bad"}, {"action": "apply"}, {"candidate_id": 5}, {"page_key": 0}])
def test_strict_writable_contract(visual_lesson, changes):
    assert post(visual_lesson, **changes).status_code == 400
    assert VisualDraftSelection.objects.count() == 0


def test_atomic_rollback_on_revalidation_conflict(visual_lesson):
    from core.visual_drafts import current_source
    calls = 0
    def racing(project, key):
        nonlocal calls
        calls += 1
        value = current_source(project, key)
        return value if calls == 1 else type(value)(**{**value.__dict__, "source_hash": "sha256:" + "f" * 64})
    with patch("core.visual_drafts.current_source", side_effect=racing):
        assert post(visual_lesson).status_code == 409
    assert VisualDraftSelection.objects.count() == VisualDraftDecision.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_simultaneous_different_decisions_never_overwrite_same_version(visual_lesson):
    owner, project, page, source, candidate, _ = visual_lesson
    barrier = Barrier(2)
    def decide(action):
        close_old_connections()
        client = APIClient(); client.force_authenticate(User.objects.get(pk=owner.pk))
        barrier.wait(timeout=5)
        result = client.post(url(project), payload(page, source, candidate, action), format="json")
        close_old_connections()
        return result.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(decide, ["select", "restore"]))
    assert all(s in (200, 409) for s in statuses), statuses
    assert statuses.count(200) <= 1
    if connection.vendor != "sqlite": assert sorted(statuses) == [200, 409]
    if statuses.count(200) == 0:  # SQLite's shared-cache lock asks both writers to retry.
        assert post(visual_lesson).status_code == 200
    assert VisualDraftSelection.objects.get().version == 1
    assert VisualDraftDecision.objects.count() == 1


def test_unrelated_project_timestamp_does_not_change_content_revision(visual_lesson):
    _, project, page, source, _, _ = visual_lesson
    project.save(update_fields=["updated_at"])
    assert build_studio_visual_source(project, page.page_key).source_hash == source.source_hash
    assert post(visual_lesson).status_code == 200


def test_original_bytes_change_even_when_stat_identity_is_preserved(visual_lesson, tmp_path):
    import os
    from django.test import override_settings
    owner, project, page, _, _, client = visual_lesson
    assert post(visual_lesson).status_code == 200
    asset = tmp_path / str(project.pk) / "images" / "original.png"
    asset.parent.mkdir(parents=True); asset.write_bytes(b"first-image")
    page.editor_document = {"scene": {"original_background_path": f"{project.pk}/images/original.png"}}; page.save()
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        source = build_studio_visual_source(project, page.page_key)
        candidate, _ = generate_suggestion(project, owner, source)
        assert candidate.status == "preserved"
        assert source.snapshot.to_dict()["original_asset_ref"].startswith("asset:")
        stamp = asset.stat(); asset.write_bytes(b"other-image")
        os.utime(asset, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert asset.stat().st_size == stamp.st_size
        assert build_studio_visual_source(project, page.page_key).source_hash != source.source_hash
        assert client.get(url(project), {"page_key": page.page_key}).data["selection"]["is_stale"] is True


def test_explicit_original_restore_and_project_cleanup(visual_lesson):
    _, project, _, _, _, _ = visual_lesson
    assert post(visual_lesson, action="restore").status_code == 200
    assert VisualDraftDecision.objects.get().action == "restore"
    assert post(visual_lesson, action="restore").data["reused"] is True
    assert post(visual_lesson, version=1).status_code == 200
    project.delete()
    assert VisualDraftDecision.objects.count() == VisualDraftSelection.objects.count() == 0


def test_cross_owner_candidate_inside_same_project_is_rejected(visual_lesson):
    outsider = User.objects.create_user(username="foreign-candidate-owner")
    VisualDesignSuggestion.objects.filter(pk=visual_lesson[4].pk).update(owner=outsider)
    assert post(visual_lesson).status_code == 404
    assert VisualDraftDecision.objects.count() == 0


def test_self_consistent_hashes_cannot_authorize_fabricated_source_text(visual_lesson):
    from core.visual_design import DeterministicPlanner, SlideSourceSnapshot, VisualPlan, VisualPreview, render_preview
    source, candidate = visual_lesson[3:5]
    invented = SlideSourceSnapshot({**source.snapshot.to_dict(), "original_display_text": "Invented educational claim"})
    other_plan = DeterministicPlanner().plan(invented)
    other_preview = render_preview(invented, other_plan)
    forged_plan = VisualPlan({**other_plan.to_dict(), "source_identity": source.snapshot.identity, "source_hash": source.source_hash})
    forged_preview = VisualPreview({**other_preview.to_dict(), "plan_hash": forged_plan.content_hash, "source_hash": source.source_hash})
    VisualDesignSuggestion.objects.filter(pk=candidate.pk).update(plan_payload=forged_plan.to_dict(), preview_payload=forged_preview.to_dict(), plan_hash=forged_plan.content_hash, preview_hash=forged_preview.content_hash)
    assert post(visual_lesson).status_code == 409
    assert VisualDraftSelection.objects.count() == 0
