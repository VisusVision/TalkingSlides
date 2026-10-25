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
        **kw,
    )


def test_canonical_signature_and_registry(chain):
    from core.visual_design.producer_contracts import signed_envelope_hash

    env, reg = signed(chain[1])
    verified = verify(env, reg)
    assert verified["payload"] == chain[1].to_dict()
    assert signed_envelope_hash(verified) == signed_envelope_hash(env)
    other = deepcopy(env)
    other["signature"] = "A" * 86
    assert signed_envelope_hash(other) != signed_envelope_hash(env)
    with pytest.raises(ValueError):
        verify(other, reg)
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


def reuse_case(chain):
    ex, ctx, obs, parent = chain
    pr = presentation(chain)
    v = {
        k: ctx.to_dict()["execution"][k]
        for k in (
            "scope",
            "job_id",
            "job_type",
            "manifest_hash",
            "source_hash",
            "speech",
        )
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
        "consumer_job_type": "video_export",
        "consumer_manifest_hash": H,
        "speech_hash": speech.content_hash,
        "scoped_speech_hash": scoped_speech_hash(ex.to_dict()["scope"], speech),
        "presentation_hash": pr.content_hash,
        "audio": parent.to_dict()["proof"]["audio"]["asset"],
        "policy": policy,
        "window": {"issued_ms": 10, "expires_ms": 10000},
    }
    return value, consumer, pr, policy


def test_reuse_is_new_use_not_synthesis(chain):
    ex, ctx, obs, parent = chain
    value, consumer, pr, policy = reuse_case(chain)
    result = AudioReuseAssociation(
        value, parent=parent, context=consumer, presentation=pr, expected_epochs=policy
    )
    assert (
        result.to_dict()["origin_job_id"] == "7"
        and parent.to_dict()["proof"]["synthesis"]["provider"] == "flite"
    )
    for k, bad in [
        ("consumer_job_id", "9"),
        ("consumer_job_type", "publication"),
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
            **args,
        )
    # Historical metadata or a path cannot substitute for a verified parent.
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            parents={**parents, "observation": {"path": "audio/slide_1.mp3"}},
            **args,
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


@pytest.mark.parametrize(
    "field", ["owner_id", "project_id", "page_key", "source_slide_index", "split_index"]
)
def test_consistent_consumer_scope_change_still_cannot_transfer_parent(chain, field):
    value, consumer, pr, policy = reuse_case(chain)
    captured = consumer.to_dict()
    presented = pr.to_dict()
    for scope in (value["scope"], captured["scope"], presented["scope"]):
        if field in ("owner_id", "project_id"):
            scope[field] = "999"
        else:
            scope["page"][field] = "other-page" if field == "page_key" else 5
    consumer = ConsumerCapture(captured)
    pr = PagePresentationBinding(presented)
    value["scoped_speech_hash"] = scoped_speech_hash(
        captured["scope"], SpeechSemantics(captured["speech"])
    )
    value["presentation_hash"] = pr.content_hash
    with pytest.raises(ValueError):
        AudioReuseAssociation(
            value,
            parent=chain[3],
            context=consumer,
            presentation=pr,
            expected_epochs=policy,
        )


