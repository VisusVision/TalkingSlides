import json
import os
import sys
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command  # noqa: E402

from core.storage_adapter import FilesystemStorageAdapter, StoragePathTraversalError  # noqa: E402
from core.storage_health import StorageHealthError, run_filesystem_storage_smoke  # noqa: E402
from core.subtitle_translation import _source_language_code  # noqa: E402
from core.views import (  # noqa: E402
    _language_detection_sidecar_for_job,
    _normalize_rel_storage_path,
    _playback_sidecar_for_job,
    _resolve_storage_file,
    _storage_rel_path_exists,
)
from worker import tasks as worker_tasks  # noqa: E402


def test_storage_paths_are_normalized_to_safe_relative_paths():
    assert _normalize_rel_storage_path(r"\uploads\1\lesson.mp4") == "uploads/1/lesson.mp4"
    assert _normalize_rel_storage_path("/uploads/1/lesson.mp4") == "uploads/1/lesson.mp4"
    assert _normalize_rel_storage_path("../secret.txt") == ""
    assert _normalize_rel_storage_path("uploads/../secret.txt") == ""


def test_storage_file_resolution_stays_inside_storage_root(tmp_path):
    storage_root = tmp_path / "storage_local"
    media_file = storage_root / "1" / "lesson.mp4"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"video")

    assert _resolve_storage_file(storage_root, "1/lesson.mp4") == media_file.resolve()
    assert _resolve_storage_file(storage_root, "../outside.txt") is None
    assert _resolve_storage_file(storage_root, "missing.mp4") is None


def test_playback_sidecar_read_uses_storage_adapter_without_changing_payload(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)
    payload = {
        "mp4_rel_path": "42/42.mp4",
        "avatar": {"track_rel_path": "42/avatar/avatar_track.mp4"},
        "protection_mode": "secure_stream",
    }
    adapter.write_text("42/playback_assets.json", json.dumps(payload))

    assert _playback_sidecar_for_job(str(tmp_path), 42) == payload


def test_playback_sidecar_missing_or_invalid_still_returns_empty_payload(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)

    assert _playback_sidecar_for_job(str(tmp_path), 42) == {}

    adapter.write_text("42/playback_assets.json", "{")

    assert _playback_sidecar_for_job(str(tmp_path), 42) == {}


def test_language_detection_sidecar_read_and_missing_fallback_use_storage_adapter(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)
    payload = {
        "detected_language": "tr",
        "resolved_language": "tr",
        "source": "detector",
        "confidence": 0.91,
        "fallback_used": False,
        "supported_languages": ["en", "tr"],
        "detector": "fixture",
    }
    adapter.write_text("42/language_detection.json", json.dumps(payload))

    assert _language_detection_sidecar_for_job(str(tmp_path), 42) == payload
    assert _source_language_code(42, storage_root=tmp_path) == "tr"

    missing = _language_detection_sidecar_for_job(str(tmp_path), 99)
    assert missing["resolved_language"] == "en"
    assert missing["source"] == "pending"


def test_language_detection_invalid_sidecar_preserves_fallback_behavior(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)
    adapter.write_text("42/language_detection.json", "{")

    payload = _language_detection_sidecar_for_job(str(tmp_path), 42)

    assert payload["resolved_language"] == "en"
    assert payload["source"] == "fallback_invalid_sidecar"
    assert _source_language_code(42, storage_root=tmp_path, fallback="en") == "en"


