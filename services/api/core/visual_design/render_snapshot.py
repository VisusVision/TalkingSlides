"""Pure immutable revision/scene contracts; never accepts a live Project or job."""
import hashlib
import json
import math
import re
import uuid

from .schemas import (DesignError, canonical_json, content_hash, fields, require,
                      identifier, index, digest, SlideSourceSnapshot, VisualPlan, VisualPreview)
from .grounding import grounded_payload, design_snapshot, VisualProposal
from .svg_renderer import render_preview
from .validation import validate_svg

SNAPSHOT_VERSION = "visual-render-snapshot.v1"
REVISION_VERSION = "visual-render-revision.v1"
SCENE_VERSION = "completed-scene.v1"
RECEIPT_VERSION = "visual-frame-receipt.v1"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_PAGES = 32
RENDER_CONFIG = {"width": 1920, "height": 1080, "purpose": "isolated_frame.v1",
    "pipeline_version": "industrial-render-pipeline:v1", "fps": 25, "tts_mode": "service",
    "language_hint": "auto", "default_pause_seconds": 2.2,
    "subtitle_policy": "narration_sidecar.v1", "avatar_policy": "unsupported.v1"}


def bounded_json(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop(); count += 1
        require(depth <= 32 and count <= 120_000, "snapshot_limit", "Snapshot nesting/node limit exceeded")
        require(type(item) in (dict, list, str, int, float, bool, type(None)), "schema", "JSON values only")
        if type(item) is dict:
            require(all(type(k) is str for k in item), "schema", "String keys only")
            stack.extend((v, depth + 1) for v in item.values())
        elif type(item) is list: stack.extend((v, depth + 1) for v in item)
        elif type(item) is str: require(len(item) <= 100_000, "snapshot_limit", "Oversized source value")
        elif type(item) in (int, float): require(math.isfinite(item) and abs(item) <= 10**15, "number", "Nonfinite/oversized input")
    try:
        encoded = canonical_json(value)
        require(len(encoded.encode("utf-8")) <= MAX_SNAPSHOT_BYTES, "snapshot_limit", "Snapshot exceeds byte limit")
    except (ValueError, UnicodeEncodeError) as exc:
        raise DesignError("snapshot_json", "Invalid finite Unicode JSON") from exc
    return encoded


def bytes_hash(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def asset_reference(ref):
    fields(ref, ("sha256", "size", "extension"))
    digest(ref["sha256"])
    require(type(ref["size"]) is int and 0 < ref["size"] <= 8 * 1024 * 1024, "asset_limit", "Invalid private asset size")
    require(ref["extension"] in ("svg", "png", "jpg", "jpeg", "webp", "ttf"), "asset_type", "Unsupported pinned asset")


class FrozenRenderSnapshot:
    """Canonical serialized value; callers receive copies, not mutable references."""
    __slots__ = ("_json",)

    def __init__(self, value):
        encoded = bounded_json(value)
        fields(value, ("schema_version", "project_id", "owner_id", "revision_hash", "state", "scenes", "fonts", "render_config"))
        require(value["schema_version"] == SNAPSHOT_VERSION, "version", "Unsupported render snapshot")
        identifier(value["project_id"]); identifier(value["owner_id"])
        digest(value["revision_hash"])
        require(value["revision_hash"] == revision_hash(value), "revision", "Revision hash mismatch")
        fields(value["state"], ("source_mode", "ordinary_draft", "project_fields", "pages", "source_stamps", "selections", "voice", "tts_settings", "avatar"))
        state = value["state"]
        require(state["source_mode"] in ("active", "draft"), "source_mode", "Unsupported source mode")
        pages = state["pages"]
        require(type(pages) is list and 1 <= len(pages) <= MAX_PAGES, "page_limit", "Bounded nonempty pages required")
        keys, orders = [], []
        for page in pages:
            fields(page, ("page_key", "order", "source_slide_index", "split_index", "original_text", "narration_text", "rich_text_html", "editor_document", "whiteboard_mode", "subtitle_chunks", "chunk_timeline", "start_seconds", "end_seconds", "duration_seconds"))
            identifier(page["page_key"])
            require(len(page["page_key"]) <= 64, "identity", "Page key too long")
            for name in ("order", "source_slide_index", "split_index"): index(page[name])
            require(all(type(page[name]) is str for name in ("original_text", "narration_text", "rich_text_html")), "source", "Exact authored strings required")
            require(type(page["editor_document"]) is dict and type(page["whiteboard_mode"]) is bool, "source", "Invalid scene source")
            require(type(page["subtitle_chunks"]) is list and all(type(s) is str for s in page["subtitle_chunks"]), "subtitles", "Invalid subtitle inputs")
            require(type(page["chunk_timeline"]) is list, "timing", "Invalid timeline")
            for name in ("start_seconds", "end_seconds", "duration_seconds"):
                n = page[name]
                require(n is None or type(n) in (int, float) and math.isfinite(n) and n >= 0, "timing", "Invalid timing input")
            if page["start_seconds"] is not None and page["end_seconds"] is not None:
                require(page["end_seconds"] >= page["start_seconds"], "timing", "Page ends before it starts")
            previous_end = None
            for chunk in page["chunk_timeline"]:
                fields(chunk, ("start", "end", "text"), ("index", "chunk_index"))
                require(type(chunk["text"]) is str and all(type(chunk[key]) in (int, float) for key in ("start", "end"))
                        and 0 <= chunk["start"] < chunk["end"], "timing", "Invalid frozen subtitle interval")
                require(previous_end is None or chunk["start"] >= previous_end, "timing", "Overlapping or unordered frozen subtitle intervals")
                if page["start_seconds"] is not None: require(chunk["start"] >= page["start_seconds"], "timing", "Subtitle precedes page")
                if page["end_seconds"] is not None: require(chunk["end"] <= page["end_seconds"], "timing", "Subtitle exceeds page")
                previous_end = chunk["end"]
            keys.append(page["page_key"]); orders.append(page["order"])
        require(len(set(keys)) == len(keys) and len(set(orders)) == len(orders) and orders == sorted(orders), "identity", "Duplicate or ambiguous page ordering/keys")
        for key in ("ordinary_draft", "project_fields", "tts_settings", "avatar"):
            require(type(state[key]) is dict, "schema", "Invalid captured settings")
        require(not state["avatar"].get("enabled"), "unsupported_avatar", "Avatar inputs are not pinned by this contract")
        require(type(state["source_stamps"]) is list and [r.get("page_key") for r in state["source_stamps"]] == keys, "identity", "Source revision mapping mismatch")
        for stamp in state["source_stamps"]:
            fields(stamp, ("page_key", "source_hash", "source_revision")); digest(stamp["source_hash"]); digest(stamp["source_revision"])
        require(type(state["selections"]) is list and [r.get("page_key") for r in state["selections"]] == keys, "identity", "Selection mapping mismatch")
        for selection in state["selections"]:
            fields(selection, ("page_key", "version", "state", "candidate_id"))
            require(type(selection["version"]) is int and selection["version"] >= 0, "selection", "Invalid selection version")
            require(selection["state"] in ("original", "selected") and (selection["candidate_id"] is None) == (selection["state"] == "original"), "selection", "Invalid selection state")
        if state["voice"] is not None:
            fields(state["voice"], ("provider", "voice_id", "language", "speed", "pitch"))
            require(all(type(state["voice"][key]) is str for key in ("provider", "voice_id", "language")), "voice", "Invalid voice identity")
            require(all(type(state["voice"][key]) in (int, float) and 0 < state["voice"][key] <= 10 for key in ("speed", "pitch")), "voice", "Unsupported voice settings")
        scenes = value["scenes"]
        require(type(scenes) is list and [s.get("page_key") for s in scenes] == keys, "identity", "Scene/page mapping mismatch")
        for scene in scenes:
            fields(scene, ("schema_version", "page_key", "type", "display_overlay", "narration_enabled", "subtitles_enabled", "original", "design"))
            require(scene["schema_version"] == SCENE_VERSION, "version", "Unsupported scene contract")
            require(scene["narration_enabled"] is True and scene["subtitles_enabled"] is True, "text_ownership", "Completed scenes retain narration/subtitles")
            fields(scene["original"], ("mode", "scene", "assets", "missing_policy"))
            require(scene["original"]["mode"] in ("original", "whiteboard", "custom", "source_background"), "operation", "Unsupported original scene")
            require(scene["original"]["missing_policy"] == "fail", "fallback", "Missing originals must fail explicitly")
            for ref in scene["original"]["assets"].values(): asset_reference(ref)
            if scene["type"] == "completed_generated":
                require(scene["display_overlay"] == "none" and scene["design"] is not None, "text_ownership", "Completed scene owns its display text")
                asset_reference(scene["design"]["asset"])
                require(scene["design"]["asset"]["extension"] == "svg", "asset_type", "Completed design requires SVG")
                require(scene["design"]["contains_text"] is True, "text_ownership", "Unsupported incomplete generated scene")
            else:
                require(scene["type"] == scene["original"]["mode"] and scene["design"] is None and scene["display_overlay"] == "existing_scene_policy", "operation", "Invalid original/fallback semantics")
        require(value["render_config"] == RENDER_CONFIG, "operation", "Unsupported prototype output")
        if any(s["design"] is not None for s in scenes):
            fields(value["fonts"], ("regular", "bold"))
        else: require(value["fonts"] == {}, "fonts", "Unexpected font inputs")
        for ref in value["fonts"].values():
            asset_reference(ref); require(ref["extension"] == "ttf", "fonts", "Pinned TrueType fonts required")
        object.__setattr__(self, "_json", encoded)

    def __setattr__(self, name, value):
        raise AttributeError("Render snapshots are immutable")

    def to_dict(self): return json.loads(self._json)
    def to_json(self): return self._json
    @property
    def content_hash(self): return content_hash(SNAPSHOT_VERSION, self.to_dict())


def revision_hash(value):
    body = {key: value[key] for key in ("project_id", "owner_id", "state", "scenes", "fonts", "render_config")}
    bounded_json(body)
    return content_hash(REVISION_VERSION, body)


def validate_frozen_design(snapshot, scene, read_asset):
    """Independent historical validation; no current candidate/draft DB reads."""
    design = scene["design"]
    fields(design, ("candidate_id", "selection_version", "source_hash", "source_revision", "design_source_hash", "plan_hash", "preview_hash", "contains_text", "grounding", "plan", "preview_metadata", "proposal", "confirmation", "asset"))
    try: uuid.UUID(design["candidate_id"])
    except (ValueError, TypeError, AttributeError) as exc: raise DesignError("identity", "Invalid candidate UUID") from exc
    require(type(design["selection_version"]) is int and design["selection_version"] > 0, "selection", "Saved selection required")
    base = SlideSourceSnapshot(design["grounding"]["base_snapshot"])
    raw = snapshot.to_dict()
    page = next(p for p in raw["state"]["pages"] if p["page_key"] == scene["page_key"])
    stamp = next(s for s in raw["state"]["source_stamps"] if s["page_key"] == scene["page_key"])
    selection = next(s for s in raw["state"]["selections"] if s["page_key"] == scene["page_key"])
    require(stamp["source_hash"] == design["source_hash"] and stamp["source_revision"] == design["source_revision"]
            and selection["state"] == "selected" and selection["candidate_id"] == design["candidate_id"]
            and selection["version"] == design["selection_version"], "selection", "Frozen selection/source mapping mismatch")
    require(base.content_hash == design["source_hash"] and base.identity == {
        "project_id": raw["project_id"], "page_key": page["page_key"], "source_slide_index": page["source_slide_index"],
        "split_index": page["split_index"], "source_revision": design["source_revision"]}, "source", "Frozen source identity mismatch")
    require(base.to_dict()["original_display_text"] == page["original_text"] and base.to_dict()["protected_narration"] == page["narration_text"], "source", "Frozen authored content mismatch")
    if design["proposal"] is not None:
        proposal = VisualProposal(design["proposal"])
        confirmation = design["confirmation"]
        fields(confirmation, ("proposal_id", "proposal_hash", "owner_id", "action", "source_hash", "created_at"))
        require(confirmation["action"] == "confirm" and confirmation["owner_id"] == raw["owner_id"]
                and confirmation["source_hash"] == design["source_hash"] and confirmation["proposal_hash"] == proposal.content_hash,
                "approval", "Frozen approval does not bind the owner/source/proposal")
        expected = grounded_payload(base, proposal, confirmation["proposal_id"])
    else:
        require(design["confirmation"] is None, "approval", "Unexpected confirmation")
        expected = grounded_payload(base)
    require(expected == design["grounding"], "grounding", "Frozen grounding integrity mismatch")
    enriched = design_snapshot(expected)
    svg = read_asset(design["asset"]).decode("utf-8")
    plan = VisualPlan(design["plan"])
    preview = VisualPreview({**design["preview_metadata"], "svg": svg})
    p, v = plan.to_dict(), preview.to_dict()
    require(design["design_source_hash"] == enriched.content_hash == p["source_hash"] == v["source_hash"]
        and design["plan_hash"] == plan.content_hash == v["plan_hash"]
        and design["preview_hash"] == preview.content_hash and v["contains_text"] is True and v["status"] == "valid"
        and validate_svg(svg, plan).valid and render_preview(enriched, plan).content_hash == preview.content_hash,
        "integrity", "Frozen plan/preview validation failed")
    return svg


def require_rendered_revision(snapshot, receipt, current_revision_hash):
    """Future promotion precondition ONLY; caller must hold its publication locks.

    A frame receipt is NOT a publishable video or moderation authorization.
    """
    fields(receipt, ("schema_version", "snapshot_hash", "revision_hash", "page_key", "frame_asset", "width", "height", "publishing_allowed"))
    require(receipt["schema_version"] == RECEIPT_VERSION and receipt["snapshot_hash"] == snapshot.content_hash
        and receipt["revision_hash"] == snapshot.to_dict()["revision_hash"] == current_revision_hash,
        "revision_conflict", "Rendered revision differs from snapshot/current authored state")
    require(receipt["page_key"] in [s["page_key"] for s in snapshot.to_dict()["scenes"]]
        and receipt["width"] == 1920 and receipt["height"] == 1080 and receipt["publishing_allowed"] is False,
        "receipt", "Invalid isolated frame receipt")
    asset_reference(receipt["frame_asset"])
    require(receipt["frame_asset"]["extension"] == "png", "receipt", "Only raster-frame receipts are supported")