def test_reuse_signature_expiry_and_caption_separation(chain):
    value, consumer, pr, policy = reuse_case(chain)
    use = AudioReuseAssociation(
        value,
        parent=chain[3],
        context=consumer,
        presentation=pr,
        expected_epochs=policy,
    )
    env, reg = signed(use, "publisher.reuse.v1")
    args = dict(
        registry=reg,
        environment="isolated-test",
        audience="p1-test",
        purpose="publisher.reuse.v1",
        now_ms=100,
        production=False,
        parents={
            "parent": chain[3],
            "context": consumer,
            "presentation": pr,
            "expected_epochs": policy,
        },
    )
    assert verify_envelope(canonical_json(env).encode(), **args)
    with pytest.raises(ValueError):
        verify_envelope(canonical_json(env).encode(), **{**args, "now_ms": 10000})
    # Display regrouping changes presentation/use, preserving original speech.
    new_pr = presentation(chain, chunks=["Plants need light", "and water to grow."])
    value.update(
        use_id=new_pr.to_dict()["use_id"], presentation_hash=new_pr.content_hash
    )
    regrouped = AudioReuseAssociation(
        value,
        parent=chain[3],
        context=consumer,
        presentation=new_pr,
        expected_epochs=policy,
    )
    assert regrouped.to_dict()["speech_hash"] == use.to_dict()["speech_hash"]
    bad = new_pr.to_dict()
    bad["caption_policy"] = "frozen_baseline.v1"
    bad = PagePresentationBinding(bad)
    value["presentation_hash"] = bad.content_hash
    with pytest.raises(ValueError):
        AudioReuseAssociation(
            value,
            parent=chain[3],
            context=consumer,
            presentation=bad,
            expected_epochs=policy,
        )


def test_spoken_context_changes_speech_but_order_alone_does_not(chain):
    base = chain[0].to_dict()["speech"]
    old = SpeechSemantics(base)
    for key in ("protected_text", "spoken_text"):
        base[key] = "Slide 2. " + base[key]
    base["chunks"] = [base["spoken_text"]]
    assert SpeechSemantics(base).content_hash != old.content_hash


@pytest.mark.parametrize(
    "value", ["0", "01", "１２", "-1", "9223372036854775808", True]
)
def test_database_ids_are_canonical_positive_bigint_strings(chain, value):
    raw = chain[0].to_dict()
    raw["job_id"] = value
    with pytest.raises(ValueError):
        ProducerExecution(raw)


def test_enrollment_does_not_bootstrap_its_own_key_and_control_purposes(chain):
    from types import SimpleNamespace

    child = Ed25519PrivateKey.generate()
    payload = {
        "schema_version": "production-producer-enrollment.v1",
        "enrollment_id": U(),
        "issuer_id": "child",
        "key_id": "child-key",
        "public_key": base64.urlsafe_b64encode(
            child.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        .decode()
        .rstrip("="),
        "purposes": ["tts.original-observation.v1"],
        "environment": "isolated-test",
        "audience": "p1-test",
        "image_hash": H,
        "profile_hash": H,
        "window": {"issued_ms": 0, "expires_ms": 1000},
    }
    obj = SimpleNamespace(
        domain=payload["schema_version"], to_dict=lambda: deepcopy(payload)
    )
    env, registry = signed(obj, "operator.enrollment.v1")
    args = dict(
        environment="isolated-test", audience="p1-test", now_ms=100, production=False
    )
    assert verify_envelope(
        canonical_json(env).encode(),
        registry=registry,
        purpose="operator.enrollment.v1",
        **args,
    )
    assert (
        len(registry) == 1
        and (payload["enrollment_id"], payload["key_id"]) not in registry
    )
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            registry={},
            purpose="operator.enrollment.v1",
            **args,
        )
    control = {
        "schema_version": "production-producer-control.v1",
        "control_id": U(),
        "expected_epoch": 1,
        "action": "revoke",
        "window": {"issued_ms": 0, "expires_ms": 1000},
    }
    obj = SimpleNamespace(
        domain=control["schema_version"], to_dict=lambda: deepcopy(control)
    )
    env, registry = signed(obj, "operator.control.v1")
    assert verify_envelope(
        canonical_json(env).encode(),
        registry=registry,
        purpose="operator.control.v1",
        **args,
    )
    env, registry = signed(obj, "publisher.attempt.v1")
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(env).encode(),
            registry=registry,
            purpose="publisher.attempt.v1",
            **args,
        )


