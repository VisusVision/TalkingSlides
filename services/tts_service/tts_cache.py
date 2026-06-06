from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


def deterministic_cache_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CacheLookupResult:
    hit: bool
    cache_key: str
    artifact_path: Path | None
    sidecar_path: Path | None
    metadata: dict[str, Any] | None
    reason: str


class TTSHashCacheStore:
    def __init__(
        self,
        root_dir: Path,
        *,
        artifact_ext: str = ".mp3",
        lock_timeout_seconds: float = 45.0,
        lock_poll_seconds: float = 0.05,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_ext = artifact_ext
        self.lock_timeout_seconds = max(float(lock_timeout_seconds), 1.0)
        self.lock_poll_seconds = max(float(lock_poll_seconds), 0.01)

    def artifact_path(self, cache_key: str) -> Path:
        prefix = cache_key[:2]
        return self.root_dir / prefix / f"{cache_key}{self.artifact_ext}"

    def sidecar_path(self, cache_key: str) -> Path:
        return self.artifact_path(cache_key).with_suffix(f"{self.artifact_ext}.json")

    def lock_path(self, cache_key: str) -> Path:
        return self.artifact_path(cache_key).with_suffix(f"{self.artifact_ext}.lock")

    @staticmethod
    def _audio_duration_seconds(path: Path) -> float:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(result.stdout or "{}")
            return float(payload.get("format", {}).get("duration") or 0.0)
        except Exception:
            return 0.0

    def _validate_artifact(self, artifact_path: Path) -> tuple[bool, str]:
        if not artifact_path.exists():
            return False, "missing_file"
        if not artifact_path.is_file():
            return False, "not_a_file"
        if artifact_path.stat().st_size <= 0:
            return False, "zero_byte"
        duration = self._audio_duration_seconds(artifact_path)
        if duration <= 0.0:
            try:
                head = artifact_path.read_bytes()[:4]
            except Exception:
                head = b""
            if head.startswith(b"ID3"):
                return True, "ok_id3_header"
            return False, "invalid_audio"
        return True, "ok"

    def _read_sidecar(self, sidecar_path: Path) -> dict[str, Any] | None:
        if not sidecar_path.exists():
            return None
        try:
            return json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def evict_key(self, cache_key: str) -> None:
        for path in (self.artifact_path(cache_key), self.sidecar_path(cache_key)):
            path.unlink(missing_ok=True)

    def lookup(self, cache_key: str) -> CacheLookupResult:
        artifact = self.artifact_path(cache_key)
        sidecar = self.sidecar_path(cache_key)
        valid, reason = self._validate_artifact(artifact)
        if not valid:
            if artifact.exists() or sidecar.exists():
                self.evict_key(cache_key)
                return CacheLookupResult(False, cache_key, None, None, None, f"corrupted:{reason}")
            return CacheLookupResult(False, cache_key, None, None, None, reason)
        metadata = self._read_sidecar(sidecar) or {}
        return CacheLookupResult(True, cache_key, artifact, sidecar, metadata, "hit")

    @contextlib.contextmanager
    def keyed_lock(self, cache_key: str) -> Iterator[None]:
        lock_file = self.lock_path(cache_key)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        fd: int | None = None
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
                break
            except FileExistsError:
                if time.perf_counter() - start >= self.lock_timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for cache lock: {cache_key}")
                time.sleep(self.lock_poll_seconds)
        try:
            yield
        finally:
            try:
                if fd is not None:
                    os.close(fd)
            finally:
                lock_file.unlink(missing_ok=True)

    def atomic_store_artifact_and_sidecar(
        self,
        cache_key: str,
        source_artifact: Path,
        sidecar_payload: dict[str, Any],
    ) -> tuple[Path, Path]:
        artifact_path = self.artifact_path(cache_key)
        sidecar_path = self.sidecar_path(cache_key)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        temp_artifact = artifact_path.with_suffix(f"{self.artifact_ext}.tmp.{os.getpid()}.{int(time.time() * 1000)}")
        temp_sidecar = sidecar_path.with_suffix(f"{sidecar_path.suffix}.tmp.{os.getpid()}.{int(time.time() * 1000)}")

        shutil.copyfile(source_artifact, temp_artifact)
        temp_sidecar.write_text(json.dumps(sidecar_payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")

        os.replace(temp_artifact, artifact_path)
        os.replace(temp_sidecar, sidecar_path)
        return artifact_path, sidecar_path

    def cleanup_expired(self, ttl_seconds: int) -> dict[str, int]:
        ttl = max(int(ttl_seconds), 1)
        cutoff = time.time() - ttl
        deleted_artifacts = 0
        deleted_sidecars = 0
        for sidecar in self.root_dir.glob("*/*.json"):
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            created_at = float(payload.get("created_at_epoch") or 0.0)
            if created_at <= 0.0:
                created_at = sidecar.stat().st_mtime
            if created_at > cutoff:
                continue
            artifact = Path(str(sidecar).removesuffix(".json"))
            if artifact.exists():
                artifact.unlink(missing_ok=True)
                deleted_artifacts += 1
            sidecar.unlink(missing_ok=True)
            deleted_sidecars += 1
        return {"deleted_artifacts": deleted_artifacts, "deleted_sidecars": deleted_sidecars}
