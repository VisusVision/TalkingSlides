"""P1-only ephemeral signers. No runtime enrollment or synthesis."""

from copy import deepcopy
import base64
import json
from pathlib import Path
import uuid
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from core.visual_design.producer_contracts import (
    SpeechSemantics,
    ProducerExecution,
    ProducerContext,
    OriginalObservation,
    OriginalReceipt,
    ConsumerCapture,
    PagePresentationBinding,
    AudioReuseAssociation,
    Issuer,
    PREFIX,
    verify_envelope,
    scoped_speech_hash,
    replay_decision,
)
from core.visual_design.schemas import canonical_json, content_hash
from core.visual_design.segment_contracts import text_hash

H = "sha256:" + "1" * 64
U = lambda: str(uuid.uuid4())


@pytest.fixture
def chain():
    # Existing engineering bytes used as contract test data only. No issuer/DB
    # record is installed; production control seam remains None.
    proof = json.loads(
        (
            Path(__file__).parent / "fixtures/visual_segments/audio_proof_0.json"
        ).read_text()
    )
    speech = {
        "schema_version": SpeechSemantics.domain,
        "protected_text": proof["narration"]["protected_text"],
        "spoken_text": proof["narration"]["spoken_text"],
        "language": "en",
        "chunks": [proof["narration"]["spoken_text"]],
        "pauses_ms": [],
        "gap_policy": "gap.v1",
        "provider": "flite",
        "voice_id": "slt",
        "controls": {"speed": "1", "pitch": "1", "gain_db": "0"},
        "dependencies": {**proof["synthesis"]["dependencies"], "segmentation_hash": H},
    }
    scope = {
        "owner_id": proof["owner_id"],
        "project_id": proof["project_id"],
        "page": proof["page"],
    }
    ex = ProducerExecution(
        {
            "schema_version": ProducerExecution.domain,
            "execution_id": U(),
            "scope": scope,
            "job_id": "7",
            "job_type": "video_export",
            "page_id": None,
            "source_hash": H,
            "manifest_hash": H,
            "order": 0,
            "speech": speech,
            "request": {
                "selected_provider": "flite",
                "selected_voice": "slt",
                "settings_hash": proof["synthesis"]["snapshot_tts_settings_hash"],
            },
        }
    )
    ctx = ProducerContext(
        {
            "schema_version": ProducerContext.domain,
            "execution": ex.to_dict(),
            "generation": 1,
            "nonce": U(),
            "window": {"issued_ms": 10, "expires_ms": 10000},
            "required_profile_hash": H,
        }
    )
    obs = OriginalObservation(
        {
            "schema_version": OriginalObservation.domain,
            "observation_id": U(),
            "context_hash": ctx.content_hash,
            "execution_id": ex.to_dict()["execution_id"],
            "generation": 1,
            "nonce": ctx.to_dict()["nonce"],
            "actual_speech": speech,
            "outcome": "complete",
            "coverage": [
                {
                    "index": 0,
                    "text_hash": text_hash(speech["spoken_text"]),
                    "status": "complete",
                }
            ],
            "outputs": {
                "mp3": {"extension": "mp3", "sha256": H, "size": 100},
                "wav": proof["processing"]["input_asset"],
            },
        },
        context=ctx,
    )
    proof["producer"]["attestation_kind"] = "producer_receipt.v1"
    proof["baseline"].update(
        job_id="7", run_id=ex.to_dict()["execution_id"], manifest_hash=H, page_order=0
    )
    receipt = OriginalReceipt(
        {
            "schema_version": OriginalReceipt.domain,
            "receipt_id": proof["receipt_id"],
            "context_hash": ctx.content_hash,
            "observation_hash": obs.content_hash,
            "proof": proof,
            "qualification": {
                "kind": "completed_full_render.v1",
                "job_id": "7",
                "manifest_hash": H,
                "result_hash": H,
            },
            "presentation_hash": H,
            "speech_hash": SpeechSemantics(speech).content_hash,
            "proof_hash": content_hash("visual-segment-audio-proof.v1", proof),
        },
        observation=obs,
        context=ctx,
        expected_proof_hash=content_hash("visual-segment-audio-proof.v1", proof),
        expected_producer_id=proof["producer"]["id"],
    )
    return ex, ctx, obs, receipt


