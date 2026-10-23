"""Explicit owner-only snapshot capture. No dispatch, promotion or lesson writes."""
from copy import deepcopy
from pathlib import Path
import subprocess

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException

from core.drafts import get_studio_transcript_pages, has_project_draft
from core.models import (Project, TranscriptPage, VoiceProfile, UserProfile, VisualDraftSelection,
                         VisualEvidenceReview, VisualRenderSnapshot)
from core.render_planner import build_project_render_planner_manifest
from core.serializers import canonical_project_tts_settings
from core.storage_adapter import FilesystemStorageAdapter
from core.visual_design.schemas import DesignError, require
from core.visual_design.render_snapshot import (FrozenRenderSnapshot, SNAPSHOT_VERSION, MAX_PAGES,
    SCENE_VERSION, RENDER_CONFIG, revision_hash, bounded_json, validate_frozen_design)
from core.visual_design_source import build_studio_visual_source
from core.visual_drafts import verified_preview, design_identity, VisualDraftConflict
from core.visual_render_assets import PrivateRenderAssets, MAX_ASSET_BYTES


class RenderSnapshotConflict(APIException):
    status_code = 409
    default_detail = "Saved lesson, selection or render inputs changed. Refresh the revision."
    default_code = "render_snapshot_conflict"


PAGE_FIELDS = ("page_key", "order", "source_slide_index", "split_index", "original_text", "narration_text",
    "rich_text_html", "editor_document", "whiteboard_mode", "subtitle_chunks", "chunk_timeline",
    "start_seconds", "end_seconds", "duration_seconds")
ASSET_FIELDS = {"original": "original_background_path", "custom": "custom_background_path", "source_background": "source_background_path"}


def _locked_project(project_id, owner):
    project = get_object_or_404(Project.objects.select_for_update(), pk=project_id, user=owner)
    # Same Project -> pages order as promotion and private visual decisions.
    list(TranscriptPage.objects.select_for_update().filter(project=project).order_by("id"))
    list(VisualDraftSelection.objects.select_for_update().filter(project=project).order_by("pk"))
    list(VoiceProfile.objects.select_for_update().filter(user=owner))
    list(UserProfile.objects.select_for_update().filter(user=owner))
    return project