def test_worker_playback_sidecar_write_uses_adapter_without_changing_output(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    payload = {
        "mp4_rel_path": "42/42.mp4",
        "avatar": {"track_rel_path": "42/avatar/avatar_track.mp4"},
        "protection_mode": "secure_stream",
    }

    written_path = worker_tasks._write_playback_sidecar(42, payload)

    sidecar = tmp_path / "42" / "playback_assets.json"
    assert written_path == str(sidecar)
    assert sidecar.read_text(encoding="utf-8") == json.dumps(payload, ensure_ascii=True, indent=2)
    assert _playback_sidecar_for_job(str(tmp_path), 42) == payload


def test_worker_language_detection_sidecar_write_creates_parent_and_preserves_format(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path))
    payload = {
        "detected_language": "tr",
        "resolved_language": "tr",
        "source": "detector",
        "confidence": 0.91,
        "fallback_used": False,
        "supported_languages": ["en", "tr"],
        "detector": "fixture",
    }

    written_path = worker_tasks._write_language_detection_sidecar(42, payload)

    sidecar = tmp_path / "42" / "language_detection.json"
    assert written_path == str(sidecar)
    assert sidecar.parent.is_dir()
    assert sidecar.read_text(encoding="utf-8") == json.dumps(payload, ensure_ascii=True, indent=2)
    assert _language_detection_sidecar_for_job(str(tmp_path), 42) == payload


def test_worker_json_sidecar_write_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_tasks, "STORAGE_ROOT", str(tmp_path / "storage"))

    with pytest.raises(StoragePathTraversalError):
        worker_tasks._write_json_sidecar("../outside", "playback_assets.json", {"unsafe": True})

    assert not (tmp_path / "outside" / "playback_assets.json").exists()


def test_storage_rel_path_exists_uses_adapter_traversal_rejection(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)
    adapter.write_bytes("42/subtitles/en.vtt", b"WEBVTT")
    outside = tmp_path.parent / "outside.vtt"
    outside.write_bytes(b"outside")

    assert _storage_rel_path_exists(str(tmp_path), "42/subtitles/en.vtt") is True
    assert _storage_rel_path_exists(str(tmp_path), "/42/subtitles/en.vtt") is True
    assert _storage_rel_path_exists(str(tmp_path), "../outside.vtt") is False
    assert _storage_rel_path_exists(str(tmp_path), str(outside)) is False
    assert _storage_rel_path_exists(str(tmp_path), "missing.vtt") is False


def test_filesystem_storage_adapter_reads_writes_and_checks_existence(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)

    adapter.write_bytes("nested/blob.bin", b"payload")
    adapter.write_text("nested/info.txt", "hello")

    assert adapter.exists("nested/blob.bin") is True
    assert adapter.read_bytes("nested/blob.bin") == b"payload"
    assert adapter.read_text("nested/info.txt") == "hello"
    assert adapter.resolve_path("nested/blob.bin") == (tmp_path / "nested" / "blob.bin").resolve()


def test_filesystem_storage_adapter_rejects_path_traversal(tmp_path):
    adapter = FilesystemStorageAdapter(tmp_path)

    for unsafe_path in ("../secret.txt", "nested/../../secret.txt", "/absolute/secret.txt"):
        with pytest.raises(StoragePathTraversalError):
            adapter.resolve_path(unsafe_path)


def test_filesystem_storage_adapter_scopes_paths_to_storage_root(tmp_path):
    root = tmp_path / "storage"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    adapter = FilesystemStorageAdapter(root)

    adapter.write_text("inside.txt", "inside")

    assert adapter.read_text("inside.txt") == "inside"
    assert outside.read_text(encoding="utf-8") == "outside"
    assert adapter.resolve_path("inside.txt").is_relative_to(root.resolve())


def test_filesystem_storage_smoke_writes_reads_and_deletes_probe(tmp_path):
    result = run_filesystem_storage_smoke(tmp_path)

    assert result["status"] == "ok"
    assert result["backend"] == "filesystem"
    assert result["write"] is True
    assert result["read"] is True
    assert result["delete"] is True
    assert not (tmp_path / ".storage-smoke").exists()


def test_filesystem_storage_smoke_rejects_missing_root(tmp_path):
    missing_root = tmp_path / "missing"

    with pytest.raises(StorageHealthError, match="does not exist"):
        run_filesystem_storage_smoke(missing_root)


def test_storage_smoke_management_command(tmp_path, capsys):
    call_command("storage_smoke_check", storage_root=str(tmp_path))

    captured = capsys.readouterr()
    assert "Storage smoke check passed" in captured.out