def signed(context, purpose="publisher.context.v1"):
    key = Ed25519PrivateKey.generate()
    enrollment = U()
    env = {
        "schema_version": context.domain,
        "purpose": purpose,
        "environment": "isolated-test",
        "audience": "p1-test",
        "issuer_enrollment_id": enrollment,
        "key_id": "ephemeral",
        "payload": context.to_dict(),
    }
    env["signature"] = (
        base64.urlsafe_b64encode(key.sign(PREFIX + canonical_json(env).encode()))
        .decode()
        .rstrip("=")
    )
    issuer = Issuer(
        enrollment,
        "ephemeral",
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
        (purpose,),
        "isolated-test",
        "p1-test",
        0,
        10000,
        engineering_only=True,
    )
    return env, {(enrollment, "ephemeral"): issuer}


def verify(env, registry, **kw):
    return verify_envelope(
        canonical_json(env).encode(),
        registry=registry,
        environment="isolated-test",
        audience="p1-test",
        purpose="publisher.context.v1",
        now_ms=100,
        production=False,
        **kw
    )


def test_canonical_signature_and_registry(chain):
    env, reg = signed(chain[1])
    assert verify(env, reg)["payload"] == chain[1].to_dict()
    with pytest.raises(ValueError):
        verify(env, {})
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            registry=reg,
            environment="isolated-test",
            audience="p1-test",
            purpose="publisher.context.v1",
            now_ms=100,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "tts.original-observation.v1"),
        ("audience", "other"),
        ("environment", "production"),
        ("key_id", "https://key.invalid"),
        ("schema_version", "unknown"),
        ("signature", "AAAA"),
    ],
)
def test_forged_headers_rejected(chain, field, value):
    env, reg = signed(chain[1])
    env[field] = value
    with pytest.raises(ValueError):
        verify(env, reg)


@pytest.mark.parametrize(
    "encoded",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b"\xff",
        b"{} " * 400000,
        b"[" * 10000,
    ],
)
def test_json_limits_and_duplicates(encoded):
    with pytest.raises(ValueError):
        verify_envelope(
            encoded,
            registry={},
            environment="test",
            audience="test",
            purpose="publisher.context.v1",
            now_ms=100,
        )


def test_signature_covers_payload_and_noncanonical_wire(chain):
    env, reg = signed(chain[1])
    env["payload"]["generation"] = 2
    with pytest.raises(ValueError):
        verify(env, reg)
    env, reg = signed(chain[1])
    with pytest.raises(ValueError):
        verify_envelope(
            json.dumps(env).encode(),
            registry=reg,
            environment="isolated-test",
            audience="p1-test",
            purpose="publisher.context.v1",
            now_ms=100,
            production=False,
        )


def test_context_expiry_and_generation(chain):
    ex, ctx, obs, receipt = chain
    for k, v in [
        ("generation", 0),
        ("generation", True),
        ("generation", 4),
        ("nonce", "not-uuid"),
    ]:
        raw = ctx.to_dict()
        raw[k] = v
        with pytest.raises(ValueError):
            ProducerContext(raw)
    env, reg = signed(ctx)
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            registry=reg,
            environment="isolated-test",
            audience="p1-test",
            purpose="publisher.context.v1",
            now_ms=10000,
            production=False,
        )


