import os
import hashlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import UserProfile, VoiceProfile  # noqa: E402
from tests.integration.schema_skip import skip_if_column_missing  # noqa: E402

pytestmark = pytest.mark.django_db


def test_avatar_prepare_returns_setup_not_prepared_when_requirements_missing(monkeypatch):
    skip_if_column_missing("core_userprofile", "avatar_image_original")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_prepare_missing_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher", avatar_enabled=False, avatar_consent_confirmed=False)

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/prepare/", {}, format="json")
    force_authenticate(request, user=user)

    response = views.AvatarPrepareView.as_view()(request, user_id=user.id)

    assert response.status_code == 400
    assert response.data.get("error_code") == "setup_not_prepared"
    missing = response.data.get("missing_requirements") or []
    assert "missing_avatar_image_original" in missing
    assert "missing_voice_profile" in missing


def test_avatar_prepare_marks_ready_when_assets_exist(monkeypatch):
    skip_if_column_missing("core_userprofile", "avatar_image_original")
    skip_if_column_missing("core_userprofile", "avatar_source_valid")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_prepare_ready_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/7/hash/original.png",
        avatar_image_processed="avatars/7/hash/processed.png",
    )
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_prepare_{suffix}")

    storage_root = Path(getattr(settings, "STORAGE_ROOT", "storage_local"))
    original_abs = storage_root / profile.avatar_image_original
    processed_abs = storage_root / profile.avatar_image_processed
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    original_abs.write_bytes(b"original")
    processed_abs.write_bytes(b"processed")
    processed_hash = hashlib.sha256(b"processed").hexdigest()

    def fake_refresh_avatar_source_validation(profile, **_kwargs):
        profile.avatar_source_valid = True
        profile.avatar_source_validation_error = ""
        profile.avatar_source_hash = processed_hash
        profile.avatar_source_image_hash = processed_hash
        profile.avatar_source_reference_type = "image"
        profile.avatar_preview_stale = False
        profile.save(
            update_fields=[
                "avatar_source_valid",
                "avatar_source_validation_error",
                "avatar_source_hash",
                "avatar_source_image_hash",
                "avatar_source_reference_type",
                "avatar_preview_stale",
                "updated_at",
            ]
        )
        return {"valid": True, "source_hash": processed_hash, "reference_type": "image"}

    monkeypatch.setattr(views, "refresh_avatar_source_validation", fake_refresh_avatar_source_validation)

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/prepare/", {}, format="json")
    force_authenticate(request, user=user)

    response = views.AvatarPrepareView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    assert response.data.get("status") == "avatar_ready"
    assert bool((response.data.get("readiness") or {}).get("ready")) is True
    assert response.data["normalized_engine"] == "liveportrait+musetalk"
    assert response.data["avatar_engine_selected"] == "liveportrait+musetalk"
    assert response.data["avatar_setup_status"]["state"] == "ready"
    assert response.data["action_required"] == "generate_preview"


def test_avatar_missing_processed_file_maps_to_needs_prepare(monkeypatch, tmp_path):
    skip_if_column_missing("core_userprofile", "avatar_image_original")

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_prepare_missing_processed_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/1/original.png",
        avatar_image_processed="avatars/1/missing_processed.png",
    )
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_missing_processed_{suffix}")
    original_abs = tmp_path / profile.avatar_image_original
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    original_abs.write_bytes(b"original")

    readiness = views._avatar_preview_readiness(
        profile,
        VoiceProfile.objects.get(user=user),
        storage_root=tmp_path,
    )

    assert readiness["avatar_setup_status"]["state"] == "needs_prepare"
    assert readiness["avatar_setup_status"]["action_required"] == "prepare_avatar"
    assert readiness["avatar_setup_status"]["primary_action_label"] == "Re-prepare avatar"
    assert "missing_processed_reference_file" in readiness["missing_requirements"]
    assert "processed_reference_path" not in readiness["checks"]


def test_avatar_prepare_regenerates_missing_processed_reference(monkeypatch, tmp_path):
    skip_if_column_missing("core_userprofile", "avatar_image_original")

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_prepare_regen_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/2/original.png",
        avatar_image_processed="avatars/2/missing_processed.png",
    )
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_prepare_regen_{suffix}")
    original_abs = tmp_path / profile.avatar_image_original
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    original_abs.write_bytes(b"original")

    def fake_preprocess_teacher_avatar_image(**_kwargs):
        processed_rel = "avatars/2/regenerated.png"
        processed_abs = tmp_path / processed_rel
        processed_abs.parent.mkdir(parents=True, exist_ok=True)
        processed_abs.write_bytes(b"processed")
        return SimpleNamespace(
            processed_rel_path=processed_rel,
            source_hash=hashlib.sha256(b"processed").hexdigest(),
            warnings=[],
        )

    def fake_refresh_avatar_source_validation(profile_arg, **_kwargs):
        processed_abs = tmp_path / profile_arg.avatar_image_processed
        source_hash = hashlib.sha256(processed_abs.read_bytes()).hexdigest()
        profile_arg.avatar_source_valid = True
        profile_arg.avatar_source_validation_error = ""
        profile_arg.avatar_source_hash = source_hash
        profile_arg.avatar_source_image_hash = source_hash
        profile_arg.avatar_source_reference_type = "image"
        profile_arg.avatar_preview_stale = False
        profile_arg.save(
            update_fields=[
                "avatar_source_valid",
                "avatar_source_validation_error",
                "avatar_source_hash",
                "avatar_source_image_hash",
                "avatar_source_reference_type",
                "avatar_preview_stale",
                "updated_at",
            ]
        )
        return {"valid": True, "source_hash": source_hash, "reference_type": "image"}

    monkeypatch.setattr(views, "preprocess_teacher_avatar_image", fake_preprocess_teacher_avatar_image)
    monkeypatch.setattr(views, "refresh_avatar_source_validation", fake_refresh_avatar_source_validation)
    monkeypatch.setattr(views, "avatar_image_moderation_auto_enabled", lambda: False)

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/prepare/", {"force_reprocess": True}, format="json")
    force_authenticate(request, user=user)

    response = views.AvatarPrepareView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.avatar_image_processed == "avatars/2/regenerated.png"
    assert (tmp_path / profile.avatar_image_processed).is_file()
    assert response.data["avatar_setup_status"]["state"] == "ready"


