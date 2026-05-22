"""Studio draft helpers.

Drafts are private Studio state. Public catalog/watch/channel code should keep
reading active Project and TranscriptPage fields directly.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.models import Project, TranscriptPage
from core.serializers import canonical_project_tts_settings


def _iso_now() -> str:
    return timezone.now().isoformat()


def _draft_page_from_active(page: TranscriptPage) -> dict[str, Any]:
    return {
        "id": page.id,
        "order": page.order,
        "source_slide_index": page.source_slide_index,
        "split_index": page.split_index,
        "page_key": page.page_key,
        "original_text": page.original_text or "",
        "narration_text": page.narration_text or "",
        "rich_text_html": page.rich_text_html or "",
        "editor_document": deepcopy(page.editor_document or {}),
        "whiteboard_mode": bool(page.whiteboard_mode),
        "subtitle_chunks": deepcopy(page.subtitle_chunks or []),
    }


def _active_pages(project: Project):
    transcript_rel = getattr(project, "transcript_pages", None)
    if transcript_rel is None:
        return TranscriptPage.objects.none()
    return transcript_rel.filter(is_active=True).order_by("order", "id")


def get_project_draft_data(project: Project) -> dict[str, Any]:
    draft_data = getattr(project, "draft_data", None)
    return deepcopy(draft_data) if isinstance(draft_data, dict) else {}


def build_draft_from_active_project(project: Project) -> dict[str, Any]:
    now = _iso_now()
    return {
        "project": {
            "title": project.title or "",
            "tts_settings": canonical_project_tts_settings(getattr(project, "tts_settings", None)),
            "cover_image_original": project.cover_image_original or "",
            "cover_image_processed": project.cover_image_processed or "",
        },
        "transcript_pages": [_draft_page_from_active(page) for page in _active_pages(project)],
        "metadata": {
            "created_at": now,
            "updated_at": now,
            "dirty": False,
        },
    }


def ensure_project_draft_data(project: Project) -> dict[str, Any]:
    draft_data = get_project_draft_data(project)
    if not draft_data:
        return build_draft_from_active_project(project)

    active_draft = build_draft_from_active_project(project)
    draft_data.setdefault("project", active_draft["project"])
    draft_data.setdefault("transcript_pages", active_draft["transcript_pages"])
    metadata = draft_data.setdefault("metadata", {})
    metadata.setdefault("created_at", _iso_now())
    metadata.setdefault("updated_at", metadata["created_at"])
    metadata.setdefault("dirty", True)
    _normalize_draft_dirty_metadata(metadata)
    return draft_data


def has_project_draft(project: Project) -> bool:
    metadata = get_project_draft_data(project).get("metadata")
    return bool(isinstance(metadata, dict) and metadata.get("dirty"))


def has_dirty_draft(project: Project) -> bool:
    return has_project_draft(project)


def draft_requires_render(project: Project) -> bool:
    metadata = get_project_draft_data(project).get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("dirty"):
        return False
    if "render_required" in metadata:
        return bool(metadata.get("render_required"))
    if not any(
        key in metadata
        for key in (
            "metadata_dirty",
            "cover_dirty",
            "transcript_dirty",
            "source_dirty",
            "tts_dirty",
            "background_dirty",
            "visual_assets_dirty",
        )
    ):
        return True
    return bool(
        metadata.get("transcript_dirty")
        or metadata.get("source_dirty")
        or metadata.get("tts_dirty")
        or metadata.get("background_dirty")
        or metadata.get("visual_assets_dirty")
    )


def mark_draft_dirty(
    draft_data: dict[str, Any],
    *,
    metadata_dirty: bool = False,
    cover_dirty: bool = False,
    transcript_dirty: bool = False,
    source_dirty: bool = False,
    tts_dirty: bool = False,
    background_dirty: bool = False,
    visual_assets_dirty: bool = False,
    render_required: bool | None = None,
) -> dict[str, Any]:
    metadata = draft_data.setdefault("metadata", {})
    if metadata_dirty:
        metadata["metadata_dirty"] = True
    if cover_dirty:
        metadata["cover_dirty"] = True
    if transcript_dirty:
        metadata["transcript_dirty"] = True
    if source_dirty:
        metadata["source_dirty"] = True
    if tts_dirty:
        metadata["tts_dirty"] = True
    if background_dirty:
        metadata["background_dirty"] = True
    if visual_assets_dirty:
        metadata["visual_assets_dirty"] = True
    if render_required is None:
        render_required = bool(
            transcript_dirty
            or source_dirty
            or tts_dirty
            or background_dirty
            or visual_assets_dirty
        )
    metadata["render_required"] = bool(metadata.get("render_required")) or bool(render_required)
    _normalize_draft_dirty_metadata(metadata)
    return draft_data


def _normalize_draft_dirty_metadata(metadata: dict[str, Any]) -> None:
    metadata.setdefault("metadata_dirty", False)
    metadata.setdefault("cover_dirty", False)
    metadata.setdefault("transcript_dirty", False)
    metadata.setdefault("source_dirty", False)
    metadata.setdefault("tts_dirty", False)
    metadata.setdefault("background_dirty", False)
    metadata.setdefault("visual_assets_dirty", False)
    metadata.setdefault("render_required", False)


def get_draft_project_fields(project: Project) -> dict[str, Any]:
    draft_data = get_project_draft_data(project)
    project_fields = draft_data.get("project")
    return deepcopy(project_fields) if isinstance(project_fields, dict) else {}


def _ordered_draft_pages(draft_data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = draft_data.get("transcript_pages")
    if not isinstance(pages, list):
        return []
    safe_pages = [deepcopy(page) for page in pages if isinstance(page, dict)]
    return sorted(safe_pages, key=lambda page: (_draft_int(page.get("order"), 0), str(page.get("page_key") or ""), _draft_int(page.get("id"), 0)))


def get_draft_transcript_pages(project: Project) -> list[dict[str, Any]]:
    draft_data = get_project_draft_data(project)
    return _ordered_draft_pages(draft_data) if has_project_draft(project) else []


def get_studio_transcript_pages(project: Project) -> list[dict[str, Any]]:
    draft_data = get_project_draft_data(project)
    pages = draft_data.get("transcript_pages")
    if isinstance(pages, list) and has_project_draft(project):
        return _ordered_draft_pages(draft_data)
    return build_draft_from_active_project(project)["transcript_pages"]


def save_project_draft_data(project: Project, draft_data: dict[str, Any], *, dirty: bool = True) -> dict[str, Any]:
    metadata = draft_data.setdefault("metadata", {})
    metadata.setdefault("created_at", _iso_now())
    metadata["updated_at"] = _iso_now()
    metadata["dirty"] = bool(dirty)
    _normalize_draft_dirty_metadata(metadata)
    project.draft_data = draft_data
    project.save(update_fields=["draft_data", "updated_at"])
    return draft_data


def clear_project_draft(project: Project) -> None:
    project.draft_data = {}
    project.save(update_fields=["draft_data", "updated_at"])


def _draft_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_page_key(raw_key: Any, existing: set[str], fallback_index: int) -> str:
    base = str(raw_key or f"draft-page-{fallback_index + 1}").strip() or f"draft-page-{fallback_index + 1}"
    candidate = base[:64]
    suffix = 2
    while candidate in existing:
        marker = f"-{suffix}"
        candidate = f"{base[: max(1, 64 - len(marker))]}{marker}"
        suffix += 1
    existing.add(candidate)
    return candidate


def promote_project_draft(
    project: Project,
    *,
    job=None,
    render_outputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote dirty Studio draft data into active public project fields.

    The transaction keeps active transcript rows unchanged if any part of the
    promotion fails. Render artifacts are intentionally handled by the worker
    after this returns successfully.
    """
    render_outputs = render_outputs if isinstance(render_outputs, dict) else {}
    page_timeline = {
        str(item.get("page_key") or ""): item
        for item in (render_outputs.get("page_timeline") or [])
        if isinstance(item, dict) and str(item.get("page_key") or "")
    }

    with transaction.atomic():
        locked_project = Project.objects.select_for_update().get(pk=project.pk)
        draft_data = get_project_draft_data(locked_project)
        if not has_project_draft(locked_project):
            return {"status": "skipped_no_dirty_draft", "project_id": locked_project.id}

        project_fields = draft_data.get("project") if isinstance(draft_data.get("project"), dict) else {}
        draft_pages = _ordered_draft_pages(draft_data)

        update_fields: list[str] = []
        if "title" in project_fields:
            locked_project.title = str(project_fields.get("title") or "")
            update_fields.append("title")
        if isinstance(project_fields.get("tts_settings"), dict):
            locked_project.tts_settings = canonical_project_tts_settings(project_fields.get("tts_settings"))
            update_fields.append("tts_settings")
        if "cover_image_original" in project_fields:
            locked_project.cover_image_original = str(project_fields.get("cover_image_original") or "")
            update_fields.append("cover_image_original")
        if "cover_image_processed" in project_fields:
            locked_project.cover_image_processed = str(project_fields.get("cover_image_processed") or "")
            update_fields.append("cover_image_processed")

        existing_pages = list(TranscriptPage.objects.select_for_update().filter(project=locked_project))
        active_by_id = {page.id: page for page in existing_pages if page.id}
        by_key = {page.page_key: page for page in existing_pages if page.page_key}
        existing_keys: set[str] = set()
        promoted_page_ids: set[int] = set()

        for order, page_data in enumerate(draft_pages):
            draft_id = _draft_int(page_data.get("id"), default=0)
            requested_key = page_data.get("page_key")
            page = active_by_id.get(draft_id) if draft_id > 0 else None
            if page is None and requested_key:
                page = by_key.get(str(requested_key))
            if page is None:
                page = TranscriptPage(project=locked_project)

            page_key = str(page.page_key or requested_key or "").strip()
            if not page_key or page_key in existing_keys:
                page_key = _clean_page_key(page_key, existing_keys, order)
            else:
                existing_keys.add(page_key)

            timeline = page_timeline.get(page_key) or {}
            page.page_key = page_key
            page.order = order
            page.source_slide_index = _draft_int(page_data.get("source_slide_index"), default=order)
            page.split_index = _draft_int(page_data.get("split_index"), default=0)
            page.original_text = str(page_data.get("original_text") or "")
            page.narration_text = str(page_data.get("narration_text") or "")
            page.rich_text_html = str(page_data.get("rich_text_html") or "")
            page.editor_document = deepcopy(page_data.get("editor_document") or {})
            page.subtitle_chunks = deepcopy(page_data.get("subtitle_chunks") or [])
            page.whiteboard_mode = bool(page_data.get("whiteboard_mode"))
            page.is_active = True
            page.deleted_at = None
            if timeline:
                page.start_seconds = float(timeline.get("start") or 0.0)
                page.end_seconds = float(timeline.get("end") or 0.0)
                page.duration_seconds = float(timeline.get("duration") or 0.0)
                page.chunk_timeline = deepcopy(timeline.get("chunk_timeline") or [])
                if timeline.get("subtitle_chunks"):
                    page.subtitle_chunks = deepcopy(timeline.get("subtitle_chunks") or [])
            page.save()
            promoted_page_ids.add(page.id)

        now = timezone.now()
        for page in existing_pages:
            if page.id not in promoted_page_ids and bool(getattr(page, "is_active", True)):
                page.is_active = False
                page.deleted_at = now
                page.save(update_fields=["is_active", "deleted_at", "updated_at"])

        locked_project.draft_data = {}
        update_fields.append("draft_data")
        locked_project.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])

    return {
        "status": "promoted",
        "project_id": project.id,
        "page_count": len(draft_pages),
        "job_id": getattr(job, "id", None),
    }


def mark_draft_moderation_failed(project: Project, moderation_result: dict[str, Any]) -> dict[str, Any]:
    draft_data = ensure_project_draft_data(project)
    metadata = draft_data.setdefault("metadata", {})
    moderation_status = str(moderation_result.get("moderation_status") or moderation_result.get("final_decision") or "revision_required")
    draft_summary = dict(moderation_result.get("moderation_summary") or moderation_result.get("summary") or {})
    if not draft_summary:
        draft_summary = {
            "moderation_status": moderation_status,
            "message": str(moderation_result.get("message") or "Draft blocked by moderation."),
            "finding_count": int(moderation_result.get("finding_count") or 0),
            "findings": list(moderation_result.get("findings") or []),
            "run_id": moderation_result.get("run_id"),
        }

    metadata.update(
        {
            "dirty": True,
            "moderation_status": moderation_status,
            "moderation_failed_at": _iso_now(),
            "moderation": draft_summary,
        }
    )
    draft_data["metadata"] = metadata

    summary = dict(project.moderation_summary or {})
    summary["draft_moderation"] = draft_summary
    project.draft_data = draft_data
    project.moderation_summary = summary
    project.save(update_fields=["draft_data", "moderation_summary", "updated_at"])
    return draft_summary
