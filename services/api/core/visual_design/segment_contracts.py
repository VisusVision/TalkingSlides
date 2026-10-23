"""Pure supplementary contracts for private, reuse-only narrated segments.

No I/O, producer certification, live authorization, dispatch or rendering occurs
here. Trust anchors MUST come from independently validated server records, never
from the request being checked. Synthetic attestations require an explicit opt-in.
Phase 3A snapshot/frame contracts retain their original meaning and hashes.
"""
from copy import deepcopy
import json
import math
import re
import uuid
import unicodedata

from .schemas import (DesignError, canonical_json, content_hash, digest, fields,
                      identifier, index, require, SlideSourceSnapshot, VisualPlan)
from .render_snapshot import FrozenRenderSnapshot, asset_reference

REQUEST_VERSION = "visual-segment-request.v1"
AUDIO_VERSION = "visual-segment-audio-proof.v1"
RECEIPT_VERSION = "visual-segment-receipt.v1"
SCENE_VERSION = "visual-segment-scene.v1"
CUES_VERSION = "visual-segment-cues.v1"
TEXT_VERSION = "visual-segment-text.v1"
RECIPE_VERSION = "h264-aac-copy.v1"
MAX_JSON_BYTES = 1024 * 1024
MAX_DURATION_MS = 120_000
LIMITS = {"max_duration_ms": MAX_DURATION_MS, "max_audio_bytes": 16 * 1024 * 1024,
          "max_mp4_bytes": 32 * 1024 * 1024, "max_caption_bytes": 128 * 1024,
          "max_attempt_bytes": 64 * 1024 * 1024, "execution_timeout_seconds": 180,
          "probe_timeout_seconds": 10, "duration_tolerance_ms": 80}
RECIPE_OPTIONS = {"purpose": "private_visual_segment.v1", "recipe_version": RECIPE_VERSION,
                  "width": 1920, "height": 1080, "fps": 25, "video_codec": "h264",
                  "video_encoder": "libx264", "pixel_format": "yuv420p", "crf": 23,
                  "preset": "medium", "audio_codec": "aac", "audio_profile": "LC",
                  "audio_policy": "reuse_without_reprocessing", "subtitle_policy": "sidecars_only.v1"}


