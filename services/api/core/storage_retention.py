"""Report-only storage retention and orphan detection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from django.db.utils import OperationalError, ProgrammingError

from core.models import AvatarRenderJob, Job, Project, TranslatedSubtitleTrack, UserProfile
from core.storage_adapter import FilesystemStorageAdapter, get_storage_adapter


RETENTION_SCAN_DIRS = (
    ("temporary", ".storage-smoke"),
    ("temporary", "tmp"),
    ("moderation_video_frames", "moderation/video_frames"),
    ("tts_cache", "tts"),
    ("tts_cache", "tts_cache"),
)

RETENTION_SUFFIXES = (".tmp", ".lock", ".part")


@dataclass(frozen=True)
class StorageFile:
    rel_path: str
    size_bytes: int
    mtime_epoch: float


def storage_root_path(storage_root: str | Path | None = None) -> Path:
    return get_storage_adapter(storage_root).root


def bytes_to_human(size_bytes: int) -> str:
    value = float(max(int(size_bytes or 0), 0))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size_bytes)} B"


def build_storage_report(
    *,
    storage_root: str | Path | None = None,
    older_than_days: int = 30,
    include_db: bool = True,
) -> dict[str, Any]:
    adapter = get_storage_adapter(storage_root)
    root = adapter.root
    cutoff_epoch = time.time() - (max(int(older_than_days), 1) * 24 * 60 * 60)
    categories = _capacity_categories(root, adapter)
    retention_candidates = _retention_candidates(root, adapter, cutoff_epoch)
    warnings: list[str] = []
    if include_db:
        try:
            orphan_candidates = _orphan_candidates(root, adapter, include_db=True)
            referenced_paths = _referenced_storage_paths()
            db_available = True
        except (OperationalError, ProgrammingError) as exc:
            orphan_candidates = []
            referenced_paths = set()
            db_available = False
            warnings.append(f"database_unavailable:{exc.__class__.__name__}")
    else:
        orphan_candidates = []
        referenced_paths = set()
        db_available = False
    referenced_existing_size = _referenced_existing_size(root, adapter, referenced_paths)

    return {
        "storage_root": str(root),
        "older_than_days": max(int(older_than_days), 1),
        "db_available": db_available,
        "warnings": warnings,
        "capacity": {
            "total_bytes": _tree_size(root, adapter),
            "referenced_existing_bytes": referenced_existing_size,
            "orphan_estimate_bytes": sum(item["size_bytes"] for item in orphan_candidates),
            "categories": categories,
        },
        "retention_candidates": retention_candidates,
        "orphan_candidates": orphan_candidates,
        "referenced_path_count": len(referenced_paths),
    }


def _capacity_categories(root: Path, adapter: FilesystemStorageAdapter) -> dict[str, dict[str, int]]:
    categories = {
        "uploads": root / "uploads",
        "render_outputs": None,
        "subtitles": None,
        "avatars": root / "avatars",
        "profiles": root / "profiles",
        "moderation_video_frames": root / "moderation" / "video_frames",
        "temporary": root / ".storage-smoke",
    }
    results: dict[str, dict[str, int]] = {}
    for name, path in categories.items():
        if path is None:
            continue
        results[name] = _path_summary(root, adapter, path)

    render_dirs = [child for child in _iter_children(root, adapter, root) if child.is_dir() and child.name.isdigit()]
    results["render_outputs"] = _paths_summary(root, adapter, render_dirs)
    subtitle_dirs = [path / "subtitles" for path in render_dirs if (path / "subtitles").exists()]
    results["subtitles"] = _paths_summary(root, adapter, subtitle_dirs)
    return results


def _retention_candidates(root: Path, adapter: FilesystemStorageAdapter, cutoff_epoch: float) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for category, rel_dir in RETENTION_SCAN_DIRS:
        base = root / rel_dir
        if not base.exists():
            continue
        for path in _iter_files(root, adapter, base):
            if _is_old(path, cutoff_epoch):
                candidates.append(_candidate_payload(root, path, category, "old_safe_area"))

    for path in _iter_files(root, adapter, root):
        if path.suffix.lower() in RETENTION_SUFFIXES and _is_old(path, cutoff_epoch):
            candidates.append(_candidate_payload(root, path, "temporary", "old_temp_suffix"))

    return sorted(_dedupe_candidates(candidates), key=lambda item: item["rel_path"])


def _orphan_candidates(root: Path, adapter: FilesystemStorageAdapter, *, include_db: bool) -> list[dict[str, Any]]:
    if not include_db:
        return []
    project_ids = {str(value) for value in Project.objects.values_list("id", flat=True)}
    user_ids = {str(value) for value in UserProfile.objects.values_list("user_id", flat=True)}
    candidates: list[dict[str, Any]] = []

    for child in _iter_children(root, adapter, root):
        if child.is_dir() and child.name.isdigit() and child.name not in project_ids:
            candidates.append(_directory_candidate(root, adapter, child, "orphan_project_render_dir", "project_missing"))

    uploads = root / "uploads"
    for child in _iter_children(root, adapter, uploads):
        if child.is_dir() and child.name.isdigit() and child.name not in project_ids:
            candidates.append(_directory_candidate(root, adapter, child, "orphan_upload_dir", "project_missing"))

    avatars = root / "avatars"
    for child in _iter_children(root, adapter, avatars):
        if child.is_dir() and child.name.isdigit() and child.name not in user_ids:
            candidates.append(_directory_candidate(root, adapter, child, "orphan_avatar_dir", "user_profile_missing"))

    return sorted(candidates, key=lambda item: item["rel_path"])


def _referenced_storage_paths() -> set[str]:
    refs: set[str] = set()
    for result_url, srt_url in Job.objects.exclude(project_id=None).values_list("result_url", "srt_url"):
        refs.update(_clean_rel_path(value) for value in (result_url, srt_url))
    for srt_path, vtt_path in TranslatedSubtitleTrack.objects.values_list("srt_path", "vtt_path"):
        refs.update(_clean_rel_path(value) for value in (srt_path, vtt_path))
    for output_path in AvatarRenderJob.objects.exclude(output_path="").values_list("output_path", flat=True):
        refs.add(_clean_rel_path(output_path))
    for fields in Project.objects.values(
        "cover_image_original",
        "cover_image_processed",
        "avatar_output_path",
    ):
        refs.update(_clean_rel_path(value) for value in fields.values())
    for fields in UserProfile.objects.values(
        "avatar_image_original",
        "avatar_image_processed",
        "avatar_video_original",
        "avatar_video_processed",
        "avatar_preview_video",
        "avatar_last_preview_path",
        "banner_image_original",
        "banner_image_processed",
        "logo_image_original",
        "logo_image_processed",
    ):
        refs.update(_clean_rel_path(value) for value in fields.values())
    refs.discard("")
    return refs


def _referenced_existing_size(root: Path, adapter: FilesystemStorageAdapter, refs: set[str]) -> int:
    total = 0
    for rel_path in refs:
        path = _safe_child(root, adapter, rel_path)
        if path and path.exists() and path.is_file():
            total += _file_size(path)
    return total


def _clean_rel_path(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not raw or "://" in raw or raw.startswith("../") or "/../" in raw:
        return ""
    return raw


def _safe_child(root: Path, adapter: FilesystemStorageAdapter, rel_path: str) -> Path | None:
    try:
        return adapter.resolve_path(rel_path)
    except (OSError, ValueError):
        return None


def _path_summary(root: Path, adapter: FilesystemStorageAdapter, path: Path) -> dict[str, int]:
    return _paths_summary(root, adapter, [path])


def _paths_summary(root: Path, adapter: FilesystemStorageAdapter, paths: list[Path]) -> dict[str, int]:
    file_count = 0
    total = 0
    for path in paths:
        for file_path in _iter_files(root, adapter, path):
            file_count += 1
            total += _file_size(file_path)
    return {"bytes": total, "files": file_count}


def _tree_size(root: Path, adapter: FilesystemStorageAdapter) -> int:
    return _path_summary(root, adapter, root)["bytes"]


def _iter_children(root: Path, adapter: FilesystemStorageAdapter, path: Path) -> list[Path]:
    rel_path = _rel_path(root, path)
    try:
        return adapter.iter_children(rel_path)
    except ValueError:
        return []


def _iter_files(root: Path, adapter: FilesystemStorageAdapter, path: Path):
    rel_path = _rel_path(root, path)
    try:
        yield from adapter.iter_files(rel_path)
    except ValueError:
        return


def _is_old(path: Path, cutoff_epoch: float) -> bool:
    try:
        return path.stat().st_mtime <= cutoff_epoch
    except OSError:
        return False


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _candidate_payload(root: Path, path: Path, category: str, reason: str) -> dict[str, Any]:
    return {
        "category": category,
        "reason": reason,
        "rel_path": _rel_path(root, path),
        "size_bytes": _file_size(path),
        "mtime_epoch": path.stat().st_mtime,
        "kind": "file",
    }


def _directory_candidate(root: Path, adapter: FilesystemStorageAdapter, path: Path, category: str, reason: str) -> dict[str, Any]:
    summary = _path_summary(root, adapter, path)
    return {
        "category": category,
        "reason": reason,
        "rel_path": _rel_path(root, path),
        "size_bytes": summary["bytes"],
        "file_count": summary["files"],
        "kind": "directory",
    }


def _rel_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        key = str(candidate.get("rel_path") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result
