"""Reuse established planners; immutable scene identities never change TTS alone."""
from copy import deepcopy
from pathlib import Path
import importlib.util

import pytest

from core.visual_design.render_snapshot import FrozenRenderSnapshot, revision_hash
from core.visual_render_assets import PrivateRenderAssets
from core.visual_render_planning import build_snapshot_planning_manifests, compare_snapshot_plans

spec = importlib.util.spec_from_file_location("planning_examples", Path(__file__).resolve().parents[2]/"docs/visual_design/phase3a/generate_prototype.py")
examples = importlib.util.module_from_spec(spec); spec.loader.exec_module(examples)


@pytest.fixture
def original(tmp_path, settings):
    settings.STORAGE_ROOT = str(tmp_path/"public"); settings.VISUAL_RENDER_PRIVATE_ROOT = str(tmp_path/"private")
    return examples.synthetic_snapshot("preserve_original", PrivateRenderAssets(1))


def manifests(snapshot): return build_snapshot_planning_manifests(snapshot)


def changed(snapshot, modify):
    raw = snapshot.to_dict(); modify(raw); raw["revision_hash"] = revision_hash(raw)
    return FrozenRenderSnapshot(raw)


def compare(old, new): return compare_snapshot_plans(manifests(old), manifests(new), required_artifacts=())


def test_unchanged_reuses_existing_classifier_contract(original):
    report = compare(original, original)
    assert report["core"]["dirty_slides"] == []
    assert report["classification"]["summary"]["unchanged"] == 1
    assert report["plan"]["summary"]["reuse_all"] == 1
    assert report["execution_enabled"] is False


def test_visual_only_change_keeps_audio_input_hashes_and_requests_recomposition(original):
    def edit(raw):
        raw["scenes"][0]["original"]["scene"]["text_scale"] = 1.2
    newer = changed(original, edit)
    old, new = manifests(original), manifests(newer)
    key = original.to_dict()["scenes"][0]["page_key"]
    for name in ("tts_input_hash", "tts_settings_hash", "narration_text_hash"):
        assert old["partial"]["pages"][key][name] == new["partial"]["pages"][key][name]
    report = compare(original, newer)
    assert "ImageChanged" in report["core"]["reasons"]["1"]
    assert report["plan"]["summary"]["recompose_visual_only_future"] == 1


@pytest.mark.parametrize("kind", ["narration", "voice", "language", "tts", "voice_speed", "voice_pitch"])
def test_audio_changes_invalidate_tts_using_existing_semantics(original, kind):
    def edit(raw):
        if kind == "narration": raw["state"]["pages"][0]["narration_text"] = "New protected narration"
        if kind == "voice": raw["state"]["voice"]["voice_id"] = "new-voice"
        if kind == "language": raw["state"]["voice"]["language"] = "tr"
        if kind == "voice_speed": raw["state"]["voice"]["speed"] = 1.2
        if kind == "voice_pitch": raw["state"]["voice"]["pitch"] = 1.1
        if kind == "tts": raw["state"]["tts_settings"]["speech_speed"] = 1.25
    newer = changed(original, edit)
    report = compare(original, newer)
    assert report["plan"]["summary"]["rerun_tts_avatar_future"] == 1
    assert not report["plan"]["summary"]["recompose_visual_only_future"]


@pytest.mark.parametrize("kind", ["timing", "split", "new_page"])
def test_structure_and_timing_require_safe_broader_render(original, kind):
    def edit(raw):
        page = raw["state"]["pages"][0]
        if kind == "timing": page["duration_seconds"] = 5
        if kind == "split": page["split_index"] = 2
        if kind == "new_page":
            next_page = deepcopy(page); next_page.update(page_key="new-page", order=1, split_index=2)
            raw["state"]["pages"].append(next_page)
            next_scene = deepcopy(raw["scenes"][0]); next_scene["page_key"] = "new-page"
            raw["scenes"].append(next_scene)
            for field in ("source_stamps", "selections"):
                item = deepcopy(raw["state"][field][0]); item["page_key"] = "new-page"; raw["state"][field].append(item)
    report = compare(original, changed(original, edit))
    assert "structural_changed" in report["classification"]["global_reasons"]
    assert report["plan"]["summary"]["full_rerender_required_future"] >= 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_artifact_never_gets_reuse_plan(original, corrupt):
    key = original.to_dict()["scenes"][0]["page_key"]
    artifacts = {key: {"slide_image": {"path": "private/image.png", "sha256": "good"},
        "tts_audio": {"path": "private/audio.wav", "sha256": "good"}, "composed_segment": {"path": "private/segment.mp4", "sha256": "good"}}}
    previous = build_snapshot_planning_manifests(original, artifacts)
    current = build_snapshot_planning_manifests(original, artifacts)
    report = compare_snapshot_plans(previous, current, artifact_exists=lambda a: corrupt, artifact_sha256=lambda a: "corrupt" if corrupt else "good")
    assert ("CorruptArtifact" if corrupt else "MissingArtifact") in report["core"]["reasons"]["1"]
    assert report["plan"]["summary"]["reuse_all"] == 0
    assert report["classification"]["summary"]["missing_artifact"] == 1


