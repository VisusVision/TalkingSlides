"""Pure contract fixtures. No DB, media execution, producer service or network.

Can also run with --noconftest and plugin autoload disabled; see Phase 3B1 docs.
Synthetic receipt outputs below are CLAIMS for schema tests, not rendered media.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unicodedata
import wave

import pytest

from core.visual_design.schemas import DesignError, canonical_json, content_hash
from core.visual_design.render_snapshot import (FrozenRenderSnapshot, revision_hash,
    validate_frozen_design, require_rendered_revision, bytes_hash)
from core.visual_design.segment_contracts import (SegmentAudioProof, SegmentRequest, SegmentReceipt,
    REQUEST_VERSION, AUDIO_VERSION, RECEIPT_VERSION, CUES_VERSION, SCENE_VERSION,
    MAX_JSON_BYTES, describe_scene, page_identity, parse_contract_json, text_hash)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "visual_segments"
TRUST = json.loads((FIXTURES / "trust_anchors.json").read_text())


def load(name):
    return json.loads((FIXTURES / name).read_text())


def audio(i=0, value=None, **overrides):
    return SegmentAudioProof(value if value is not None else load(f"audio_proof_{i}.json"), **{
        "expected_hash": TRUST["audio_proof_hashes"][i], "expected_producer_id": TRUST["producer_id"],
        "allow_synthetic": True, **overrides})


def request(i=0, value=None, **overrides):
    return SegmentRequest(value if value is not None else load(f"request_{i}.json"), **{
        "snapshot": FrozenRenderSnapshot(load("snapshot.json")), "snapshot_id": TRUST["snapshot_id"],
        "expected_snapshot_hash": TRUST["snapshot_hash"], "audio_proof": audio(i),
        "supported_runtime_hashes": {TRUST["runtime_hash"]}, **overrides})


def receipt(i=0, value=None, **overrides):
    original = load(f"receipt_{i}.json")
    return SegmentReceipt(value if value is not None else original, **{
        "request": request(i), "expected_frame_asset": original["inputs"]["frame_asset"],
        "expected_attempt_generation": 1, **overrides})


def change(value, path, replacement):
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = replacement
    else:
        current[parts[-1]] = replacement
    return value


@pytest.mark.parametrize("i", range(3))
def test_golden_requests_proofs_receipts_and_unchanged_phase3a_hashes(i):
    frozen = FrozenRenderSnapshot(load("snapshot.json"))
    assert frozen.content_hash == TRUST["snapshot_hash"] == "sha256:e3029ba276ee6befc116c68ec263a8d0276aafd4db6f14e20e036b3ffe389dd2"
    assert frozen.to_dict()["revision_hash"] == TRUST["revision_hash"]
    assert frozen.to_dict()["render_config"]["purpose"] == "isolated_frame.v1"
    proof, req, result = audio(i), request(i), receipt(i)
    assert proof.content_hash == TRUST["audio_proof_hashes"][i]
    assert req.execution_hash == TRUST["execution_hashes"][i]
    assert result.content_hash == TRUST["receipt_hashes"][i]
    assert req.execution_hash == content_hash(REQUEST_VERSION, req.to_dict())
    assert req.to_dict()["narration"]["protected_text"] == load("snapshot.json")["state"]["pages"][i]["narration_text"]
    assert result.to_dict()["publishing_allowed"] is False
    for obj in (frozen, proof, req, result):
        copy = obj.to_dict(); copy.clear()
        assert obj.to_dict()
        with pytest.raises(AttributeError):
            obj._json = "edited"
    # JSON key order cannot affect canonical identities.
    value = dict(reversed(list(req.to_dict().items())))
    assert request(i, value).execution_hash == req.execution_hash
    assert parse_contract_json(req.to_json()) == req.to_dict()


def test_index_zero_splits_and_scene_audio_cue_mapping():
    mapping = load("mapping.json")
    for i in range(3):
        req = request(i).to_dict()
        page = req["page"]
        assert page["order"] == mapping["display_positions"][i]
        assert page["page_key"] == mapping["page_keys"][i]
        assert page["source_slide_index"] == mapping["source_slide_indices"][i]
        assert page["split_index"] == mapping["split_indices"][i]
        for actual in (req["scene"]["page"], req["scene"]["original"]["page"], req["audio_proof"]["page"], req["subtitles"]["page"]):
            assert actual == page_identity(page)
        assert req["scene"]["original"]["source_template_index"] == page["source_slide_index"]
    assert len(set(mapping["page_keys"])) == 3
    assert request(0).to_dict()["page"]["source_slide_index"] == request(2).to_dict()["page"]["source_slide_index"] == 0
    assert request(0).to_dict()["page"]["split_index"] != request(2).to_dict()["page"]["split_index"]


def test_reordering_retains_audio_source_association_and_baseline_order():
    raw = load("snapshot.json")
    for name in ("pages", "source_stamps", "selections"):
        raw["state"][name] = list(reversed(raw["state"][name]))
    raw["scenes"].reverse()
    for i, page in enumerate(raw["state"]["pages"]):
        page["order"] = i
    raw["revision_hash"] = revision_hash(raw)
    frozen = FrozenRenderSnapshot(raw)
    data = load("request_0.json")
    data["snapshot"].update(snapshot_hash=frozen.content_hash, authored_revision_hash=raw["revision_hash"])
    data["page"]["order"] = 2
    reordered = request(value=data, snapshot=frozen, expected_snapshot_hash=frozen.content_hash)
    assert reordered.to_dict()["page"]["order"] == 2
    assert reordered.to_dict()["audio_proof"]["baseline"]["page_order"] == 0
    assert reordered.to_dict()["audio_proof"] == audio().to_dict()
    assert reordered.execution_hash != request().execution_hash


@pytest.mark.parametrize("path", ["order", "source_slide_index", "split_index"])
@pytest.mark.parametrize("bad", [None, True, False, -1, 1.0, "0", 1_000_001])
def test_invalid_page_indices_fail(path, bad):
    with pytest.raises(DesignError):
        request(value=change(load("request_0.json"), "page." + path, bad))


def test_missing_duplicate_and_ambiguous_snapshot_mapping():
    for edit in (lambda r: r["state"]["pages"][0].pop("source_slide_index"),
                 lambda r: r["state"]["pages"][1].update(page_key=r["state"]["pages"][0]["page_key"]),
                 lambda r: r["state"]["pages"][1].update(order=0)):
        raw = load("snapshot.json"); edit(raw); raw["revision_hash"] = revision_hash(raw)
        with pytest.raises(DesignError):
            FrozenRenderSnapshot(raw)


@pytest.mark.parametrize("path,bad", [
    ("schema_version", "visual-render-snapshot.v1"), ("extra", "unknown"),
    ("snapshot.snapshot_hash", "sha256:" + "0" * 64), ("snapshot.authored_revision_hash", "sha256:" + "0" * 64),
    ("snapshot.snapshot_id", "wrong"), ("snapshot.owner_id", "other"), ("snapshot.project_id", "other"),
    ("page.page_key", "foreign-page"), ("page.source_slide_index", 2), ("page.split_index", 0),
    ("scene.original.source_template_index", 2), ("scene.page.source_slide_index", False),
    ("scene.display_overlay", "transcript"), ("scene.narration_enabled", False), ("scene.subtitles_enabled", False),
    ("scene.contains_text", False), ("scene.design.grounded_design_hash", "sha256:" + "0" * 64),
    ("scene.design.renderer_version", "unknown.v99"), ("scene.design.candidate_id", "00000000-0000-4000-8000-000000000999"),
    ("scene.asset.extension", "html"), ("scene.asset.url", "https://example.com/asset.svg"),
    ("scene.original.asset.path", "/tmp/original.png"), ("scene.fonts.regular.sha256", "sha256:" + "0" * 64),
    ("narration.protected_text", "Rewritten narration"), ("narration.audio_proof_hash", "sha256:" + "0" * 64),
    ("audio_mode", "synthesize_on_failure.v1"), ("authorization_policy", "trust_frozen_forever.v1"),
    ("audio_proof.audio.asset.sha256", "sha256:" + "0" * 64),
    ("recipe.purpose", "video_export"), ("recipe.fps", True), ("recipe.width", 1280), ("recipe.video_codec", "vp9"),
    ("recipe.audio_policy", "resample"), ("recipe.crf", 0), ("recipe.fonts_hash", "sha256:" + "0" * 64),
    ("recipe.runtime.build_id", "unknown-runtime"), ("recipe.runtime.command", "echo bad"),
    ("recipe.limits.max_duration_ms", 999999), ("job_status", "done"), ("attempt_generation", 2),
    ("lease_generation", 1), ("created_at", "2026-01-01"), ("recipe.filters", "drawtext=bad")])
def test_request_tampering_unknown_fields_and_forbidden_execution_inputs(path, bad):
    with pytest.raises(DesignError):
        request(value=change(load("request_0.json"), path, bad))


@pytest.mark.parametrize("path,bad", [
    ("schema_version", "legacy-audio.v1"), ("producer.attestation_kind", "hashed-existing-path.v1"),
    ("receipt_id", "not-a-uuid"), ("producer.id", "https://external.invalid/producer"),
    ("producer.build_hash", "sha256:bad"), ("baseline.manifest_hash", "sha256:bad"),
    ("baseline.job_id", "../another-job"), ("baseline.page_order", True), ("page.source_slide_index", False),
    ("synthesis.provider", "auto"), ("synthesis.provider", "cached"), ("synthesis.provider", "silent"),
    ("synthesis.voice_id", "unknown"), ("synthesis.fallback", "gtts"), ("narration.language", "auto"),
    ("narration.protected_hash", "sha256:" + "0" * 64), ("narration.spoken_hash", "sha256:" + "0" * 64),
    ("synthesis.settings.speed", 1.25), ("synthesis.settings.pitch", True), ("synthesis.settings.extra", 1),
    ("synthesis.dependencies.model_hash", None), ("synthesis.dependencies.preparation_hash", "missing"),
    ("synthesis.dependencies.sample", {"url": "https://example.com/voice.wav"}),
    ("audio.codec", "pcm_s16le"), ("audio.asset.extension", "wav"), ("audio.asset.size", 16 * 1024 * 1024 + 1),
    ("audio.asset.path", "/etc/passwd"), ("audio.duration_ms", float("inf")), ("audio.audible_end_ms", 99999),
    ("audio.audible_end_ms", 0), ("audio.sample_rate", True), ("audio.channels", 0),
    ("processing.operations.0.operation", "arbitrary_filter"), ("processing.operations.0.input_hash", "sha256:" + "0" * 64),
    ("processing.operations.0.output_hash", "sha256:" + "0" * 64), ("processing.operations", [])])
def test_bad_audio_is_ineligible_even_if_attestation_digest_is_self_consistent(path, bad):
    value = change(load("audio_proof_0.json"), path, bad)
    # Independently trusted fixtures can still contain invalid/unsupported data;
    # structural eligibility must fail BEFORE trusting even a matching digest.
    try:
        forged_hash = content_hash(AUDIO_VERSION, value)
    except ValueError:
        forged_hash = TRUST["audio_proof_hashes"][0]
    with pytest.raises(DesignError):
        audio(value=value, expected_hash=forged_hash)


def test_self_consistent_forged_proof_is_not_the_trusted_producer_receipt():
    value = load("audio_proof_0.json")
    value["audio"]["packet_payload_hash"] = "sha256:" + "a" * 64
    assert content_hash(AUDIO_VERSION, value) != TRUST["audio_proof_hashes"][0]
    with pytest.raises(DesignError, match="independent producer trust anchor"):
        audio(value=value)
    with pytest.raises(DesignError):
        audio(expected_producer_id="untrusted-producer")
    with pytest.raises(DesignError, match="test opt-in"):
        audio(allow_synthetic=False)
    with pytest.raises(TypeError):
        SegmentAudioProof(value)
    legacy = load("audio_proof_0.json"); legacy["synthesis"]["dependencies"].pop("model_hash")
    with pytest.raises(DesignError):
        audio(value=legacy)


@pytest.mark.parametrize("path,bad", [("page.page_key", "another-page"), ("page.source_slide_index", 2),
    ("page.split_index", 0), ("owner_id", "802"), ("project_id", "702"),
    ("narration.protected_text", "Different authored text"), ("synthesis.provider", "other-provider"),
    ("synthesis.voice_id", "different-voice"), ("narration.language", "tr"),
    ("synthesis.snapshot_tts_settings_hash", "sha256:" + "b" * 64)])
def test_independently_attested_but_incompatible_audio_cannot_narrate_page(path, bad):
    value = change(load("audio_proof_0.json"), path, bad)
    value["narration"]["protected_hash"] = text_hash(value["narration"]["protected_text"])
    digest = content_hash(AUDIO_VERSION, value)
    trusted_other = audio(value=value, expected_hash=digest)
    req = load("request_0.json"); req["audio_proof"] = value
    req["narration"] = {**value["narration"], "audio_proof_hash": digest}
    with pytest.raises(DesignError):
        request(value=req, audio_proof=trusted_other)


@pytest.mark.parametrize("path,bad", [
    ("subtitles.cues.0.start_ms", -1), ("subtitles.cues.0.start_ms", 0), ("subtitles.cues.0.end_ms", 2400),
    ("subtitles.cues.0.start_ms", 2180), ("subtitles.cues.0.chunk_index", True), ("subtitles.cues.0.text", "Changed caption"),
    ("subtitles.authored_chunks.0", "Changed caption"), ("subtitles.cues_hash", "sha256:" + "0" * 64),
    ("subtitles.page.split_index", 0), ("subtitles.hold_duration_ms", -1), ("subtitles.hold_duration_ms", float("nan")),
    ("subtitles.target_duration_ms", 120001), ("subtitles.audio_duration_ms", 2334), ("subtitles.time_base", "seconds"),
    ("subtitles.provenance.producer_receipt_id", "other"), ("subtitles.provenance.allocator_version", "invented"),
    ("subtitles.frozen_absolute.start_seconds", None), ("subtitles.frozen_absolute.chunk_timeline.0.text", "Changed"),
    ("subtitles.cues", []), ("subtitles.authored_chunks", []), ("subtitles.policy", "normalize_spoken_text")])
def test_caption_bounds_and_provenance_fail_closed(path, bad):
    with pytest.raises(DesignError):
        request(value=change(load("request_0.json"), path, bad))


def test_arbitrary_local_cue_hash_cannot_redefine_a_frozen_baseline():
    value = load("request_0.json")
    value["subtitles"]["cues"][0]["start_ms"] += 10
    value["subtitles"]["cues_hash"] = content_hash(CUES_VERSION, {k:value["subtitles"][k] for k in ("page", "cues")})
    with pytest.raises(DesignError, match="Baseline cues|Local/absolute"):
        request(value=value)


def test_dependency_hash_domains_and_execution_changes():
    initial = request(); value = initial.to_dict()
    # Hold changes execution/caption recipe, but never the existing audio proof.
    value["subtitles"]["hold_duration_ms"] += 40; value["subtitles"]["target_duration_ms"] += 40
    assert request(value=value).execution_hash != initial.execution_hash
    assert request(value=value).to_dict()["audio_proof"] == initial.to_dict()["audio_proof"]
    for path, new in [("synthesis.dependencies.preparation_hash", "sha256:" + "a" * 64),
                      ("synthesis.dependencies.model_hash", "sha256:" + "a" * 64),
                      ("audio.packet_payload_hash", "sha256:" + "a" * 64),
                      ("narration.spoken_text", "Plants need light & water to grow.")]:
        proof = change(load("audio_proof_0.json"), path, new)
        proof["narration"]["spoken_hash"] = text_hash(proof["narration"]["spoken_text"])
        new_hash = content_hash(AUDIO_VERSION, proof)
        other_audio = audio(value=proof, expected_hash=new_hash)
        value = initial.to_dict(); value["audio_proof"] = proof
        value["narration"] = {**proof["narration"], "audio_proof_hash": new_hash}
        assert request(value=value, audio_proof=other_audio).execution_hash != initial.execution_hash
    assert content_hash(AUDIO_VERSION, {}) != content_hash(REQUEST_VERSION, {}) != content_hash(RECEIPT_VERSION, {})
    exact = "Öğrenme & ışık: İ, ı, Ş, ç. العربية"
    assert text_hash(exact) != text_hash(unicodedata.normalize("NFD", exact))
    assert "Öğrenme" in canonical_json({"text": exact})


@pytest.mark.parametrize("path,bad", [
    ("schema_version", "visual-frame-receipt.v1"), ("execution_hash", "sha256:" + "0" * 64),
    ("snapshot.authored_revision_hash", "sha256:" + "0" * 64), ("page.order", False),
    ("scene_hash", "sha256:" + "0" * 64), ("design.source_hash", "sha256:" + "0" * 64),
    ("inputs.frame_asset.sha256", "sha256:" + "0" * 64), ("inputs.audio_asset.size", 1),
    ("inputs.cues_hash", "sha256:" + "0" * 64), ("producer.receipt_id", "another-receipt"),
    ("recipe_hash", "sha256:" + "0" * 64), ("runtime.build_id", "different-runtime"),
    ("attempt_generation", 0), ("attempt_generation", True), ("attempt_generation", 2),
    ("outputs.mp4.extension", "avi"), ("outputs.mp4.size", 32 * 1024 * 1024 + 1),
    ("outputs.srt.size", 128 * 1024 + 1), ("outputs.vtt.path", "../../public/x.vtt"),
    ("streams.video.width", 1280), ("streams.video.fps", 24), ("streams.video.pixel_format", "rgba"),
    ("streams.video.duration_ms", 3000), ("streams.audio.duration_ms", 3000), ("streams.video.frame_count", 0),
    ("streams.audio.packet_payload_hash", "sha256:" + "0" * 64), ("streams.audio.sample_rate", 48000),
    ("streams.container_duration_ms", -1), ("streams.caption_end_ms", 2500),
    ("acceptance.status", "published"), ("acceptance.integrity", "not_checked"),
    ("acceptance.validator_version", "unknown"), ("publishing_allowed", True), ("job_progress", 100)])
def test_segment_receipt_rejects_wrong_bindings_streams_and_publication(path, bad):
    with pytest.raises(DesignError):
        receipt(value=change(load("receipt_0.json"), path, bad))


def test_attempt_identity_does_not_change_request_and_receipts_are_not_frame_receipts():
    first = receipt(); later = first.to_dict(); later["attempt_generation"] = 2
    second = receipt(value=later, expected_attempt_generation=2)
    assert second.content_hash != first.content_hash
    assert second.to_dict()["execution_hash"] == first.to_dict()["execution_hash"] == request().execution_hash
    frame = {"schema_version":"visual-frame-receipt.v1", "snapshot_hash":TRUST["snapshot_hash"],
             "revision_hash":TRUST["revision_hash"], "page_key":"source-0.split-1",
             "frame_asset":first.to_dict()["inputs"]["frame_asset"], "width":1920, "height":1080, "publishing_allowed":False}
    frozen = FrozenRenderSnapshot(load("snapshot.json"))
    require_rendered_revision(frozen, frame, TRUST["revision_hash"])
    with pytest.raises(DesignError):
        receipt(value=frame)
    with pytest.raises(DesignError):
        require_rendered_revision(frozen, first.to_dict(), TRUST["revision_hash"])


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[', '{"x":"\\ud800"}', '['*1000+']'*1000])
def test_strict_json_ingress(raw):
    with pytest.raises(DesignError):
        parse_contract_json(raw)


def test_payload_limits_and_non_json_objects():
    for extra in ["x" * 100001, [0]*257, {"x":object()}, {"x":float("inf")}, {"x":10**1000}, {"x":{1:2}}, {"x":"\ud800"}]:
        with pytest.raises(DesignError):
            request(value={**load("request_0.json"), "oversized":extra})
    with pytest.raises(DesignError):
        parse_contract_json(" "*(MAX_JSON_BYTES+1))
    # Within individual bounds, but over total byte budget.
    with pytest.raises(DesignError):
        request(value={**load("request_0.json"), "extra":["x"*100000 for _ in range(11)]})


def test_actual_speech_fixture_bytes_and_pcm_properties_without_media_tools():
    metadata = load("producer_metadata.json")
    assert (FIXTURES/"transcript.txt").read_text().removesuffix("\n") == metadata["text"] == audio().to_dict()["narration"]["protected_text"]
    for ext in ("wav", "m4a"):
        data = (FIXTURES/f"narration.{ext}").read_bytes()
        assert bytes_hash(data) == metadata[ext]["sha256"] and len(data) == metadata[ext]["size"]
    assert metadata["m4a"] == audio().to_dict()["audio"]["asset"]
    # This verifies PCM properties/energy, not linguistic truth. The association
    # comes from the documented offline synthetic producer + independent anchor.
    with wave.open(str(FIXTURES/"narration.wav")) as speech:
        assert speech.getframerate() == metadata["wav"]["sample_rate"] == 16000
        assert speech.getnchannels() == 1 and speech.getsampwidth() == 2
        assert speech.getnframes() == metadata["wav"]["frames"]
        frames = speech.readframes(speech.getnframes())
    samples = struct.unpack('<'+'h'*(len(frames)//2),frames)
    active = [i for i,x in enumerate(samples) if abs(x)>100]
    assert len(set(samples)) > 1000 and len(active) > 10000
    assert active[0]*1000//16000 == metadata["audible_start_ms"]
    assert (active[-1]+1)*1000//16000 == metadata["audible_end_ms"]
    assert b'ftyp' in (FIXTURES/"narration.m4a").read_bytes()[:32]


def test_contract_import_and_fixtures_work_without_application_or_execution_modules():
    code = '''
import importlib.abc, json, pathlib, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'django','celery','requests','httpx','socket','subprocess','worker'}:
            raise AssertionError('No application/media/network import: '+fullname)
sys.meta_path.insert(0, Block())
from core.visual_design.segment_contracts import SegmentAudioProof, SegmentRequest, SegmentReceipt
from core.visual_design.render_snapshot import FrozenRenderSnapshot
root = pathlib.Path(sys.argv[1])
def load(n): return json.loads((root/n).read_text())
t = load('trust_anchors.json')
p = SegmentAudioProof(load('audio_proof_0.json'), expected_hash=t['audio_proof_hashes'][0], expected_producer_id=t['producer_id'], allow_synthetic=True)
r = SegmentRequest(load('request_0.json'), snapshot=FrozenRenderSnapshot(load('snapshot.json')),snapshot_id=t['snapshot_id'], expected_snapshot_hash=t['snapshot_hash'], audio_proof=p, supported_runtime_hashes={t['runtime_hash']})
s = load('receipt_0.json')
SegmentReceipt(s, request=r, expected_frame_asset=s['inputs']['frame_asset'], expected_attempt_generation=1)
print(r.execution_hash)
'''
    result = subprocess.run([sys.executable, '-c', code, str(FIXTURES)], capture_output=True, text=True, timeout=10,
                            env={**os.environ, 'PYTHONPATH':str(FIXTURES.parents[2]/'services/api')})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == TRUST["execution_hashes"][0]


def rebuild_snapshot(raw):
    raw["revision_hash"] = revision_hash(raw)
    return FrozenRenderSnapshot(raw)


def rebind_request(value, frozen):
    value["snapshot"].update(snapshot_hash=frozen.content_hash, authored_revision_hash=frozen.to_dict()["revision_hash"])
    value["scene"] = describe_scene(frozen, value["page"]["page_key"])
    value["recipe"]["fonts_hash"] = content_hash("visual-segment-fonts.v1", value["scene"]["fonts"])
    return value


def test_pinned_original_and_explicit_restore_have_distinct_request_identity():
    raw = load("snapshot.json")
    scene = raw["scenes"][0]
    scene.update(type="original", display_overlay="existing_scene_policy", design=None)
    raw["state"]["selections"][0].update(state="original", candidate_id=None, version=2)
    frozen = rebuild_snapshot(raw)
    value = rebind_request(load("request_0.json"), frozen)
    restored = request(value=value, snapshot=frozen, expected_snapshot_hash=frozen.content_hash)
    assert restored.to_dict()["scene"]["kind"] == "pinned_original_raster.v1"
    assert restored.to_dict()["scene"]["contains_text"] is None  # no invented OCR assertion
    assert restored.to_dict()["scene"]["fonts"] == {}
    assert restored.to_dict()["scene"]["original"]["recovery_policy"] == "explicit_new_request.v1"
    assert restored.execution_hash != request().execution_hash
    assert restored.to_dict()["audio_proof"] == audio().to_dict()
    # An invalid selected visual cannot silently become the original request.
    broken = load("request_0.json"); broken["scene"]["asset"] = value["scene"]["asset"]
    with pytest.raises(DesignError): request(value=broken)


@pytest.mark.parametrize("mode", ["whiteboard", "custom", "source_background"])
def test_original_overlay_modes_and_missing_raster_are_deferred(mode):
    raw = load("snapshot.json"); raw["scenes"][0].update(type=mode, display_overlay="existing_scene_policy", design=None)
    raw["scenes"][0]["original"]["mode"] = mode
    raw["state"]["selections"][0].update(state="original", candidate_id=None)
    with pytest.raises(DesignError, match="Pinned original"):
        describe_scene(rebuild_snapshot(raw), raw["state"]["pages"][0]["page_key"])
    raw["scenes"][0].update(type="original"); raw["scenes"][0]["original"].update(mode="original", assets={})
    with pytest.raises(DesignError): describe_scene(rebuild_snapshot(raw), raw["state"]["pages"][0]["page_key"])


def test_visual_font_and_supported_runtime_changes_change_execution_without_rewriting_audio():
    original = request()
    raw = load("snapshot.json"); raw["fonts"]["regular"]["sha256"] = "sha256:"+"a"*64
    frozen = rebuild_snapshot(raw); value = rebind_request(original.to_dict(), frozen)
    updated = request(value=value, snapshot=frozen, expected_snapshot_hash=frozen.content_hash)
    assert updated.execution_hash != original.execution_hash
    assert updated.to_dict()["audio_proof"] == original.to_dict()["audio_proof"]
    value = original.to_dict(); value["recipe"]["runtime"]["ffmpeg_binary_hash"] = "sha256:"+"a"*64
    with pytest.raises(DesignError, match="independently supported"):
        request(value=value)
    allowed = {content_hash("visual-segment-runtime.v1", value["recipe"]["runtime"])}
    assert request(value=value, supported_runtime_hashes=allowed).execution_hash != original.execution_hash


def test_independent_snapshot_anchor_cannot_be_replaced_with_self_consistent_forgery():
    raw = load("snapshot.json"); raw["state"]["project_fields"]["title"] = "Forged revision"
    forged = rebuild_snapshot(raw)
    value = rebind_request(load("request_0.json"), forged)
    with pytest.raises(DesignError, match="independent server identity"):
        request(value=value, snapshot=forged)  # expected hash stays independently pinned
    assert request().to_dict()["snapshot"]["snapshot_hash"] == TRUST["snapshot_hash"]


@pytest.mark.parametrize("path,bad", [("design.plan_hash", "sha256:"+"a"*64),
    ("design.grounding.base_snapshot.source_slide_index", 2), ("design.plan.schema_version", "unknown"),
    ("design.preview_metadata.renderer_version", "unknown.v1"), ("design.preview_metadata.status", "invalid"),
    ("design.selection_version", 2), ("design.plan", None)])
def test_frozen_scene_incompatibilities_fail_even_under_an_explicitly_supplied_new_snapshot_anchor(path,bad):
    raw = load("snapshot.json"); change(raw["scenes"][0], path, bad)
    frozen = rebuild_snapshot(raw)
    with pytest.raises(DesignError):
        describe_scene(frozen, raw["state"]["pages"][0]["page_key"])


def test_local_allocator_policy_is_explicit_and_absolute_inputs_remain_frozen():
    value = load("request_0.json")
    value["subtitles"]["policy"] = "result_allocator.v1"
    value["subtitles"]["provenance"]["allocator_version"] = "allocate_chunk_timings.v1"
    value["subtitles"]["cues"][0]["start_ms"] += 20
    value["subtitles"]["cues_hash"] = content_hash(CUES_VERSION,{k:value["subtitles"][k] for k in ("page","cues")})
    assert request(value=value).execution_hash != request().execution_hash
    assert request(value=value).to_dict()["subtitles"]["frozen_absolute"] == request().to_dict()["subtitles"]["frozen_absolute"]


def test_page_captions_can_never_be_replaced_by_effective_spoken_preprocessing():
    proof = load("audio_proof_0.json")
    proof["narration"]["spoken_text"] = "Plants need light and water to grow!"
    proof["narration"]["spoken_hash"] = text_hash(proof["narration"]["spoken_text"])
    expected = content_hash(AUDIO_VERSION,proof)
    trusted = audio(value=proof,expected_hash=expected)
    value = load("request_0.json"); value.update(audio_proof=proof,narration={**proof["narration"],"audio_proof_hash":expected})
    assert request(value=value,audio_proof=trusted).to_dict()["subtitles"]["authored_chunks"] == [proof["narration"]["protected_text"]]
    value["subtitles"]["authored_chunks"] = [proof["narration"]["spoken_text"]]
    with pytest.raises(DesignError): request(value=value,audio_proof=trusted)


def test_receipt_wrong_attempt_expected_frame_and_duplicate_outputs():
    with pytest.raises(DesignError): receipt(expected_attempt_generation=2)
    with pytest.raises(DesignError): receipt(expected_frame_asset={"sha256":"sha256:"+"a"*64,"size":1,"extension":"png"})
    value = load("receipt_0.json"); value["outputs"]["vtt"]["sha256"] = value["outputs"]["srt"]["sha256"]
    with pytest.raises(DesignError): receipt(value=value)


def test_audio_byte_changes_require_new_processing_proof_and_execution_identity():
    value = load("audio_proof_0.json")
    value["audio"]["asset"]["sha256"] = "sha256:"+"a"*64
    value["processing"]["operations"][-1]["output_hash"] = value["audio"]["asset"]["sha256"]
    proof_hash = content_hash(AUDIO_VERSION,value)
    with pytest.raises(DesignError): audio(value=value)
    new_proof = audio(value=value,expected_hash=proof_hash)  # a separately trusted synthetic scenario
    req = load("request_0.json"); req["audio_proof"] = value; req["narration"]["audio_proof_hash"] = proof_hash
    assert request(value=req,audio_proof=new_proof).execution_hash != request().execution_hash


@pytest.mark.parametrize("language,text", [("tr","Bitkiler ışık & su ister: İ, ı, Ş, ç."), ("ar","تحتاج النباتات إلى الضوء والماء.")])
def test_exact_multilingual_narration_is_preserved_without_rendering_or_synthesis(language,text):
    # Contract-only synthetic attestation. This does NOT claim the English audio
    # fixture really speaks Turkish/Arabic; independently expected metadata is
    # deliberately supplied for this isolated string/identity test.
    raw = load("snapshot.json")
    p = raw["state"]["pages"][0]; p["narration_text"] = text; p["subtitle_chunks"] = [text]; p["chunk_timeline"][0]["text"] = text
    raw["state"]["voice"]["language"] = language
    source = raw["scenes"][0]
    source.update(type="original",display_overlay="existing_scene_policy",design=None)
    raw["state"]["selections"][0].update(state="original",candidate_id=None)
    frozen = rebuild_snapshot(raw)
    proof = load("audio_proof_0.json"); proof["narration"].update(language=language,protected_text=text,spoken_text=text,protected_hash=text_hash(text),spoken_hash=text_hash(text))
    req = rebind_request(load("request_0.json"),frozen)
    captions = req["subtitles"]
    captions["authored_chunks"] = [text]; captions["cues"][0]["text"] = text
    captions["cues_hash"] = content_hash(CUES_VERSION,{k:captions[k] for k in ("page","cues")})
    captions["frozen_absolute"]["chunk_timeline"][0]["text"] = text
    proof["baseline"]["cues_hash"] = captions["cues_hash"]
    proof_hash = content_hash(AUDIO_VERSION,proof); attested = audio(value=proof,expected_hash=proof_hash)
    req.update(audio_proof=proof,narration={**proof["narration"],"audio_proof_hash":proof_hash})
    result = request(value=req,snapshot=frozen,expected_snapshot_hash=frozen.content_hash,audio_proof=attested)
    assert result.to_dict()["narration"]["protected_text"] == text
    assert text in result.to_json()


@pytest.mark.parametrize("overlap", [False, True])
def test_multiple_authored_chunks_preserve_order_and_reject_overlapping_cues(overlap):
    raw = load("snapshot.json"); p = raw["state"]["pages"][0]
    chunks = ["Plants need light", "and water to grow."]
    p["subtitle_chunks"] = chunks
    cues = [{"chunk_index":i,"start_ms":start,"end_ms":end,"text":text}
            for i,(start,end,text) in enumerate([(243,1100,chunks[0]),(1100,2180,chunks[1])])]
    p["chunk_timeline"] = [{"start":c["start_ms"]/1000,"end":c["end_ms"]/1000,"text":c["text"]} for c in cues]
    frozen = rebuild_snapshot(raw)
    value = rebind_request(load("request_0.json"),frozen)
    value["subtitles"].update(authored_chunks=chunks,cues=cues,
        frozen_absolute={k:p[k] for k in ("start_seconds","end_seconds","duration_seconds","chunk_timeline")})
    if overlap: value["subtitles"]["cues"][1]["start_ms"] = 1000
    value["subtitles"]["cues_hash"] = content_hash(CUES_VERSION,{k:value["subtitles"][k] for k in ("page","cues")})
    proof = load("audio_proof_0.json"); proof["baseline"]["cues_hash"] = value["subtitles"]["cues_hash"]
    proof_hash = content_hash(AUDIO_VERSION,proof); attested = audio(value=proof,expected_hash=proof_hash)
    value.update(audio_proof=proof,narration={**proof["narration"],"audio_proof_hash":proof_hash})
    if overlap:
        with pytest.raises(DesignError,match="interval"):
            request(value=value,snapshot=frozen,expected_snapshot_hash=frozen.content_hash,audio_proof=attested)
    else:
        assert request(value=value,snapshot=frozen,expected_snapshot_hash=frozen.content_hash,audio_proof=attested).to_dict()["subtitles"]["authored_chunks"] == chunks


@pytest.mark.parametrize("i,expected", [(0,"flowchart"),(1,"comparison"),(2,"chart")])
def test_scene_fixtures_still_pass_phase3a_independent_design_validation(i,expected):
    from core.visual_design.grounding import design_snapshot
    from core.visual_design.schemas import VisualPlan
    from core.visual_design.svg_renderer import render_preview
    frozen = FrozenRenderSnapshot(load("snapshot.json")); scene = frozen.to_dict()["scenes"][i]
    design = scene["design"]
    preview = render_preview(design_snapshot(design["grounding"]),VisualPlan(design["plan"]))
    svg = preview.to_dict()["svg"].encode()
    def read(ref):
        assert ref["sha256"] == bytes_hash(svg) and ref["size"] == len(svg) and ref["extension"] == "svg"
        return svg
    assert validate_frozen_design(frozen,scene,read) == svg.decode()
    assert design["plan"]["strategy"] == expected
    if expected == "chart":
        assert [p["value"] for p in design["plan"]["layout"]["points"]] == [12,18]
        assert design["plan"]["layout"]["unit"] == "cm"


def test_frozen_teacher_confirmation_is_bound_but_never_substitutes_for_live_revocation():
    from core.visual_design.grounding import VisualProposal, grounded_payload, design_snapshot
    from core.visual_design.schemas import SlideSourceSnapshot
    from core.visual_design import DeterministicPlanner, render_preview
    raw = load("snapshot.json"); scene = raw["scenes"][0]; design = scene["design"]
    base = SlideSourceSnapshot(design["grounding"]["base_snapshot"]); source_text = base.to_dict()["original_display_text"]
    evidence = design["plan"]["layout"]
    proposal = VisualProposal({"schema_version":"visual-proposal.v1","strategy":"flowchart","rationale":"Review source-backed order.",
        "evidence":{"kind":"process","data":{k:evidence[k] for k in ("steps","order","relationship")}},
        "references":[{"start":0,"end":len(source_text),"quote":source_text}]})
    proposal_id = "00000000-0000-4000-8000-000000000555"
    grounding = grounded_payload(base,proposal,proposal_id)
    enriched = design_snapshot(grounding); plan = DeterministicPlanner().plan(enriched); preview = render_preview(enriched,plan)
    v = preview.to_dict(); svg = v["svg"].encode()
    design.update(grounding=grounding,plan=plan.to_dict(),plan_hash=plan.content_hash,preview_hash=preview.content_hash,
        design_source_hash=enriched.content_hash,preview_metadata={k:x for k,x in v.items() if k!="svg"},proposal=proposal.to_dict(),
        asset={"sha256":bytes_hash(svg),"size":len(svg),"extension":"svg"},
        confirmation={"proposal_id":proposal_id,"proposal_hash":proposal.content_hash,"owner_id":"801","action":"confirm",
                      "source_hash":design["source_hash"],"created_at":"2026-10-23T00:00:00+00:00"})
    frozen = rebuild_snapshot(raw)
    value = rebind_request(load("request_0.json"),frozen)
    result = request(value=value,snapshot=frozen,expected_snapshot_hash=frozen.content_hash)
    assert result.to_dict()["scene"]["design"]["proposal_id"] == proposal_id
    assert result.to_dict()["scene"]["design"]["confirmation_hash"]
    assert result.to_dict()["authorization_policy"] == "live_authorization_revocation_required.v1"
    validate_frozen_design(frozen,frozen.to_dict()["scenes"][0],lambda ref:svg)
    for key,bad in [("owner_id","other-owner"),("action","reject"),("proposal_hash","sha256:"+"a"*64)]:
        broken = deepcopy(raw); broken["scenes"][0]["design"]["confirmation"][key] = bad
        with pytest.raises(DesignError): describe_scene(rebuild_snapshot(broken),value["page"]["page_key"])


@pytest.mark.parametrize("kind,sample,valid", [("cloned",None,False),("built_in",{"sha256":"sha256:"+"a"*64,"size":100,"extension":"wav"},False),
    ("cloned",{"sha256":"sha256:"+"a"*64,"size":100,"extension":"wav"},True),("auto",None,False)])
def test_voice_sample_dependencies_are_explicit_and_bound(kind,sample,valid):
    value = load("audio_proof_0.json"); value["synthesis"]["voice_kind"] = kind
    value["synthesis"]["dependencies"]["sample"] = sample
    expected = content_hash(AUDIO_VERSION,value)
    if valid:
        assert audio(value=value,expected_hash=expected).to_dict()["synthesis"]["dependencies"]["sample"] == sample
    else:
        with pytest.raises(DesignError): audio(value=value,expected_hash=expected)


@pytest.mark.parametrize("control", ["\x00","\x7f","\x85"])
def test_unsafe_narration_controls_are_rejected_without_normalizing_text(control):
    with pytest.raises(DesignError): text_hash("Narration"+control)
