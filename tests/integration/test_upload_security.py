import os
import sys
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.models import UserProfile, VoiceProfile  # noqa: E402


pytestmark = pytest.mark.django_db


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_teacher(username: str) -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _tiny_wav_bytes(payload: bytes = b"\x00\x00\x00\x00") -> bytes:
    data_size = len(payload)
    riff_size = 36 + data_size
    return (
        b"RIFF"
        + riff_size.to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
        + payload
    )


def _upload(user: User, file_obj: SimpleUploadedFile):
    return _client(user).post(f"/api/v1/users/{user.id}/voice/", {"voice_file": file_obj}, format="multipart")


def test_voice_upload_accepts_valid_tiny_wav(tmp_path):
    teacher = _make_teacher("voice_valid_teacher")
    upload = SimpleUploadedFile("sample.wav", _tiny_wav_bytes(), content_type="audio/wav")

    with override_settings(STORAGE_ROOT=str(tmp_path), AVATAR_VOICE_SAMPLE_MAX_BYTES=1024):
        response = _upload(teacher, upload)

    assert response.status_code == 200
    voice_id = response.data["voice_id"]
    assert VoiceProfile.objects.get(user=teacher).voice_id == voice_id
    saved = tmp_path / "voices" / f"{voice_id}.wav"
    assert saved.exists()
    assert saved.read_bytes().startswith(b"RIFF")


def test_voice_upload_rejects_oversized_file_before_writing(tmp_path):
    teacher = _make_teacher("voice_oversized_teacher")
    upload = SimpleUploadedFile("sample.wav", _tiny_wav_bytes(b"0" * 128), content_type="audio/wav")

    with override_settings(STORAGE_ROOT=str(tmp_path), AVATAR_VOICE_SAMPLE_MAX_BYTES=32):
        response = _upload(teacher, upload)

    assert response.status_code == 400
    assert "exceeds" in response.data["error"]
    assert not (tmp_path / "voices").exists()


def test_voice_upload_rejects_extension_mismatch(tmp_path):
    teacher = _make_teacher("voice_ext_teacher")
    upload = SimpleUploadedFile("sample.mp3", _tiny_wav_bytes(), content_type="audio/wav")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _upload(teacher, upload)

    assert response.status_code == 400
    assert "extension" in response.data["error"]
    assert not (tmp_path / "voices").exists()


def test_voice_upload_rejects_mime_mismatch(tmp_path):
    teacher = _make_teacher("voice_mime_teacher")
    upload = SimpleUploadedFile("sample.wav", _tiny_wav_bytes(), content_type="text/plain")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _upload(teacher, upload)

    assert response.status_code == 400
    assert "MIME" in response.data["error"]
    assert not (tmp_path / "voices").exists()


def test_voice_upload_rejects_random_bytes(tmp_path):
    teacher = _make_teacher("voice_random_teacher")
    upload = SimpleUploadedFile("sample.wav", b"not actually audio", content_type="audio/wav")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _upload(teacher, upload)

    assert response.status_code == 400
    assert "does not match" in response.data["error"]
    assert not (tmp_path / "voices").exists()


def test_voice_upload_rejects_non_owner(tmp_path):
    teacher = _make_teacher("voice_owner_teacher")
    other = _make_teacher("voice_other_teacher")
    upload = SimpleUploadedFile("sample.wav", _tiny_wav_bytes(), content_type="audio/wav")

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = _client(other).post(f"/api/v1/users/{teacher.id}/voice/", {"voice_file": upload}, format="multipart")

    assert response.status_code == 403
    assert not (tmp_path / "voices").exists()
