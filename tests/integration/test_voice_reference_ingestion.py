import json
import logging
from pathlib import Path
import shutil
import subprocess
import uuid
import wave

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views, voice_ingestion
from core.models import UserProfile, VoiceProfile


def _make_teacher(prefix: str = "voice_ingestion"):
    user = User.objects.create_user(username=f"{prefix}_{uuid.uuid4().hex[:8]}", password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _install_fake_audio_tools(monkeypatch, *, duration_seconds: float = 11.0, fail_transcode: bool = False):
    commands = []

    monkeypatch.setattr(voice_ingestion.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        executable = Path(command[0]).name
        if executable == "ffmpeg":
            if fail_transcode:
                raise subprocess.CalledProcessError(1, command, stderr="invalid input")
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame_count = int(voice_ingestion.VOICE_REFERENCE_SAMPLE_RATE * duration_seconds)
            with wave.open(str(output_path), "wb") as wav_file:
                wav_file.setnchannels(voice_ingestion.VOICE_REFERENCE_CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(voice_ingestion.VOICE_REFERENCE_SAMPLE_RATE)
                wav_file.writeframes(b"\x00\x00" * frame_count)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        if executable == "ffprobe":
            with wave.open(str(command[-1]), "rb") as wav_file:
                duration = wav_file.getnframes() / float(wav_file.getframerate())
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
            payload = {
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "pcm_s16le",
                        "sample_rate": str(sample_rate),
                        "channels": channels,
                        "duration": str(duration),
                    }
                ],
                "format": {"duration": str(duration)},
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        raise AssertionError(f"Unexpected process: {command}")

    monkeypatch.setattr(voice_ingestion.subprocess, "run", fake_run)
    return commands


def _make_audio_fixture(tmp_path: Path, *, extension: str, duration_seconds: float = 10.5) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for real voice-ingestion integration coverage")
    output_path = tmp_path / f"source.{extension}"
    codec_args = {
        "wav": ["-c:a", "pcm_s16le"],
        "ogg": ["-c:a", "libvorbis"],
        "webm": ["-c:a", "libopus"],
    }[extension]
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration_seconds}",
            *codec_args,
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"ffmpeg cannot generate {extension} fixture: {result.stderr}")
    return output_path.read_bytes()


def _upload_voice(user, tmp_path, *, name: str, content: bytes):
    request = APIRequestFactory().post(
        f"/api/v1/users/{user.id}/voice/",
        {
            "voice_file": SimpleUploadedFile(
                name,
                content,
                content_type="audio/octet-stream",
            )
        },
        format="multipart",
    )
    force_authenticate(request, user=user)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        return views.VoiceUploadView.as_view()(request, user_id=user.id)


def _assert_canonical_wav(path: Path):
    assert path.exists()
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 24000
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() > 0


@pytest.mark.django_db
def test_valid_wav_upload_is_canonicalized_and_accepted(tmp_path, monkeypatch):
    teacher = _make_teacher("voice_wav")
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)
    source = _make_audio_fixture(tmp_path, extension="wav")

    response = _upload_voice(teacher, tmp_path, name="teacher.wav", content=source)

    assert response.status_code == 200
    assert response.data["status"] == "ready"
    assert response.data["audio"]["format"] == "wav"
    assert response.data["audio"]["codec"] == "pcm_s16le"
    assert response.data["audio"]["sample_rate"] == 24000
    assert response.data["audio"]["channels"] == 1
    assert response.data["audio"]["duration_seconds"] == pytest.approx(10.5, abs=0.1)
    profile = VoiceProfile.objects.get(user=teacher)
    canonical_path = tmp_path / "voices" / f"{profile.voice_id}.wav"
    _assert_canonical_wav(canonical_path)


@pytest.mark.django_db
@pytest.mark.parametrize("filename", ["teacher.ogg", "teacher.webm"])
def test_browser_audio_upload_is_transcoded_to_canonical_wav(tmp_path, monkeypatch, filename):
    teacher = _make_teacher("voice_browser")
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)
    extension = Path(filename).suffix.lstrip(".")
    source = _make_audio_fixture(tmp_path, extension=extension)

    response = _upload_voice(teacher, tmp_path, name=filename, content=source)

    assert response.status_code == 200
    profile = VoiceProfile.objects.get(user=teacher)
    canonical_path = tmp_path / "voices" / f"{profile.voice_id}.wav"
    _assert_canonical_wav(canonical_path)
    assert response.data["audio"]["duration_seconds"] == pytest.approx(10.5, abs=0.1)


