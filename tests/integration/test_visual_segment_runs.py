"""Persistent private control gates; native fixture input validation, no outputs.

Only this test module injects synthetic producer trust. Production has no source.
PostgreSQL races exercise actual row locks, not mocked transaction semantics.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections, transaction, IntegrityError
from django.http import Http404
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError as APIValidationError

from core import visual_segment_runs as runs
from core.models import (Project, TranscriptPage, VoiceProfile, Job, VisualSegmentRun, VisualEvidenceRevocation,
    VisualEvidenceProposal, VisualEvidenceReview, VisualDesignSuggestion, VisualDraftSelection,
    LessonSegment, RenderFollowUpIntent, Notification, PRIVATE_SEGMENT_JOB)
from core.visual_design.schemas import DesignError, canonical_json, content_hash
from core.visual_design.segment_contracts import SegmentAudioProof
from core.visual_segment_artifacts import pin_verified_narration, PrivateSegmentMedia
from core.visual_render_snapshots import current_render_revision, freeze_render_snapshot
from core.visual_design_source import build_studio_visual_source
from core.visual_suggestions import generate_suggestion
from core.visual_drafts import decide_visual_draft
from core.visual_evidence import review_proposal
from tests.integration.test_visual_segment_artifacts import FixtureTrust, native  # Existing real audio and independent anchors.

pytestmark = pytest.mark.django_db
FIXTURES = Path(__file__).parents[1]/"fixtures/visual_segments"
PROCESS = "Measurement process\n1. Collect sample\n2. Record value\n3. Check result"
TEXT = "Plants need light and water to grow."


def load(name):
    return json.loads((FIXTURES/name).read_text())


def _engineering_proof(dependencies, receipt_id):
    """TEST ONLY. Expected digest comes from committed anchors, not request data."""
    association = dependencies.trusted_source.lookup(receipt_id)
    if association is None or not association.engineering_only or dependencies.trusted_source.currently_authorized(association) is not True:
        raise DesignError("audio_provenance_unavailable", "Engineering producer association unavailable")
    proof = SegmentAudioProof(dependencies.proof_lookup(receipt_id), expected_hash=association.expected_proof_hash,
        expected_producer_id=association.producer_id, allow_synthetic=True)
    return proof, association


def _engineering_pin(request, frozen, row, owner, dependencies, store):
    return pin_verified_narration(request.to_dict(), snapshot=frozen, snapshot_id=str(row.pk),
        expected_snapshot_hash=row.snapshot_hash, owner_id=owner.pk, project_id=row.project_id,
        store=store, trusted_source=dependencies.trusted_source, verifier=dependencies.verifier,
        supported_runtime_hashes=dependencies.supported_runtime_hashes, allow_engineering=True)


@pytest.fixture
def lesson(tmp_path, settings, monkeypatch, native):
    settings.STORAGE_ROOT = str(tmp_path/"public")
    settings.MEDIA_ROOT = str(tmp_path/"media")
    settings.STATIC_ROOT = str(tmp_path/"static")
    settings.VISUAL_RENDER_PRIVATE_ROOT = str(tmp_path/"private")
    # Provisioned private mount, rather than relaxing Phase 3B-2 permissions.
    Path(settings.VISUAL_RENDER_PRIVATE_ROOT).mkdir(mode=0o700)
    source = tmp_path/"producer"/"801"/"701"/"synthetic-baseline-run"
    source.mkdir(mode=0o700, parents=True)
    for directory in [tmp_path/"producer", source.parents[1], source.parent, source]:
        directory.chmod(0o700)
    for name, original in [("speech.m4a", "narration.m4a"), ("original-speech.wav", "narration.wav")]:
        file = source/name; file.write_bytes((FIXTURES/original).read_bytes()); file.chmod(0o600)
    settings.VISUAL_SEGMENT_PRODUCER_ROOTS = {"engineering": str(tmp_path/"producer")}
    owner = User.objects.create_user(pk=801, username="segment-owner")
    project = Project.objects.create(pk=701, user=owner, title="Private engineering lesson", status="ready", is_published=True)
    VoiceProfile.objects.create(user=owner, provider="flite", voice_id="slt", language="en")
    pages = []
    for i in range(3):
        proof = load(f"audio_proof_{i}.json")
        pages.append(TranscriptPage.objects.create(project=project, **proof["page"], order=i, original_text=PROCESS,
            narration_text=TEXT, subtitle_chunks=[TEXT], start_seconds=i*3, end_seconds=i*3+2.335,
            duration_seconds=2.335, chunk_timeline=[{"start": i*3+.243, "end": i*3+2.180, "text": TEXT}]))
    baseline = Job.objects.create(project=project, job_type="video_export", status="done", result_url="existing.mp4", srt_url="existing.srt")
    trust = FixtureTrust()
    proofs = {load(f"audio_proof_{i}.json")["receipt_id"]: load(f"audio_proof_{i}.json") for i in range(3)}
    control = load("control_trust_anchors.json")
    for i, (receipt, proof) in enumerate(proofs.items()):
        # Explicit synthetic binding, independently expected by reviewed constants.
        # No original receipt/audio fixture is modified or certified for production.
        proof["synthesis"]["snapshot_tts_settings_hash"] = control["snapshot_tts_settings_hash"]
        trust.records[receipt] = replace(trust.records[receipt], expected_proof_hash=control["audio_proof_hashes"][i])
    dependencies = SimpleNamespace(trusted_source=trust, proof_lookup=lambda key: deepcopy(proofs[key]), verifier=native,
        runtime=load("request_0.json")["recipe"]["runtime"], supported_runtime_hashes={load("trust_anchors.json")["runtime_hash"]})
    monkeypatch.setattr(runs, "_producer_dependencies", lambda: dependencies)
    monkeypatch.setattr(runs, "_proof_from_source", _engineering_proof)
    monkeypatch.setattr(runs, "_pin_audio", _engineering_pin)
    result = SimpleNamespace(owner=owner, project=project, pages=pages, baseline=baseline, dependencies=dependencies,
        trust=trust, proofs=proofs, source=source, inferred_review=None)
    select_all(result)
    result.snapshot = freeze(result)
    return result


def select_all(lesson):
    for page in lesson.pages:
        source = build_studio_visual_source(lesson.project, page.page_key)
        candidate, _ = generate_suggestion(lesson.project, lesson.owner, source)
        assert candidate.status == "ready"
        old = VisualDraftSelection.objects.filter(project=lesson.project, page_key=page.page_key).first()
        decide_visual_draft(lesson.project.pk, lesson.owner, {"page_key": page.page_key, "action": "select",
            "candidate_id": candidate.pk, "expected_source_hash": source.source_hash, "expected_version": old.version if old else 0})


def freeze(lesson):
    return freeze_render_snapshot(lesson.project.pk, lesson.owner, current_render_revision(lesson.project.pk, lesson.owner))


def reserve(lesson, i=0, **overrides):
    row = lesson.snapshot
    return runs.reserve_visual_segment(lesson.project.pk, lesson.owner, **{
        "snapshot_id": row.pk, "expected_snapshot_hash": row.snapshot_hash, "expected_revision_hash": row.revision_hash,
        "page_key": lesson.pages[i].page_key, "producer_receipt_id": load(f"audio_proof_{i}.json")["receipt_id"], **overrides})


def claim(lesson, run, **overrides):
    run.refresh_from_db()
    return runs.claim_attempt(lesson.project.pk, lesson.owner, run.pk, **{"execution_hash": run.execution_hash,
        "expected_generation": run.attempt_generation, "lease_owner": "engineering-worker", "lease_seconds": 60, **overrides})


def acceptance(lesson, run, attempt):
    return runs.prepare_receipt_acceptance(lesson.project.pk, lesson.owner, run.pk, execution_hash=attempt.execution_hash,
        generation=attempt.attempt_generation, claim_token=attempt.claim_token)


def infer(lesson):
    page = lesson.pages[0]
    page.original_text = "Process ideas\n- Collect sample\n- Record value\n- Check result"; page.save()
    source = build_studio_visual_source(lesson.project, page.page_key)
    from core.visual_design.grounding import VisualProposal, PROPOSAL_VERSION
    labels = [line[2:] for line in page.original_text.splitlines()[1:]]
    proposal = VisualProposal({"schema_version": PROPOSAL_VERSION, "strategy": "flowchart", "rationale": "Teacher verifies proposed order.",
        "evidence": {"kind": "process", "data": {"steps": [{"id": f"s{i}", "label": label} for i, label in enumerate(labels)],
            "order": [f"s{i}" for i in range(3)], "relationship": "sequence"}},
        "references": [{"start": 0, "end": len(page.original_text), "quote": page.original_text}]})
    row = VisualEvidenceProposal.objects.create(project=lesson.project, owner=lesson.owner, page_key=page.page_key,
        source_hash=source.source_hash, source_revision=source.source_revision, proposal_hash=proposal.content_hash, payload=proposal.to_dict())
    result = review_proposal(lesson.project.pk, lesson.owner, row.pk, "confirm", source.source_hash)
    selection = VisualDraftSelection.objects.get(project=lesson.project, page_key=page.page_key)
    decide_visual_draft(lesson.project.pk, lesson.owner, {"page_key": page.page_key, "action": "select",
        "candidate_id": result["suggestion"]["id"], "expected_source_hash": source.source_hash, "expected_version": selection.version})
    lesson.snapshot = freeze(lesson)
    lesson.inferred_review = VisualEvidenceReview.objects.get(proposal=row)
    return lesson.inferred_review


def revoke(lesson, **overrides):
    review = lesson.inferred_review
    return runs.revoke_confirmation(lesson.project.pk, lesson.owner, review.pk, **{
        "expected_confirmation_hash": content_hash("visual-segment-confirmation.v1", runs._confirmation(review)),
        "reason": "Teacher withdraws inferred order", **overrides})


def protected(lesson):
    return [list(model.objects.order_by("pk").values()) for model in (Project, TranscriptPage, LessonSegment, RenderFollowUpIntent, Notification)] + [list(Job.objects.filter(pk=lesson.baseline.pk).values())]


def test_reservation_uses_real_pins_and_server_inputs_and_is_idempotent(lesson):
    before = protected(lesson)
    with patch("core.views._celery_app.send_task", side_effect=AssertionError("No dispatch")), patch("core.drafts.promote_project_draft", side_effect=AssertionError("No promotion")):
        run, reused = reserve(lesson)
        again, reused_again = reserve(lesson)
    assert not reused and reused_again and again.pk == run.pk
    assert VisualSegmentRun.objects.count() == Job.objects.filter(job_type=PRIVATE_SEGMENT_JOB).count() == 1
    assert run.job.status == "pending" and run.pending_dispatch and run.attempt_generation == 0
    assert run.claim_token is None and run.dispatch_task_id and not run.job.celery_task_id
    assert run.request_payload["page"] == {**load("audio_proof_0.json")["page"], "order": 0}
    assert run.execution_hash == content_hash("visual-segment-request.v1", run.request_payload)
    assert not run.job.result_url and not run.job.srt_url and run.completed_receipt is None
    store = PrivateSegmentMedia(lesson.owner.pk, lesson.project.pk)
    assert store.read(run.input_pins["media"]["narration"]) == (FIXTURES/"narration.m4a").read_bytes()
    assert protected(lesson) == before
    meta = runs.read_private_run(lesson.project.pk, lesson.owner, run.pk)
    assert not meta["execution_enabled"] and not meta["result_available"] and not meta["publishing_allowed"]
    assert not any(key in str(meta) for key in ["svg", "input_pins", "/private", "request_payload", "claim_token"])


def test_distinct_pages_remain_distinct_and_concurrency_limit_is_explicit(lesson):
    rows = [reserve(lesson, i)[0] for i in range(3)]
    assert len({row.pk for row in rows}) == 3 and len({row.execution_hash for row in rows}) == 3
    assert [row.request_payload["page"]["source_slide_index"] for row in rows] == [0, 2, 0]
    assert [row.request_payload["page"]["split_index"] for row in rows] == [1, 0, 0]
    first = claim(lesson, rows[0])
    with pytest.raises(DesignError, match="Only one active"):
        claim(lesson, rows[1])
    runs.release_claim(lesson.project.pk, lesson.owner, rows[0].pk, execution_hash=first.execution_hash,
        generation=first.attempt_generation, claim_token=first.claim_token)
    assert claim(lesson, rows[1]).attempt_generation == 1


@pytest.mark.parametrize("operation", ["read", "claim", "cancel", "access", "reserve"])
def test_owner_only_and_foreign_project_rejection(lesson, operation):
    run, _ = reserve(lesson); other = User.objects.create_user(username="foreign-segment-owner")
    foreign = Project.objects.create(user=other, title="Foreign")
    if operation == "read": call = lambda: runs.read_private_run(lesson.project.pk, other, run.pk)
    elif operation == "cancel": call = lambda: runs.cancel_run(lesson.project.pk, other, run.pk, reason="No")
    elif operation == "access": call = lambda: runs.private_result_access_policy(foreign.pk, other, run.pk)
    elif operation == "claim": call = lambda: runs.claim_attempt(foreign.pk, other, run.pk, execution_hash=run.execution_hash,
        expected_generation=0, lease_owner="foreign")
    else: call = lambda: runs.reserve_visual_segment(foreign.pk, other, snapshot_id=lesson.snapshot.pk,
        expected_snapshot_hash=lesson.snapshot.snapshot_hash, expected_revision_hash=lesson.snapshot.revision_hash,
        page_key=lesson.pages[0].page_key, producer_receipt_id=load("audio_proof_0.json")["receipt_id"])
    with pytest.raises(Http404): call()


@pytest.mark.parametrize("change", ["expected_snapshot_hash", "expected_revision_hash", "page_key", "receipt", "source", "split"])
def test_mismatched_revision_page_and_producer_scope_fail_without_partial_job(lesson, change):
    kwargs = {}
    if change in ("expected_snapshot_hash", "expected_revision_hash"): kwargs[change] = "sha256:"+"b"*64
    if change == "page_key": kwargs[change] = "missing-page"
    if change == "receipt": kwargs["producer_receipt_id"] = load("audio_proof_1.json")["receipt_id"]
    if change in ("source", "split"):
        field = "source_slide_index" if change == "source" else "split_index"
        setattr(lesson.pages[0], field, 5); lesson.pages[0].save()
    with pytest.raises((DesignError, Http404)):
        reserve(lesson, **kwargs)
    assert not VisualSegmentRun.objects.exists() and Job.objects.count() == 1


def test_normal_production_source_and_synthetic_proofs_fail_closed(lesson, monkeypatch):
    monkeypatch.setattr(runs, "_producer_dependencies", lambda: None)
    with pytest.raises(DesignError) as exc: reserve(lesson)
    assert exc.value.code == "audio_provenance_unavailable"
    # Restoring only the producer dependency cannot authenticate a synthetic proof.
    monkeypatch.setattr(runs, "_producer_dependencies", lambda: lesson.dependencies)
    with patch.object(runs, "_proof_from_source", _PRODUCTION_PROOF):
        with pytest.raises(DesignError): reserve(lesson)
    assert not VisualSegmentRun.objects.exists() and Job.objects.count() == 1


# Capture the strict function before any fixture monkeypatch; no test bypass in runtime.
_PRODUCTION_PROOF = runs._proof_from_source


@pytest.mark.parametrize("write", ["save", "update", "bulk_update", "bulk_create", "base_manager", "delete", "receipt", "job"])
def test_immutable_request_and_operational_fields_cannot_be_written_generically(lesson, write):
    run, _ = reserve(lesson)
    with pytest.raises(ValidationError):
        if write == "save": run.request_payload = {}; run.save()
        if write == "update": VisualSegmentRun.objects.filter(pk=run.pk).update(execution_hash="forged")
        if write == "bulk_update": VisualSegmentRun.objects.bulk_update([run], ["request_payload"])
        if write == "bulk_create": VisualSegmentRun.objects.bulk_create([run])
        if write == "base_manager": VisualSegmentRun._base_manager.filter(pk=run.pk).update(attempt_generation=99)
        if write == "delete": run.delete()
        if write == "receipt": VisualSegmentRun.objects.filter(pk=run.pk).update(completed_receipt={"success": True})
        if write == "job": Job.objects.filter(pk=run.job_id).update(status="done", result_url="private.mp4")
    run.refresh_from_db()
    assert run.execution_hash == content_hash("visual-segment-request.v1", run.request_payload)
    assert run.attempt_generation == 0 and run.completed_receipt is None


def _sql_update(model, pk, column, value):
    # Deliberate administrator-level corruption to test read-boundary detection.
    table = connection.ops.quote_name(model._meta.db_table)
    col = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET {col} = %s WHERE id = %s", [value, model._meta.pk.get_db_prep_value(pk, connection)])


@pytest.mark.parametrize("corruption", ["execution_hash", "purpose", "snapshot_hash"])
def test_sql_tampering_fails_validation_not_recertification(lesson, corruption):
    run, _ = reserve(lesson)
    if corruption == "purpose": _sql_update(Job, run.job_id, "job_type", "video_export")
    else: _sql_update(VisualSegmentRun, run.pk, corruption, "sha256:"+"f"*64)
    with pytest.raises(DesignError): runs.read_private_run(lesson.project.pk, lesson.owner, run.pk)


def test_corrupt_or_missing_pin_prevents_claim_without_audio_fallback(lesson):
    run, _ = reserve(lesson)
    store = PrivateSegmentMedia(lesson.owner.pk, lesson.project.pk)
    pin = run.input_pins["media"]["narration"]; components, name = store._location(pin)
    path = store.root.joinpath(*components, name)
    original = path.read_bytes(); path.write_bytes(b"x"*len(original))
    assert not runs.check_run_executable(lesson.project.pk, lesson.owner, run.pk).allowed
    path.unlink()
    assert runs.check_run_executable(lesson.project.pk, lesson.owner, run.pk).reason_code == "invalid_or_corrupt_artifact"
    run.refresh_from_db(); assert run.job.status == "pending"


def test_source_change_after_preflight_rolls_back_reservation(lesson, monkeypatch):
    original = runs._current_preflight
    def changed(project, owner, frozen):
        value = original(project, owner, frozen)
        page = lesson.pages[0]; page.narration_text = "Changed source after preflight"; page.save()
        return value
    monkeypatch.setattr(runs, "_current_preflight", changed)
    with pytest.raises(DesignError) as exc: reserve(lesson)
    assert exc.value.code == "revision_conflict"
    assert not VisualSegmentRun.objects.exists() and Job.objects.count() == 1


def test_mid_transaction_failure_rolls_back_both_run_and_job(lesson, monkeypatch):
    def fail(*args, **kwargs): raise DesignError("simulated_failure", "Engineering rollback")
    monkeypatch.setattr(VisualSegmentRun, "save", fail)
    with pytest.raises(DesignError): reserve(lesson)
    assert not VisualSegmentRun.objects.exists() and Job.objects.count() == 1


def test_later_source_edits_preserve_historical_request_but_block_new_claims(lesson):
    run, _ = reserve(lesson); original = deepcopy(run.request_payload)
    page = lesson.pages[0]; page.narration_text = "Later B"; page.save()
    with pytest.raises(DesignError): claim(lesson, run)
    with pytest.raises(DesignError): reserve(lesson)
    run.refresh_from_db(); assert run.request_payload == original
    assert runs.read_private_run(lesson.project.pk, lesson.owner, run.pk)["execution_hash"] == run.execution_hash


@pytest.mark.parametrize("when", ["before", "claimed"])
def test_cancellation_fences_claim_acceptance_and_access_without_normal_job_mutation(lesson, when):
    run, _ = reserve(lesson); before = protected(lesson)
    attempt = claim(lesson, run) if when == "claimed" else None
    cancelled = runs.cancel_run(lesson.project.pk, lesson.owner, run.pk, reason="Teacher cancels preview")
    repeated = runs.cancel_run(lesson.project.pk, lesson.owner, run.pk, reason="Retry")
    assert cancelled.attempt_generation == repeated.attempt_generation and repeated.cancellation_reason == "Teacher cancels preview"
    assert repeated.job.status == "failed" and repeated.cancelled_at and not repeated.pending_dispatch
    assert runs.private_result_access_policy(lesson.project.pk, lesson.owner, run.pk).reason_code == "run_cancelled"
    with pytest.raises(DesignError): claim(lesson, run)
    with pytest.raises(DesignError): reserve(lesson)
    if attempt:
        with pytest.raises(DesignError): acceptance(lesson, run, attempt)
    assert protected(lesson) == before


def test_expired_takeover_and_release_increment_generation_and_fence_old_attempt(lesson, monkeypatch):
    run, _ = reserve(lesson); first = claim(lesson, run)
    with pytest.raises(DesignError): claim(lesson, run)
    later = first.lease_expires_at + timedelta(seconds=1)
    monkeypatch.setattr(runs, "_now", lambda: later)
    with pytest.raises(DesignError) as exc: acceptance(lesson, run, first)
    assert exc.value.code == "lease_expired"
    second = claim(lesson, run)
    assert second.attempt_generation == first.attempt_generation + 1 and second.claim_token != first.claim_token
    with pytest.raises(DesignError) as exc: acceptance(lesson, run, first)
    assert exc.value.code == "stale_attempt"
    assert acceptance(lesson, run, second).reason_code == "media_validation_unavailable"
    runs.release_claim(lesson.project.pk, lesson.owner, run.pk, execution_hash=second.execution_hash,
        generation=second.attempt_generation, claim_token=second.claim_token)
    with pytest.raises(DesignError): acceptance(lesson, run, second)
    run.refresh_from_db(); assert run.pending_dispatch and run.job.status == "pending"
    third = claim(lesson, run)
    assert third.attempt_generation > second.attempt_generation
    assert set(third.handoff()) == {"run_id", "execution_hash", "attempt_generation"}


def test_explicit_expiry_is_cas_and_terminal_failure_never_resurrects(lesson, monkeypatch):
    run, _ = reserve(lesson); first = claim(lesson, run)
    with pytest.raises(DesignError): runs.expire_claim(lesson.project.pk, lesson.owner, run.pk, expected_generation=1)
    monkeypatch.setattr(runs, "_now", lambda: first.lease_expires_at + timedelta(seconds=1))
    runs.expire_claim(lesson.project.pk, lesson.owner, run.pk, expected_generation=1)
    with pytest.raises(DesignError): runs.expire_claim(lesson.project.pk, lesson.owner, run.pk, expected_generation=1)
    second = claim(lesson, run)
    runs.record_terminal_failure(lesson.project.pk, lesson.owner, run.pk, execution_hash=second.execution_hash,
        generation=second.attempt_generation, claim_token=second.claim_token, reason_code="validation_failed")
    with pytest.raises(DesignError): acceptance(lesson, run, second)
    with pytest.raises(DesignError): reserve(lesson)
    assert not runs.check_run_executable(lesson.project.pk, lesson.owner, run.pk).allowed


def test_revocation_is_permanent_append_only_and_fences_only_matching_evidence(lesson):
    review = infer(lesson); before_review = deepcopy(review.__dict__)
    inferred, _ = reserve(lesson); deterministic, _ = reserve(lesson, 1)
    attempt = claim(lesson, inferred)
    row, reused = revoke(lesson); again, reused_again = revoke(lesson)
    assert not reused and reused_again and row.pk == again.pk and VisualEvidenceRevocation.objects.count() == 1
    review.refresh_from_db(); assert review.action == "confirm" and review.proposal_hash == before_review["proposal_hash"]
    assert runs.private_result_access_policy(lesson.project.pk, lesson.owner, inferred.pk).reason_code == "evidence_revoked"
    with pytest.raises(DesignError): acceptance(lesson, inferred, attempt)
    with pytest.raises(DesignError): reserve(lesson)
    assert claim(lesson, deterministic).attempt_generation == 1
    for operation in (lambda: row.save(), lambda: row.delete(), lambda: VisualEvidenceRevocation.objects.filter(pk=row.pk).update(reason="Undo")):
        with pytest.raises(ValidationError): operation()
    # Existing immutable review retry cannot regain control authority.
    review_proposal(lesson.project.pk, lesson.owner, review.proposal_id, "confirm", review.source_hash)
    with pytest.raises(DesignError): reserve(lesson)
    assert lesson.snapshot.payload["scenes"][0]["design"]["confirmation"]["action"] == "confirm"


def test_revocation_before_reservation_and_owner_only_revocation(lesson):
    review = infer(lesson); revoke(lesson)
    with pytest.raises(DesignError) as exc: reserve(lesson)
    assert exc.value.code == "evidence_revoked"
    assert not VisualSegmentRun.objects.exists()
    stranger = User.objects.create_user(username="not-confirming-owner")
    with pytest.raises(Http404): runs.revoke_confirmation(lesson.project.pk, stranger, review.pk,
        expected_confirmation_hash=content_hash("visual-segment-confirmation.v1", runs._confirmation(review)), reason="Foreign")


def test_private_jobs_hidden_from_generic_status_listing_serialization_and_notifications(lesson):
    from core.serializers import JobSerializer
    from core.notifications import notify_render_completed, notify_render_failed
    from core.views import _active_video_export_job, _latest_completed_video_export_job
    run, _ = reserve(lesson)
    client = APIClient(); client.force_authenticate(lesson.owner)
    assert client.get(f"/api/v1/projects/{lesson.project.pk}/jobs/{run.job_id}/").status_code == 404
    assert client.get(f"/api/v1/jobs/{run.job_id}/").status_code == 404
    with pytest.raises(APIValidationError): JobSerializer(run.job).data
    assert _active_video_export_job(lesson.project) is None
    assert _latest_completed_video_export_job(lesson.project).pk == lesson.baseline.pk
    assert notify_render_completed(lesson.project, run.job) is None
    assert notify_render_failed(lesson.project, run.job) is None
    assert not Notification.objects.exists()
    from core.render_followup_intents import merge_render_followup_intent, claim_render_followup_intent_for_completed_job
    intent = merge_render_followup_intent(project=lesson.project, metadata={"active_job_id": run.job_id})
    assert claim_render_followup_intent_for_completed_job(project_id=lesson.project.pk, completed_job_id=run.job_id) is None
    intent.refresh_from_db(); assert intent.status == "pending"


def test_owner_loss_or_project_deletion_never_allows_stale_attempt(lesson):
    run, _ = reserve(lesson); attempt = claim(lesson, run)
    other = User.objects.create_user(username="new-owner")
    Project.objects.filter(pk=lesson.project.pk).update(user=other)
    with pytest.raises(Http404): acceptance(lesson, run, attempt)
    lesson.project.refresh_from_db(); lesson.project.delete()
    assert not VisualSegmentRun.objects.filter(pk=run.pk).exists()
    assert not Job.objects.filter(pk=run.job_id).exists()


def _postgres():
    if connection.vendor != "postgresql":
        pytest.skip("Real PostgreSQL row-lock race; hosted PostgreSQL gate runs this test")


def _race(actions):
    barrier = Barrier(len(actions))
    def perform(action):
        close_old_connections()
        try:
            barrier.wait(timeout=15)
            try: return action()
            except DesignError as exc: return exc.code
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(perform, actions))


@pytest.mark.django_db(transaction=True)
def test_postgres_simultaneous_identical_reservations_make_exactly_one_run_job(lesson):
    _postgres()
    result = _race([lambda: str(reserve(lesson)[0].pk) for _ in range(3)])
    assert len(set(result)) == 1, result
    assert VisualSegmentRun.objects.count() == Job.objects.filter(job_type=PRIVATE_SEGMENT_JOB).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("edit", ["source", "restore"])
def test_postgres_reservation_racing_saved_source_or_selection_never_mixes_revisions(lesson, edit):
    _postgres()
    def change():
        owner = User.objects.get(pk=lesson.owner.pk)
        with transaction.atomic():
            project = Project.objects.select_for_update().get(pk=lesson.project.pk)
            if edit == "source":
                page = TranscriptPage.objects.select_for_update().get(pk=lesson.pages[0].pk)
                page.narration_text = "Later source B"; page.save()
            else:
                source = build_studio_visual_source(project, lesson.pages[0].page_key)
                decide_visual_draft(project.pk, owner, {"page_key": lesson.pages[0].page_key, "action": "restore",
                    "expected_source_hash": source.source_hash, "expected_version": 1})
        return "edited"
    result = _race([lambda: str(reserve(lesson)[0].pk), change])
    assert result[1] == "edited"
    assert VisualSegmentRun.objects.count() <= 1
    if VisualSegmentRun.objects.exists():
        row = VisualSegmentRun.objects.get()
        assert row.snapshot_hash == lesson.snapshot.snapshot_hash
        with pytest.raises(DesignError): claim(lesson, row)
    else:
        assert result[0] in {"revision_conflict", "stale_source"}
        assert not Job.objects.filter(job_type=PRIVATE_SEGMENT_JOB).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", ["reserve", "claim", "completion", "revoke"])
def test_postgres_revocation_serializes_with_reservation_claim_and_completion_preconditions(lesson, action):
    _postgres(); infer(lesson)
    run = reserve(lesson)[0] if action != "reserve" else None
    attempt = claim(lesson, run) if action == "completion" else None
    if action == "reserve": competing = lambda: str(reserve(lesson)[0].pk)
    elif action == "claim": competing = lambda: claim(lesson, run).attempt_generation
    elif action == "completion": competing = lambda: acceptance(lesson, run, attempt).reason_code
    else: competing = lambda: revoke(lesson)[0].pk
    result = _race([competing, lambda: revoke(lesson)[0].pk])
    assert VisualEvidenceRevocation.objects.count() == 1, result
    for row in VisualSegmentRun.objects.all():
        row.refresh_from_db()
        assert row.cancelled_at and row.job.status == "failed" and row.completed_receipt is None
        assert runs.private_result_access_policy(lesson.project.pk, lesson.owner, row.pk).reason_code == "evidence_revoked"
        with pytest.raises(DesignError): claim(lesson, row)
    with pytest.raises(DesignError): reserve(lesson)


@pytest.mark.django_db(transaction=True)
def test_postgres_duplicate_claim_and_completion_cancellation_races_are_fenced(lesson):
    _postgres(); run, _ = reserve(lesson)
    result = _race([lambda: claim(lesson, run).attempt_generation for _ in range(2)])
    assert sum(isinstance(item, int) for item in result) == 1, result
    run.refresh_from_db()
    attempt = runs.AttemptClaim(run.pk, run.execution_hash, run.attempt_generation, run.claim_token, run.lease_expires_at)
    outcomes = _race([lambda: acceptance(lesson, run, attempt).reason_code,
        lambda: str(runs.cancel_run(lesson.project.pk, lesson.owner, run.pk, reason="Cancel during work").pk)])
    assert outcomes[0] in {"media_validation_unavailable", "run_cancelled"}
    run.refresh_from_db(); assert run.job.status == "failed" and run.completed_receipt is None
    with pytest.raises(DesignError): acceptance(lesson, run, attempt)


@pytest.mark.parametrize("settings_change", ["missing", "public"])
def test_private_storage_unavailable_never_creates_a_job(lesson, settings, settings_change):
    settings.VISUAL_RENDER_PRIVATE_ROOT = None if settings_change == "missing" else str(Path(settings.STORAGE_ROOT)/"private")
    with pytest.raises(DesignError): reserve(lesson)
    assert not VisualSegmentRun.objects.exists() and Job.objects.count() == 1


def test_unknown_payloads_and_unsafe_claim_inputs_cannot_be_used_as_a_control_api(lesson):
    with pytest.raises(TypeError): reserve(lesson, request_payload=load("request_0.json"))
    run, _ = reserve(lesson)
    for overrides in ({"expected_generation": True}, {"expected_generation": -1},
            {"lease_seconds": 0}, {"lease_seconds": 301}, {"execution_hash": "forged"}):
        with pytest.raises(DesignError): claim(lesson, run, **overrides)
    with pytest.raises(TypeError):
        runs.prepare_receipt_acceptance(lesson.project.pk, lesson.owner, run.pk, execution_hash=run.execution_hash,
            generation=0, claim_token=uuid.uuid4(), receipt={"success": True}, trusted=True)
    run.refresh_from_db(); assert run.job.status == "pending" and run.completed_receipt is None


def test_pending_quota_does_not_prevent_exact_idempotent_retry(lesson, monkeypatch):
    reserve(lesson)
    monkeypatch.setattr(runs, "MAX_PENDING_PROJECT", 1)
    assert reserve(lesson)[1]
    with pytest.raises(DesignError) as exc: reserve(lesson, 1)
    assert exc.value.code == "concurrency_limit" and VisualSegmentRun.objects.count() == 1


def test_ordinary_private_draft_is_isolated_from_all_control_operations(lesson):
    from core.drafts import build_draft_from_active_project, save_project_draft_data
    draft = build_draft_from_active_project(lesson.project)
    for row, page in zip(draft["transcript_pages"], lesson.pages):
        row.update({name: deepcopy(getattr(page, name)) for name in ("start_seconds", "end_seconds", "duration_seconds", "chunk_timeline")})
    save_project_draft_data(lesson.project, draft)
    lesson.project.refresh_from_db(); select_all(lesson); lesson.snapshot = freeze(lesson)
    before = protected(lesson)
    run, _ = reserve(lesson); attempt = claim(lesson, run)
    assert acceptance(lesson, run, attempt).reason_code == "media_validation_unavailable"
    runs.cancel_run(lesson.project.pk, lesson.owner, run.pk, reason="Private-only cancellation")
    assert protected(lesson) == before
    lesson.project.refresh_from_db(); assert lesson.project.draft_data


def test_private_job_cannot_be_created_or_repurposed_outside_atomic_reservation(lesson):
    for call in (
        lambda: Job.objects.create(project=lesson.project, job_type=PRIVATE_SEGMENT_JOB),
        lambda: Job.objects.bulk_create([Job(project=lesson.project, job_type=PRIVATE_SEGMENT_JOB)]),
        lambda: Job.objects.filter(pk=lesson.baseline.pk).update(job_type=PRIVATE_SEGMENT_JOB),
    ):
        with pytest.raises(ValidationError): call()
    run, _ = reserve(lesson); run.job.job_type = "video_export"
    with pytest.raises(ValidationError): run.job.save()
    with pytest.raises(ValidationError): Job.objects.bulk_update([run.job], ["job_type"])


def test_database_constraints_prevent_public_result_and_unverified_success(lesson):
    run, _ = reserve(lesson)
    for model, pk, field, value in ((VisualSegmentRun, run.pk, "completed_receipt_hash", "sha256:"+"c"*64),
            (Job, run.job_id, "result_url", "private-segment.mp4"), (Job, run.job_id, "status", "done")):
        with pytest.raises(IntegrityError), transaction.atomic(): _sql_update(model, pk, field, value)


def test_expired_or_wrong_token_cannot_record_terminal_failure(lesson, monkeypatch):
    run, _ = reserve(lesson); attempt = claim(lesson, run)
    def fail(token):
        runs.record_terminal_failure(lesson.project.pk, lesson.owner, run.pk, execution_hash=attempt.execution_hash,
            generation=attempt.attempt_generation, claim_token=token, reason_code="validation_failed")
    with pytest.raises(DesignError): fail(uuid.uuid4())
    monkeypatch.setattr(runs, "_now", lambda: attempt.lease_expires_at+timedelta(seconds=1))
    with pytest.raises(DesignError): fail(attempt.claim_token)
    run.refresh_from_db(); assert not run.terminal_failure_reason and run.job.status == "running"


def test_revocation_before_claim_and_forged_confirmation_denial(lesson):
    infer(lesson); run, _ = reserve(lesson)
    with pytest.raises(DesignError): revoke(lesson, expected_confirmation_hash="sha256:"+"a"*64)
    assert not VisualEvidenceRevocation.objects.exists()
    revoke(lesson)
    with pytest.raises(DesignError): claim(lesson, run)
    assert runs.private_result_access_policy(lesson.project.pk, lesson.owner, run.pk).reason_code == "evidence_revoked"
    assert runs.read_private_run(lesson.project.pk, lesson.owner, run.pk)["reason_code"] == "evidence_revoked"


def test_revoked_producer_cannot_authorize_an_existing_pin(lesson):
    run, _ = reserve(lesson); lesson.trust.authorized = False
    assert not runs.check_run_executable(lesson.project.pk, lesson.owner, run.pk).allowed
    assert run.job.status == "pending"


@pytest.mark.django_db(transaction=True)
def test_postgres_takeover_fences_an_earlier_attempt_without_process_local_locks(lesson, monkeypatch):
    _postgres(); run, _ = reserve(lesson); old = claim(lesson, run)
    monkeypatch.setattr(runs, "_now", lambda: old.lease_expires_at+timedelta(seconds=1))
    outcomes = _race([lambda: claim(lesson, run).attempt_generation,
        lambda: acceptance(lesson, run, old).reason_code])
    assert outcomes[0] == old.attempt_generation+1
    assert outcomes[1] in {"lease_expired", "stale_attempt"}
    run.refresh_from_db(); assert run.attempt_generation == old.attempt_generation+1 and run.completed_receipt is None


def test_revocation_cannot_be_removed_by_deleting_its_confirmation(lesson):
    from django.db.models.deletion import RestrictedError
    review = infer(lesson); revocation, _ = revoke(lesson)
    for delete in (lambda: review.delete(), lambda: review.proposal.delete()):
        with pytest.raises(RestrictedError): delete()
    assert VisualEvidenceRevocation.objects.get(pk=revocation.pk).review_id == review.pk
    # Account/project lifecycle cleanup is deliberately distinct from undoing a grant.
    lesson.project.delete()
    assert not VisualEvidenceRevocation.objects.filter(pk=revocation.pk).exists()


def test_public_stream_rejects_private_job_even_with_a_signed_token(lesson):
    from core import views
    run, _ = reserve(lesson)
    token = views.generate_media_token(run.job_id, "video")
    client = APIClient(); client.force_authenticate(lesson.owner)
    response = client.get(f"/api/v1/stream/{token}/")
    assert response.status_code in (403, 404) and "input_pins" not in str(response.content)


def test_valid_but_unattested_private_pin_cannot_replace_request_audio(lesson):
    import hashlib
    run, _ = reserve(lesson)
    store = PrivateSegmentMedia(lesson.owner.pk, lesson.project.pk)
    path = lesson.source/"unattested.m4a"; data = b"This is not attested narration."
    path.write_bytes(data); path.chmod(0o600)
    asset = {"sha256": "sha256:"+hashlib.sha256(data).hexdigest(), "size": len(data), "extension": "m4a"}
    with path.open("rb") as file:
        foreign_input = store.pin_from_fd(file.fileno(), asset, "narration")
    assert store.read(foreign_input) == data  # Byte integrity alone is not authorization.
    rewritten = deepcopy(run.input_pins); rewritten["media"]["narration"] = foreign_input
    _sql_update(VisualSegmentRun, run.pk, "input_pins", json.dumps(rewritten))
    result = runs.check_run_executable(lesson.project.pk, lesson.owner, run.pk)
    assert not result.allowed and result.reason_code == "run_integrity"


def test_persisted_synthesis_descriptor_must_fully_match_authenticated_proof(lesson):
    run, _ = reserve(lesson)
    rewritten = deepcopy(run.audio_verification)
    rewritten["dependencies"]["synthesis"]["dependencies"]["model_hash"] = "sha256:"+"a"*64
    _sql_update(VisualSegmentRun, run.pk, "audio_verification", json.dumps(rewritten))
    result = runs.check_run_executable(lesson.project.pk, lesson.owner, run.pk)
    assert not result.allowed and result.reason_code == "run_integrity"
