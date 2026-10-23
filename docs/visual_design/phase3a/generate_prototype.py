"""Synthetic, isolated FFmpeg frames. No production records or model downloads.

Run from repository root with --output /tmp/visual-render-examples. Export only
synthetic documentation frames; the intermediate CAS and fonts remain private.
"""
import argparse
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT / "services")]
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_test")
import django
django.setup()

from django.test import override_settings
from PIL import Image, ImageDraw, ImageFont
from core.visual_design import SlideSourceSnapshot, DeterministicPlanner, render_preview
from core.visual_design.schemas import SOURCE_VERSION, content_hash
from core.visual_design.grounding import grounded_payload, design_snapshot
from core.visual_design.render_snapshot import FrozenRenderSnapshot, SNAPSHOT_VERSION, SCENE_VERSION, RENDER_CONFIG, revision_hash
from core.visual_render_assets import PrivateRenderAssets
from core.visual_render_snapshots import _fonts
from worker.completed_scene import compose_frozen_frame

EXAMPLES = {
    "flowchart": "Ölçüm süreci\n1. Örneği topla\n2. Ölçümü kaydet & kontrol et\n3. Sonucu arşivle",
    "comparison": "Energy storage comparison\nProperty | Battery | Capacitor\nStorage | Chemical | Electric field\nDischarge | Slow | Fast",
    "chart": "Bitki büyümesi\nGrup | Boy (cm)\nKontrol | 12\nDeney | 18",
    "typography": "Öğrenme & observation: retain exact source meaning.",
    "preserve_original": "Preserve the original educational drawing.",
}


def synthetic_snapshot(kind, assets):
    text = EXAMPLES[kind]
    key = kind + ".source-0.split-1"
    source_value = {"schema_version": SOURCE_VERSION, "project_id": "1", "page_key": key,
        "source_slide_index": 0, "split_index": 1, "source_revision": content_hash("synthetic-render-revision.v1", text),
        "original_display_text": text, "protected_narration": "Narration remains unchanged. Synthetic fixture.", "evidence": []}
    original_assets = {}
    if kind == "preserve_original":
        image = Image.new("RGB", (1920, 1080), "#f4f7fb")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("DejaVuSans.ttf", 58)
        draw.text((110, 100), "Original: observation & measurement", font=font, fill="#172e46")
        draw.rectangle((160, 350, 780, 800), fill="#147d92")
        draw.ellipse((1080, 350, 1530, 800), fill="#5268b5")
        out = BytesIO(); image.save(out, format="PNG")
        original_assets["original"] = assets.reference(out.getvalue(), "png", pin=True)
        source_value["original_asset_ref"] = "asset:synthetic-original"
    source = SlideSourceSnapshot(source_value)
    grounding = grounded_payload(source)
    enriched = design_snapshot(grounding)
    plan = DeterministicPlanner().plan(enriched); preview = render_preview(enriched, plan).to_dict()
    design = None
    if preview["status"] == "valid":
        design = {"candidate_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-visual:"+kind)), "selection_version": 1,
            "source_hash": source.content_hash, "source_revision": source_value["source_revision"], "design_source_hash": enriched.content_hash,
            "plan_hash": plan.content_hash, "preview_hash": render_preview(enriched, plan).content_hash, "contains_text": True,
            "grounding": grounding, "plan": plan.to_dict(), "preview_metadata": {k:v for k,v in preview.items() if k != "svg"},
            "proposal": None, "confirmation": None, "asset": assets.reference(preview["svg"].encode(), "svg", pin=True)}
    else: assert preview["status"] == "preserved"
    page = {"page_key": key, "order": 0, "source_slide_index": 0, "split_index": 1, "original_text": text,
        "narration_text": source_value["protected_narration"], "rich_text_html": "", "editor_document": {},
        "whiteboard_mode": False, "subtitle_chunks": [source_value["protected_narration"]], "chunk_timeline": [],
        "start_seconds": 0, "end_seconds": 1.5, "duration_seconds": 1.5}
    mode = "original" if original_assets else "whiteboard"
    value = {"schema_version": SNAPSHOT_VERSION, "project_id": "1", "owner_id": "1", "revision_hash": "",
        "state": {"source_mode": "active", "ordinary_draft": {}, "project_fields": {"title": "Synthetic isolated example"},
            "pages": [page], "source_stamps": [{"page_key": key, "source_hash": source.content_hash, "source_revision": source_value["source_revision"]}],
            "selections": [{"page_key": key, "version": 1 if design else 0, "state": "selected" if design else "original", "candidate_id": design["candidate_id"] if design else None}],
            "voice": {"provider": "xtts_v2", "voice_id": "synthetic-no-tts-call", "language": "en", "speed": 1, "pitch": 1},
            "tts_settings": {"speech_speed": 1, "pause_seconds": 0.2}, "avatar": {"enabled": False}},
        "scenes": [{"schema_version": SCENE_VERSION, "page_key": key, "type": "completed_generated" if design else mode,
            "display_overlay": "none" if design else "existing_scene_policy", "narration_enabled": True, "subtitles_enabled": True,
            "original": {"mode": mode, "scene": {}, "assets": original_assets, "missing_policy": "fail"}, "design": design}],
        "fonts": _fonts(assets, True) if design else {}, "render_config": deepcopy(RENDER_CONFIG)}
    value["revision_hash"] = revision_hash(value)
    return FrozenRenderSnapshot(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    manifest = []
    with tempfile.TemporaryDirectory(prefix="visual-render-prototype-") as temp, override_settings(
            STORAGE_ROOT=str(Path(temp)/"public"), VISUAL_RENDER_PRIVATE_ROOT=str(Path(temp)/"private")):
        assets = PrivateRenderAssets(1)
        for name in EXAMPLES:
            snapshot = synthetic_snapshot(name, assets)
            receipt = compose_frozen_frame(snapshot, snapshot.to_dict()["scenes"][0]["page_key"], assets)
            filename = name + ".png"
            (args.output/filename).write_bytes(assets.read(receipt["frame_asset"]))
            manifest.append({"fixture": name, "file": filename, "strategy": snapshot.to_dict()["scenes"][0]["design"]["plan"]["strategy"] if snapshot.to_dict()["scenes"][0]["design"] else "preserve_original",
                "source_text": EXAMPLES[name], "receipt": receipt, "synthetic": True})
        (args.output/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
        (args.output/"index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Phase 3A isolated frames</title><style>body{font:16px system-ui;margin:24px;background:#edf2f7}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:20px}figure{margin:0;background:white;padding:16px;border-radius:12px}img{width:100%;aspect-ratio:16/9}</style><h1>Phase 3A · Synthetic isolated frames</h1><p>FFmpeg/librsvg raster proof. No lesson, audio, jobs or publishing.</p><main>'+''.join('<figure><img src="'+item["file"]+'" alt="'+item["fixture"]+'"><figcaption>'+item["fixture"]+' · 1920×1080</figcaption></figure>' for item in manifest)+'</main>')
    print(f"Composed {len(manifest)} isolated frames into {args.output}")


if __name__ == "__main__": main()
