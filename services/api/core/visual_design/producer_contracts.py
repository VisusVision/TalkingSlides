"""Inactive P1 statements. No registry defaults, ORM, media I/O or access grants.

Signature verification authenticates a statement against an independently supplied
registry. It does not observe synthesis or implement ProducerTrustSource.
"""

import base64
from dataclasses import dataclass
import json
import re
import uuid
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .schemas import canonical_json, content_hash, fields, require, digest
from .segment_contracts import (
    _Frozen,
    _bounded,
    _page,
    _integer,
    _asset,
    SegmentAudioProof,
    LIMITS,
    text_hash,
)

PREFIX = b"talkingslides-producer-signature.v1\n"
VERSIONS = {
    "production-producer-enrollment.v1": ("operator.enrollment.v1",),
    "production-producer-context.v1": ("publisher.context.v1",),
    "production-tts-observation.v1": ("tts.original-observation.v1",),
    "production-audio-receipt.v1": ("publisher.original-receipt.v1",),
    "production-audio-reuse.v1": ("publisher.reuse.v1",),
    "production-producer-control.v1": ("operator.control.v1", "publisher.attempt.v1"),
}


def identifier(value):
    require(
        type(value) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value),
        "identity",
        "Opaque identity required",
    )


def db_id(value):
    require(
        type(value) is str
        and re.fullmatch(r"[1-9][0-9]{0,18}", value)
        and int(value) <= 2**63 - 1,
        "identity",
        "Canonical database ID required",
    )


def uid(value):
    require(type(value) is str, "identity", "UUID required")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("Invalid UUID")
    require(str(parsed) == value, "identity", "Canonical UUID required")


def text(value):
    # Same bounded exact-text/Unicode policy as the existing audio proof.
    text_hash(value)


def _scope(value):
    fields(value, ("owner_id", "project_id", "page"))
    db_id(value["owner_id"])
    db_id(value["project_id"])
    _page(value["page"])


def _window(value):
    fields(value, ("issued_ms", "expires_ms"))
    _integer(value["issued_ms"], 0, 10**15)
    _integer(value["expires_ms"], 1, 10**15)
    require(
        value["issued_ms"] < value["expires_ms"], "expiry", "Invalid validity window"
    )


def _frozen(instance, value):
    instance._freeze(value)


class SpeechSemantics(_Frozen):
    domain = "production-speech-semantics.v1"

    def __init__(self, value):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "protected_text",
                "spoken_text",
                "language",
                "chunks",
                "pauses_ms",
                "gap_policy",
                "provider",
                "voice_id",
                "controls",
                "dependencies",
            ),
        )
        require(
            value["schema_version"] == self.domain,
            "version",
            "Unsupported speech identity",
        )
        text(value["protected_text"])
        text(value["spoken_text"])
        require(
            type(value["language"]) is str
            and re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", value["language"])
            and value["language"].split("-")[0] not in ("xx", "zz"),
            "language",
            "Concrete language required",
        )
        for k in ("provider", "voice_id"):
            identifier(value[k])
            require(
                value[k].lower()
                not in (
                    "cached",
                    "auto",
                    "unknown",
                    "none",
                    "fallback",
                    "silent",
                    "default",
                    "placeholder",
                ),
                "speech",
                "Actual synthesizer required",
            )
        require(
            type(value["chunks"]) is list and 1 <= len(value["chunks"]) <= 256,
            "chunks",
            "Bounded engine plan required",
        )
        for item in value["chunks"]:
            text(item)
        require(
            type(value["pauses_ms"]) is list
            and len(value["pauses_ms"]) == len(value["chunks"]) - 1,
            "chunks",
            "Inter-chunk pauses required",
        )
        for pause in value["pauses_ms"]:
            _integer(pause, 0, 120000)
        identifier(value["gap_policy"])
        fields(value["controls"], ("speed", "pitch", "gain_db"))
        require(
            value["controls"] == {"speed": "1", "pitch": "1", "gain_db": "0"},
            "controls",
            "Initial neutral profile required",
        )
        fields(
            value["dependencies"],
            (
                "model_hash",
                "config_hash",
                "preparation_hash",
                "pronunciation_hash",
                "segmentation_hash",
                "sample",
            ),
        )
        for k, v in value["dependencies"].items():
            if k != "sample":
                digest(v)
        if value["dependencies"]["sample"] is not None:
            _asset(value["dependencies"]["sample"], ("wav",), LIMITS["max_audio_bytes"])
        _frozen(self, value)