@pytest.mark.parametrize(
    "change",
    [
        "extra-field",
        "oversize-text",
        "too-many-chunks",
        "non-neutral-control",
        "subtitle-field",
    ],
)
def test_schema_and_budget_reject_even_authentically_signed_bad_payloads(chain, change):
    from types import SimpleNamespace

    raw = chain[1].to_dict()
    if change == "extra-field":
        raw["algorithm"] = "none"
    if change == "oversize-text":
        raw["execution"]["speech"]["protected_text"] = "x" * 1_048_576
    if change == "too-many-chunks":
        raw["execution"]["speech"]["chunks"] = ["word"] * 257
    if change == "non-neutral-control":
        raw["execution"]["speech"]["controls"]["pitch"] = "1.2"
    if change == "subtitle-field":
        raw["execution"]["speech"]["subtitle_chunks"] = ["display"]
    env, reg = signed(
        SimpleNamespace(domain=ProducerContext.domain, to_dict=lambda: raw)
    )
    with pytest.raises(ValueError):
        verify(env, reg)


@pytest.mark.parametrize("outcome", ["failed", "ineligible"])
def test_failed_original_observation_is_history_and_never_eligible_receipt(
    chain, outcome
):
    ex, ctx, obs, parent = chain
    raw = obs.to_dict()
    raw.update(
        outcome=outcome,
        actual_speech=None,
        coverage=[],
        outputs={"mp3": None, "wav": None},
    )
    failed = OriginalObservation(raw, context=ctx)
    receipt = parent.to_dict()
    receipt["observation_hash"] = failed.content_hash
    with pytest.raises(ValueError):
        OriginalReceipt(
            receipt,
            observation=failed,
            context=ctx,
            expected_proof_hash=receipt["proof_hash"],
            expected_producer_id=receipt["proof"]["producer"]["id"],
        )


@pytest.mark.parametrize("field", ["environment", "audience", "public_key"])
def test_signed_enrollment_cannot_transfer_environment_or_operator_key_family(field):
    key = Ed25519PrivateKey.generate()
    enrollment = U()
    pk = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    proposed_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    p = {
        "schema_version": "production-producer-enrollment.v1",
        "enrollment_id": U(),
        "issuer_id": "child",
        "key_id": "child",
        "public_key": base64.urlsafe_b64encode(proposed_key).decode().rstrip("="),
        "purposes": ["tts.original-observation.v1"],
        "environment": "isolated-test",
        "audience": "p1-test",
        "image_hash": H,
        "profile_hash": H,
        "window": {"issued_ms": 0, "expires_ms": 1000},
    }
    if field == "public_key":
        p[field] = base64.urlsafe_b64encode(pk).decode().rstrip("=")
    else:
        p[field] = "production"
    e = {
        "schema_version": p["schema_version"],
        "purpose": "operator.enrollment.v1",
        "environment": "isolated-test",
        "audience": "p1-test",
        "issuer_enrollment_id": enrollment,
        "key_id": "root",
        "payload": p,
    }
    e["signature"] = (
        base64.urlsafe_b64encode(key.sign(PREFIX + canonical_json(e).encode()))
        .decode()
        .rstrip("=")
    )
    reg = {
        (enrollment, "root"): Issuer(
            enrollment,
            "root",
            pk,
            ("operator.enrollment.v1",),
            "isolated-test",
            "p1-test",
            0,
            1000,
            engineering_only=True,
        )
    }
    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(e).encode(),
            registry=reg,
            environment="isolated-test",
            audience="p1-test",
            purpose="operator.enrollment.v1",
            now_ms=100,
            production=False,
        )


@pytest.mark.parametrize("purpose", [None, "publication"])
def test_consumer_capture_requires_explicit_supported_job_purpose(chain, purpose):
    value, consumer, pr, policy = reuse_case(chain)
    raw = consumer.to_dict()
    if purpose is None:
        del raw["job_type"]
    else:
        raw["job_type"] = purpose
    with pytest.raises(ValueError):
        ConsumerCapture(raw)
