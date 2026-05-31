"""Small filesystem storage checks for production readiness."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings


class StorageHealthError(RuntimeError):
    """Raised when the configured filesystem storage root is not usable."""


def configured_storage_root(storage_root: str | os.PathLike[str] | None = None) -> Path:
    raw_value = storage_root if storage_root is not None else getattr(settings, "STORAGE_ROOT", "storage_local")
    return Path(str(raw_value or "")).expanduser()


def validate_filesystem_storage_root(storage_root: str | os.PathLike[str] | None = None) -> Path:
    root = configured_storage_root(storage_root).resolve()
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

    root = validate_filesystem_storage_root(storage_root)
    smoke_dir = root / namespace
    probe = smoke_dir / f"probe-{uuid.uuid4().hex}.txt"
    payload = f"visus-storage-smoke:{uuid.uuid4().hex}\n".encode("utf-8")

    try:
        smoke_dir.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(payload)
        if probe.read_bytes() != payload:
            raise StorageHealthError("storage smoke readback mismatch")
        probe.unlink()
        try:
            smoke_dir.rmdir()
        except OSError:
            pass
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