def test_zero_split_and_presentation_are_separate(chain):
    ex = chain[0]
    before = SpeechSemantics(ex.to_dict()["speech"])
    for order, source, split in [(0, 0, 1), (1, 2, 0), (2, 0, 0)]:
        v = ex.to_dict()
        v["order"] = order
        v["scope"]["page"].update(source_slide_index=source, split_index=split)
        new = ProducerExecution(v)
        assert (
            SpeechSemantics(new.to_dict()["speech"]).content_hash == before.content_hash
        )
        assert new.to_dict()["scope"]["page"]["source_slide_index"] == source
    v = ex.to_dict()
    v["scope"]["page"]["source_slide_index"] = False
    with pytest.raises(ValueError):
        ProducerExecution(v)
    v = ex.to_dict()
    v["speech"]["subtitle_chunks"] = ["unrelated"]
    with pytest.raises(ValueError):
        ProducerExecution(v)


@pytest.mark.parametrize(
    "field",
    [
        "model_hash",
        "config_hash",
        "preparation_hash",
        "pronunciation_hash",
        "segmentation_hash",
    ],
)
def test_loaded_dependencies_change_speech(chain, field):
    speech = SpeechSemantics(chain[0].to_dict()["speech"])
    v = speech.to_dict()
    v["dependencies"][field] = "sha256:" + "2" * 64
    assert SpeechSemantics(v).content_hash != speech.content_hash


def test_plan_pauses_provider_and_coverage(chain):
    ex, ctx, obs, _ = chain
    v = ex.to_dict()["speech"]
    v["chunks"] = ["First.", "Second."]
    v["pauses_ms"] = [200]
    assert (
        SpeechSemantics(v).content_hash
        != SpeechSemantics(ex.to_dict()["speech"]).content_hash
    )
    v["provider"] = "cached"
    with pytest.raises(ValueError):
        SpeechSemantics(v)
    for status in ["failed", "filtered", "recovered"]:
        raw = obs.to_dict()
        raw["coverage"][0]["status"] = status
        with pytest.raises(ValueError):
            OriginalObservation(raw, context=ctx)
    raw = obs.to_dict()
    raw["nonce"] = U()
    with pytest.raises(ValueError):
        OriginalObservation(raw, context=ctx)
    assert replay_decision(obs.content_hash, obs) == "duplicate"
    with pytest.raises(ValueError):
        replay_decision(H, obs)


def presentation(chain, *, chunks=None):
    ex, ctx, obs, parent = chain
    proof = parent.to_dict()["proof"]
    speech = ex.to_dict()["speech"]
    parts = chunks or [speech["protected_text"]]
    window = proof["audio"]
    start = window["audible_start_ms"]
    end = window["audible_end_ms"]
    step = (end - start) // len(parts)
    return PagePresentationBinding(
        {
            "schema_version": PagePresentationBinding.domain,
            "use_id": U(),
            "scope": ex.to_dict()["scope"],
            "job_id": "8",
            "manifest_hash": H,
            "source_hash": H,
            "order": 3,
            "scene_hash": H,
            "audio": window["asset"],
            "window": {
                "duration_ms": window["duration_ms"],
                "start_ms": start,
                "end_ms": end,
            },
            "chunks": parts,
            "cues": [
                {
                    "index": i,
                    "text": t,
                    "start_ms": start + i * step,
                    "end_ms": end if i == len(parts) - 1 else start + (i + 1) * step,
                }
                for i, t in enumerate(parts)
            ],
            "caption_policy": "result_allocator.v1",
            "video_recipe_hash": H,
            "hold_ms": 200,
            "frozen_absolute": {"start_ms": None, "end_ms": None},
        }
    )


