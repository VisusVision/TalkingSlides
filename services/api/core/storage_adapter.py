"""Storage adapter primitives for runtime media helpers.

Filesystem storage remains the default backend. The S3-compatible adapter is a
feature-flagged foundation for future runtime migration work and is only
selected when explicit settings request it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

from django.conf import settings


class StorageAdapterError(RuntimeError):
    """Base error for storage adapter failures."""


class StoragePathTraversalError(StorageAdapterError, ValueError):
    """Raised when a relative storage path escapes the storage root."""


class StorageConfigurationError(StorageAdapterError):
    """Raised when storage backend configuration is invalid or incomplete."""


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


class S3StorageAdapter:
    backend = "s3"

    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        key_prefix: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str | None = None,
        use_ssl: bool | None = None,
        verify_ssl: bool | None = None,
        client: Any | None = None,
        session: Any | None = None,
    ):
        self.bucket_name = (bucket_name if bucket_name is not None else getattr(settings, "S3_BUCKET_NAME", "")).strip()
        self.endpoint_url = endpoint_url if endpoint_url is not None else getattr(settings, "S3_ENDPOINT_URL", None)
        self.access_key_id = (
            access_key_id if access_key_id is not None else getattr(settings, "S3_ACCESS_KEY_ID", "")
        ).strip()
        self.secret_access_key = (
            secret_access_key if secret_access_key is not None else getattr(settings, "S3_SECRET_ACCESS_KEY", "")
        ).strip()
        self.region_name = region_name if region_name is not None else getattr(settings, "S3_REGION_NAME", None)
        self.use_ssl = bool(getattr(settings, "S3_USE_SSL", True) if use_ssl is None else use_ssl)
        self.verify_ssl = bool(getattr(settings, "S3_VERIFY_SSL", True) if verify_ssl is None else verify_ssl)
        self.key_prefix = self._normalize_prefix(
            key_prefix if key_prefix is not None else getattr(settings, "S3_KEY_PREFIX", "")
        )

        missing = []
        if not self.bucket_name:
            missing.append("S3_BUCKET_NAME")
        if not self.access_key_id:
            missing.append("S3_ACCESS_KEY_ID")
        if not self.secret_access_key:
            missing.append("S3_SECRET_ACCESS_KEY")
        if missing:
            raise StorageConfigurationError(
                "S3 storage backend requires " + ", ".join(missing) + " to be configured."
            )

        self.client = client or self._build_client(session=session)

    def object_key(self, relative_path: str | os.PathLike[str] | None = "") -> str:
        rel = self._normalize_relative_path(relative_path)
        if not rel:
            return self.key_prefix
        return f"{self.key_prefix}/{rel}" if self.key_prefix else rel

    def exists(self, relative_path: str | os.PathLike[str] | None = "") -> bool:
        key = self.object_key(relative_path)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise

    def read_bytes(self, relative_path: str | os.PathLike[str]) -> bytes:
        key = self.object_key(relative_path)
        response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        return response["Body"].read()

    def write_bytes(self, relative_path: str | os.PathLike[str], data: bytes) -> None:
        key = self.object_key(relative_path)
        self.client.put_object(Bucket=self.bucket_name, Key=key, Body=data)

    def read_text(self, relative_path: str | os.PathLike[str], *, encoding: str = "utf-8") -> str:
        return self.read_bytes(relative_path).decode(encoding)

    def write_text(self, relative_path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(relative_path, text.encode(encoding))

    def delete_file(self, relative_path: str | os.PathLike[str], *, missing_ok: bool = False) -> None:
        key = self.object_key(relative_path)
        if not missing_ok or self.exists(relative_path):
            self.client.delete_object(Bucket=self.bucket_name, Key=key)

    def make_dirs(self, relative_path: str | os.PathLike[str] | None = "") -> None:
        self.object_key(relative_path)

    def iter_files(self, relative_path: str | os.PathLike[str] | None = ""):
        raise NotImplementedError(
            "S3StorageAdapter.iter_files is not enabled because object listing semantics "
            "need a separate reviewed retention/cleanup migration."
        )

    def iter_children(self, relative_path: str | os.PathLike[str] | None = ""):
        raise NotImplementedError(
            "S3StorageAdapter.iter_children is not enabled because object listing semantics "
            "need a separate reviewed retention/cleanup migration."
        )

    def _build_client(self, *, session: Any | None = None):
        if session is None:
            import boto3

            session = boto3.session.Session()
        return session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region_name,
            use_ssl=self.use_ssl,
            verify=self.verify_ssl,
        )

    def _normalize_prefix(self, key_prefix: str | os.PathLike[str] | None) -> str:
        raw = str(key_prefix or "").replace("\\", "/").strip().strip("/")
        if raw in {"", "."}:
            return ""
        if self._has_windows_drive_prefix(raw):
            raise StoragePathTraversalError(f"S3 key prefix must be relative: {key_prefix}")
        parts = [part for part in raw.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise StoragePathTraversalError(f"S3 key prefix must stay inside bucket namespace: {key_prefix}")
        return "/".join(parts)

    def _normalize_relative_path(self, relative_path: str | os.PathLike[str] | None) -> str:
        raw = str(relative_path or "").replace("\\", "/").strip()
        if raw in {"", "."}:
            return ""
        candidate = Path(raw)
        if raw.startswith("/") or candidate.is_absolute() or candidate.drive or self._has_windows_drive_prefix(raw):
            raise StoragePathTraversalError(f"storage object key must be relative: {relative_path}")
        parts = [part for part in raw.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise StoragePathTraversalError(f"storage object key must stay inside configured prefix: {relative_path}")
        return "/".join(parts)

    def _has_windows_drive_prefix(self, raw_path: str) -> bool:
        return len(raw_path) >= 3 and raw_path[0].isalpha() and raw_path[1:3] == ":/"

    def _is_not_found(self, exc: Exception) -> bool:
        response = getattr(exc, "response", {}) or {}
        code = str((response.get("Error") or {}).get("Code", "")).lower()
        return code in {"404", "notfound", "nosuchkey"}


def get_storage_adapter(
    storage_root: str | os.PathLike[str] | None = None,
) -> FilesystemStorageAdapter | S3StorageAdapter:
    backend = str(getattr(settings, "STORAGE_BACKEND", "filesystem") or "filesystem").strip().lower()
    if backend in {"filesystem", "local"}:
        return FilesystemStorageAdapter(storage_root=storage_root)
    if backend == "s3":
        return S3StorageAdapter()
    raise StorageConfigurationError(
        f"Unknown STORAGE_BACKEND '{backend}'. Expected one of: filesystem, local, s3."
    )
