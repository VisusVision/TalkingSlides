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
