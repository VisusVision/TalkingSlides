"""Frozen server state, source integrity and zero authoritative pipeline writes."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection, close_old_connections
from django.core.exceptions import ValidationError
from django.http import Http404
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.test import APIClient
from PIL import Image

from core.models import Project, TranscriptPage, VoiceProfile, Job, VisualDraftSelection, VisualRenderSnapshot, VisualDesignSuggestion
from core.drafts import build_draft_from_active_project, save_project_draft_data
from core.visual_design.schemas import DesignError
from core.visual_design.render_snapshot import FrozenRenderSnapshot, require_rendered_revision, RECEIPT_VERSION
from core.visual_design_source import build_studio_visual_source
from core.visual_suggestions import generate_suggestion
from core.visual_drafts import decide_visual_draft, VisualDraftConflict
from core.visual_render_snapshots import (current_render_revision, freeze_render_snapshot, read_render_snapshot, RenderSnapshotConflict)
from core.visual_render_assets import PrivateRenderAssets

pytestmark = pytest.mark.django_db
PROCESS = "Measurement process\n1. Collect sample\n2. Record value\n3. Check result"


@pytest.fixture
def lesson(tmp_path, settings):
    settings.STORAGE_ROOT = str(tmp_path / "public")
    settings.VISUAL_RENDER_PRIVATE_ROOT = str(tmp_path / "private")
    owner = User.objects.create_user(username="snapshot-owner")
    project = Project.objects.create(user=owner, title="Exact frozen lesson", status="ready", is_published=True)
    page = TranscriptPage.objects.create(project=project, page_key="slide-0-split-1", order=0, source_slide_index=0,
        split_index=1, original_text=PROCESS, narration_text="Protected narration.", subtitle_chunks=["Protected narration."],
        start_seconds=0, end_seconds=2, duration_seconds=2)
    VoiceProfile.objects.create(user=owner, voice_id="teacher-voice", provider="xtts_v2", language="en")
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="authoritative.mp4")
    client = APIClient(); client.force_authenticate(owner)
    return owner, project, page, client


def select(lesson):
    owner, project, page, _ = lesson
    project.refresh_from_db()
    source = build_studio_visual_source(project, page.page_key)
    candidate, _ = generate_suggestion(project, owner, source)
    assert candidate.status == "ready"
    decide_visual_draft(project.pk, owner, {"page_key": page.page_key, "action": "select", "candidate_id": candidate.pk,
        "expected_source_hash": source.source_hash, "expected_version": 0})
    return candidate


def freeze(lesson):
    owner, project, _, _ = lesson
    return freeze_render_snapshot(project.pk, owner, current_render_revision(project.pk, owner))


def url(lesson): return f"/api/v1/projects/{lesson[1].pk}/visual-render-snapshots/"


def protected(): return [list(model.objects.values()) for model in (Project, TranscriptPage, Job)]


def test_snapshot_pins_exact_selected_revision_and_is_repeatable_without_pipeline_mutation(lesson):
    candidate = select(lesson)
    initial = protected()
    with patch("core.views._celery_app.send_task", side_effect=AssertionError("No job dispatch")), patch("core.drafts.promote_project_draft", side_effect=AssertionError("No promotion")):
        row = freeze(lesson)
        again = freeze(lesson)
    assert again.pk == row.pk and VisualRenderSnapshot.objects.count() == 1
    assert protected() == initial
    _, frozen = read_render_snapshot(lesson[1].pk, lesson[0], row.pk)
    value = frozen.to_dict()
    assert value["state"]["pages"][0]["source_slide_index"] == 0
    assert value["state"]["pages"][0]["split_index"] == 1
    assert value["state"]["pages"][0]["start_seconds"] == 0
    assert value["scenes"][0]["type"] == "completed_generated"
    assert value["scenes"][0]["display_overlay"] == "none"
    assert value["scenes"][0]["narration_enabled"] is value["scenes"][0]["subtitles_enabled"] is True
    design = value["scenes"][0]["design"]
    assert design["source_hash"] == candidate.source_hash
    assert design["design_source_hash"] == candidate.plan_payload["source_hash"] != candidate.source_hash
    assert value["state"]["pages"][0]["narration_text"] == "Protected narration."
    value["state"]["pages"][0]["narration_text"] = "Mutated copy"
    assert frozen.to_dict()["state"]["pages"][0]["narration_text"] == "Protected narration."
    with pytest.raises(AttributeError): frozen._json = "mutated"
    with pytest.raises(ValidationError): row.save()


def test_draft_precedence_and_later_edits_cannot_relabel_frozen_A_as_B(lesson):
    owner, project, page, _ = lesson
    draft = build_draft_from_active_project(project)
    draft["transcript_pages"][0].update(id=-17, original_text="Private display A", narration_text="Private narration A")
    save_project_draft_data(project, draft)
    candidate = select(lesson); row = freeze(lesson)
    _, frozen = read_render_snapshot(project.pk, owner, row.pk)
    assert frozen.to_dict()["state"]["source_mode"] == "draft"
    assert frozen.to_dict()["state"]["pages"][0]["narration_text"] == "Private narration A"
    old_json = frozen.to_json()
    receipt = {"schema_version": RECEIPT_VERSION, "snapshot_hash": frozen.content_hash,
        "revision_hash": frozen.to_dict()["revision_hash"], "page_key": page.page_key,
        "frame_asset": {"sha256": "sha256:"+"a"*64, "size": 100, "extension": "png"},
        "width": 1920, "height": 1080, "publishing_allowed": False}
    require_rendered_revision(frozen, receipt, row.revision_hash)
    draft["transcript_pages"][0]["narration_text"] = "Later B"
    save_project_draft_data(project, draft)
    assert read_render_snapshot(project.pk, owner, row.pk)[1].to_json() == old_json
    # The old candidate is now stale and cannot make another frozen revision.
    with pytest.raises(VisualDraftConflict) as exc: current_render_revision(project.pk, owner)
    assert "stale" in str(exc.value).lower() or "saved" in str(exc.value).lower()
    with pytest.raises(DesignError): require_rendered_revision(frozen, receipt, "sha256:"+"b"*64)
    forged = {**receipt, "snapshot_hash": "sha256:"+"b"*64}
    with pytest.raises(DesignError): require_rendered_revision(frozen, forged, row.revision_hash)
    page.refresh_from_db(); assert page.narration_text == "Protected narration."


def test_snapshot_api_is_owner_only_strict_and_metadata_only(lesson):
    _, project, _, client = lesson
    select(lesson)
    revision = client.get(url(lesson)); assert revision.status_code == 200
    assert VisualRenderSnapshot.objects.count() == 0
    result = client.post(url(lesson), {"expected_revision_hash": revision.data["revision_hash"]}, format="json")
    assert result.status_code == 201, result.data
    detail = url(lesson)+result.data["id"]+"/"
    response = client.get(detail)
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]
    assert "payload" not in response.data and "svg" not in str(response.data)
    for extra in ({"svg": "<svg/>"}, {"pages": []}, {"owner": 1}, {"candidate_id": "fake"}):
        assert client.post(url(lesson), {"expected_revision_hash": revision.data["revision_hash"], **extra}, format="json").status_code == 400
    other = User.objects.create_user(username="snapshot-other"); stranger = APIClient(); stranger.force_authenticate(other)
    for c in (APIClient(), stranger):
        assert c.get(detail).status_code in (401, 403, 404)
        assert c.get(url(lesson)).status_code in (401, 403, 404)
    with pytest.raises(Http404) as exc: read_render_snapshot(project.pk, other, result.data["id"])
    assert "No VisualRenderSnapshot" in str(exc.value)


def test_revision_conflict_does_not_write_snapshot_or_job(lesson):
    select(lesson)
    with pytest.raises(RenderSnapshotConflict): freeze_render_snapshot(lesson[1].pk, lesson[0], "sha256:"+"a"*64)
    assert VisualRenderSnapshot.objects.count() == 0 and Job.objects.count() == 1


@pytest.mark.parametrize("tamper", ["source", "candidate", "selection", "snapshot", "pinned_asset", "font"])
def test_integrity_failures_cannot_be_hidden_by_fallback(lesson, tamper):
    candidate = select(lesson); row = freeze(lesson)
    if tamper == "source":
        lesson[2].original_text = "Edited"; lesson[2].save()
        with pytest.raises((DesignError, VisualDraftConflict)): current_render_revision(lesson[1].pk, lesson[0])
        return
    if tamper == "candidate": VisualDesignSuggestion.objects.filter(pk=candidate.pk).update(preview_hash="sha256:"+"0"*64)
    if tamper == "selection": VisualDraftSelection.objects.filter(project=lesson[1]).update(plan_hash="sha256:"+"0"*64)
    if tamper in ("candidate", "selection"):
        with pytest.raises((DesignError, VisualDraftConflict)): current_render_revision(lesson[1].pk, lesson[0])
        return
    if tamper == "snapshot":
        data = deepcopy(row.payload); data["state"]["pages"][0]["original_text"] = "Forged"
        VisualRenderSnapshot.objects.filter(pk=row.pk).update(payload=data)
    else:
        ref = row.payload["fonts"]["regular"] if tamper == "font" else row.payload["scenes"][0]["design"]["asset"]
        PrivateRenderAssets(lesson[1].pk).path(ref).write_bytes(b"tampered")
    with pytest.raises(DesignError): read_render_snapshot(lesson[1].pk, lesson[0], row.pk)


def test_original_assets_are_private_pinned_bytes_not_mutable_paths(lesson, settings):
    owner, project, page, _ = lesson
    path = __import__('pathlib').Path(settings.STORAGE_ROOT) / str(project.pk) / "original.png"
    path.parent.mkdir(parents=True); Image.new("RGB", (320, 180), "navy").save(path)
    page.editor_document = {"scene": {"background_mode": "original", "original_background_path": f"{project.pk}/original.png"}}; page.save()
    row = freeze(lesson)
    _, frozen = read_render_snapshot(project.pk, owner, row.pk)
    asset = frozen.to_dict()["scenes"][0]["original"]["assets"]["original"]
    pinned = PrivateRenderAssets(project.pk).read(asset)
    Image.new("RGB", (320, 180), "red").save(path)
    assert PrivateRenderAssets(project.pk).read(asset) == pinned
    assert read_render_snapshot(project.pk, owner, row.pk)[1].content_hash == row.snapshot_hash
    assert current_render_revision(project.pk, owner) != row.revision_hash


@pytest.mark.parametrize("path", ["../secret.png", "9999/foreign.png", "https://example.com/img.png", "/etc/passwd", "uploads/9999/x.png"])
def test_cross_project_and_traversal_originals_rejected(lesson, path):
    lesson[2].editor_document = {"scene": {"original_background_path": path}}; lesson[2].save()
    with pytest.raises(DesignError): current_render_revision(lesson[1].pk, lesson[0])


def test_duplicate_keys_unknown_modes_nonfinite_and_oversized_inputs_fail(lesson):
    owner, project, page, _ = lesson
    draft = build_draft_from_active_project(project)
    draft["transcript_pages"].append(deepcopy(draft["transcript_pages"][0]))
    save_project_draft_data(project, draft)
    with pytest.raises(APIValidationError): current_render_revision(project.pk, owner)
    draft["transcript_pages"].pop(); draft["transcript_pages"][0]["order"] = True
    save_project_draft_data(project, draft)
    with pytest.raises(DesignError): current_render_revision(project.pk, owner)


@pytest.mark.django_db(transaction=True)
def test_freeze_and_selection_restore_serialize_on_postgres(lesson):
    if connection.vendor != "postgresql": pytest.skip("PostgreSQL row-lock assertion in CI")
    owner, project, page, _ = lesson
    candidate = select(lesson); revision = current_render_revision(project.pk, owner)
    barrier = Barrier(2)
    def perform(freeze_it):
        close_old_connections()
        try:
            user = User.objects.get(pk=owner.pk); barrier.wait(timeout=5)
            if freeze_it:
                try: return str(freeze_render_snapshot(project.pk, user, revision).pk)
                except RenderSnapshotConflict: return "conflict"
            source = build_studio_visual_source(Project.objects.get(pk=project.pk), page.page_key)
            decide_visual_draft(project.pk, user, {"page_key": page.page_key, "action": "restore",
                "expected_source_hash": source.source_hash, "expected_version": 1})
            return "restored"
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(perform, (True, False)))
    assert results[1] == "restored"
    if results[0] != "conflict":
        row, frozen = read_render_snapshot(project.pk, owner, results[0])
        assert frozen.to_dict()["scenes"][0]["design"]["candidate_id"] == str(candidate.pk)
        assert row.revision_hash != current_render_revision(project.pk, owner)


def test_confirmed_model_evidence_is_frozen_independently_of_later_live_rows(lesson):
    from core.models import VisualEvidenceProposal, VisualEvidenceReview
    from core.visual_design.grounding import VisualProposal, PROPOSAL_VERSION
    from core.visual_evidence import review_proposal
    owner, project, page, _ = lesson
    page.original_text = "Process ideas\n- Collect sample\n- Record value\n- Check result"; page.save()
    source = build_studio_visual_source(project, page.page_key)
    labels = [line[2:] for line in page.original_text.splitlines()[1:]]
    proposal = VisualProposal({"schema_version": PROPOSAL_VERSION, "strategy": "flowchart", "rationale": "Teacher reviews proposed order.",
        "evidence": {"kind": "process", "data": {"steps": [{"id": f"s{i}", "label": label} for i,label in enumerate(labels)],
                    "order": [f"s{i}" for i in range(len(labels))], "relationship": "sequence"}},
        "references": [{"start": 0, "end": len(page.original_text), "quote": page.original_text}]})
    row = VisualEvidenceProposal.objects.create(project=project, owner=owner, page_key=page.page_key,
        source_hash=source.source_hash, source_revision=source.source_revision, proposal_hash=proposal.content_hash, payload=proposal.to_dict())
    result = review_proposal(project.pk, owner, row.pk, "confirm", source.source_hash)
    decide_visual_draft(project.pk, owner, {"page_key": page.page_key, "action": "select", "candidate_id": result["suggestion"]["id"],
        "expected_source_hash": source.source_hash, "expected_version": 0})
    snapshot = freeze(lesson)
    _, frozen = read_render_snapshot(project.pk, owner, snapshot.pk)
    assert frozen.to_dict()["scenes"][0]["design"]["confirmation"]["proposal_hash"] == proposal.content_hash
    # Simulated database tamper cannot retroactively rewrite a previously valid
    # immutable approval captured in A. It prevents creating any new snapshot.
    VisualEvidenceReview.objects.filter(proposal=row).update(action="reject")
    assert read_render_snapshot(project.pk, owner, snapshot.pk)[1].content_hash == snapshot.snapshot_hash
    with pytest.raises(VisualDraftConflict): current_render_revision(project.pk, owner)


def test_selection_restore_and_voice_edit_create_new_revisions_without_relabeling_A(lesson):
    candidate = select(lesson); row = freeze(lesson)
    owner, project, page, _ = lesson
    old_json = read_render_snapshot(project.pk, owner, row.pk)[1].to_json()
    source = build_studio_visual_source(project, page.page_key)
    decide_visual_draft(project.pk, owner, {"page_key": page.page_key, "action": "restore",
        "expected_source_hash": source.source_hash, "expected_version": 1})
    restored = current_render_revision(project.pk, owner)
    assert restored != row.revision_hash
    VoiceProfile.objects.filter(user=owner).update(voice_id="different-voice")
    assert current_render_revision(project.pk, owner) != restored
    assert read_render_snapshot(project.pk, owner, row.pk)[1].to_json() == old_json


def test_racing_source_change_during_capture_rolls_back_snapshot(lesson):
    from core import visual_render_snapshots as service
    select(lesson)
    expected = current_render_revision(lesson[1].pk, lesson[0])
    original = service._capture
    calls = []
    def capture(project, owner, **kwargs):
        result = original(project, owner, **kwargs)
        calls.append(kwargs.get("pin"))
        if len(calls) == 1:
            project.title = "Concurrent ordinary lesson edit"; project.save(update_fields=["title"])
        return result
    with patch.object(service, "_capture", side_effect=capture):
        with pytest.raises(RenderSnapshotConflict): freeze_render_snapshot(lesson[1].pk, lesson[0], expected)
    assert not VisualRenderSnapshot.objects.exists()
    lesson[1].refresh_from_db(); assert lesson[1].title == "Exact frozen lesson"


def test_private_store_cannot_be_configured_inside_public_or_escape_via_symlink(lesson, settings, tmp_path):
    settings.VISUAL_RENDER_PRIVATE_ROOT = str(__import__('pathlib').Path(settings.STORAGE_ROOT)/"bad-private")
    with pytest.raises(DesignError): PrivateRenderAssets(lesson[1].pk)
    settings.VISUAL_RENDER_PRIVATE_ROOT = str(tmp_path/"private-safe")
    store = PrivateRenderAssets(lesson[1].pk)
    store.root.mkdir(parents=True)
    store.project_root.symlink_to(tmp_path/"outside", target_is_directory=True)
    with pytest.raises(DesignError): PrivateRenderAssets(lesson[1].pk)


def test_unsupported_scene_and_payload_budget_fail_before_any_snapshot_creation(lesson):
    lesson[2].editor_document = {"scene": {"background_mode": "arbitrary_html"}}; lesson[2].save()
    with pytest.raises(DesignError): current_render_revision(lesson[1].pk, lesson[0])
    lesson[2].editor_document = {}; lesson[2].original_text = "x"*100001; lesson[2].save()
    with pytest.raises(DesignError): current_render_revision(lesson[1].pk, lesson[0])
    assert VisualRenderSnapshot.objects.count() == 0


def test_deduplicated_capture_rejects_corrupted_existing_snapshot(lesson):
    select(lesson); row = freeze(lesson)
    data = deepcopy(row.payload); data["scenes"][0]["design"]["plan_hash"] = "sha256:"+"0"*64
    VisualRenderSnapshot.objects.filter(pk=row.pk).update(payload=data)
    with pytest.raises(RenderSnapshotConflict): freeze(lesson)


def test_unconfigured_private_root_fails_instead_of_ephemeral_or_public_storage(lesson, settings, monkeypatch):
    del settings.VISUAL_RENDER_PRIVATE_ROOT
    monkeypatch.delenv("VISUAL_RENDER_PRIVATE_ROOT", raising=False)
    with pytest.raises(DesignError, match="Configure an absolute persistent"):
        PrivateRenderAssets(lesson[1].pk)