def scoped_speech_hash(scope, speech):
    _scope(scope)
    require(isinstance(speech, SpeechSemantics), "speech", "Validated speech required")
    return content_hash(
        "production-scoped-speech.v1",
        {**scope, "speech_semantics_hash": speech.content_hash},
    )


class ProducerExecution(_Frozen):
    domain = "production-producer-execution.v1"

    def __init__(self, value):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "execution_id",
                "scope",
                "job_id",
                "job_type",
                "page_id",
                "source_hash",
                "manifest_hash",
                "order",
                "speech",
                "request",
            ),
        )
        require(
            value["schema_version"] == self.domain,
            "version",
            "Original execution required",
        )
        uid(value["execution_id"])
        _scope(value["scope"])
        db_id(value["job_id"])
        require(
            value["job_type"] == "video_export",
            "purpose",
            "Ordinary origin Job required",
        )
        if value["page_id"] is not None:
            db_id(value["page_id"])
        digest(value["source_hash"])
        digest(value["manifest_hash"])
        _integer(value["order"])
        SpeechSemantics(value["speech"])
        fields(
            value["request"], ("selected_provider", "selected_voice", "settings_hash")
        )
        identifier(value["request"]["selected_provider"])
        identifier(value["request"]["selected_voice"])
        digest(value["request"]["settings_hash"])
        _frozen(self, value)


class ProducerContext(_Frozen):
    domain = "production-producer-context.v1"

    def __init__(self, value):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "execution",
                "generation",
                "nonce",
                "window",
                "required_profile_hash",
            ),
        )
        require(value["schema_version"] == self.domain, "version", "Context required")
        ProducerExecution(value["execution"])
        _integer(value["generation"], 1, 3)
        uid(value["nonce"])
        _window(value["window"])
        digest(value["required_profile_hash"])
        _frozen(self, value)


class OriginalObservation(_Frozen):
    domain = "production-tts-observation.v1"

    def __init__(self, value, *, context):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "observation_id",
                "context_hash",
                "execution_id",
                "generation",
                "nonce",
                "actual_speech",
                "outcome",
                "coverage",
                "outputs",
            ),
        )
        require(
            value["schema_version"] == self.domain
            and isinstance(context, ProducerContext),
            "version",
            "Issued context required",
        )
        ctx = context.to_dict()
        uid(value["observation_id"])
        require(
            value["context_hash"] == context.content_hash
            and value["execution_id"] == ctx["execution"]["execution_id"]
            and type(value["generation"]) is int
            and value["generation"] == ctx["generation"]
            and value["nonce"] == ctx["nonce"],
            "binding",
            "Observation context mismatch",
        )
        require(
            value["outcome"] in ("complete", "failed", "ineligible"),
            "outcome",
            "Explicit outcome required",
        )
        require(
            type(value["coverage"]) is list and len(value["coverage"]) <= 256,
            "coverage",
            "Bounded coverage required",
        )
        for item in value["coverage"]:
            fields(item, ("index", "text_hash", "status"))
            _integer(item["index"], 0, 255)
            digest(item["text_hash"])
            require(
                item["status"] in ("complete", "failed", "filtered", "recovered"),
                "coverage",
                "Explicit coverage status",
            )
        fields(value["outputs"], ("mp3", "wav"))
        for k in ("mp3", "wav"):
            if value["outputs"][k] is not None:
                _asset(value["outputs"][k], (k,), LIMITS["max_audio_bytes"])
        if value["actual_speech"] is not None:
            SpeechSemantics(value["actual_speech"])
        if value["outcome"] == "complete":
            require(
                canonical_json(value["actual_speech"])
                == canonical_json(ctx["execution"]["speech"]),
                "speech",
                "Actual plan drift",
            )
            chunks = ctx["execution"]["speech"]["chunks"]
            expected = [
                {"index": i, "text_hash": text_hash(t), "status": "complete"}
                for i, t in enumerate(chunks)
            ]
            require(
                value["coverage"] == expected and all(value["outputs"].values()),
                "coverage",
                "Complete original production required",
            )
        _frozen(self, value)


