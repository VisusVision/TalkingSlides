from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views
from core.models import UserProfile, VoiceProfile


@pytest.mark.django_db
def test_avatar_video_can_create_named_profile_with_video_voice(tmp_path, monkeypatch):
    user = User.objects.create_user(username=f"avatar_video_voice_{uuid.uuid4().hex[:8]}", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)
    monkeypatch.setattr(views, "_composite_engine_configured", lambda: True)
    monkeypatch.setattr(
        views,
        "preprocess_avatar_video",
        lambda **_kwargs: {
            "processed_rel_path": "",
            "video_rel_path": "avatars/processed-avatar.webm",
            "source_hash": "avatar-source-hash",
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        views,
        "refresh_avatar_source_validation",
        lambda profile, **_kwargs: {"valid": True, "reference_type": "video"},
    )

    def fake_ingest(_upload, *, storage_root, voice_id):
        path = Path(storage_root) / "voices" / f"{voice_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF-video-voice")
        return SimpleNamespace(
            duration_seconds=15.0,
            codec_name="pcm_s16le",
            sample_rate=24000,
            channels=1,
            path=path,
        )

    monkeypatch.setattr(views, "ingest_voice_reference", fake_ingest)
    request = APIRequestFactory().post(
        f"/api/v1/users/{user.id}/avatar/",
        {
            "avatar_video_file": SimpleUploadedFile("avatar.webm", b"video-with-audio", content_type="video/webm"),
            "avatar_consent_confirmed": "1",
            "avatar_name": "Fen Anlatıcım",
            "avatar_voice_source": "video",
            "avatar_lipsync_engine": "musetalk",
        },
        format="multipart",
    )
    force_authenticate(request, user=user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.AvatarProfileView.as_view()(request, user_id=user.id)

    assert response.status_code == 200, response.data
    user.profile.refresh_from_db()
    assert user.profile.avatar_name == "Fen Anlatıcım"
    assert user.profile.avatar_voice_source == "video"
    voice = VoiceProfile.objects.get(user=user)
    assert voice.voice_id.startswith("voice_")
    assert (tmp_path / "voices" / f"{voice.voice_id}.wav").exists()
    assert "voice_created_from_avatar_video" in response.data["warnings"]


@pytest.mark.django_db
def test_avatar_video_validation_failure_keeps_confirmed_consent(tmp_path, monkeypatch):
    user = User.objects.create_user(username=f"avatar_video_invalid_{uuid.uuid4().hex[:8]}", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)
    monkeypatch.setattr(
        views,
        "preprocess_avatar_video",
        lambda **_kwargs: (_ for _ in ()).throw(
            views.AvatarValidationError("No clear front-facing frame was found in the video.")
        ),
    )
    request = APIRequestFactory().post(
        f"/api/v1/users/{user.id}/avatar/",
        {
            "avatar_video_file": SimpleUploadedFile("avatar.webm", b"invalid-video", content_type="video/webm"),
            "avatar_consent_confirmed": "1",
            "avatar_voice_source": "video",
            "avatar_lipsync_engine": "musetalk",
        },
        format="multipart",
    )
    force_authenticate(request, user=user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.AvatarProfileView.as_view()(request, user_id=user.id)

    assert response.status_code == 400
    assert response.data["error"] == "No clear front-facing frame was found in the video."
    user.profile.refresh_from_db()
    assert user.profile.avatar_consent_confirmed is True
    assert response.data["avatar_setup_status"]["state"] != "missing_consent"