SEGMENT_FIXTURES=Path(__file__).parents[1]/"fixtures/visual_segments"


def segment_fixture(name):
    import json
    return json.loads((SEGMENT_FIXTURES/name).read_text())


def private_synthesis():
    from core.visual_design.segment_contracts import SegmentAudioProof
    from core.visual_design.segment_dependencies import dependencies_from_verified_audio
    trust=segment_fixture("trust_anchors.json"); result={}
    for i in range(3):
        proof=SegmentAudioProof(segment_fixture(f"audio_proof_{i}.json"),expected_hash=trust["audio_proof_hashes"][i],expected_producer_id=trust["producer_id"],allow_synthetic=True)
        # Pure planner fixture only, not media acceptance/producer enrollment.
        dep=dependencies_from_verified_audio(proof,"sha256:"+"a"*64).to_dict()
        result[dep["page"]["page_key"]]=dep
    return result


def visual_environment():
    from core.visual_design.segment_dependencies import VISUAL_VERSION
    from core.visual_design.schemas import content_hash
    return {"schema_version":VISUAL_VERSION,"runtime":segment_fixture("request_0.json")["recipe"]["runtime"],
            "font_configuration":{"schema_version":"pinned-font-configuration.v1","family":"DejaVu Sans",
                                  "configuration_hash":content_hash("fixture-font-config.v1","pinned-two-fonts")}}


def private_manifests(snapshot=None,**overrides):
    return build_snapshot_planning_manifests(snapshot or FrozenRenderSnapshot(segment_fixture("snapshot.json")),
        visual_environment=overrides.pop("visual_environment",visual_environment()),
        synthesis_dependencies_by_page_key=overrides.pop("synthesis_dependencies_by_page_key",private_synthesis()),**overrides)


def test_snapshot_adapter_default_path_retains_exact_phase3a_legacy_golden():
    baseline=segment_fixture("planner_legacy_golden.json")
    snapshot=FrozenRenderSnapshot(segment_fixture("snapshot.json"))
    assert build_snapshot_planning_manifests(snapshot)==baseline["snapshot_plans"]
    assert snapshot.content_hash==segment_fixture("trust_anchors.json")["snapshot_hash"]


@pytest.mark.parametrize("dependency",["regular","bold","rasterizer","fontconfig_binary","fontconfig_configuration","media_build"])
def test_opt_in_fonts_and_rasterizer_invalidate_visuals_without_tts(dependency):
    snapshot=FrozenRenderSnapshot(segment_fixture("snapshot.json"));environment=visual_environment();newer=snapshot
    if dependency in ("regular","bold"):
        newer=changed(snapshot,lambda raw:raw["fonts"][dependency].update(sha256="sha256:"+"b"*64))
    elif dependency=="fontconfig_configuration": environment["font_configuration"]["configuration_hash"]="sha256:"+"b"*64
    else:
        key={"rasterizer":"rasterizer_build_hash","fontconfig_binary":"fontconfig_build_hash","media_build":"ffmpeg_binary_hash"}[dependency]
        environment["runtime"][key]="sha256:"+"b"*64
    old=private_manifests(snapshot);new=private_manifests(newer,visual_environment=environment)
    report=compare_snapshot_plans(old,new,required_artifacts=())
    assert report["plan"]["summary"]["recompose_visual_only_future"]==3
    assert not report["plan"]["summary"]["rerun_tts_avatar_future"] and report["execution_enabled"] is False
    for key in old["partial"]["pages"]:
        for name in ("tts_input_hash","tts_settings_hash","narration_text_hash"):
            assert old["partial"]["pages"][key][name]==new["partial"]["pages"][key][name]
        assert old["core"]["pages"][key]["inputs"]["private_synthesis_dependencies"]==new["core"]["pages"][key]["inputs"]["private_synthesis_dependencies"]


def test_unchanged_private_descriptors_keep_existing_reuse_report_and_require_separate_eligibility():
    before=private_manifests();report=compare_snapshot_plans(before,before,required_artifacts=())
    assert report["plan"]["summary"]["reuse_all"]==3
    assert report["execution_enabled"] is False
    # Presence of a descriptor or visual-only classification is never a producer
    # trust or filesystem eligibility check; no dispatch/media boundary is called.