def test_avatar_source_hash_mismatch_requires_reprepare(monkeypatch, tmp_path):
    skip_if_column_missing("core_userprofile", "avatar_source_hash")

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_hash_mismatch_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_original="avatars/3/original.png",
        avatar_image_processed="avatars/3/processed.png",
        avatar_source_valid=True,
        avatar_source_hash="old-hash",
    )
    voice_profile = VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_hash_mismatch_{suffix}")
    processed_abs = tmp_path / profile.avatar_image_processed
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.write_bytes(b"new-processed")

    readiness = views._avatar_preview_readiness(profile, voice_profile, storage_root=tmp_path)

    assert readiness["avatar_setup_status"]["state"] == "needs_prepare"
    assert readiness["avatar_setup_status"]["action_required"] == "prepare_avatar"
    assert "avatar_source_validation_stale" in readiness["missing_requirements"]


def test_avatar_portrait_upload_with_consent_returns_setup_status(monkeypatch, tmp_path):
    skip_if_column_missing("core_userprofile", "avatar_image_original")

    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username=f"teacher_upload_portrait_{suffix}", password="pass")
    UserProfile.objects.create(user=user, role="teacher", avatar_enabled=False, avatar_consent_confirmed=False)
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_upload_portrait_{suffix}")

    def fake_preprocess_teacher_avatar_image(**_kwargs):
        processed_rel = "avatars/upload/processed.png"
        processed_abs = tmp_path / processed_rel
        processed_abs.parent.mkdir(parents=True, exist_ok=True)
        processed_abs.write_bytes(b"processed")
        return SimpleNamespace(
            processed_rel_path=processed_rel,
            source_hash=hashlib.sha256(b"processed").hexdigest(),
            warnings=[],
        )

    def fake_refresh_avatar_source_validation(profile_arg, **_kwargs):
        processed_abs = tmp_path / profile_arg.avatar_image_processed
        source_hash = hashlib.sha256(processed_abs.read_bytes()).hexdigest()
        profile_arg.avatar_source_valid = True
        profile_arg.avatar_source_validation_error = ""
        profile_arg.avatar_source_hash = source_hash
        profile_arg.avatar_source_image_hash = source_hash
        profile_arg.avatar_source_reference_type = "image"
        profile_arg.avatar_preview_stale = False
        profile_arg.save(
            update_fields=[
                "avatar_source_valid",
                "avatar_source_validation_error",
                "avatar_source_hash",
                "avatar_source_image_hash",
                "avatar_source_reference_type",
                "avatar_preview_stale",
                "updated_at",
            ]
        )
        return {"valid": True, "source_hash": source_hash, "reference_type": "image"}

    monkeypatch.setattr(views, "preprocess_teacher_avatar_image", fake_preprocess_teacher_avatar_image)
    monkeypatch.setattr(views, "refresh_avatar_source_validation", fake_refresh_avatar_source_validation)
    monkeypatch.setattr(views, "run_avatar_image_moderation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(views, "avatar_image_moderation_gate", lambda *_args, **_kwargs: {"blocked": False})

    upload = SimpleUploadedFile("portrait.png", b"portrait", content_type="image/png")
    factory = APIRequestFactory()
    request = factory.post(
        f"/api/v1/users/{user.id}/avatar/",
        {
            "avatar_file": upload,
            "avatar_consent_confirmed": "1",
            "avatar_lipsync_engine": "liveportrait+musetalk",
        },
        format="multipart",
    )
    force_authenticate(request, user=user)

    response = views.AvatarProfileView.as_view()(request, user_id=user.id)

    assert response.status_code == 200
    profile = UserProfile.objects.get(user=user)
    assert profile.avatar_consent_confirmed is True
    assert profile.avatar_enabled is True
    assert profile.avatar_image_original
    assert profile.avatar_image_processed == "avatars/upload/processed.png"
    assert response.data["avatar_setup_status"]["state"] == "ready"
    assert response.data["action_required"] == "generate_preview"
