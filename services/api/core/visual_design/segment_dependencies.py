"""Opt-in private dependency semantics shared by the established planners.

These pure descriptors are fingerprints, not authority to reuse audio. Only the
separate producer/byte/media eligibility boundary may issue a reuse decision.
Legacy manifests omit these fields entirely; no legacy version/hash is changed.
"""
from .schemas import fields, digest, require, canonical_json, content_hash
from .segment_contracts import (_Frozen, _bounded, _opaque, _page, _asset, _runtime,
                                _integer, SegmentAudioProof, LIMITS)

SYNTHESIS_VERSION = "visual-segment-synthesis-dependencies.v1"
CAPTION_VERSION = "visual-segment-caption-dependencies.v1"
VISUAL_VERSION = "visual-segment-visual-environment.v1"
MEDIA_VALIDATOR_VERSION = "native-audio-validation.v1"
SYNTHESIS_KEY = "private_synthesis_dependencies"
CAPTION_KEY = "private_caption_dependencies"


class SynthesisDependencies(_Frozen):
    __slots__ = ()
    domain = SYNTHESIS_VERSION

    def __init__(self, value):
        _bounded(value)
        fields(value, ("schema_version", "owner_id", "project_id", "page", "narration", "synthesis", "producer", "audio", "integrity"))
        require(value["schema_version"] == SYNTHESIS_VERSION, "version", "Unsupported private synthesis dependency semantics")
        _opaque(value["owner_id"]); _opaque(value["project_id"]); _page(value["page"])
        fields(value["narration"], ("protected_hash", "spoken_hash", "language"))
        for key in ("protected_hash", "spoken_hash"):
            digest(value["narration"][key])
        language = value["narration"]["language"]
        # Reuse the existing strict proof validator with an independently trusted
        # proof at construction time in dependencies_from_verified_audio. Here
        # only the standalone planner descriptor structure is being checked.
        import re
        require(type(language) is str and re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language) is not None
                and language.split("-")[0] not in ("xx", "zz"), "language", "Concrete language required")
        synthesis = value["synthesis"]
        fields(synthesis, ("provider", "voice_id", "voice_kind", "fallback", "settings", "dependencies", "snapshot_tts_settings_hash"))
        for key in ("provider", "voice_id"):
            _opaque(synthesis[key])
            require(synthesis[key].lower() not in ("auto", "unknown", "cached", "silent", "silence", "none", "default", "placeholder", "unspecified"), "provenance", "Concrete synthesis identity required")
        require(synthesis["fallback"] == "none", "provenance", "Fallback audio unsupported")
        fields(synthesis["settings"], ("schema_version", "speed", "pitch", "gain_db"))
        require(synthesis["settings"]["schema_version"] == "neutral-audio-settings.v1", "version", "Unsupported settings")
        for key, neutral in (("speed", 1), ("pitch", 1), ("gain_db", 0)):
            require(type(synthesis["settings"][key]) in (int, float) and synthesis["settings"][key] == neutral, "audio_settings", "Unsupported nonneutral controls")
        digest(synthesis["snapshot_tts_settings_hash"])
        deps = synthesis["dependencies"]
        fields(deps, ("model_hash", "config_hash", "preparation_hash", "pronunciation_hash", "sample"))
        for key in ("model_hash", "config_hash", "preparation_hash", "pronunciation_hash"):
            digest(deps[key])
        require(synthesis["voice_kind"] in ("built_in", "cloned") and (deps["sample"] is None) == (synthesis["voice_kind"] == "built_in"), "provenance", "Voice/sample dependency mismatch")
        if deps["sample"] is not None:
            _asset(deps["sample"], ("wav",), LIMITS["max_audio_bytes"])
        fields(value["producer"], ("receipt_id", "producer_id", "producer_build_hash", "proof_hash", "baseline_manifest_hash", "baseline_job_id", "baseline_run_id"))
        from .segment_contracts import _uuid
        _uuid(value["producer"]["receipt_id"])
        for key in ("producer_id", "baseline_job_id", "baseline_run_id"):
            _opaque(value["producer"][key])
        for key in ("producer_build_hash", "proof_hash", "baseline_manifest_hash"):
            digest(value["producer"][key])
        fields(value["audio"], ("asset", "packet_payload_hash", "duration_ms", "processing_hash"))
        _asset(value["audio"]["asset"], ("m4a",), LIMITS["max_audio_bytes"])
        digest(value["audio"]["packet_payload_hash"]); digest(value["audio"]["processing_hash"])
        _integer(value["audio"]["duration_ms"], 1, LIMITS["max_duration_ms"])
        fields(value["integrity"], ("status", "validator_version", "validation_hash"))
        require(value["integrity"]["status"] == "verified_bytes_and_media.v1"
                and value["integrity"]["validator_version"] == MEDIA_VALIDATOR_VERSION, "integrity", "Verified private input descriptor required")
        digest(value["integrity"]["validation_hash"])
        self._freeze(value)