def _bounded(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        require(depth <= 20 and count <= 30_000, "payload_limit", "Segment nesting/node budget exceeded")
        require(type(item) in (dict, list, str, int, float, bool, type(None)), "schema", "JSON values only")
        if type(item) is dict:
            require(len(item) <= 128 and all(type(k) is str and len(k) <= 128 for k in item),
                    "schema", "Bounded string keys required")
            stack.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            require(len(item) <= 256, "payload_limit", "Array budget exceeded")
            stack.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            require(len(item) <= 100_000, "payload_limit", "String budget exceeded")
        elif type(item) in (int, float):
            require(abs(item) <= 10**15 and math.isfinite(item), "number", "Nonfinite/oversized number")
    try:
        encoded = canonical_json(value)
        require(len(encoded.encode("utf-8")) <= MAX_JSON_BYTES, "payload_limit", "Segment byte budget exceeded")
    except (UnicodeEncodeError, ValueError) as exc:
        raise DesignError("json", "Invalid finite Unicode JSON") from exc
    return encoded


def _same(left, right):
    """JSON identity equality must not equate False with zero or True with one."""
    return canonical_json(left) == canonical_json(right)


def _integer(value, minimum=0, maximum=1_000_000):
    require(type(value) is int and minimum <= value <= maximum, "integer", "Bounded integer required; bool is not an index")


def _string(value, maximum=100_000):
    require(type(value) is str and 0 < len(value) <= maximum and bool(value.strip()), "text", "Nonempty bounded exact text required")
    require(all(c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cs") for c in value),
            "text", "Control characters are forbidden")


def _opaque(value):
    identifier(value)
    require(not any(c in value for c in "/\\%") and not value.startswith(".") and not re.match(r"(?i)^(?:https?|file|data|javascript):", value), "identity", "Opaque internal identity required")


def _uuid(value):
    require(type(value) is str, "identity", "Canonical UUID required")
    try:
        require(str(uuid.UUID(value)) == value, "identity", "Canonical UUID required")
    except (ValueError, AttributeError) as exc:
        raise DesignError("identity", "Canonical UUID required") from exc


def text_hash(value):
    """Exact text, without normalization, trimming or rewriting."""
    _string(value)
    return content_hash(TEXT_VERSION, value)


def page_identity(page):
    result = {k: page[k] for k in ("page_key", "source_slide_index", "split_index")}
    fields(result, ("page_key", "source_slide_index", "split_index"))
    _opaque(result["page_key"])
    require(len(result["page_key"]) <= 64, "identity", "Page key too long")
    index(result["source_slide_index"])
    index(result["split_index"])
    return result


def _page(value, *, ordered=False):
    fields(value, ("page_key", "source_slide_index", "split_index", "order") if ordered else
           ("page_key", "source_slide_index", "split_index"))
    page_identity(value)
    if ordered:
        index(value["order"])


def _asset(ref, extensions, maximum):
    fields(ref, ("sha256", "size", "extension"))
    digest(ref["sha256"])
    _integer(ref["size"], 1, maximum)
    require(type(ref["extension"]) is str and ref["extension"] in extensions, "asset_type", "Unsupported media type")


def _runtime(value):
    fields(value, ("build_id", "ffmpeg_version", "ffmpeg_binary_hash", "rasterizer_id",
                   "rasterizer_build_hash", "fontconfig_build_hash"))
    for k in ("build_id", "ffmpeg_version", "rasterizer_id"):
        _opaque(value[k])
    require(value["rasterizer_id"] == "librsvg.v1", "runtime", "Unsupported rasterizer")
    for k in ("ffmpeg_binary_hash", "rasterizer_build_hash", "fontconfig_build_hash"):
        digest(value[k])


class _Frozen:
    __slots__ = ("_json",)
    domain = ""

    def _freeze(self, value):
        object.__setattr__(self, "_json", _bounded(value))

    def __setattr__(self, name, value):
        raise AttributeError("Segment contracts are immutable")

    def to_dict(self):
        return json.loads(self._json)

    def to_json(self):
        return self._json

    @property
    def content_hash(self):
        return content_hash(self.domain, self.to_dict())


def parse_contract_json(encoded):
    """Strict JSON ingress; duplicate keys and NaN are errors, not last-wins."""
    require(type(encoded) is str and len(encoded) <= MAX_JSON_BYTES, "payload_limit", "Oversized JSON")
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "duplicate", "Duplicate JSON object key")
            result[k] = v
        return result
    def invalid_number(value):
        raise DesignError("number", "Nonfinite JSON number: " + value)
    try:
        value = json.loads(encoded, object_pairs_hook=pairs, parse_constant=invalid_number)
        _bounded(value)
        return value
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, DesignError):
            raise
        raise DesignError("json", "Invalid JSON") from exc