class PagePresentationBinding(_Frozen):
    domain = "production-page-presentation.v1"

    def __init__(self, value):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "use_id",
                "scope",
                "job_id",
                "manifest_hash",
                "source_hash",
                "order",
                "scene_hash",
                "audio",
                "window",
                "chunks",
                "cues",
                "caption_policy",
                "video_recipe_hash",
                "hold_ms",
                "frozen_absolute",
            ),
        )
        require(
            value["schema_version"] == self.domain, "version", "Presentation required"
        )
        uid(value["use_id"])
        _scope(value["scope"])
        db_id(value["job_id"])
        _integer(value["order"])
        for k in ("manifest_hash", "source_hash", "scene_hash", "video_recipe_hash"):
            digest(value[k])
        _asset(value["audio"], ("m4a",), LIMITS["max_audio_bytes"])
        fields(value["window"], ("duration_ms", "start_ms", "end_ms"))
        w = value["window"]
        for v in w.values():
            _integer(v, 0, 120000)
        require(
            0 <= w["start_ms"] < w["end_ms"] <= w["duration_ms"],
            "captions",
            "Audio window required",
        )
        _integer(value["hold_ms"], 0, 120000)
        require(
            w["duration_ms"] + value["hold_ms"] <= 120000, "captions", "Duration budget"
        )
        require(
            value["caption_policy"] in ("frozen_baseline.v1", "result_allocator.v1"),
            "captions",
            "Explicit caption policy",
        )
        require(
            type(value["chunks"]) is list
            and 1 <= len(value["chunks"]) <= 128
            and type(value["cues"]) is list
            and len(value["cues"]) == len(value["chunks"]),
            "captions",
            "Authored captions required",
        )
        previous = w["start_ms"]
        for i, (chunk, cue) in enumerate(zip(value["chunks"], value["cues"])):
            text(chunk)
            fields(cue, ("index", "text", "start_ms", "end_ms"))
            _integer(cue["index"], 0, 127)
            _integer(cue["start_ms"], 0, 120000)
            _integer(cue["end_ms"], 0, 120000)
            require(
                cue["index"] == i
                and cue["text"] == chunk
                and previous <= cue["start_ms"] < cue["end_ms"] <= w["end_ms"],
                "captions",
                "Exact ordered audio-local captions",
            )
            previous = cue["end_ms"]
        fields(value["frozen_absolute"], ("start_ms", "end_ms"))
        for v in value["frozen_absolute"].values():
            if v is not None:
                _integer(v, 0, 10**15)
        _frozen(self, value)