def validate_synthesis_dependencies(value, *, project_id=None, page_key=None, page=None):
    descriptor = SynthesisDependencies(value).to_dict()
    if project_id is not None:
        require(descriptor["project_id"] == str(project_id), "audio_scope", "Dependency project mismatch")
    if page_key is not None:
        require(descriptor["page"]["page_key"] == page_key, "audio_scope", "Dependency stable page mismatch")
    if page is not None:
        from .segment_contracts import page_identity
        require(canonical_json(descriptor["page"]) == canonical_json(page_identity(page)), "audio_scope", "Dependency source/split mapping mismatch")
    return descriptor


def dependencies_from_verified_audio(proof, validation_hash):
    """Called after independent provenance AND native media/byte verification.

    This pure constructor records supplied validation identity; it cannot perform
    or authenticate that verification itself. A planner descriptor is not a grant.
    """
    require(isinstance(proof, SegmentAudioProof), "provenance", "Validated producer proof required")
    raw = proof.to_dict()
    return SynthesisDependencies({"schema_version": SYNTHESIS_VERSION, "owner_id": raw["owner_id"], "project_id": raw["project_id"],
        "page": raw["page"], "narration": {k:raw["narration"][k] for k in ("protected_hash", "spoken_hash", "language")},
        "synthesis": raw["synthesis"], "producer": {"receipt_id": raw["receipt_id"], "producer_id": raw["producer"]["id"],
            "producer_build_hash": raw["producer"]["build_hash"], "proof_hash": proof.content_hash,
            "baseline_manifest_hash": raw["baseline"]["manifest_hash"], "baseline_job_id": raw["baseline"]["job_id"], "baseline_run_id": raw["baseline"]["run_id"]},
        "audio": {"asset": raw["audio"]["asset"], "packet_payload_hash": raw["audio"]["packet_payload_hash"], "duration_ms": raw["audio"]["duration_ms"],
                  "processing_hash": content_hash("visual-segment-processing.v1", raw["processing"])},
        "integrity": {"status": "verified_bytes_and_media.v1", "validator_version": MEDIA_VALIDATOR_VERSION, "validation_hash": validation_hash}})


def validate_caption_dependencies(value):
    _bounded(value)
    fields(value, ("schema_version", "cues_hash", "authored_chunks_hash", "policy", "time_base", "audio_duration_ms", "hold_duration_ms", "target_duration_ms"))
    require(value["schema_version"] == CAPTION_VERSION and value["policy"] in ("frozen_baseline.v1", "result_allocator.v1")
            and value["time_base"] == "milliseconds.v1", "version", "Unsupported caption dependencies")
    digest(value["cues_hash"]); digest(value["authored_chunks_hash"])
    for key in ("audio_duration_ms", "hold_duration_ms", "target_duration_ms"):
        _integer(value[key], 0, LIMITS["max_duration_ms"])
    require(value["audio_duration_ms"] > 0 and value["audio_duration_ms"] + value["hold_duration_ms"] == value["target_duration_ms"], "timing", "Inconsistent caption timing")
    import json
    return json.loads(canonical_json(value))


def caption_dependencies(request):
    value = request.to_dict()["subtitles"]
    return validate_caption_dependencies({"schema_version": CAPTION_VERSION,
        "cues_hash": value["cues_hash"], "authored_chunks_hash": content_hash("visual-segment-authored-chunks.v1", value["authored_chunks"]),
        **{k:value[k] for k in ("policy", "time_base", "audio_duration_ms", "hold_duration_ms", "target_duration_ms")}})


def validate_visual_environment(value):
    _bounded(value)
    fields(value, ("schema_version", "runtime", "font_configuration"))
    require(value["schema_version"] == VISUAL_VERSION, "version", "Unsupported visual environment")
    _runtime(value["runtime"])
    fields(value["font_configuration"], ("schema_version", "family", "configuration_hash"))
    require(value["font_configuration"]["schema_version"] == "pinned-font-configuration.v1"
            and value["font_configuration"]["family"] == "DejaVu Sans", "fonts", "Unsupported font configuration")
    digest(value["font_configuration"]["configuration_hash"])
    import json
    return json.loads(canonical_json(value))