def test_reuse_is_new_use_not_synthesis(chain):
    ex, ctx, obs, parent = chain
    pr = presentation(chain)
    v = {
        k: ctx.to_dict()["execution"][k]
        for k in ("scope", "job_id", "manifest_hash", "source_hash", "speech")
    }
    v["job_id"] = "8"
    v["schema_version"] = ConsumerCapture.domain
    consumer = ConsumerCapture(v)
    speech = SpeechSemantics(v["speech"])
    policy = {
        "required_profile_hash": H,
        "consent_hash": H,
        "authority_epoch": 1,
        "receipt_epoch": 1,
    }
    value = {
        "schema_version": AudioReuseAssociation.domain,
        "use_id": pr.to_dict()["use_id"],
        "parent_receipt_hash": parent.content_hash,
        "parent_observation_hash": obs.content_hash,
        "origin_execution_id": ex.to_dict()["execution_id"],
        "origin_job_id": "7",
        "scope": ex.to_dict()["scope"],
        "consumer_job_id": "8",
        "consumer_manifest_hash": H,
        "speech_hash": speech.content_hash,
        "scoped_speech_hash": scoped_speech_hash(ex.to_dict()["scope"], speech),
        "presentation_hash": pr.content_hash,
        "audio": parent.to_dict()["proof"]["audio"]["asset"],
        "policy": policy,
        "window": {"issued_ms": 10, "expires_ms": 10000},
    }
    result = AudioReuseAssociation(
        value, parent=parent, context=consumer, presentation=pr, expected_epochs=policy
    )
    assert (
        result.to_dict()["origin_job_id"] == "7"
        and parent.to_dict()["proof"]["synthesis"]["provider"] == "flite"
    )
    for k, bad in [
        ("consumer_job_id", "9"),
        ("parent_receipt_hash", H),
        ("speech_hash", H),
    ]:
        wrong = deepcopy(value)
        wrong[k] = bad
        with pytest.raises(ValueError):
            AudioReuseAssociation(
                wrong,
                parent=parent,
                context=consumer,
                presentation=pr,
                expected_epochs=policy,
            )
    other = deepcopy(policy)
    other["authority_epoch"] = 2
    with pytest.raises(ValueError):
        AudioReuseAssociation(
            value,
            parent=parent,
            context=consumer,
            presentation=pr,
            expected_epochs=other,
        )
    assert obs.content_hash == chain[2].content_hash
    assert (
        presentation(
            chain, chunks=["Plants need light", "and water to grow."]
        ).content_hash
        != pr.content_hash
    )


def test_closed_production_gates_and_six_models():
    from core.visual_segment_runs import _producer_dependencies
    from core.visual_segment_dispatch import _deployment_ready
    from django.apps import apps

    assert _producer_dependencies() is None and _deployment_ready() is False
    assert (
        len([m for m in apps.get_models() if m.__name__.startswith("AudioProducer")])
        == 6
    )


def test_signed_observation_requires_independent_context(chain):
    obs = chain[2]
    env, reg = signed(obs, "tts.original-observation.v1")
    args = dict(
        registry=reg,
        environment="isolated-test",
        audience="p1-test",
        purpose="tts.original-observation.v1",
        now_ms=100,
        production=False,
    )
    wire = canonical_json(env).encode()
    with pytest.raises(ValueError):
        verify_envelope(wire, **args)
    assert (
        verify_envelope(wire, parents={"context": chain[1]}, **args)["payload"]
        == obs.to_dict()
    )
    raw = chain[1].to_dict()
    raw["nonce"] = U()
    with pytest.raises(ValueError):
        verify_envelope(wire, parents={"context": ProducerContext(raw)}, **args)
    with pytest.raises(ValueError):
        verify_envelope(
            wire, parents={"context": chain[1]}, **{**args, "now_ms": 10000}
        )


def test_signed_receipt_requires_original_parent_and_independent_digest(chain):
    ex, ctx, obs, parent = chain
    env, reg = signed(parent, "publisher.original-receipt.v1")
    args = dict(
        registry=reg,
        environment="isolated-test",
        audience="p1-test",
        purpose="publisher.original-receipt.v1",
        now_ms=100,
        production=False,
    )
    parents = {
        "observation": obs,
        "context": ctx,
        "expected_proof_hash": parent.to_dict()["proof_hash"],
        "expected_producer_id": parent.to_dict()["proof"]["producer"]["id"],
    }
    assert verify_envelope(canonical_json(env).encode(), parents=parents, **args)
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            parents={**parents, "expected_proof_hash": H},
            **args
        )
    # Historical metadata or a path cannot substitute for a verified parent.
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            parents={**parents, "observation": {"path": "audio/slide_1.mp3"}},
            **args
        )