class SegmentAudioProof(_Frozen):
    """Attested association, eligible only against an independent expected digest.

    The expected hash/producer must be retrieved from a trusted producer ledger
    in future stages. Supplying the payload's own computed hash is NOT trust.
    This class neither reads media nor cryptographically authenticates a producer.
    """
    __slots__ = ()
    domain = AUDIO_VERSION

    def __init__(self, value, *, expected_hash, expected_producer_id, allow_synthetic=False):
        _bounded(value)
        fields(value, ("schema_version", "receipt_id", "producer", "owner_id", "project_id", "page",
                       "baseline", "narration", "synthesis", "audio", "processing"))
        require(value["schema_version"] == AUDIO_VERSION, "version", "Unsupported audio provenance")
        _uuid(value["receipt_id"])
        for k in ("owner_id", "project_id"):
            _opaque(value[k])
        _page(value["page"])
        producer = value["producer"]
        fields(producer, ("id", "build_hash", "attestation_kind"))
        _opaque(producer["id"])
        digest(producer["build_hash"])
        require(producer["attestation_kind"] in ("producer_receipt.v1", "synthetic_engineering_attestation.v1"),
                "provenance", "No legacy/path-only audio certification")
        require(producer["attestation_kind"] != "synthetic_engineering_attestation.v1" or allow_synthetic is True,
                "provenance", "Synthetic engineering proof requires explicit test opt-in")
        fields(value["baseline"], ("job_id", "run_id", "manifest_hash", "page_order", "cues_hash"))
        for k in ("job_id", "run_id"):
            _opaque(value["baseline"][k])
        digest(value["baseline"]["manifest_hash"])
        if value["baseline"]["cues_hash"] is not None:
            digest(value["baseline"]["cues_hash"])
        index(value["baseline"]["page_order"])
        narration = value["narration"]
        fields(narration, ("protected_text", "protected_hash", "spoken_text", "spoken_hash", "language"))
        for k in ("protected", "spoken"):
            require(text_hash(narration[k + "_text"]) == narration[k + "_hash"], "narration", "Text association hash mismatch")
        require(type(narration["language"]) is str and re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", narration["language"]) is not None,
                "language", "Concrete language tag required, never auto/unknown")
        require(narration["language"].split("-")[0] not in ("xx", "zz"), "language", "Placeholder language is not resolved")
        synthesis = value["synthesis"]
        fields(synthesis, ("provider", "voice_id", "voice_kind", "fallback", "settings", "dependencies", "snapshot_tts_settings_hash"))
        for k in ("provider", "voice_id"):
            _opaque(synthesis[k])
            require(synthesis[k].lower() not in ("auto", "unknown", "unspecified", "default", "cached", "none", "silent", "silence", "placeholder"),
                    "provenance", "Concrete actual provider/voice required")
        require(synthesis["fallback"] == "none", "provenance", "Fallback audio is ineligible in v1")
        # Explicit supported control semantics. Nondefault speed/pitch/gain have
        # no established producer contract in this stage, even if legacy metadata hashes them.
        fields(synthesis["settings"], ("schema_version", "speed", "pitch", "gain_db"))
        require(synthesis["settings"]["schema_version"] == "neutral-audio-settings.v1", "version", "Unsupported audio settings")
        for key, expected in (("speed", 1), ("pitch", 1), ("gain_db", 0)):
            require(type(synthesis["settings"][key]) in (int, float) and synthesis["settings"][key] == expected,
                    "audio_settings", "Only attested neutral controls are supported")
        digest(synthesis["snapshot_tts_settings_hash"])
        deps = synthesis["dependencies"]
        fields(deps, ("model_hash", "config_hash", "preparation_hash", "pronunciation_hash", "sample"))
        for k in ("model_hash", "config_hash", "preparation_hash", "pronunciation_hash"):
            digest(deps[k])
        require(synthesis["voice_kind"] in ("built_in", "cloned"), "provenance", "Explicit voice dependency kind required")
        require((deps["sample"] is None) == (synthesis["voice_kind"] == "built_in"),
                "provenance", "Cloned voices require an attested sample identity; built-in voices explicitly have none")
        if deps["sample"] is not None:
            _asset(deps["sample"], ("wav",), LIMITS["max_audio_bytes"])
        audio = value["audio"]
        fields(audio, ("asset", "codec", "profile", "sample_rate", "channels", "duration_ms",
                       "audible_start_ms", "audible_end_ms", "packet_payload_hash"))
        _asset(audio["asset"], ("m4a",), LIMITS["max_audio_bytes"])
        require(audio["codec"] == "aac" and audio["profile"] == "LC", "audio_format", "AAC-LC required for no-reprocessing MP4 reuse")
        _integer(audio["sample_rate"], 8000, 96000)
        require(audio["sample_rate"] in (16000, 22050, 24000, 32000, 44100, 48000), "audio_format", "Unsupported sample rate")
        _integer(audio["channels"], 1, 2)
        for k in ("duration_ms", "audible_start_ms", "audible_end_ms"):
            _integer(audio[k], 0, MAX_DURATION_MS)
        require(0 <= audio["audible_start_ms"] < audio["audible_end_ms"] <= audio["duration_ms"],
                "timing", "A measured nonempty audible window is required")
        digest(audio["packet_payload_hash"])
        processing = value["processing"]
        fields(processing, ("schema_version", "input_asset", "operations", "measurement_version"))
        require(processing["schema_version"] == "audio-postprocessing.v1", "version", "Unsupported processing provenance")
        _asset(processing["input_asset"], ("wav", "m4a"), LIMITS["max_audio_bytes"])
        require(type(processing["operations"]) is list and 1 <= len(processing["operations"]) <= 8, "processing", "Explicit processing history required")
        for operation in processing["operations"]:
            fields(operation, ("operation", "implementation_hash", "settings_hash", "input_hash", "output_hash"))
            require(operation["operation"] in ("identity.v1", "resample_aac_encode.v1", "aac_encode.v1"), "processing", "Unsupported processing operation")
            for k in ("implementation_hash", "settings_hash", "input_hash", "output_hash"):
                digest(operation[k])
        chain = processing["input_asset"]["sha256"]
        for operation in processing["operations"]:
            require(operation["input_hash"] == chain, "processing", "Broken processing chain")
            if operation["operation"] == "identity.v1":
                require(operation["output_hash"] == chain, "processing", "Identity cannot change bytes")
            chain = operation["output_hash"]
        require(chain == audio["asset"]["sha256"], "processing", "Processed output differs from attested audio")
        _opaque(processing["measurement_version"])
        digest(expected_hash)
        _opaque(expected_producer_id)
        require(value["producer"]["id"] == expected_producer_id and content_hash(AUDIO_VERSION, value) == expected_hash,
                "audio_trust", "Audio proof does not match independent producer trust anchor")
        self._freeze(value)