class OriginalReceipt(_Frozen):
    domain = "production-audio-receipt.v1"

    def __init__(
        self, value, *, observation, context, expected_proof_hash, expected_producer_id
    ):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "receipt_id",
                "context_hash",
                "observation_hash",
                "proof",
                "qualification",
                "presentation_hash",
                "speech_hash",
                "proof_hash",
            ),
        )
        require(
            value["schema_version"] == self.domain
            and isinstance(observation, OriginalObservation)
            and isinstance(context, ProducerContext),
            "receipt",
            "Validated original parent required",
        )
        obs = observation.to_dict()
        ctx = context.to_dict()
        ex = ctx["execution"]
        require(
            value["speech_hash"] == SpeechSemantics(obs["actual_speech"]).content_hash,
            "receipt",
            "Original actual speech identity required",
        )
        require(
            obs["context_hash"] == context.content_hash
            and obs["outcome"] == "complete"
            and value["observation_hash"] == observation.content_hash
            and value["context_hash"] == context.content_hash,
            "receipt",
            "Qualified complete parent required",
        )
        require(
            value["proof_hash"] == expected_proof_hash,
            "receipt",
            "Independent proof digest required",
        )
        proof = SegmentAudioProof(
            value["proof"],
            expected_hash=expected_proof_hash,
            expected_producer_id=expected_producer_id,
        ).to_dict()
        require(
            value["receipt_id"] == proof["receipt_id"]
            and proof["owner_id"] == ex["scope"]["owner_id"]
            and proof["project_id"] == ex["scope"]["project_id"]
            and proof["page"] == ex["scope"]["page"],
            "scope",
            "Receipt scope mismatch",
        )
        require(
            proof["baseline"]["job_id"] == ex["job_id"]
            and proof["baseline"]["run_id"] == ex["execution_id"]
            and proof["baseline"]["manifest_hash"] == ex["manifest_hash"]
            and proof["baseline"]["page_order"] == ex["order"],
            "receipt",
            "Origin baseline mismatch",
        )
        speech = obs["actual_speech"]
        deps = proof["synthesis"]["dependencies"]
        require(
            proof["narration"]["protected_text"] == speech["protected_text"]
            and proof["narration"]["spoken_text"] == speech["spoken_text"]
            and proof["narration"]["language"] == speech["language"]
            and proof["synthesis"]["provider"] == speech["provider"]
            and proof["synthesis"]["voice_id"] == speech["voice_id"],
            "receipt",
            "Actual narration/provider mismatch",
        )
        require(
            all(deps[k] == speech["dependencies"][k] for k in deps)
            and proof["processing"]["input_asset"] == obs["outputs"]["wav"],
            "receipt",
            "Original resource/processing mismatch",
        )
        fields(
            value["qualification"], ("job_id", "manifest_hash", "result_hash", "kind")
        )
        require(
            value["qualification"]["kind"] == "completed_full_render.v1"
            and value["qualification"]["job_id"] == ex["job_id"]
            and value["qualification"]["manifest_hash"] == ex["manifest_hash"],
            "receipt",
            "Full qualification required",
        )
        require(
            proof["synthesis"]["snapshot_tts_settings_hash"]
            == ex["request"]["settings_hash"],
            "receipt",
            "Captured settings differ",
        )
        digest(value["qualification"]["result_hash"])
        digest(value["presentation_hash"])
        _frozen(self, value)


class ConsumerCapture(_Frozen):
    """Pure consuming Job inputs: no synthesis execution/generation/nonce."""

    domain = "production-consumer-capture.v1"

    def __init__(self, value):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "scope",
                "job_id",
                "manifest_hash",
                "source_hash",
                "speech",
            ),
        )
        require(
            value["schema_version"] == self.domain,
            "version",
            "Consumer capture required",
        )
        _scope(value["scope"])
        db_id(value["job_id"])
        digest(value["manifest_hash"])
        digest(value["source_hash"])
        SpeechSemantics(value["speech"])
        _frozen(self, value)