def test_distinct_authority_keys_are_mandatory(chain):
    from dataclasses import replace

    env, reg = signed(chain[1])
    grant = next(iter(reg.values()))
    other = replace(grant, enrollment_id=U(), purposes=("tts.original-observation.v1",))
    reg[(other.enrollment_id, other.key_id)] = other
    with pytest.raises(ValueError):
        verify(env, reg)
    for bad in (
        replace(grant, revoked=True),
        replace(grant, expires_ms=99),
        replace(grant, not_before_ms=101),
    ):
        with pytest.raises(ValueError):
            verify(env, {(grant.enrollment_id, grant.key_id): bad})


@pytest.mark.parametrize("field", ["owner_id", "project_id"])
def test_scope_is_not_granted_by_speech_equality(chain, field):
    ex, ctx, obs, parent = chain
    raw = parent.to_dict()
    raw["proof"][field] = "999"
    digest = content_hash("visual-segment-audio-proof.v1", raw["proof"])
    raw["proof_hash"] = digest
    with pytest.raises(ValueError):
        OriginalReceipt(
            raw,
            observation=obs,
            context=ctx,
            expected_proof_hash=digest,
            expected_producer_id=raw["proof"]["producer"]["id"],
        )


@pytest.mark.parametrize(
    "change",
    [
        "voice",
        "sample",
        "controls",
        "spoken",
        "chunking",
        "fallback",
        "missing-output",
        "dropped-chunk",
    ],
)
def test_actual_changes_cannot_claim_original_complete_observation(chain, change):
    ex, ctx, obs, parent = chain
    raw = obs.to_dict()
    s = raw["actual_speech"]
    if change == "voice":
        s["voice_id"] = "other"
    if change == "sample":
        s["dependencies"]["sample"] = {"extension": "wav", "sha256": H, "size": 32}
    if change == "controls":
        s["controls"]["speed"] = "1.1"
    if change == "spoken":
        s["spoken_text"] += " Changed."
    if change == "chunking":
        s["chunks"] = ["Different boundaries."]
        s["pauses_ms"] = []
    if change == "fallback":
        s["provider"] = "other"
    if change == "missing-output":
        raw["outputs"]["wav"] = None
    if change == "dropped-chunk":
        raw["coverage"] = []
    with pytest.raises(ValueError):
        OriginalObservation(raw, context=ctx)


def test_nondeterministic_new_attempt_is_not_duplicate_synthesis(chain):
    ex, ctx, obs, parent = chain
    raw = ctx.to_dict()
    raw["generation"] = 2
    raw["nonce"] = U()
    next_context = ProducerContext(raw)
    observed = obs.to_dict()
    observed.update(
        observation_id=U(),
        generation=2,
        nonce=raw["nonce"],
        context_hash=next_context.content_hash,
    )
    next_obs = OriginalObservation(observed, context=next_context)
    assert next_obs.content_hash != obs.content_hash
    assert (
        SpeechSemantics(next_obs.to_dict()["actual_speech"]).content_hash
        == SpeechSemantics(obs.to_dict()["actual_speech"]).content_hash
    )


def test_unicode_exact_text_and_canonical_golden():
    # New-domain literal anchor; existing v1 hashes remain owned by their tests.
    assert canonical_json({"z": "é", "a": 0}) == '{"a":0,"z":"é"}'
    assert (
        content_hash("production-consumer-capture.v1", {"a": 0, "z": "é"})
        == "sha256:a24d7e73aabc0262f948a0003daeb155ee10a4763c570c879ef112f0051b9106"
    )