def snapshot_page(snapshot, page_key):
    require(isinstance(snapshot, FrozenRenderSnapshot), "snapshot", "Validated Phase 3A snapshot required")
    _opaque(page_key)
    raw = snapshot.to_dict()
    matching = [p for p in raw["state"]["pages"] if p["page_key"] == page_key]
    require(len(matching) == 1, "identity", "Exactly one frozen page required")
    return matching[0]


def describe_scene(snapshot, page_key):
    require(isinstance(snapshot, FrozenRenderSnapshot), "snapshot", "Validated Phase 3A snapshot required")
    try:
        return _describe_scene(snapshot, page_key)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise DesignError("scene", "Malformed frozen scene descriptor") from exc


def _describe_scene(snapshot, page_key):
    """Structural descriptor only; asset bytes/approval revocation are later gates.

    The caller must obtain the snapshot via the existing validated server read.
    All locators in this descriptor are content identities, never paths/URLs.
    """
    raw = snapshot.to_dict()
    page = snapshot_page(snapshot, page_key)
    pos = raw["state"]["pages"].index(page)
    scene, stamp = raw["scenes"][pos], raw["state"]["source_stamps"][pos]
    original = scene["original"]["assets"].get("original")
    if original is not None:
        _asset(original, ("png", "jpg", "jpeg", "webp"), 8 * 1024 * 1024)
    design = scene["design"]
    metadata = None
    if design is not None:
        require(scene["type"] == "completed_generated" and scene["display_overlay"] == "none"
                and design["contains_text"] is True, "scene", "Only completed text-owning designs supported")
        _uuid(design["candidate_id"])
        require(design["source_hash"] == stamp["source_hash"] and design["source_revision"] == stamp["source_revision"],
                "scene", "Design/source stamp mismatch")
        selection = raw["state"]["selections"][pos]
        require(selection["candidate_id"] == design["candidate_id"] and selection["version"] == design["selection_version"]
                and selection["state"] == "selected", "scene", "Selected design mismatch")
        base = SlideSourceSnapshot(design["grounding"]["base_snapshot"])
        plan = VisualPlan(design["plan"])
        require(base.content_hash == stamp["source_hash"] and base.identity == {
            "project_id": raw["project_id"], **page_identity(page), "source_revision": stamp["source_revision"]}
            and base.to_dict()["original_display_text"] == page["original_text"]
            and base.to_dict()["protected_narration"] == page["narration_text"], "scene", "Grounding base differs from frozen authored source")
        preview_metadata = design["preview_metadata"]
        require(plan.content_hash == design["plan_hash"] == preview_metadata["plan_hash"]
                and design["design_source_hash"] == plan.to_dict()["source_hash"] == preview_metadata["source_hash"]
                and preview_metadata["status"] == "valid" and preview_metadata["contains_text"] is True,
                "scene", "Frozen plan/preview metadata mismatch")
        confirmation = design["confirmation"]
        if design["proposal"] is None:
            require(confirmation is None, "approval", "Unexpected confirmation")
        else:
            require(type(confirmation) is dict and confirmation.get("action") == "confirm"
                    and confirmation.get("owner_id") == raw["owner_id"]
                    and confirmation.get("source_hash") == stamp["source_hash"], "approval", "Missing frozen owner confirmation")
            _uuid(confirmation["proposal_id"])
            from .grounding import VisualProposal
            require(VisualProposal(design["proposal"]).content_hash == confirmation["proposal_hash"],
                    "approval", "Proposal/confirmation identity mismatch")
        metadata = {"candidate_id": design["candidate_id"], "selection_version": design["selection_version"],
                    "proposal_id": confirmation["proposal_id"] if confirmation else None,
                    "proposal_hash": confirmation["proposal_hash"] if confirmation else None,
                    "confirmation_hash": content_hash("visual-segment-confirmation.v1", confirmation) if confirmation else None,
                    "grounded_design_hash": design["design_source_hash"], "plan_hash": design["plan_hash"],
                    "preview_hash": design["preview_hash"], "plan_schema": design["plan"]["schema_version"],
                    "preview_schema": design["preview_metadata"]["schema_version"],
                    "template_version": design["plan"]["template_version"],
                    "renderer_version": design["preview_metadata"]["renderer_version"]}
        for k in ("grounded_design_hash", "plan_hash", "preview_hash"):
            digest(metadata[k])
        from .schemas import PLAN_VERSION, PREVIEW_VERSION, TEMPLATE_VERSION, RENDERER_VERSION
        require((metadata["plan_schema"], metadata["preview_schema"], metadata["template_version"], metadata["renderer_version"]) ==
                (PLAN_VERSION, PREVIEW_VERSION, TEMPLATE_VERSION, RENDERER_VERSION), "version", "Unsupported design versions")
        asset_reference(design["asset"])
    else:
        require(scene["type"] == "original" and original is not None, "scene", "Pinned original raster required; overlay modes deferred")
        require(raw["state"]["selections"][pos]["state"] == "original", "scene", "Unexpected selected original")
    return {"schema_version": SCENE_VERSION, "page": page_identity(page),
            "kind": "completed_generated.v1" if design else "pinned_original_raster.v1",
            "text_owner": "completed_design" if design else "original_raster", "display_overlay": "none",
            "contains_text": True if design else None, "narration_enabled": True, "subtitles_enabled": True,
            "source_hash": stamp["source_hash"], "source_revision": stamp["source_revision"], "design": metadata,
            "asset": deepcopy(design["asset"] if design else original), "fonts": deepcopy(raw["fonts"] if design else {}),
            "original": {"page": page_identity(page), "source_template_index": page["source_slide_index"],
                         "asset": deepcopy(original), "recovery_policy": "explicit_new_request.v1", "missing_policy": "fail"}}


