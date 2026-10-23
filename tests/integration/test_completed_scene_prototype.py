"""Actual bounded FFmpeg/librsvg acceptance, with no database or production job."""
from copy import deepcopy
import importlib.util
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageChops
import pytest

from core.visual_design.render_snapshot import (FrozenRenderSnapshot, require_rendered_revision, revision_hash, bytes_hash)
from core.visual_design.schemas import DesignError
from core.visual_render_assets import PrivateRenderAssets
from worker.completed_scene import compose_frozen_frame

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("visual_prototype_examples", ROOT / "docs/visual_design/phase3a/generate_prototype.py")
examples = importlib.util.module_from_spec(spec); spec.loader.exec_module(examples)


@pytest.fixture
def media(tmp_path, settings):
    settings.STORAGE_ROOT = str(tmp_path/"public")
    settings.VISUAL_RENDER_PRIVATE_ROOT = str(tmp_path/"private")
    available = bool(shutil.which("ffmpeg") and shutil.which("fc-match"))
    if available:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True, timeout=10)
        available = "librsvg" in result.stdout
    if not available:
        if os.environ.get("VISUAL_MEDIA_REQUIRE") == "1": pytest.fail("Required FFmpeg/librsvg/fontconfig unavailable")
        pytest.skip("FFmpeg/librsvg/fontconfig unavailable; no physical media acceptance claimed")
    return PrivateRenderAssets(1)


@pytest.mark.parametrize("name", examples.EXAMPLES)
def test_real_1920_frame_has_faithful_design_or_original_and_no_second_overlay(name, media, monkeypatch):
    snapshot = examples.synthetic_snapshot(name, media)
    before = snapshot.to_json()
    from worker import completed_scene
    from scripts.ffmpeg_helpers import _run
    commands = []
    def run(command, **options):
        commands.append(command)
        assert "drawtext" not in " ".join(command) and "subtitles=" not in " ".join(command)
        assert options["timeout_seconds"] == 20
        return _run(command, **options)
    monkeypatch.setattr(completed_scene, "_run", run)
    receipt = compose_frozen_frame(snapshot, snapshot.to_dict()["scenes"][0]["page_key"], media)
    require_rendered_revision(snapshot, receipt, snapshot.to_dict()["revision_hash"])
    frame = media.read(receipt["frame_asset"])
    assert frame.startswith(b"\x89PNG") and receipt["publishing_allowed"] is False
    with Image.open(BytesIO(frame)) as image:
        assert image.size == (1920, 1080)
        assert len(image.convert("RGB").getcolors(1920*1080)) > 10
    scene = snapshot.to_dict()["scenes"][0]
    if scene["design"]:
        plan = scene["design"]["plan"]
        assert plan["strategy"] == {"typography": "typography_only"}.get(name, name)
        if name == "chart":
            assert plan["layout"]["unit"] == "cm"
            assert [p["value"] for p in plan["layout"]["points"]] == [12, 18]
        assert scene["display_overlay"] == "none"
    else:
        assert scene["type"] == "original"
        # Same-resolution fallback must keep the original raster pixels exactly.
        with Image.open(BytesIO(media.read(scene["original"]["assets"]["original"]))) as original, Image.open(BytesIO(frame)) as rendered:
            assert ImageChops.difference(original.convert("RGB"), rendered.convert("RGB")).getbbox() is None
    assert snapshot.to_json() == before and len(commands) == 1
    # Same snapshot/fonts/runtime produces the same raster repeatedly.
    assert compose_frozen_frame(snapshot, scene["page_key"], media)["frame_asset"] == receipt["frame_asset"]


def test_corrupt_private_asset_or_cross_project_scope_never_falls_back(media):
    snapshot = examples.synthetic_snapshot("flowchart", media)
    ref = snapshot.to_dict()["scenes"][0]["design"]["asset"]
    media.path(ref).write_bytes(b"<svg onload='bad'/>")
    with pytest.raises(DesignError): compose_frozen_frame(snapshot, snapshot.to_dict()["scenes"][0]["page_key"], media)
    other = PrivateRenderAssets(2)
    with pytest.raises(DesignError): compose_frozen_frame(snapshot, snapshot.to_dict()["scenes"][0]["page_key"], other)


def test_missing_original_and_unsupported_original_modes_fail_explicitly(media):
    snapshot = examples.synthetic_snapshot("preserve_original", media)
    raw = snapshot.to_dict(); raw["scenes"][0]["original"]["assets"] = {}
    raw["revision_hash"] = revision_hash(raw)
    with pytest.raises(DesignError, match="No pinned original"): compose_frozen_frame(FrozenRenderSnapshot(raw), raw["scenes"][0]["page_key"], media)
    raw["scenes"][0].update(type="whiteboard")
    raw["scenes"][0]["original"]["mode"] = "whiteboard"
    raw["revision_hash"] = revision_hash(raw)
    with pytest.raises(DesignError, match="prototype only"): compose_frozen_frame(FrozenRenderSnapshot(raw), raw["scenes"][0]["page_key"], media)


@pytest.mark.parametrize("attack", ["overlay", "narration_disabled", "unknown", "nonfinite", "oversized", "duplicate", "svg_hash", "unsafe_svg"])
def test_strict_snapshot_boundary_rejects_unsupported_or_tampered_inputs(media, attack):
    snapshot = examples.synthetic_snapshot("flowchart", media)
    raw = snapshot.to_dict()
    if attack == "overlay": raw["scenes"][0]["display_overlay"] = "transcript"
    if attack == "narration_disabled": raw["scenes"][0]["narration_enabled"] = False
    if attack == "unknown": raw["shell"] = "untrusted"
    if attack == "nonfinite": raw["state"]["pages"][0]["duration_seconds"] = float("inf")
    if attack == "oversized": raw["state"]["pages"][0]["original_text"] = "x"*100001
    if attack == "duplicate":
        raw["state"]["pages"].append(deepcopy(raw["state"]["pages"][0])); raw["scenes"].append(deepcopy(raw["scenes"][0]))
    if attack == "svg_hash": raw["scenes"][0]["design"]["preview_hash"] = "sha256:"+"0"*64
    if attack == "unsafe_svg": raw["scenes"][0]["design"]["asset"] = media.reference(b'<svg xmlns="http://www.w3.org/2000/svg" onload="bad"/>', "svg", pin=True)
    with pytest.raises((DesignError, ValueError)):
        raw["revision_hash"] = revision_hash(raw)
        frozen = FrozenRenderSnapshot(raw)
        compose_frozen_frame(frozen, raw["scenes"][0]["page_key"], media)