@pytest.mark.django_db
def test_malformed_upload_is_rejected_without_creating_profile(tmp_path, monkeypatch):
    teacher = _make_teacher("voice_bad")
    _install_fake_audio_tools(monkeypatch, fail_transcode=True)
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)

    response = _upload_voice(teacher, tmp_path, name="broken.webm", content=b"malformed")

    assert response.status_code == 400
    assert response.data["status"] == "rejected"
    assert response.data["error_code"] == "voice_transcode_failed"
    assert not VoiceProfile.objects.filter(user=teacher).exists()
    assert not list((tmp_path / "voices").glob("voice_*.wav"))


@pytest.mark.django_db
def test_too_short_upload_is_rejected(tmp_path, monkeypatch):
    teacher = _make_teacher("voice_short")
    _install_fake_audio_tools(monkeypatch, duration_seconds=2.0)
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)

    response = _upload_voice(teacher, tmp_path, name="short.ogg", content=b"short-audio")

    assert response.status_code == 400
    assert response.data["error_code"] == "voice_sample_too_short"
    assert "at least 10 seconds" in response.data["error"]
    assert not VoiceProfile.objects.filter(user=teacher).exists()
    assert not list((tmp_path / "voices").glob("voice_*.wav"))


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("duration_seconds", "error_code"),
    [
        (0.0, "voice_duration_invalid"),
        (61.0, "voice_sample_too_long"),
    ],
)
def test_zero_or_overlong_audio_is_rejected(tmp_path, monkeypatch, duration_seconds, error_code):
    teacher = _make_teacher("voice_duration")
    _install_fake_audio_tools(monkeypatch, duration_seconds=duration_seconds)
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)

    response = _upload_voice(teacher, tmp_path, name="duration.ogg", content=b"audio-data")

    assert response.status_code == 400
    assert response.data["error_code"] == error_code
    assert not VoiceProfile.objects.filter(user=teacher).exists()
    assert not list((tmp_path / "voices").glob("voice_*.wav"))


@pytest.mark.django_db
def test_bad_replacement_preserves_previous_valid_voice(tmp_path, monkeypatch):
    teacher = _make_teacher("voice_preserve")
    previous_id = "voice_previous_valid"
    previous_path = tmp_path / "voices" / f"{previous_id}.wav"
    previous_path.parent.mkdir(parents=True, exist_ok=True)
    previous_path.write_bytes(b"previous-valid-reference")
    VoiceProfile.objects.create(user=teacher, provider="xtts_v2", voice_id=previous_id)
    _install_fake_audio_tools(monkeypatch, fail_transcode=True)
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)

    response = _upload_voice(teacher, tmp_path, name="broken.ogg", content=b"malformed")

    assert response.status_code == 400
    profile = VoiceProfile.objects.get(user=teacher)
    assert profile.voice_id == previous_id
    assert previous_path.read_bytes() == b"previous-valid-reference"
    assert list((tmp_path / "voices").glob("voice_*.wav")) == [previous_path]


@pytest.mark.django_db
def test_render_voice_lookup_returns_current_canonical_voice(tmp_path, monkeypatch):
    teacher = _make_teacher("voice_lookup")
    _install_fake_audio_tools(monkeypatch)
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)

    response = _upload_voice(teacher, tmp_path, name="teacher.ogg", content=b"browser-audio")

    assert response.status_code == 200
    teacher.refresh_from_db()
    profile = VoiceProfile.objects.get(user=teacher)
    assert views._get_voice_id(teacher) == profile.voice_id
    assert (tmp_path / "voices" / f"{profile.voice_id}.wav").exists()


@pytest.mark.django_db
def test_render_voice_lookup_logs_missing_profile_separately(caplog):
    teacher = _make_teacher("voice_missing")

    with caplog.at_level(logging.INFO, logger=views.__name__):
        assert views._get_voice_id(teacher) == ""

    record = next(record for record in caplog.records if record.message == "render_voice_lookup_empty")
    assert record.voice_lookup_reason == "voice_profile_missing"
    assert record.user_id == teacher.id


def test_render_voice_lookup_logs_unexpected_failure(caplog):
    class BrokenUser:
        id = 987

        @property
        def voice_profile(self):
            raise RuntimeError("synthetic lookup failure")

    with caplog.at_level(logging.ERROR, logger=views.__name__):
        assert views._get_voice_id(BrokenUser()) == ""

    record = next(record for record in caplog.records if record.message == "render_voice_lookup_failed")
    assert record.voice_lookup_reason == "unexpected_error"
    assert record.user_id == 987