def _captions(value, page, proof):
    fields(value, ("page", "authored_chunks", "cues", "cues_hash", "policy", "provenance", "audio_duration_ms",
                   "hold_duration_ms", "target_duration_ms", "time_base", "frozen_absolute"))
    require(_same(value["page"], page_identity(page)), "captions", "Caption page mapping mismatch")
    require(_same(value["authored_chunks"], page["subtitle_chunks"]) and 1 <= len(value["authored_chunks"]) <= 128,
            "captions", "Exact nonempty authored subtitle chunks required")
    for text in value["authored_chunks"]:
        _string(text, 4000)
    require(type(value["cues"]) is list and len(value["cues"]) == len(value["authored_chunks"]), "captions", "One cue per authored chunk required")
    audio = proof["audio"]
    previous = audio["audible_start_ms"]
    for i, cue in enumerate(value["cues"]):
        fields(cue, ("chunk_index", "start_ms", "end_ms", "text"))
        _integer(cue["chunk_index"], 0, 127)
        _integer(cue["start_ms"], 0, MAX_DURATION_MS)
        _integer(cue["end_ms"], 0, MAX_DURATION_MS)
        require(cue["chunk_index"] == i and cue["text"] == value["authored_chunks"][i]
                and previous <= cue["start_ms"] < cue["end_ms"] <= audio["audible_end_ms"],
                "captions", "Cue text/order/audible interval mismatch")
        previous = cue["end_ms"]
    require(value["cues_hash"] == content_hash(CUES_VERSION, {"page": value["page"], "cues": value["cues"]}),
            "captions", "Cue identity mismatch")
    require(value["policy"] in ("frozen_baseline.v1", "result_allocator.v1") and value["time_base"] == "milliseconds.v1",
            "captions", "Unsupported cue policy/time base")
    fields(value["provenance"], ("baseline_job_id", "baseline_run_id", "producer_receipt_id", "allocator_version"))
    require(value["provenance"]["baseline_job_id"] == proof["baseline"]["job_id"]
            and value["provenance"]["baseline_run_id"] == proof["baseline"]["run_id"]
            and value["provenance"]["producer_receipt_id"] == proof["receipt_id"], "captions", "Cue provenance mismatch")
    require(value["provenance"]["allocator_version"] == (None if value["policy"] == "frozen_baseline.v1" else "allocate_chunk_timings.v1"),
            "captions", "Allocator provenance mismatch")
    for k in ("audio_duration_ms", "hold_duration_ms", "target_duration_ms"):
        _integer(value[k], 0, MAX_DURATION_MS)
    require(value["audio_duration_ms"] == audio["duration_ms"]
            and value["target_duration_ms"] == value["audio_duration_ms"] + value["hold_duration_ms"], "timing", "Inconsistent audio/hold/target durations")
    expected_absolute = {k: page[k] for k in ("start_seconds", "end_seconds", "duration_seconds", "chunk_timeline")}
    require(_same(value["frozen_absolute"], expected_absolute), "captions", "Frozen absolute timing must be retained separately, verbatim")
    if value["policy"] == "frozen_baseline.v1":
        require(value["cues_hash"] == proof["baseline"]["cues_hash"], "captions", "Baseline cues differ from independent producer association")
        timeline = page["chunk_timeline"]
        require(len(timeline) == len(value["cues"]) and page["start_seconds"] is not None, "captions", "Baseline cue timeline unavailable")
        for frozen, cue in zip(timeline, value["cues"]):
            require(frozen["text"] == cue["text"], "captions", "Baseline caption text mismatch")
            for name in ("start", "end"):
                local_ms = (frozen[name] - page["start_seconds"]) * 1000
                require(abs(local_ms - cue[name + "_ms"]) < 0.001, "captions", "Local/absolute cue mismatch")