class AudioReuseAssociation(_Frozen):
    domain = "production-audio-reuse.v1"

    def __init__(self, value, *, parent, context, presentation, expected_epochs):
        _bounded(value)
        fields(
            value,
            (
                "schema_version",
                "use_id",
                "parent_receipt_hash",
                "parent_observation_hash",
                "origin_execution_id",
                "origin_job_id",
                "scope",
                "consumer_job_id",
                "consumer_manifest_hash",
                "speech_hash",
                "scoped_speech_hash",
                "presentation_hash",
                "audio",
                "policy",
                "window",
            ),
        )
        require(
            value["schema_version"] == self.domain
            and isinstance(parent, OriginalReceipt)
            and isinstance(context, ConsumerCapture)
            and isinstance(presentation, PagePresentationBinding),
            "reuse",
            "Independently validated parent/consumer required",
        )
        p = parent.to_dict()
        proof = p["proof"]
        ex = context.to_dict()
        pr = presentation.to_dict()
        require(
            value["parent_receipt_hash"] == parent.content_hash
            and value["parent_observation_hash"] == p["observation_hash"]
            and value["origin_execution_id"] == proof["baseline"]["run_id"]
            and value["origin_job_id"] == proof["baseline"]["job_id"],
            "reuse",
            "Original lineage mismatch",
        )
        _scope(value["scope"])
        require(
            value["scope"]
            == ex["scope"]
            == pr["scope"]
            == {
                "owner_id": proof["owner_id"],
                "project_id": proof["project_id"],
                "page": proof["page"],
            },
            "scope",
            "No implicit scope transfer",
        )
        require(
            value["use_id"] == pr["use_id"]
            and value["consumer_job_id"] == ex["job_id"] == pr["job_id"]
            and value["consumer_manifest_hash"]
            == ex["manifest_hash"]
            == pr["manifest_hash"]
            and value["presentation_hash"] == presentation.content_hash,
            "reuse",
            "Explicit consumer required",
        )
        require(
            pr["source_hash"] == ex["source_hash"],
            "reuse",
            "Exact consumer source required",
        )
        speech = SpeechSemantics(ex["speech"])
        require(
            value["speech_hash"] == speech.content_hash == p["speech_hash"]
            and value["scoped_speech_hash"] == scoped_speech_hash(ex["scope"], speech),
            "reuse",
            "Speech mismatch",
        )
        require(
            speech.to_dict()["protected_text"] == proof["narration"]["protected_text"]
            and speech.to_dict()["spoken_text"] == proof["narration"]["spoken_text"]
            and speech.to_dict()["provider"] == proof["synthesis"]["provider"]
            and speech.to_dict()["voice_id"] == proof["synthesis"]["voice_id"],
            "reuse",
            "Original provider and text required",
        )
        require(
            value["audio"] == proof["audio"]["asset"] == pr["audio"],
            "reuse",
            "Exact eligible audio required",
        )
        require(
            pr["window"]
            == {
                "duration_ms": proof["audio"]["duration_ms"],
                "start_ms": proof["audio"]["audible_start_ms"],
                "end_ms": proof["audio"]["audible_end_ms"],
            },
            "captions",
            "Exact parent audio window required",
        )
        if pr["caption_policy"] == "frozen_baseline.v1":
            cues = [
                {
                    "chunk_index": c["index"],
                    "text": c["text"],
                    "start_ms": c["start_ms"],
                    "end_ms": c["end_ms"],
                }
                for c in pr["cues"]
            ]
            require(
                content_hash(
                    "visual-segment-cues.v1",
                    {"page": pr["scope"]["page"], "cues": cues},
                )
                == proof["baseline"]["cues_hash"],
                "captions",
                "Changed cues cannot claim frozen baseline",
            )
        fields(
            value["policy"],
            (
                "required_profile_hash",
                "consent_hash",
                "authority_epoch",
                "receipt_epoch",
            ),
        )
        require(
            canonical_json(value["policy"]) == canonical_json(expected_epochs),
            "reuse",
            "Current independent policy required",
        )
        for k in ("required_profile_hash", "consent_hash"):
            digest(value["policy"][k])
        for k in ("authority_epoch", "receipt_epoch"):
            _integer(value["policy"][k], 1, 10**15)
        _window(value["window"])
        _frozen(self, value)


@dataclass(frozen=True)
class Issuer:
    enrollment_id: str
    key_id: str
    public_key: bytes
    purposes: tuple
    environment: str
    audience: str
    not_before_ms: int
    expires_ms: int
    revoked: bool = False
    engineering_only: bool = False


def decode_key(value, length):
    require(
        type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]+", value),
        "signature",
        "Canonical base64url required",
    )
    try:
        raw = base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))
    except ValueError:
        raise ValueError("Invalid base64url")
    require(
        len(raw) == length
        and base64.urlsafe_b64encode(raw).decode().rstrip("=") == value,
        "signature",
        "Invalid key/signature",
    )
    return raw


