"""Isolated completed-scene frame adapter, deliberately not a Celery task.

Caller must supply a server-validated frozen snapshot and its private CAS.
There is no transcript overlay, audio synthesis, publication or MP4 replacement.
"""
from html import escape
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageStat
from core.visual_design.schemas import require
from core.visual_design.render_snapshot import (FrozenRenderSnapshot, validate_frozen_design,
                                                RECEIPT_VERSION)
from scripts.ffmpeg_helpers import _run


def compose_frozen_frame(snapshot, page_key, assets):
    require(isinstance(snapshot, FrozenRenderSnapshot), "snapshot", "Validated frozen contract required")
    data = snapshot.to_dict()
    require(str(assets.project_root.name) == data["project_id"], "asset_scope", "Wrong private project storage")
    matching = [s for s in data["scenes"] if s["page_key"] == page_key]
    require(len(matching) == 1, "identity", "Unknown snapshot page")
    scene = matching[0]
    assets.project_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".frame-", dir=assets.project_root) as temp:
        temp = Path(temp)
        env = os.environ.copy()
        if scene["type"] == "completed_generated":
            svg = validate_frozen_design(snapshot, scene, assets.read)
            require(scene["display_overlay"] == "none", "text_ownership", "Completed text must not receive an overlay")
            source = temp / "design.svg"
            source.write_bytes(svg.encode("utf-8"))
            fonts = temp / "fonts"; fonts.mkdir()
            for name, ref in data["fonts"].items(): (fonts / (name + ".ttf")).write_bytes(assets.read(ref))
            config = temp / "fonts.conf"
            config.write_text('<?xml version="1.0"?><fontconfig><dir>'+escape(str(fonts))+'</dir><cachedir>'+escape(str(temp / "font-cache"))+'</cachedir></fontconfig>')
            env.update(FONTCONFIG_FILE=str(config), FONTCONFIG_PATH=str(temp))
            decoder = ["-c:v", "librsvg"]
        else:
            # Original raster only. Whiteboard/custom/source-background need the
            # existing overlay renderer in Phase 3B; never emit an incomplete frame.
            require(scene["type"] == "original", "unsupported_original_scene", "This prototype only composes original rasters and completed SVGs")
            ref = scene["original"]["assets"].get("original")
            require(ref is not None, "missing_original", "No pinned original raster; fallback must fail explicitly")
            source = temp / ("original." + ref["extension"])
            source.write_bytes(assets.read(ref)); decoder = []
            with Image.open(source) as original:
                require(original.width * original.height <= 16_000_000, "raster_limit", "Original pixel count exceeds prototype limit")
        frame = temp / "frame.png"
        _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe",
              *decoder, "-i", str(source), "-frames:v", "1", "-vf",
              "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white",
              "-threads", "1", str(frame)], timeout_seconds=20, env=env)
        with Image.open(frame) as image:
            require(image.format == "PNG" and image.size == (1920, 1080), "frame", "Invalid frame dimensions/type")
            require(max(ImageStat.Stat(image.convert("RGB")).stddev) > 1, "blank_frame", "Blank output frame")
        ref = assets.reference(frame.read_bytes(), "png", pin=True)
    return {"schema_version": RECEIPT_VERSION, "snapshot_hash": snapshot.content_hash,
        "revision_hash": data["revision_hash"], "page_key": page_key, "frame_asset": ref,
        "width": 1920, "height": 1080, "publishing_allowed": False}