class SegmentRequest(_Frozen):
    __slots__ = ()
    domain = REQUEST_VERSION

    def __init__(self, value, *, snapshot, snapshot_id, expected_snapshot_hash, audio_proof, supported_runtime_hashes):
        _bounded(value)
        fields(value, ("schema_version", "snapshot", "page", "scene", "narration", "audio_mode", "audio_proof",
                       "subtitles", "recipe", "authorization_policy"))
        require(value["schema_version"] == REQUEST_VERSION, "version", "Unsupported segment request")
        _uuid(snapshot_id)
        digest(expected_snapshot_hash)
        require(isinstance(snapshot, FrozenRenderSnapshot) and snapshot.content_hash == expected_snapshot_hash,
                "snapshot", "Snapshot differs from independent server identity")
        raw = snapshot.to_dict()
        expected = {"owner_id": raw["owner_id"], "project_id": raw["project_id"], "snapshot_id": snapshot_id,
                    "snapshot_hash": expected_snapshot_hash, "authored_revision_hash": raw["revision_hash"]}
        require(_same(value["snapshot"], expected), "snapshot", "Snapshot/revision/owner/project binding mismatch")
        _page(value["page"], ordered=True)
        page = snapshot_page(snapshot, value["page"]["page_key"])
        require(_same(value["page"], {**page_identity(page), "order": page["order"]}), "identity", "Frozen page order/source/split mismatch")
        require(_same(value["scene"], describe_scene(snapshot, page["page_key"])), "scene", "Scene differs from frozen server descriptor")
        require(isinstance(audio_proof, SegmentAudioProof) and _same(value["audio_proof"], audio_proof.to_dict()),
                "audio_trust", "An independently trusted audio proof is required")
        proof = audio_proof.to_dict()
        require(proof["owner_id"] == raw["owner_id"] and proof["project_id"] == raw["project_id"]
                and _same(proof["page"], page_identity(page)), "audio_scope", "Audio producer page/project/owner mismatch")
        require(proof["narration"]["protected_text"] == page["narration_text"], "narration", "Protected narration differs from frozen page")
        require(_same(value["narration"], {**proof["narration"], "audio_proof_hash": audio_proof.content_hash}), "narration", "Narration association mismatch")
        require(value["audio_mode"] == "reuse_verified_audio.v1", "audio_mode", "Only verified reuse is supported; no synthesis fallback")
        require(value["authorization_policy"] == "live_authorization_revocation_required.v1", "authorization", "Live gates cannot be disabled by the request")
        # Voice/TTS in v1 are not a complete execution recipe. Where specified,
        # they must nevertheless agree with actual attested audio dependencies.
        voice = raw["state"]["voice"]
        if voice is not None:
            require(voice["provider"] == proof["synthesis"]["provider"] and voice["voice_id"] == proof["synthesis"]["voice_id"]
                    and voice["language"] == proof["narration"]["language"] and type(voice["speed"]) in (int, float) and type(voice["pitch"]) in (int, float)
                    and voice["speed"] == 1 and voice["pitch"] == 1,
                    "voice", "Snapshot voice is not concretely bound to the producer")
        settings = raw["state"]["tts_settings"]
        require(type(settings.get("speech_speed", 1)) in (int, float) and type(settings.get("volume_gain_db", 0)) in (int, float)
                and settings.get("speech_speed", 1) == 1 and settings.get("volume_gain_db", 0) == 0,
                "audio_settings", "Unsupported legacy synthesis controls")
        require(content_hash("visual-segment-snapshot-tts.v1", settings) == proof["synthesis"]["snapshot_tts_settings_hash"],
                "audio_settings", "Snapshot TTS settings need an exact producer association")
        _captions(value["subtitles"], page, proof)
        recipe = value["recipe"]
        fields(recipe, (*RECIPE_OPTIONS, "runtime", "fonts_hash", "limits"))
        for k, v in RECIPE_OPTIONS.items():
            require(type(recipe[k]) is type(v) and recipe[k] == v, "recipe", "Unsupported codec/options: " + k)
        require(recipe["limits"] == LIMITS and all(type(recipe["limits"][k]) is int for k in LIMITS), "limits", "Unsupported execution limit profile")
        _runtime(recipe["runtime"])
        require(type(supported_runtime_hashes) in (set, frozenset) and 1 <= len(supported_runtime_hashes) <= 32,
                "runtime", "Independent bounded runtime allowlist required")
        for supported in supported_runtime_hashes:
            digest(supported)
        require(content_hash("visual-segment-runtime.v1", recipe["runtime"]) in supported_runtime_hashes,
                "runtime", "Runtime/build is not independently supported")
        require(recipe["fonts_hash"] == content_hash("visual-segment-fonts.v1", value["scene"]["fonts"]), "fonts", "Pinned font identity mismatch")
        self._freeze(value)

    @property
    def execution_hash(self):
        return self.content_hash


