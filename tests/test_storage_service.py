import os
import sys
from pathlib import Path

import django


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.views import _normalize_rel_storage_path, _resolve_storage_file  # noqa: E402


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
