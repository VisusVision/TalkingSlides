import os
import hashlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.test.utils import override_settings
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
API_ROOT = REPO_ROOT / "services" / "api"
WORKER_ROOT = REPO_ROOT / "services" / "worker"
for path in [SERVICES_ROOT, API_ROOT, WORKER_ROOT]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402

from core.avatar_readiness import avatar_preview_readiness  # noqa: E402
from core.avatar_source_validation import (  # noqa: E402
    apply_avatar_source_validation,
    refresh_avatar_source_validation,
    validate_active_avatar_source,
)
from core.models import UserProfile, VoiceProfile  # noqa: E402


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def _require_avatar_source_columns():
    if not _table_has_column("core_userprofile", "avatar_source_valid"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")


@pytest.mark.django_db
def test_blank_active_avatar_source_fails_validation_and_readiness(tmp_path):
    _require_avatar_source_columns()
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_blank_avatar_{suffix}", password="pass")
    rel_path = f"avatars/{user.id}/blank.png"
    blank_path = tmp_path / rel_path
    blank_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1024, 1024), (218, 208, 198)).save(blank_path)

    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original=rel_path,
        avatar_image_processed=rel_path,
        avatar_reference_type="image",
        avatar_image_status="ready",
    )
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_{suffix}")

    validation = refresh_avatar_source_validation(profile, storage_root=tmp_path, persist=True)
    profile.refresh_from_db()
    readiness = avatar_preview_readiness(profile, profile.user.voice_profile, storage_root=tmp_path)

    assert validation["valid"] is False
    assert profile.avatar_source_valid is False
    assert "avatar_input_face_not_detected" in profile.avatar_source_validation_error
    assert readiness["ready"] is False
    assert readiness["avatar_ready"] is False
    assert "avatar_source_invalid" in readiness["missing_requirements"]


@pytest.mark.django_db
def test_stale_preview_is_invalidated_when_active_source_hash_changes(tmp_path):
    _require_avatar_source_columns()
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_stale_preview_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/1/old.png",
        avatar_image_processed="avatars/1/old.png",
        avatar_reference_type="image",
        avatar_preview_video="avatars/1/preview/preview.mp4",
        avatar_last_preview_path="avatars/1/preview/preview.mp4",
        avatar_last_preview_status="ready",
        avatar_preview_source_hash="old-source-hash",
    )

    stale_cleared = apply_avatar_source_validation(
        profile,
        {
            "valid": True,
            "error": "",
            "source_hash": "new-source-hash",
            "image_hash": "new-source-hash",
            "video_hash": "",
            "reference_type": "image",
        },
        storage_root=tmp_path,
        invalidate_preview=True,
    )
    profile.save()
    profile.refresh_from_db()

    assert stale_cleared is True
    assert profile.avatar_preview_stale is True
    assert profile.avatar_preview_video == ""
    assert profile.avatar_last_preview_path == ""
    assert profile.avatar_last_preview_status == "stale"
    assert profile.avatar_preview_source_hash == ""


@pytest.mark.django_db
def test_avatar_ready_requires_current_preview_source_hash(tmp_path, monkeypatch):
    _require_avatar_source_columns()
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_ready_hash_{suffix}", password="pass")
    preview_rel = f"avatars/{user.id}/preview/preview.mp4"
    preview_abs = tmp_path / preview_rel
    preview_abs.parent.mkdir(parents=True, exist_ok=True)
    preview_abs.write_bytes(b"preview")
    source_rel = f"avatars/{user.id}/face.png"
    source_abs = tmp_path / source_rel
    source_abs.parent.mkdir(parents=True, exist_ok=True)
    source_abs.write_bytes(b"source")
    source_hash = hashlib.sha256(b"source").hexdigest()

    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original=source_rel,
        avatar_image_processed=source_rel,
        avatar_reference_type="image",
        avatar_source_valid=True,
        avatar_source_hash="current-hash",
        avatar_source_image_hash="current-hash",
        avatar_preview_source_hash="current-hash",
        avatar_preview_video=preview_rel,
        avatar_last_preview_path=preview_rel,
        avatar_last_preview_status="ready",
    )
    voice = VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_{suffix}")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        readiness = avatar_preview_readiness(profile, voice, storage_root=tmp_path)

    assert readiness["ready"] is False
    assert readiness["avatar_ready"] is False
    assert "avatar_source_validation_stale" in readiness["missing_requirements"]

    profile.avatar_source_hash = source_hash
    profile.avatar_source_image_hash = source_hash
    profile.avatar_preview_source_hash = source_hash
    profile.save()
    readiness = avatar_preview_readiness(profile, voice, storage_root=tmp_path)

    assert readiness["ready"] is True
    assert readiness["avatar_ready"] is True


@pytest.mark.django_db
def test_video_preferred_validation_does_not_silently_fallback_to_valid_image(tmp_path):
    _require_avatar_source_columns()
    user = SimpleNamespace(id=1)
    image_rel = "avatars/1/valid-image.png"
    video_rel = "avatars/1/invalid-video.mp4"
    image_abs = tmp_path / image_rel
    video_abs = tmp_path / video_rel
    image_abs.parent.mkdir(parents=True, exist_ok=True)
    image_abs.write_bytes(b"not-used-image")
    video_abs.write_bytes(b"not-a-real-video")

    profile = SimpleNamespace(
        user=user,
        avatar_reference_type="video",
        avatar_image_original=image_rel,
        avatar_image_processed=image_rel,
        avatar_video_original=video_rel,
        avatar_video_processed="",
        avatar_lipsync_engine="liveportrait+musetalk",
        avatar_engine_primary="liveportrait+musetalk",
    )

    validation = validate_active_avatar_source(profile, storage_root=tmp_path)

    assert validation["valid"] is False
    assert validation["reference_type"] == "video"
    assert validation["source_hash"] == validation["video_hash"]
    assert validation["image_hash"]
    assert validation["error"] in {"avatar_input_missing_or_unreadable", "avatar_input_face_not_detected"}