class SegmentReceipt(_Frozen):
    """Checks a claimed result's bindings, not the unprovided output file bytes.

    A future media validator must supply the independently verified frame ref,
    inspect/hash every output and enforce attempt fencing before persisting this.
    """
    __slots__ = ()
    domain = RECEIPT_VERSION

    def __init__(self, value, *, request, expected_frame_asset, expected_attempt_generation):
        _bounded(value)
        fields(value, ("schema_version", "execution_hash", "snapshot", "page", "scene_hash", "design", "inputs",
                       "producer", "recipe_hash", "runtime", "attempt_generation", "outputs", "streams", "acceptance", "publishing_allowed"))
        require(value["schema_version"] == RECEIPT_VERSION, "version", "Not a supported segment receipt; frame receipts are distinct")
        require(isinstance(request, SegmentRequest), "request", "Validated segment request required")
        req = request.to_dict()
        for k in ("snapshot", "page"):
            require(_same(value[k], req[k]), "receipt", "Receipt identity mismatch: " + k)
        require(value["execution_hash"] == request.execution_hash
                and value["scene_hash"] == content_hash(SCENE_VERSION, req["scene"])
                and _same(value["design"], {"source_hash": req["scene"]["source_hash"], "source_revision": req["scene"]["source_revision"],
                                       "selected": req["scene"]["design"]}), "receipt", "Execution/scene/design receipt mismatch")
        _asset(expected_frame_asset, ("png",), 8 * 1024 * 1024)
        require(_same(value["inputs"], {"frame_asset": expected_frame_asset, "audio_asset": req["audio_proof"]["audio"]["asset"],
                                    "audio_proof_hash": req["narration"]["audio_proof_hash"], "cues_hash": req["subtitles"]["cues_hash"]}),
                "receipt", "Input frame/audio/cue identity mismatch")
        proof = req["audio_proof"]
        require(_same(value["producer"], {"receipt_id": proof["receipt_id"], "producer_id": proof["producer"]["id"],
                                      "baseline": proof["baseline"]}), "receipt", "Producer receipt association mismatch")
        require(value["recipe_hash"] == content_hash("visual-segment-recipe.v1", req["recipe"])
                and _same(value["runtime"], req["recipe"]["runtime"]), "receipt", "Recipe/runtime mismatch")
        _integer(expected_attempt_generation, 1)
        _integer(value["attempt_generation"], 1)
        require(value["attempt_generation"] == expected_attempt_generation, "attempt", "Unexpected attempt generation")
        fields(value["outputs"], ("mp4", "srt", "vtt"))
        for role, maximum in (("mp4", LIMITS["max_mp4_bytes"]), ("srt", LIMITS["max_caption_bytes"]), ("vtt", LIMITS["max_caption_bytes"])):
            _asset(value["outputs"][role], (role,), maximum)
        require(len({a["sha256"] for a in value["outputs"].values()}) == 3, "receipt", "Output roles must have distinct byte identities")
        fields(value["streams"], ("video", "audio", "container_duration_ms", "caption_end_ms"))
        video, audio = value["streams"]["video"], value["streams"]["audio"]
        fields(video, ("codec", "width", "height", "fps", "pixel_format", "duration_ms", "frame_count"))
        require(all(type(video[k]) is int for k in ("width", "height", "fps", "frame_count"))
                and (video["codec"], video["width"], video["height"], video["fps"], video["pixel_format"]) == ("h264", 1920, 1080, 25, "yuv420p"),
                "streams", "Unsupported video stream")
        fields(audio, ("codec", "profile", "sample_rate", "channels", "duration_ms", "packet_payload_hash"))
        for k in ("codec", "profile", "sample_rate", "channels", "packet_payload_hash"):
            require(type(audio[k]) is type(proof["audio"][k]) and audio[k] == proof["audio"][k], "streams", "Audio was changed/reprocessed")
        tolerance = LIMITS["duration_tolerance_ms"]
        for duration, expected in ((video["duration_ms"], req["subtitles"]["target_duration_ms"]),
                                   (audio["duration_ms"], proof["audio"]["duration_ms"]),
                                   (value["streams"]["container_duration_ms"], req["subtitles"]["target_duration_ms"])):
            _integer(duration, 1, MAX_DURATION_MS)
            require(abs(duration - expected) <= tolerance, "timing", "Output duration exceeds recipe tolerance")
        _integer(video["frame_count"], 1, 3002)
        require(abs(video["frame_count"] * 40 - video["duration_ms"]) <= tolerance, "streams", "Frame count/duration mismatch")
        _integer(value["streams"]["caption_end_ms"], 1, MAX_DURATION_MS)
        require(value["streams"]["caption_end_ms"] == req["subtitles"]["cues"][-1]["end_ms"], "captions", "Output cue bounds differ from frozen cues")
        fields(value["acceptance"], ("status", "validator_version", "integrity", "warnings"))
        require(value["acceptance"]["status"] == "accepted_private.v1"
                and value["acceptance"]["validator_version"] == "visual-segment-validator.v1"
                and value["acceptance"]["integrity"] == "all_output_hashes_verified.v1", "acceptance", "Only validated private results may receive receipts")
        warnings = value["acceptance"]["warnings"]
        require(type(warnings) is list and len(warnings) <= 16, "acceptance", "Bounded warnings required")
        for warning in warnings:
            _opaque(warning)
        require(value["publishing_allowed"] is False, "publication", "Segment receipts never authorize publication")
        self._freeze(value)
