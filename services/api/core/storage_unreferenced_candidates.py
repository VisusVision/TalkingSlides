"""Report-only unreferenced storage candidate detection."""

from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from core.storage_reference_inventory import build_storage_reference_inventory


def build_storage_unreferenced_candidates_report(
    *,
    storage_root: str | Path | None = None,
    project_id: int | str | None = None,
    older_than_days: int | None = None,
) -> dict[str, Any]:
    """Compare filesystem files with the reference inventory without mutating storage."""

    root = Path(storage_root or getattr(settings, "STORAGE_ROOT", "storage_local")).expanduser().resolve()
    inventory = build_storage_reference_inventory(storage_root=root, project_id=project_id, include_missing=True)
    referenced_paths, referenced_globs = _referenced_path_sets(inventory)
    files, skipped_paths, unsafe_paths = _walk_storage_files(root, project_id=project_id)
    cutoff_epoch = _cutoff_epoch(older_than_days)
    candidates: list[dict[str, Any]] = []

    for file_info in files:
        rel_path = file_info["path"]
        if cutoff_epoch is not None and file_info["mtime_epoch"] > cutoff_epoch:
            skipped_paths += 1
            continue
        if rel_path in referenced_paths or any(fnmatch.fnmatchcase(rel_path, pattern) for pattern in referenced_globs):
            continue
        candidates.append(
            {
                "path": rel_path,
                "size_bytes": file_info["size_bytes"],
                "mtime": file_info["mtime"],
                "reason": "not_found_in_reference_inventory",
                "risk_level": "review_required",
                "delete_eligible": False,
            }
        )

    candidates.sort(key=lambda item: item["path"])

    return {
        "mode": "read-only/report-only",
        "storage_root": str(root),
        "project_id": str(project_id) if project_id not in (None, "") else "",
        "older_than_days": int(older_than_days) if older_than_days is not None else None,
        "db_available": bool(inventory.get("db_available")),
        "warnings": list(inventory.get("warnings") or []),
        "summary": {
            "total_files_scanned": len(files),
            "total_referenced_paths": len(referenced_paths) + len(referenced_globs),
            "total_candidates": len(candidates),
            "total_candidate_bytes": sum(int(item["size_bytes"] or 0) for item in candidates),
            "skipped_paths": skipped_paths,
            "unsafe_paths": unsafe_paths,
        },
        "candidates": candidates,
    }


def _referenced_path_sets(inventory: dict[str, Any]) -> tuple[set[str], set[str]]:
    referenced_paths: set[str] = set()
    referenced_globs: set[str] = set()
    for entry in inventory.get("references") or []:
        raw_path = str((entry or {}).get("path") or "").strip().replace("\\", "/")
        if not raw_path or _is_unsafe_rel_path(raw_path):
            continue
        normalized = _normalize_rel_path(raw_path)
        if "*" in normalized:
            referenced_globs.add(normalized)
        else:
            referenced_paths.add(normalized)
    return referenced_paths, referenced_globs


def _walk_storage_files(root: Path, *, project_id: int | str | None) -> tuple[list[dict[str, Any]], int, int]:
    files: list[dict[str, Any]] = []
    skipped_paths = 0
    unsafe_paths = 0
    if not root.exists() or not root.is_dir():
        return files, skipped_paths, unsafe_paths

    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)
        safe_current = _safe_relative_path(root, current)
        if safe_current is None:
            unsafe_paths += 1
            dirnames[:] = []
            continue

        kept_dirnames = []
        for dirname in sorted(dirnames):
            child = current / dirname
            if child.is_symlink():
                skipped_paths += 1
                continue
            if _safe_relative_path(root, child) is None:
                unsafe_paths += 1
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink():
                skipped_paths += 1
                continue
            rel_path = _safe_relative_path(root, path)
            if rel_path is None:
                unsafe_paths += 1
                continue
            if not _path_in_project_scope(rel_path, project_id):
                skipped_paths += 1
                continue
            try:
                stat = path.stat()
            except OSError:
                skipped_paths += 1
                continue
            if not path.is_file():
                skipped_paths += 1
                continue
            files.append(
                {
                    "path": rel_path,
                    "size_bytes": int(stat.st_size),
                    "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "mtime_epoch": float(stat.st_mtime),
                }
            )

    files.sort(key=lambda item: item["path"])
    return files, skipped_paths, unsafe_paths


def _safe_relative_path(root: Path, path: Path) -> str | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        rel_path = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    if _is_unsafe_rel_path(rel_path):
        return None
    return rel_path


def _path_in_project_scope(rel_path: str, project_id: int | str | None) -> bool:
    if project_id in (None, ""):
        return True
    project = str(project_id)
    prefixes = (
        f"{project}/",
        f"uploads/{project}/",
        f"projects/{project}/",
    )
    return rel_path == project or any(rel_path.startswith(prefix) for prefix in prefixes)


def _normalize_rel_path(raw_path: str) -> str:
    return "/".join(part for part in raw_path.replace("\\", "/").split("/") if part not in {"", "."})


def _is_unsafe_rel_path(raw_path: str) -> bool:
    raw = str(raw_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or "://" in raw:
        return True
    if len(raw) >= 3 and raw[0].isalpha() and raw[1:3] == ":/":
        return True
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    return any(part == ".." for part in parts)


def _cutoff_epoch(older_than_days: int | None) -> float | None:
    if older_than_days is None:
        return None
    days = max(int(older_than_days), 1)
    return datetime.now(tz=timezone.utc).timestamp() - (days * 24 * 60 * 60)