def verify_envelope(
    encoded,
    *,
    registry,
    environment,
    audience,
    purpose,
    now_ms,
    production=True,
    parents=None
):
    """Return authenticated statement bytes; never returns execution permission.

    registry is a server-supplied mapping of (enrollment UUID,key ID) to Issuer.
    No enrollment messages can add keys to it. Expiry is checked at ingress;
    historical replay requires the already accepted stored record, not this API.
    """
    require(
        type(encoded) is bytes and len(encoded) <= 1048576,
        "json",
        "Bounded UTF-8 bytes required",
    )

    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "duplicate", "Duplicate JSON key")
            result[k] = v
        return result

    try:
        e = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda v: (_ for _ in ()).throw(
                ValueError("Nonfinite JSON")
            ),
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid canonical JSON") from exc
    _bounded(e)
    fields(
        e,
        (
            "schema_version",
            "purpose",
            "environment",
            "audience",
            "issuer_enrollment_id",
            "key_id",
            "payload",
            "signature",
        ),
    )
    require(canonical_json(e).encode() == encoded, "canonical", "Noncanonical envelope")
    require(
        e["schema_version"] in VERSIONS
        and purpose in VERSIONS[e["schema_version"]]
        and e["purpose"] == purpose
        and e["environment"] == environment
        and e["audience"] == audience,
        "purpose",
        "Wrong purpose/environment/audience",
    )
    uid(e["issuer_enrollment_id"])
    identifier(e["key_id"])
    issuer = registry.get((e["issuer_enrollment_id"], e["key_id"]))
    require(
        isinstance(issuer, Issuer)
        and issuer.enrollment_id == e["issuer_enrollment_id"]
        and issuer.key_id == e["key_id"]
        and issuer.environment == environment
        and issuer.audience == audience
        and purpose in issuer.purposes
        and not issuer.revoked
        and (not production or not issuer.engineering_only),
        "issuer",
        "Independent issuer unavailable",
    )
    _integer(now_ms, 0, 10**15)
    # Even separately enrolled grants cannot share a producer/publisher key.
    # A caller cannot select a second purpose grant to collapse independence.
    for grant in registry.values():
        require(
            isinstance(grant, Issuer)
            and type(grant.public_key) is bytes
            and len(grant.public_key) == 32,
            "issuer",
            "Invalid independent registry",
        )
        related = [
            g
            for g in registry.values()
            if isinstance(g, Issuer) and g.public_key == grant.public_key
        ]
        purposes = {p for g in related for p in g.purposes}
        families = {p.split(".")[0] for p in purposes}
        require(
            len(families) == 1
            and purposes <= {p for ps in VERSIONS.values() for p in ps},
            "issuer",
            "Signing authority families must be independent",
        )
    require(
        issuer.not_before_ms <= now_ms < issuer.expires_ms, "expiry", "Issuer expired"
    )
    sig = decode_key(e["signature"], 64)
    body = {k: v for k, v in e.items() if k != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(issuer.public_key).verify(
            sig, PREFIX + canonical_json(body).encode()
        )
    except (ValueError, InvalidSignature) as exc:
        raise ValueError("Invalid Ed25519 signature") from exc
    require(
        type(e["payload"]) is dict
        and e["payload"].get("schema_version") == e["schema_version"],
        "version",
        "Payload version mismatch",
    )
    # Shape-specific constructors require independently supplied parent statements.
    if e["schema_version"] == "production-producer-context.v1":
        ctx = ProducerContext(e["payload"])
        w = ctx.to_dict()["window"]
        require(w["issued_ms"] <= now_ms < w["expires_ms"], "expiry", "Context expired")
    _payload_shape(e["schema_version"], e["payload"], purpose)
    if e["schema_version"] in (
        "production-audio-reuse.v1",
        "production-producer-control.v1",
        "production-producer-enrollment.v1",
    ):
        w = e["payload"]["window"]
        require(
            w["issued_ms"] <= now_ms < w["expires_ms"], "expiry", "Statement expired"
        )
    # Signatures never skip structural/parent validation. There is no hash-only
    # authentication path; these arguments come from independent stored records.
    constructors = {
        "production-tts-observation.v1": OriginalObservation,
        "production-audio-receipt.v1": OriginalReceipt,
        "production-audio-reuse.v1": AudioReuseAssociation,
    }
    if e["schema_version"] in constructors:
        require(
            type(parents) is dict, "binding", "Independent validated parents required"
        )
        try:
            constructors[e["schema_version"]](e["payload"], **parents)
        except TypeError as exc:
            raise ValueError("Independent parent arguments required") from exc
        if e["schema_version"] == "production-tts-observation.v1":
            w = parents["context"].to_dict()["window"]
            require(
                w["issued_ms"] <= now_ms < w["expires_ms"],
                "expiry",
                "Observation ingress expired",
            )
    return json.loads(canonical_json(e))


def _payload_shape(version, value, purpose):
    keys = {
        "production-producer-enrollment.v1": (
            "schema_version",
            "enrollment_id",
            "issuer_id",
            "key_id",
            "public_key",
            "purposes",
            "environment",
            "audience",
            "image_hash",
            "profile_hash",
            "window",
        ),
        "production-tts-observation.v1": (
            "schema_version",
            "observation_id",
            "context_hash",
            "execution_id",
            "generation",
            "nonce",
            "actual_speech",
            "outcome",
            "coverage",
            "outputs",
        ),
        "production-audio-receipt.v1": (
            "schema_version",
            "receipt_id",
            "context_hash",
            "observation_hash",
            "proof",
            "qualification",
            "presentation_hash",
            "speech_hash",
            "proof_hash",
        ),
        "production-audio-reuse.v1": (
            "schema_version",
            "use_id",
            "parent_receipt_hash",
            "parent_observation_hash",
            "origin_execution_id",
            "origin_job_id",
            "scope",
            "consumer_job_id",
            "consumer_manifest_hash",
            "speech_hash",
            "scoped_speech_hash",
            "presentation_hash",
            "audio",
            "policy",
            "window",
        ),
        "production-producer-control.v1": (
            "schema_version",
            "control_id",
            "expected_epoch",
            "action",
            "window",
        ),
    }
    if version == "production-producer-context.v1":
        return
    wanted = keys[version]
    if version == "production-producer-control.v1":
        if value.get("action") == "attempt_issued":
            wanted += ("generation", "nonce", "context_hash", "expires_ms")
        if value.get("action") == "accept":
            wanted += ("receipt_id",)
    fields(value, wanted)
    if version == "production-producer-enrollment.v1":
        uid(value["enrollment_id"])
        identifier(value["issuer_id"])
        identifier(value["key_id"])
        decode_key(value["public_key"], 32)
        identifier(value["environment"])
        identifier(value["audience"])
        digest(value["image_hash"])
        digest(value["profile_hash"])
        _window(value["window"])
        allowed = {p for ps in VERSIONS.values() for p in ps}
        require(
            type(value["purposes"]) is list
            and 1 <= len(value["purposes"]) <= 4
            and len(set(value["purposes"])) == len(value["purposes"])
            and set(value["purposes"]) <= allowed,
            "issuer",
            "Known purposes required",
        )
        require(
            not (
                "tts.original-observation.v1" in value["purposes"]
                and any(p.startswith("publisher.") for p in value["purposes"])
            ),
            "issuer",
            "Producer and publisher authority must be separate",
        )
    if version == "production-producer-control.v1":
        uid(value["control_id"])
        _integer(value["expected_epoch"], 0, 10**15)
        _window(value["window"])
        allowed = (
            ("activate", "revoke", "retire")
            if purpose == "operator.control.v1"
            else ("attempt_issued", "invalidate", "accept")
        )
        require(value["action"] in allowed, "purpose", "Control purpose mismatch")
        if value["action"] == "attempt_issued":
            _integer(value["generation"], 1, 3)
            uid(value["nonce"])
            digest(value["context_hash"])
            require(
                value["expires_ms"] == value["window"]["expires_ms"]
                and value["expires_ms"] - value["window"]["issued_ms"] <= 300000,
                "expiry",
                "Bounded attempt window required",
            )
        if value["action"] == "accept":
            uid(value["receipt_id"])


def replay_decision(known_hash, statement):
    """Pure equality only; persisted uniqueness and current fences remain required."""
    if known_hash is None:
        return "new"
    require(known_hash == statement.content_hash, "replay", "Conflicting identity")
    return "duplicate"


def signed_envelope_hash(envelope):
    """Identity of exact signed bytes, never an authentication shortcut.

    Callers must retain the independently verified envelope and issuer record.
    Nested statement hashes and signed-envelope hashes are different identities.
    """
    _bounded(envelope)
    fields(
        envelope,
        (
            "schema_version",
            "purpose",
            "environment",
            "audience",
            "issuer_enrollment_id",
            "key_id",
            "payload",
            "signature",
        ),
    )
    require(
        envelope["schema_version"] in VERSIONS, "version", "Known envelope required"
    )
    return content_hash(
        envelope["schema_version"].replace(".v1", "-envelope.v1"), envelope
    )
