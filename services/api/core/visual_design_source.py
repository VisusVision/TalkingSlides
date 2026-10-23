"""Read-only bridge from saved Studio content to the pure Phase 1 contracts."""
from dataclasses import dataclass
from pathlib import Path
import hashlib

from rest_framework.exceptions import NotFound, ValidationError

from core.drafts import get_studio_transcript_pages, has_project_draft
from core.storage_adapter import FilesystemStorageAdapter, StorageAdapterError
from core.visual_design.schemas import SOURCE_VERSION, DesignError, SlideSourceSnapshot, content_hash

ADAPTER_VERSION = "studio-source.v2"
MAX_PAGES = 1000
MAX_ASSET_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class StudioVisualSource:
    page_key: str
    source_mode: str
    source_revision: str
    source_hash: str
    snapshot: SlideSourceSnapshot | None
    errors: tuple[str, ...] = ()
    original_available: bool = False
    asset_page_ref: str | None = None

    def metadata(self):
        return {
            "adapter_version": ADAPTER_VERSION,
            "page_key": self.page_key,
            "source_mode": self.source_mode,
            "source_revision": self.source_revision,
            "source_hash": self.source_hash,
            "supported": self.snapshot is not None,
            "errors": list(self.errors),
            "evidence_policy": "explicit_text_structures.v1",
            "original_available": self.original_available,
            "asset_page_ref": self.asset_page_ref,
        }


def _asset_identity(project, scene):
    """Fingerprint bounded project-scoped originals, including their actual bytes.

    This is a revision fingerprint, not semantic image analysis.
    No SVG or external asset is loaded, and no storage path leaves the adapter.
    """
    raw = scene.get("original_background_path")
    if not isinstance(raw, str) or not raw:
        return None
    parts = Path(raw.replace("\\", "/")).parts
    allowed = (str(project.pk),) if parts[:1] == (str(project.pk),) else ("uploads", str(project.pk))
    if parts[:len(allowed)] != allowed or any(p in {"..", "."} for p in parts):
        return {"reference_hash": content_hash("asset-reference.v1", raw), "available": False}
    try:
        storage = FilesystemStorageAdapter()
        path = storage.resolve_path(raw)
        # Resolve must still be under this project's own namespace after symlinks.
        path.relative_to(storage.root.joinpath(*allowed))
        stat = path.stat()
        available = path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        identity = {"reference_hash": content_hash("asset-reference.v1", raw), "available": available, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        if available:
            if stat.st_size > MAX_ASSET_BYTES:
                return {**identity, "unverified": True}
            digest, total = hashlib.sha256(), 0
            with path.open("rb") as asset_file:
                while chunk := asset_file.read(256 * 1024):
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        return {**identity, "unverified": True}
                    digest.update(chunk)
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                return {**identity, "unverified": True}
            identity["sha256"] = digest.hexdigest()
        return identity
    except (OSError, ValueError, StorageAdapterError):
        return {"reference_hash": content_hash("asset-reference.v1", raw), "available": False}


def build_studio_visual_source(project, page_key):
    """Use stable page keys only. Never ensure/save/promote a draft or sync pages.

    Saved private drafts take precedence exactly as in get_studio_transcript_pages.
    Unsaved browser state is deliberately not an accepted input to this adapter.
    No existing extraction/Intelligence structure is trusted as verified evidence.
    """
    if not isinstance(page_key, str) or not page_key or len(page_key) > 64:
        raise ValidationError({"page_key": "An existing stable page key is required."})
    draft = project.draft_data if isinstance(project.draft_data, dict) else {}
    draft_pages = draft.get("transcript_pages")
    if (isinstance(draft_pages, list) and len(draft_pages) > MAX_PAGES) or project.transcript_pages.filter(is_active=True).count() > MAX_PAGES:
        raise ValidationError({"page_key": "This lesson exceeds the bounded visual-preview page limit."})
    pages = get_studio_transcript_pages(project)
    matching = [p for p in pages if p.get("page_key") == page_key]
    if not matching:
        raise NotFound("The selected page is no longer in the saved Studio source.")
    if len(matching) != 1:
        raise ValidationError({"page_key": "Conflicting saved page identities."})
    page = matching[0]
    mode = "draft" if has_project_draft(project) and isinstance(draft_pages, list) else "active"
    document = page.get("editor_document")
    document = document if isinstance(document, dict) else {}
    scene = document.get("scene")
    scene = scene if isinstance(scene, dict) else {}
    asset = _asset_identity(project, scene)
    # Persisted revision stamps detect restores/reorders even when text is identical.
    # Do not hash the transient timestamps built by build_draft_from_active_project.
    active_stamps = list(project.transcript_pages.filter(page_key=page_key).order_by("id").values_list("page_key", "is_active", "updated_at"))
    structure = [{k: p.get(k) for k in ("page_key", "order", "source_slide_index", "split_index")} for p in pages]
    revision_input = {
        "adapter_version": ADAPTER_VERSION,
        "project_id": str(project.pk), "page_key": page_key, "source_mode": mode,
        "active_stamps": [[key, active, stamp.isoformat()] for key, active, stamp in active_stamps],
        "draft_updated_at": (draft.get("metadata") or {}).get("updated_at") if mode == "draft" else None,
        "structure": structure,
        "page": {k: page.get(k) for k in ("original_text", "narration_text", "source_slide_index", "split_index", "rich_text_html", "editor_document", "whiteboard_mode")},
        "original_asset": asset,
    }
    revision = content_hash(ADAPTER_VERSION, revision_input)
    value = {
        "schema_version": SOURCE_VERSION, "project_id": str(project.pk), "page_key": page_key,
        "source_slide_index": page.get("source_slide_index"), "split_index": page.get("split_index"),
        "original_display_text": page.get("original_text"), "protected_narration": page.get("narration_text"),
        "source_revision": revision, "evidence": [],
    }
    if asset and asset["available"]:
        value["original_asset_ref"] = "asset:" + asset["reference_hash"].split(":", 1)[1]
    original_kwargs = {"original_available": bool(asset and asset["available"]),
                       "asset_page_ref": page_key if mode == "draft" else str(page["id"]) if type(page.get("id")) is int and page["id"] > 0 else None}
    try:
        if asset and asset.get("unverified"):
            raise DesignError("asset_limit", "Original asset cannot be fingerprinted safely")
        snapshot = SlideSourceSnapshot(value)
        return StudioVisualSource(page_key, mode, revision, snapshot.content_hash, snapshot, **original_kwargs)
    except DesignError as exc:
        # Unsupported legacy content remains untouched. Keep its digest, not a
        # truncated/re-written substitute. Messages contain codes, never source text.
        return StudioVisualSource(page_key, mode, revision, content_hash("unsupported-source.v1", value), None, (f"{exc.code}: source is outside the Phase 1 contract",), **original_kwargs)
