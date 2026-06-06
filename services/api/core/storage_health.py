"""Small filesystem storage checks for production readiness."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from core.storage_adapter import StoragePathTraversalError, get_storage_adapter


class StorageHealthError(RuntimeError):
    """Raised when the configured filesystem storage root is not usable."""


def configured_storage_root(storage_root: str | os.PathLike[str] | None = None) -> Path:
    return get_storage_adapter(storage_root).root


def validate_filesystem_storage_root(storage_root: str | os.PathLike[str] | None = None) -> Path:
    root = configured_storage_root(storage_root)
    if not root.exists():
        raise StorageHealthError(f"storage root does not exist: {root}")
    if not root.is_dir():
        raise StorageHealthError(f"storage root is not a directory: {root}")
    if not os.access(root, os.R_OK):
        raise StorageHealthError(f"storage root is not readable: {root}")
    if not os.access(root, os.W_OK):
        raise StorageHealthError(f"storage root is not writable: {root}")
    return root


def run_filesystem_storage_smoke(
    storage_root: str | os.PathLike[str] | None = None,
    *,
    namespace: str = ".storage-smoke",
) -> dict[str, Any]:
    """Write, read, and delete a small probe file under STORAGE_ROOT."""

    adapter = get_storage_adapter(storage_root)
    root = validate_filesystem_storage_root(storage_root)
    smoke_dir_rel = str(namespace or ".storage-smoke")
    probe_rel = f"{smoke_dir_rel}/probe-{uuid.uuid4().hex}.txt"
    payload = f"visus-storage-smoke:{uuid.uuid4().hex}\n".encode("utf-8")

    try:
        adapter.make_dirs(smoke_dir_rel)
        adapter.write_bytes(probe_rel, payload)
        if adapter.read_bytes(probe_rel) != payload:
            raise StorageHealthError("storage smoke readback mismatch")
        adapter.delete_file(probe_rel)
        try:
            adapter.resolve_path(smoke_dir_rel).rmdir()
        except OSError:
            pass
    except StoragePathTraversalError as exc:
        raise StorageHealthError(f"storage smoke failed: {exc}") from exc
    except StorageHealthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StorageHealthError(f"storage smoke failed: {exc}") from exc

    return {
        "status": "ok",
        "backend": "filesystem",
        "storage_root": str(root),
        "write": True,
        "read": True,
        "delete": True,
    }