def test_reorder_and_split_never_reuse_audio_by_position_or_source_slide_alone():
    snapshot=FrozenRenderSnapshot(segment_fixture("snapshot.json"));raw=snapshot.to_dict()
    for key in ("pages","source_stamps","selections"): raw["state"][key].reverse()
    raw["scenes"].reverse()
    for order,page in enumerate(raw["state"]["pages"]):page["order"]=order
    raw["revision_hash"]=revision_hash(raw)
    reordered=FrozenRenderSnapshot(raw)
    report=compare_snapshot_plans(private_manifests(snapshot),private_manifests(reordered),required_artifacts=())
    assert report["plan"]["summary"]["full_rerender_required_future"]>=2
    assert "structural_changed" in report["classification"]["global_reasons"]
    for key,page in private_manifests(reordered)["core"]["pages"].items():
        original=next(p for p in snapshot.to_dict()["state"]["pages"] if p["page_key"]==key)
        assert page["inputs"]["private_synthesis_dependencies"]["page"]["source_slide_index"]==original["source_slide_index"]
        assert page["inputs"]["private_synthesis_dependencies"]["page"]["split_index"]==original["split_index"]
    split=changed(snapshot,lambda raw:raw["state"]["pages"][0].update(split_index=2))
    with pytest.raises(ValueError):private_manifests(split)


def test_private_cue_and_hold_dependencies_change_timing_without_invalidating_audio():
    from core.visual_design.segment_dependencies import CAPTION_VERSION
    from core.visual_design.schemas import content_hash
    key="source-0.split-1"
    captions={"schema_version":CAPTION_VERSION,"cues_hash":segment_fixture("request_0.json")["subtitles"]["cues_hash"],
        "authored_chunks_hash":content_hash("visual-segment-authored-chunks.v1",["Plants need light and water to grow."]),
        "policy":"frozen_baseline.v1","time_base":"milliseconds.v1","audio_duration_ms":2335,"hold_duration_ms":200,"target_duration_ms":2535}
    old=private_manifests(caption_dependencies_by_page_key={key:captions})
    for name in ("hold","cue"):
        value=deepcopy(captions)
        if name=="hold":value.update(hold_duration_ms=240,target_duration_ms=2575)
        else:value["cues_hash"]="sha256:"+"b"*64
        new=private_manifests(caption_dependencies_by_page_key={key:value})
        assert old["partial"]["pages"][key]["tts_settings_hash"]==new["partial"]["pages"][key]["tts_settings_hash"]
        report=compare_snapshot_plans(old,new,required_artifacts=())
        assert "TimingChanged" in report["core"]["reasons"]["1"]
        assert "structural_changed" in report["classification"]["pages"][key]["reasons"]


def test_private_descriptors_with_missing_or_corrupt_artifacts_do_not_get_reuse():
    before=private_manifests()
    report=compare_snapshot_plans(before,before,artifact_exists=lambda a:False)
    assert report["plan"]["summary"]["reuse_all"]==0
    assert all("MissingArtifact" in page["reasons"] for page in report["core"]["slides"])


def test_unknown_visual_environment_or_foreign_page_dependencies_fail_before_planning():
    env=visual_environment();env["schema_version"]="unknown.v2"
    with pytest.raises(ValueError):private_manifests(visual_environment=env)
    deps=private_synthesis();deps["foreign-page"]=deepcopy(next(iter(deps.values())))
    with pytest.raises(ValueError):private_manifests(synthesis_dependencies_by_page_key=deps)


def test_partial_opt_in_cannot_omit_visual_environment_or_forge_owner():
    snapshot=FrozenRenderSnapshot(segment_fixture("snapshot.json"))
    with pytest.raises(ValueError):
        build_snapshot_planning_manifests(snapshot,synthesis_dependencies_by_page_key=private_synthesis())
    deps=private_synthesis();deps["source-0.split-1"]["owner_id"]="802"
    with pytest.raises(ValueError):private_manifests(synthesis_dependencies_by_page_key=deps)


def test_corrupt_private_caption_semantics_are_not_hidden_by_old_hashes():
    from core.visual_design.segment_dependencies import CAPTION_VERSION
    caps={"schema_version":CAPTION_VERSION,"cues_hash":"sha256:"+"a"*64,"authored_chunks_hash":"sha256:"+"b"*64,
        "policy":"frozen_baseline.v1","time_base":"milliseconds.v1","audio_duration_ms":2335,"hold_duration_ms":200,"target_duration_ms":2535}
    old=private_manifests(caption_dependencies_by_page_key={"source-0.split-1":caps});bad=deepcopy(old)
    bad["core"]["pages"]["source-0.split-1"]["inputs"]["timing"]["private_caption_dependencies"]["schema_version"]="unknown.v2"
    bad["partial"]["pages"]["source-0.split-1"]["private_caption_dependencies"]["schema_version"]="unknown.v2"
    report=compare_snapshot_plans(old,bad,required_artifacts=())
    assert "MissingBaseline" in report["core"]["reasons"]["1"]
    assert "unknown_requires_full" in report["classification"]["pages"]["source-0.split-1"]["reasons"]
