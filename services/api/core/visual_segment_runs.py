"""Private segment control plane. No task dispatch, output acceptance or rendering.

Producer dependencies are deliberately unconfigured in production. Tests replace
private dependency functions with independently anchored engineering attestations;
there is no environment switch, request payload or public API for that injection.
Lock order: owner -> Project -> pages -> selections -> voice/profile -> proposal /
review -> Jobs -> Runs. Native inspection and hashing happen before row locks.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import json
import os
import uuid

from django.contrib.auth.models import User
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException

from core.models import (Project, TranscriptPage, VoiceProfile, UserProfile, Job,
    VisualDraftSelection, VisualDesignSuggestion, VisualEvidenceProposal, VisualEvidenceReview,
    VisualEvidenceRevocation, VisualRenderSnapshot, VisualSegmentRun,
    PRIVATE_SEGMENT_JOB, _SEGMENT_CONTROL_WRITE)
from core.visual_design.schemas import DesignError, require, content_hash, canonical_json, digest
from core.visual_design.render_snapshot import FrozenRenderSnapshot
from core.visual_design.segment_contracts import (SegmentAudioProof, SegmentRequest, REQUEST_VERSION,
    RECIPE_OPTIONS, LIMITS, CUES_VERSION, describe_scene, snapshot_page, page_identity, _uuid, _string, _integer)
from core.visual_design.segment_dependencies import dependencies_from_verified_audio, MEDIA_VALIDATOR_VERSION
from core.visual_render_snapshots import read_render_snapshot, _capture
from core.visual_render_assets import PrivateRenderAssets
from core.visual_segment_artifacts import (PrivateSegmentMedia, pin_verified_narration,
    _descend, _open_media, _digest_fd, _stat_identity)
from core.storage_adapter import FilesystemStorageAdapter

MAX_PENDING_PROJECT = 4
MAX_PENDING_OWNER = 8
MAX_LEASE_SECONDS = 300


class SegmentRunError(DesignError):
    """Machine-readable failure; never a request to synthesize or publish."""
    status_code = 409


@dataclass(frozen=True)
class ControlDecision:
    allowed: bool
    reason_code: str
    execution_enabled: bool = False


@dataclass(frozen=True)
class AttemptClaim:
    run_id: uuid.UUID
    execution_hash: str
    attempt_generation: int
    claim_token: uuid.UUID
    lease_expires_at: object
    execution_enabled: bool = False

    def handoff(self):
        return {"run_id": str(self.run_id), "execution_hash": self.execution_hash,
                "attempt_generation": self.attempt_generation}


def _now():
    return timezone.now()


def _producer_dependencies():
    """Future independently authenticated server integration. None today.

    The future dependency supplies trusted_source, proof_lookup(receipt UUID),
    verifier, runtime and supported_runtime_hashes. It must not accept user paths,
    trust digests or runtime settings. Tests monkeypatch this private seam only.
    """
    return None


def _dependencies():
    result = _producer_dependencies()
    if result is None:
        raise SegmentRunError("audio_provenance_unavailable", "Authenticated producer receipts are unavailable.")
    return result


def _proof_from_source(dependencies, receipt_id):
    """Strict production boundary; synthetic proofs are never enabled here."""
    _uuid(receipt_id)
    association = dependencies.trusted_source.lookup(receipt_id)
    require(association is not None and not association.engineering_only
            and dependencies.trusted_source.currently_authorized(association) is True,
            "audio_provenance_unavailable", "Current authenticated producer association required")
    proof = SegmentAudioProof(dependencies.proof_lookup(receipt_id), expected_hash=association.expected_proof_hash,
                              expected_producer_id=association.producer_id)
    return proof, association


def _pin_audio(request, frozen, row, owner, dependencies, store):
    # No production call can opt in to engineering/synthetic attestations.
    return pin_verified_narration(request.to_dict(), snapshot=frozen, snapshot_id=str(row.pk),
        expected_snapshot_hash=row.snapshot_hash, owner_id=owner.pk, project_id=row.project_id,
        store=store, trusted_source=dependencies.trusted_source, verifier=dependencies.verifier,
        supported_runtime_hashes=dependencies.supported_runtime_hashes)


def _request(frozen, row, page_key, proof, dependencies):
    page = snapshot_page(frozen, page_key)
    p = proof.to_dict()
    scene = describe_scene(frozen, page_key)
    # Initial control stage deliberately requires attested saved baseline cues.
    # No browser timing, allocator claim or latest transcript fallback is accepted.
    require(page["start_seconds"] is not None and page["chunk_timeline"],
            "captions_unavailable", "Frozen producer-associated subtitle cues required")
    cues = []
    for i, cue in enumerate(page["chunk_timeline"]):
        start = (cue["start"] - page["start_seconds"]) * 1000
        end = (cue["end"] - page["start_seconds"]) * 1000
        require(abs(start-round(start)) < .001 and abs(end-round(end)) < .001,
                "captions_unavailable", "Saved cues must have exact millisecond timing")
        cues.append({"chunk_index": i, "start_ms": round(start), "end_ms": round(end), "text": cue["text"]})
    identity = page_identity(page)
    # Hold is a server recipe constant, never mutable Project defaults.
    hold = 200
    subtitles = {"page": identity, "authored_chunks": deepcopy(page["subtitle_chunks"]), "cues": cues,
        "cues_hash": content_hash(CUES_VERSION, {"page": identity, "cues": cues}),
        "policy": "frozen_baseline.v1", "provenance": {"baseline_job_id": p["baseline"]["job_id"],
            "baseline_run_id": p["baseline"]["run_id"], "producer_receipt_id": p["receipt_id"], "allocator_version": None},
        "audio_duration_ms": p["audio"]["duration_ms"], "hold_duration_ms": hold,
        "target_duration_ms": p["audio"]["duration_ms"] + hold, "time_base": "milliseconds.v1",
        "frozen_absolute": {k: deepcopy(page[k]) for k in ("start_seconds", "end_seconds", "duration_seconds", "chunk_timeline")}}
    value = {"schema_version": REQUEST_VERSION,
        "snapshot": {"owner_id": str(row.owner_id), "project_id": str(row.project_id), "snapshot_id": str(row.pk),
            "snapshot_hash": row.snapshot_hash, "authored_revision_hash": row.revision_hash},
        "page": {**identity, "order": page["order"]}, "scene": scene,
        "narration": {**p["narration"], "audio_proof_hash": proof.content_hash}, "audio_mode": "reuse_verified_audio.v1",
        "audio_proof": p, "subtitles": subtitles,
        "recipe": {**RECIPE_OPTIONS, "runtime": deepcopy(dependencies.runtime),
            "fonts_hash": content_hash("visual-segment-fonts.v1", scene["fonts"]), "limits": deepcopy(LIMITS)},
        "authorization_policy": "live_authorization_revocation_required.v1"}
    return SegmentRequest(value, snapshot=frozen, snapshot_id=str(row.pk), expected_snapshot_hash=row.snapshot_hash,
                          audio_proof=proof, supported_runtime_hashes=dependencies.supported_runtime_hashes)


def _database_stamp(project_id, owner_id):
    """Conservative bounded saved-state identity, no filesystem/native work.

    This closes the preflight/lock gap without interpreting the v1 snapshot again
    from a new revision. Private Runs/Jobs are intentionally not source inputs.
    """
    selections = list(VisualDraftSelection.objects.filter(project_id=project_id).order_by("pk").values()[:1025])
    require(len(selections) <= 1024, "source_limit", "Selection budget exceeded")
    candidate_ids = {row["candidate_id"] for row in selections if row["state"] == "selected" and row["candidate_id"]}
    candidates = list(VisualDesignSuggestion.objects.filter(pk__in=candidate_ids).order_by("pk").values())
    proposal_ids = {row["proposal_id"] for row in candidates if row["proposal_id"]}
    groups = [list(Project.objects.filter(pk=project_id).values()), selections, candidates]
    for model, query in ((TranscriptPage, {"project_id": project_id}),
            (VoiceProfile, {"user_id": owner_id}), (UserProfile, {"user_id": owner_id}),
            (VisualEvidenceProposal, {"pk__in": proposal_ids}),
            (VisualEvidenceReview, {"proposal_id__in": proposal_ids})):
        rows = list(model.objects.filter(**query).order_by("pk").values()[:1025])
        require(len(rows) <= 1024, "source_limit", "Saved control-source budget exceeded")
        groups.append(rows)
    encoded = json.dumps(groups, cls=DjangoJSONEncoder)
    require(len(encoded.encode("utf-8")) <= 8 * 1024 * 1024, "source_limit", "Saved-state byte budget exceeded")
    return content_hash("visual-segment-saved-state.v1", json.loads(encoded))


class _InputGuard:
    """Hash outside locks; hold FDs and recheck inode/stat/reachability inside.

    Same-UID/administrator filesystem tampering is not a database transaction.
    These checks detect replacement/mutation during reservation without scanning
    media under row locks. Later media consumption must verify hashes again.
    """
    def __init__(self, owner_id, project_id, pins):
        self.media = PrivateSegmentMedia(owner_id, project_id)
        self.pins = pins
        self.opened = []
        self.stack = ExitStack()

    def __enter__(self):
        total = 0
        try:
            locations = []
            for pin in self.pins["media"].values():
                components, name = self.media._location(pin)
                locations.append((components, name, pin["asset"]))
            for ref in self.pins["visual"]:
                locations.append(((str(self.media.project_id),), ref["sha256"][7:]+"."+ref["extension"], ref))
            for components, name, ref in locations:
                total += ref["size"]
                require(total <= LIMITS["max_attempt_bytes"], "asset_limit", "Input budget exceeded")
                directory = self.stack.enter_context(_descend(self.media.root, components, create=False))
                fd = _open_media(directory, name)
                self.stack.callback(os.close, fd)
                identity = _stat_identity(_digest_fd(fd, ref))
                self.opened.append((components, name, fd, identity))
            return self
        except BaseException:
            self.stack.close()
            raise

    def check(self):
        for components, name, fd, expected in self.opened:
            require(_stat_identity(os.fstat(fd)) == expected, "artifact_integrity", "Pinned input changed during control operation")
            with _descend(self.media.root, components, create=False) as directory:
                fresh = _open_media(directory, name)
                try:
                    require(_stat_identity(os.fstat(fresh)) == expected, "artifact_integrity", "Private input replaced or removed")
                finally:
                    os.close(fresh)

    def __exit__(self, *exc):
        self.stack.close()


@contextmanager
def _source_files(frozen):
    """Observe saved source assets on both sides of the short DB transaction."""
    storage = FilesystemStorageAdapter()
    observed = []
    for scene in frozen.to_dict()["scenes"]:
        for key in ("original_background_path", "custom_background_path", "source_background_path"):
            raw = scene["original"]["scene"].get(key)
            if raw:
                path = storage.resolve_path(raw)
                observed.append((raw, path, _stat_identity(path.stat())))
    def check():
        for raw, path, expected in observed:
            require(storage.resolve_path(raw) == path and _stat_identity(path.stat()) == expected, "revision_conflict", "Source asset changed during reservation")
    check()
    yield check


def _pins(frozen, proof, store):
    """Derive every expected input reference from immutable contracts, not rows."""
    attested = proof.to_dict()
    media = {"narration": store.reference(attested["audio"]["asset"], "narration")}
    if attested["processing"]["input_asset"]["extension"] == "wav":
        media["source_wav"] = store.reference(attested["processing"]["input_asset"], "source_wav")
    sample = attested["synthesis"]["dependencies"]["sample"]
    if sample is not None:
        media["voice_sample"] = store.reference(sample, "voice_sample")
    raw = frozen.to_dict()
    refs = []
    for scene in raw["scenes"]:
        refs.extend(scene["original"]["assets"].values())
        if scene["design"]:
            refs.append(scene["design"]["asset"])
    refs.extend(raw["fonts"].values())
    unique = {(ref["sha256"], ref["extension"]): ref for ref in refs}
    return {"media": media, "visual": list(unique.values())}


def _confirmation(review):
    return {"proposal_id": str(review.proposal_id), "proposal_hash": review.proposal_hash,
        "owner_id": str(review.owner_id), "action": review.action, "source_hash": review.source_hash,
        "created_at": review.created_at.isoformat()}


def _approval(project, owner, request):
    design = request.to_dict()["scene"]["design"]
    if design is None:
        return None, None, None
    candidate = get_object_or_404(VisualDesignSuggestion, pk=design["candidate_id"], project=project, owner=owner,
                                page_key=request.to_dict()["page"]["page_key"])
    require(candidate.plan_hash == design["plan_hash"] and candidate.preview_hash == design["preview_hash"],
            "candidate_integrity", "Candidate identity changed")
    if design["proposal_id"] is None:
        require(candidate.proposal_id is None, "approval", "Unexpected inferred candidate")
        return candidate, None, None
    proposal = get_object_or_404(VisualEvidenceProposal, pk=design["proposal_id"], project=project, owner=owner)
    review = get_object_or_404(VisualEvidenceReview, proposal=proposal, owner=owner, action="confirm")
    require(candidate.proposal_id == proposal.pk and proposal.proposal_hash == review.proposal_hash == design["proposal_hash"]
            and candidate.source_hash == review.source_hash == proposal.source_hash
            and content_hash("visual-segment-confirmation.v1", _confirmation(review)) == design["confirmation_hash"],
            "approval", "Teacher confirmation differs from frozen approval")
    require(not VisualEvidenceRevocation.objects.filter(review=review).exists(), "evidence_revoked", "Teacher confirmation permanently revoked")
    return candidate, proposal, review


def _producer_current(dependencies, proof, association):
    current = dependencies.trusted_source.lookup(proof.to_dict()["receipt_id"])
    require(current == association and dependencies.trusted_source.currently_authorized(current) is True,
            "audio_provenance_unavailable", "Producer authorization changed during operation")


@contextmanager
def _locked_project(project_id, owner):
    require(getattr(owner, "is_authenticated", False) is True, "owner", "Authenticated owner required")
    with transaction.atomic():
        # The owner lock serializes cross-project quotas as well as project locks.
        get_object_or_404(User.objects.select_for_update(no_key=True), pk=owner.pk, is_active=True)
        project = get_object_or_404(Project.objects.select_for_update(), pk=project_id, user=owner)
        for model, query in ((TranscriptPage, {"project": project}), (VisualDraftSelection, {"project": project}),
                (VoiceProfile, {"user": owner}), (UserProfile, {"user": owner}),
                (VisualEvidenceProposal, {"project": project}), (VisualEvidenceReview, {"proposal__project": project})):
            # No select_related: PostgreSQL cannot lock nullable outer joins.
            ids = list(model.objects.select_for_update().filter(**query).order_by("pk").values_list("pk", flat=True)[:1025])
            require(len(ids) <= 1024, "source_limit", "Locked source/control row budget exceeded")
        yield project


def _locked_run(project, owner, run_id):
    reference = get_object_or_404(VisualSegmentRun, pk=run_id, project=project, owner=owner)
    job = get_object_or_404(Job.objects.select_for_update(), pk=reference.job_id)
    run = get_object_or_404(VisualSegmentRun.objects.select_for_update(), pk=run_id, project=project, owner=owner, job=job)
    _stored_identity(run, job)
    return run, job


def _stored_identity(run, job):
    value = run.request_payload
    require(run.execution_hash == content_hash(REQUEST_VERSION, value), "run_integrity", "Immutable Run request changed")
    frozen = FrozenRenderSnapshot(run.snapshot.payload)
    expected = {"owner_id": str(run.owner_id), "project_id": str(run.project_id), "snapshot_id": str(run.snapshot_id),
        "snapshot_hash": run.snapshot_hash, "authored_revision_hash": run.revision_hash}
    require(value["snapshot"] == expected and run.snapshot.snapshot_hash == frozen.content_hash == run.snapshot_hash
            and run.snapshot.revision_hash == run.revision_hash and run.snapshot.owner_id == run.owner_id
            and run.snapshot.project_id == run.project_id and value["page"]["page_key"] == run.page_key
            and job.pk == run.job_id and job.project_id == run.project_id and job.job_type == PRIVATE_SEGMENT_JOB
            and not job.result_url and not job.srt_url and not job.celery_task_id,
            "run_integrity", "Private Run/snapshot/Job binding changed")
    design = value["scene"]["design"]
    require((str(run.candidate_id) if run.candidate_id else None) == (design["candidate_id"] if design else None)
            and (str(run.proposal_id) if run.proposal_id else None) == (design["proposal_id"] if design else None),
            "run_integrity", "Run candidate/proposal reference changed")
    if run.confirmation_id:
        require(run.confirmation.proposal_id == run.proposal_id, "run_integrity", "Run confirmation reference changed")
    else:
        require(run.proposal_id is None, "run_integrity", "Missing Run confirmation")


def _terminal(run, job):
    if run.confirmation_id and VisualEvidenceRevocation.objects.filter(review_id=run.confirmation_id).exists():
        return "evidence_revoked"
    if run.cancelled_at:
        return "run_cancelled"
    if run.terminal_failure_reason or job.status == "failed":
        return "terminal_failure"
    if job.status == "done" or run.completed_receipt is not None:
        return "unsupported_completion"
    return None


def _write_run(run, **values):
    VisualSegmentRun.objects.filter(pk=run.pk)._control_update(_SEGMENT_CONTROL_WRITE, **values, updated_at=_now())
    for key, value in values.items():
        setattr(run, key, value)


def _write_job(job, status, reason=""):
    Job.objects.filter(pk=job.pk, job_type=PRIVATE_SEGMENT_JOB)._control_update(_SEGMENT_CONTROL_WRITE,
        status=status, progress=0, error_message=reason, updated_at=_now())
    job.status = status


def _clear_claim(run, **values):
    _write_run(run, attempt_generation=run.attempt_generation + 1, claim_token=None, lease_owner="",
               lease_expires_at=None, **values)


def _current_preflight(project, owner, frozen):
    before = _database_stamp(project.pk, owner.pk)
    project.refresh_from_db()
    try:
        captured = _capture(project, owner, pin=False)
    except (APIException, DesignError) as exc:
        raise SegmentRunError("revision_conflict", "Saved source or selection is stale/incompatible.") from exc
    require(captured.content_hash == frozen.content_hash, "revision_conflict", "Snapshot is no longer the exact saved revision")
    require(before == _database_stamp(project.pk, owner.pk), "revision_conflict", "Saved state changed during preflight")
    return before


def reserve_visual_segment(project_id, owner, *, snapshot_id, expected_snapshot_hash, expected_revision_hash,
                           page_key, producer_receipt_id):
    """Construct from saved server records. Never accepts request JSON or SVG."""
    project = get_object_or_404(Project, pk=project_id, user=owner, user__is_active=True)
    dependencies = _dependencies()
    row, frozen = read_render_snapshot(project.pk, owner, snapshot_id)
    require(row.snapshot_hash == expected_snapshot_hash and row.revision_hash == expected_revision_hash,
            "revision_conflict", "Expected immutable revision mismatch")
    proof, association = _proof_from_source(dependencies, producer_receipt_id)
    request = _request(frozen, row, page_key, proof, dependencies)
    store = PrivateSegmentMedia(owner.pk, project.pk)
    eligibility = _pin_audio(request, frozen, row, owner, dependencies, store)
    require(eligibility.eligible and eligibility.execution_hash == request.execution_hash
            and all(getattr(eligibility, name) for name in ("proof_validated", "producer_authenticated", "bytes_verified", "media_verified", "pin_verified")),
            eligibility.reason_code, "Verified private audio reuse is unavailable")
    pins = _pins(frozen, proof, store)
    require(canonical_json(pins["media"]) == canonical_json({"narration": eligibility.pin, **eligibility.supporting_pins}),
            "run_integrity", "Eligibility pins differ from immutable producer inputs")
    with _InputGuard(owner.pk, project.pk, pins) as guard, _source_files(frozen) as source_check:
        stamp = _current_preflight(project, owner, frozen)
        with _locked_project(project.pk, owner) as locked:
            require(stamp == _database_stamp(project.pk, owner.pk), "revision_conflict", "Saved revision changed before reservation")
            persisted = get_object_or_404(VisualRenderSnapshot, pk=row.pk, project=locked, owner=owner)
            require(FrozenRenderSnapshot(persisted.payload).content_hash == frozen.content_hash and persisted.snapshot_hash == row.snapshot_hash
                    and persisted.revision_hash == row.revision_hash, "snapshot_integrity", "Snapshot changed during reservation")
            candidate, proposal, review = _approval(locked, owner, request)
            _producer_current(dependencies, proof, association)
            guard.check(); source_check()
            existing = VisualSegmentRun.objects.filter(project=locked, owner=owner, execution_hash=request.execution_hash).first()
            if existing:
                existing, job = _locked_run(locked, owner, existing.pk)
                require(canonical_json(existing.request_payload) == request.to_json() and existing.input_pins == pins,
                        "run_integrity", "Existing execution identity has conflicting content")
                terminal = _terminal(existing, job)
                require(terminal is None, terminal or "terminal_failure", "Historical terminal Run cannot be resurrected")
                return existing, True
            active = VisualSegmentRun.objects.filter(job__status__in=["pending", "running"])
            require(active.filter(project=locked).count() < MAX_PENDING_PROJECT and active.filter(owner=owner).count() < MAX_PENDING_OWNER,
                    "concurrency_limit", "Private pending reservation limit reached")
            job = Job(project=locked, job_type=PRIVATE_SEGMENT_JOB, status="pending")
            job.save(_control_token=_SEGMENT_CONTROL_WRITE)
            run = VisualSegmentRun(project=locked, owner=owner, snapshot=persisted,
                snapshot_hash=row.snapshot_hash, revision_hash=row.revision_hash, page_key=page_key,
                candidate=candidate, proposal=proposal, confirmation=review, request_payload=request.to_dict(), execution_hash=request.execution_hash,
                input_pins=pins, audio_verification={"native": json.loads(eligibility.verification_json), "dependencies": eligibility.dependencies}, job=job)
            run.save(_control_token=_SEGMENT_CONTROL_WRITE)
            require(stamp == _database_stamp(project.pk, owner.pk), "revision_conflict", "Saved revision changed before commit")
            guard.check(); source_check()
            return run, False


def _prepare_run(project_id, owner, run_id):
    run = get_object_or_404(VisualSegmentRun, pk=run_id, project_id=project_id, owner=owner, project__user=owner)
    _stored_identity(run, run.job)
    dependencies = _dependencies()
    row, frozen = read_render_snapshot(project_id, owner, run.snapshot_id)
    proof, association = _proof_from_source(dependencies, run.request_payload["audio_proof"]["receipt_id"])
    request = SegmentRequest(run.request_payload, snapshot=frozen, snapshot_id=str(row.pk), expected_snapshot_hash=row.snapshot_hash,
        audio_proof=proof, supported_runtime_hashes=dependencies.supported_runtime_hashes)
    require(request.execution_hash == run.execution_hash, "run_integrity", "Execution request changed")
    expected_pins = _pins(frozen, proof, PrivateSegmentMedia(owner.pk, project_id))
    require(canonical_json(run.input_pins) == canonical_json(expected_pins),
            "run_integrity", "Persisted pins differ from immutable request inputs")
    expected_dependencies = dependencies_from_verified_audio(proof, content_hash(MEDIA_VALIDATOR_VERSION, run.audio_verification["native"]))
    require(expected_dependencies.to_json() == canonical_json(run.audio_verification["dependencies"]),
            "run_integrity", "Stored synthesis dependencies differ from the authenticated proof")
    return run, frozen, dependencies, proof, association, request


@contextmanager
def _executable_run(project_id, owner, run_id):
    prepared, frozen, dependencies, proof, association, request = _prepare_run(project_id, owner, run_id)
    project = get_object_or_404(Project, pk=project_id, user=owner)
    with _InputGuard(owner.pk, project.pk, prepared.input_pins) as guard, _source_files(frozen) as source_check:
        stamp = _current_preflight(project, owner, frozen)
        with _locked_project(project.pk, owner) as locked:
            run, job = _locked_run(locked, owner, run_id)
            require(run.execution_hash == prepared.execution_hash and run.input_pins == prepared.input_pins
                    and stamp == _database_stamp(project.pk, owner.pk), "revision_conflict", "Control inputs changed before claim")
            terminal = _terminal(run, job)
            require(terminal is None, terminal or "terminal_failure", "Run is terminal or unauthorized")
            _approval(locked, owner, request)
            _producer_current(dependencies, proof, association)
            guard.check(); source_check()
            yield run, job
            guard.check(); source_check()


def check_run_executable(project_id, owner, run_id):
    try:
        with _executable_run(project_id, owner, run_id):
            return ControlDecision(True, "control_eligible")
    except DesignError as exc:
        return ControlDecision(False, exc.code)
    except OSError:
        return ControlDecision(False, "invalid_or_corrupt_artifact")


def claim_attempt(project_id, owner, run_id, *, execution_hash, expected_generation, lease_owner, lease_seconds=180):
    digest(execution_hash); _integer(expected_generation); _string(lease_owner, 128)
    _integer(lease_seconds, 30, MAX_LEASE_SECONDS)
    with _executable_run(project_id, owner, run_id) as (run, job):
        require(run.execution_hash == execution_hash and run.attempt_generation == expected_generation,
                "stale_attempt", "Execution or generation differs from persisted Run")
        now = _now()
        require(run.lease_expires_at is None or run.lease_expires_at <= now, "claim_busy", "Another claim has a live lease")
        active = VisualSegmentRun.objects.filter(owner=owner, lease_expires_at__gt=now, job__status="running").exclude(pk=run.pk)
        require(not active.exists(), "concurrency_limit", "Only one active private segment claim per owner")
        token = uuid.uuid4(); expiry = now + timedelta(seconds=lease_seconds)
        _write_run(run, attempt_generation=run.attempt_generation + 1, claim_token=token,
            lease_owner=lease_owner, lease_expires_at=expiry, pending_dispatch=False)
        _write_job(job, "running")
        return AttemptClaim(run.pk, run.execution_hash, run.attempt_generation, token, expiry)


def _claim_matches(run, execution_hash, generation, claim_token):
    digest(execution_hash); _integer(generation); _uuid(str(claim_token))
    require(run.execution_hash == execution_hash and run.attempt_generation == generation
            and run.claim_token == uuid.UUID(str(claim_token)), "stale_attempt", "Claim has been fenced")
    require(run.lease_expires_at is not None and run.lease_expires_at > _now(), "lease_expired", "Claim lease expired")


def release_claim(project_id, owner, run_id, *, execution_hash, generation, claim_token):
    with _locked_project(project_id, owner) as project:
        run, job = _locked_run(project, owner, run_id)
        terminal = _terminal(run, job)
        require(terminal is None, terminal or "terminal_failure", "Terminal Run cannot be released")
        _claim_matches(run, execution_hash, generation, claim_token)
        _clear_claim(run, pending_dispatch=True)
        _write_job(job, "pending")


def expire_claim(project_id, owner, run_id, *, expected_generation):
    _integer(expected_generation)
    with _locked_project(project_id, owner) as project:
        run, job = _locked_run(project, owner, run_id)
        terminal = _terminal(run, job)
        require(terminal is None, terminal or "terminal_failure", "Terminal Run cannot expire into a new attempt")
        require(run.attempt_generation == expected_generation, "stale_attempt", "Generation changed")
        require(run.lease_expires_at is not None and run.lease_expires_at <= _now(), "lease_active", "No expired lease to release")
        _clear_claim(run, pending_dispatch=True)
        _write_job(job, "pending")


def cancel_run(project_id, owner, run_id, *, reason):
    _string(reason, 160)
    with _locked_project(project_id, owner) as project:
        run, job = _locked_run(project, owner, run_id)
        if run.cancelled_at is None:
            _clear_claim(run, cancelled_at=_now(), cancellation_reason=reason, pending_dispatch=False)
            _write_job(job, "failed", "private_segment_cancelled")
        return run


def record_terminal_failure(project_id, owner, run_id, *, execution_hash, generation, claim_token, reason_code):
    require(reason_code in {"invalid_input", "invalid_or_corrupt_artifact", "unsupported_media", "validation_failed", "runtime_unavailable"},
            "failure_reason", "Classified terminal validation reason required")
    with _locked_project(project_id, owner) as project:
        run, job = _locked_run(project, owner, run_id)
        terminal = _terminal(run, job)
        require(terminal is None, terminal or "terminal_failure", "Run already terminal")
        _claim_matches(run, execution_hash, generation, claim_token)
        _clear_claim(run, terminal_failure_reason=reason_code, pending_dispatch=False)
        _write_job(job, "failed", reason_code)


def revoke_confirmation(project_id, owner, review_id, *, expected_confirmation_hash, reason):
    digest(expected_confirmation_hash); _string(reason, 160)
    with _locked_project(project_id, owner) as project:
        review = get_object_or_404(VisualEvidenceReview, pk=review_id, owner=owner, action="confirm", proposal__project=project)
        require(content_hash("visual-segment-confirmation.v1", _confirmation(review)) == expected_confirmation_hash,
                "approval", "Expected confirmation differs from immutable review")
        existing = VisualEvidenceRevocation.objects.filter(review=review).first()
        if existing:
            return existing, True
        proposal = review.proposal
        revocation = VisualEvidenceRevocation(project=project, owner=owner, review=review, proposal=proposal,
            source_hash=review.source_hash, source_revision=proposal.source_revision, proposal_hash=review.proposal_hash,
            confirmation_hash=expected_confirmation_hash, reason=reason)
        revocation.save(_control_token=_SEGMENT_CONTROL_WRITE)
        affected = VisualSegmentRun.objects.filter(project=project, owner=owner, confirmation=review)
        jobs = {job.pk: job for job in Job.objects.select_for_update().filter(visual_segment_run__in=affected).order_by("pk")}
        for run in affected.select_for_update().order_by("pk"):
            job = jobs[run.job_id]; _stored_identity(run, job)
            if run.cancelled_at is None:
                _clear_claim(run, cancelled_at=_now(), cancellation_reason="evidence_revoked", pending_dispatch=False)
                _write_job(job, "failed", "private_segment_evidence_revoked")
        return revocation, False


def prepare_receipt_acceptance(project_id, owner, run_id, *, execution_hash, generation, claim_token):
    """Validate control preconditions only; NEVER persist success/receipt.

    3B-4 must supply independent output probing and verified output pins before
    adding receipt persistence. No witness/receipt supplied by a caller can turn
    this gate into successful segment completion in 3B-3.
    """
    with _executable_run(project_id, owner, run_id) as (run, job):
        _claim_matches(run, execution_hash, generation, claim_token)
        require(job.status == "running", "run_integrity", "Claimed private Job required")
        return ControlDecision(False, "media_validation_unavailable")


def private_result_access_policy(project_id, owner, run_id):
    with _locked_project(project_id, owner) as project:
        run, job = _locked_run(project, owner, run_id)
        terminal = _terminal(run, job)
        if terminal:
            return ControlDecision(False, terminal)
        return ControlDecision(False, "completed_media_unavailable")


def read_private_run(project_id, owner, run_id):
    with _locked_project(project_id, owner) as project:
        run, job = _locked_run(project, owner, run_id)
        reason = _terminal(run, job)
        return {"id": str(run.pk), "job_id": job.pk, "purpose": PRIVATE_SEGMENT_JOB,
            "execution_hash": run.execution_hash, "snapshot_id": str(run.snapshot_id), "page_key": run.page_key,
            "snapshot_hash": run.snapshot_hash, "revision_hash": run.revision_hash, "status": job.status,
            "pending_dispatch": run.pending_dispatch, "attempt_generation": run.attempt_generation,
            "cancelled": run.cancelled_at is not None, "reason_code": reason,
            "result_available": False, "execution_enabled": False, "publishing_allowed": False}
