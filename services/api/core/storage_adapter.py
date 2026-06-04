"""Storage adapter primitives for runtime media helpers.

Only the filesystem adapter is active. The interface is intentionally small so
future object storage work has a clear boundary without changing path formats.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from django.conf import settings


class StorageAdapterError(RuntimeError):
    """Base error for storage adapter failures."""


class StoragePathTraversalError(StorageAdapterError, ValueError):
    """Raised when a relative storage path escapes the storage root."""


class FilesystemStorageAdapter:
    backend = "filesystem"

    def __init__(self, storage_root: str | os.PathLike[str] | None = None):
        raw_root = storage_root if storage_root is not None else getattr(settings, "STORAGE_ROOT", "storage_local")
        self.root = Path(str(raw_root or "")).expanduser().resolve()

    def resolve_path(self, relative_path: str | os.PathLike[str] | None = "") -> Path:
        rel = self._normalize_relative_path(relative_path)
        candidate = (self.root / rel).resolve() if rel else self.root
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise StoragePathTraversalError(f"storage path escapes root: {relative_path}") from exc
        return candidate

    def exists(self, relative_path: str | os.PathLike[str] | None = "") -> bool:
        return self.resolve_path(relative_path).exists()

    def read_bytes(self, relative_path: str | os.PathLike[str]) -> bytes:
        return self.resolve_path(relative_path).read_bytes()

    def write_bytes(self, relative_path: str | os.PathLike[str], data: bytes) -> None:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_text(self, relative_path: str | os.PathLike[str], *, encoding: str = "utf-8") -> str:
        return self.resolve_path(relative_path).read_text(encoding=encoding)

    def write_text(self, relative_path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8") -> None:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)

    def delete_file(self, relative_path: str | os.PathLike[str], *, missing_ok: bool = False) -> None:
        self.resolve_path(relative_path).unlink(missing_ok=missing_ok)

    def make_dirs(self, relative_path: str | os.PathLike[str] | None = "") -> None:
        self.resolve_path(relative_path).mkdir(parents=True, exist_ok=True)

    def iter_files(self, relative_path: str | os.PathLike[str] | None = "") -> Iterator[Path]:
        path = self.resolve_path(relative_path)
        if not path.exists():
            return
        if path.is_file():
            yield path
            return
        if not path.is_dir():
            return
        try:
            for child in path.rglob("*"):
                if child.is_file():
                    yield child
        except OSError:
            return

    def iter_children(self, relative_path: str | os.PathLike[str] | None = "") -> list[Path]:
        path = self.resolve_path(relative_path)
        if not path.exists() or not path.is_dir():
            return []
        try:
            return list(path.iterdir())
        except OSError:
            return []

    def _normalize_relative_path(self, relative_path: str | os.PathLike[str] | None) -> str:
        raw = str(relative_path or "").replace("\\", "/").strip()
        if raw in {"", "."}:
            return ""
        candidate = Path(raw)
        if raw.startswith("/") or candidate.is_absolute() or candidate.drive:
            raise StoragePathTraversalError(f"storage path must be relative: {relative_path}")
        parts = [part for part in raw.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise StoragePathTraversalError(f"storage path must stay inside root: {relative_path}")
        return "/".join(parts)


def get_storage_adapter(storage_root: str | os.PathLike[str] | None = None) -> FilesystemStorageAdapter:
    return FilesystemStorageAdapter(storage_root=storage_root)