def _original_assets(project, scene, assets, pin):
    public = FilesystemStorageAdapter()
    result = {}
    for role, field in ASSET_FIELDS.items():
        raw = scene.get(field)
        if raw in (None, ""): continue
        require(type(raw) is str and "\\" not in raw, "asset_path", "Unsupported original asset locator")
        parts = Path(raw).parts
        prefix = (str(project.pk),) if parts[:1] == (str(project.pk),) else ("uploads", str(project.pk))
        require(parts[:len(prefix)] == prefix and not Path(raw).is_absolute() and ".." not in parts,
                "asset_path", "Cross-project or external original asset")
        path = public.resolve_path(raw)
        require(path.is_relative_to(public.root.joinpath(*prefix)) and path.is_file(), "asset_path", "Original asset missing or escaped project")
        require(path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"), "asset_type", "Only bounded raster originals supported")
        require(path.stat().st_size <= MAX_ASSET_BYTES, "asset_limit", "Original asset too large")
        with path.open("rb") as file: data = file.read(MAX_ASSET_BYTES + 1)
        result[role] = assets.reference(data, path.suffix.lower()[1:], pin=pin)
    return result


def _fonts(assets, pin):
    result = {}
    for name, pattern in (("regular", "DejaVu Sans:style=Book"), ("bold", "DejaVu Sans:style=Bold")):
        try:
            output = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, check=True, timeout=5)
            path = Path(output.stdout)
            require(path.name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"), "font", "Exact DejaVu Sans fonts unavailable")
            require(path.stat().st_size <= MAX_ASSET_BYTES, "font", "Font resource exceeds limit")
            result[name] = assets.reference(path.read_bytes(), "ttf", pin=pin)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DesignError("font", "Pinned DejaVu Sans runtime is unavailable") from exc
    return result


def _capture(project, owner, *, pin=False):
    # Read raw saved data before permissive Studio ordering helpers can omit rows.
    draft = deepcopy(project.draft_data) if isinstance(project.draft_data, dict) else {}
    bounded_json(draft)
    if has_project_draft(project):
        require(type(draft.get("transcript_pages")) is list, "source", "Saved ordinary draft requires an explicit complete page list")
    draft_mode = has_project_draft(project) and isinstance(draft.get("transcript_pages"), list)
    if draft_mode:
        raw_pages = draft["transcript_pages"]
        require(all(type(p) is dict for p in raw_pages), "source", "Malformed saved draft page")
    else:
        raw_pages = list(project.transcript_pages.filter(is_active=True).order_by("order", "id").values(*PAGE_FIELDS))
    require(1 <= len(raw_pages) <= MAX_PAGES, "page_limit", "Snapshot supports one to 32 saved pages")
    rows = get_studio_transcript_pages(project) if draft_mode else raw_pages
    pages = []
    for raw in rows:
        page = {key: deepcopy(raw.get(key)) for key in PAGE_FIELDS}
        for key, default in (("chunk_timeline", []), ("start_seconds", None), ("end_seconds", None), ("duration_seconds", None)):
            if key not in raw: page[key] = default
        # Missing display/narration/identity is an error, never invented text/index.
        pages.append(page)
    assets = PrivateRenderAssets(project.pk)
    selections = {row.page_key: row for row in VisualDraftSelection.objects.filter(project=project, owner=owner).select_related("candidate__proposal")}
    scenes, stamps, selection_stamps = [], [], []
    for page in pages:
        scene = page["editor_document"].get("scene", {}) if type(page["editor_document"]) is dict else {}
        require(type(scene) is dict, "scene", "Malformed saved editor scene")
        mode = scene.get("background_mode", "whiteboard" if page["whiteboard_mode"] else "original")
        require(mode in ("original", "whiteboard", "custom", "source_background"), "operation", "Unsupported saved scene mode")
        original = {"mode": mode, "scene": deepcopy(scene), "assets": _original_assets(project, scene, assets, pin), "missing_policy": "fail"}
        source = build_studio_visual_source(project, page["page_key"])
        stamps.append({"page_key": page["page_key"], "source_hash": source.source_hash, "source_revision": source.source_revision})
        row = selections.get(page["page_key"])
        selection_stamps.append({"page_key": page["page_key"], "version": row.version if row else 0,
            "state": row.state if row else "original", "candidate_id": str(row.candidate_id) if row and row.candidate_id else None})
        design = None
        if row and row.state == "selected":
            candidate = row.candidate
            require(candidate is not None and candidate.project_id == project.pk and candidate.owner_id == owner.pk
                and candidate.page_key == page["page_key"], "selection", "Selected candidate scope mismatch")
            preview = verified_preview(candidate, source)
            require(all(getattr(row, key) == value for key, value in design_identity(candidate, preview).items()), "selection", "Saved selection identity was modified")
            confirmation = None
            if candidate.proposal_id:
                review = VisualEvidenceReview.objects.get(proposal=candidate.proposal, owner=owner, action="confirm")
                confirmation = {"proposal_id": str(candidate.proposal_id), "proposal_hash": review.proposal_hash,
                    "owner_id": str(owner.pk), "action": review.action, "source_hash": review.source_hash, "created_at": review.created_at.isoformat()}
            design = {"candidate_id": str(candidate.pk), "selection_version": row.version,
                "source_hash": candidate.source_hash, "source_revision": candidate.source_revision,
                "design_source_hash": candidate.plan_payload["source_hash"], "plan_hash": candidate.plan_hash,
                "preview_hash": candidate.preview_hash, "contains_text": preview["contains_text"],
                "grounding": deepcopy(candidate.grounding_payload), "plan": deepcopy(candidate.plan_payload),
                "preview_metadata": {key: value for key, value in preview.items() if key != "svg"},
                "proposal": deepcopy(candidate.proposal.payload) if candidate.proposal_id else None,
                "confirmation": confirmation, "asset": assets.reference(preview["svg"].encode(), "svg", pin=pin)}
        scenes.append({"schema_version": SCENE_VERSION, "page_key": page["page_key"], "type": "completed_generated" if design else mode,
            "display_overlay": "none" if design else "existing_scene_policy", "narration_enabled": True,
            "subtitles_enabled": True, "original": original, "design": design})
    voice_row = VoiceProfile.objects.filter(user=owner).first()
    voice = {name: getattr(voice_row, name) for name in ("provider", "voice_id", "language", "speed", "pitch")} if voice_row else None
    project_fields = deepcopy(draft.get("project", {})) if draft_mode else {"title": project.title, "tts_settings": deepcopy(project.tts_settings)}
    tts = canonical_project_tts_settings(project_fields.get("tts_settings", project.tts_settings))
    planner = build_project_render_planner_manifest(project, pages=pages, voice_profile=voice_row)
    avatar = planner["pages"][pages[0]["page_key"]]["inputs"]["avatar"]
    require(not avatar.get("enabled"), "unsupported_avatar", "Avatar-enabled immutable capture requires future asset/model pinning")
    payload = {"schema_version": SNAPSHOT_VERSION, "project_id": str(project.pk), "owner_id": str(owner.pk), "revision_hash": "",
        "state": {"source_mode": "draft" if draft_mode else "active", "ordinary_draft": {k:v for k,v in draft.items() if k != "transcript_pages"},
            "project_fields": project_fields, "pages": pages, "source_stamps": stamps, "selections": selection_stamps,
            "voice": voice, "tts_settings": tts, "avatar": avatar},
        "scenes": scenes, "fonts": _fonts(assets, pin) if any(s["design"] for s in scenes) else {},
        "render_config": deepcopy(RENDER_CONFIG)}
    payload["revision_hash"] = revision_hash(payload)
    snapshot = FrozenRenderSnapshot(payload)
    if pin:
        for scene in scenes:
            if scene["design"]: validate_frozen_design(snapshot, scene, assets.read)
    return snapshot


@transaction.atomic
def current_render_revision(project_id, owner):
    project = _locked_project(project_id, owner)
    return _capture(project, owner).to_dict()["revision_hash"]


@transaction.atomic
def freeze_render_snapshot(project_id, owner, expected_revision_hash):
    project = _locked_project(project_id, owner)
    try:
        frozen = _capture(project, owner, pin=True)
        require(frozen.to_dict()["revision_hash"] == expected_revision_hash, "revision_conflict", "Saved revision changed")
        project.refresh_from_db()
        checked = _capture(project, owner, pin=False)
        require(checked.content_hash == frozen.content_hash, "revision_conflict", "Source changed during snapshot creation")
    except (DesignError, VisualDraftConflict) as exc:
        raise RenderSnapshotConflict(str(exc)) from exc
    row, _ = VisualRenderSnapshot.objects.get_or_create(project=project, owner=owner, snapshot_hash=frozen.content_hash,
        defaults={"schema_version": SNAPSHOT_VERSION, "revision_hash": frozen.to_dict()["revision_hash"], "payload": frozen.to_dict()})
    try:
        persisted = FrozenRenderSnapshot(row.payload)
        require(persisted.content_hash == frozen.content_hash == row.snapshot_hash
            and row.revision_hash == persisted.to_dict()["revision_hash"] and row.schema_version == SNAPSHOT_VERSION,
            "snapshot_integrity", "Existing snapshot failed integrity validation")
    except (DesignError, KeyError, TypeError, ValueError) as exc:
        raise RenderSnapshotConflict("Stored snapshot failed integrity validation.") from exc
    return row


def read_render_snapshot(project_id, owner, snapshot_id):
    row = get_object_or_404(VisualRenderSnapshot, pk=snapshot_id, project_id=project_id, owner=owner, project__user=owner)
    frozen = FrozenRenderSnapshot(row.payload)
    require(row.schema_version == SNAPSHOT_VERSION and row.snapshot_hash == frozen.content_hash
        and row.revision_hash == frozen.to_dict()["revision_hash"] and str(row.project_id) == frozen.to_dict()["project_id"]
        and str(row.owner_id) == frozen.to_dict()["owner_id"], "snapshot_integrity", "Stored immutable snapshot changed")
    assets = PrivateRenderAssets(project_id)
    for scene in frozen.to_dict()["scenes"]:
        for ref in scene["original"]["assets"].values(): assets.read(ref)
        if scene["design"]: validate_frozen_design(frozen, scene, assets.read)
    for ref in frozen.to_dict()["fonts"].values(): assets.read(ref)
    return row, frozen
