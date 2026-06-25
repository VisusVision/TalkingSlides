import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core import views  # noqa: E402
from core.models import Job, LessonShareLink, Project, UserProfile  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _make_ready_lesson(owner, title="Shareable lesson", *, srt=True):
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=False,
    )
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt" if srt else "",
    )
    return project, job


def _write_media_files(tmp_path, project, job, *, vtt=True):
    project_dir = tmp_path / str(project.id)
    project_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / job.result_url).write_bytes(b"fake-mp4")
    if job.srt_url:
        (tmp_path / job.srt_url).write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nShared caption\n",
            encoding="utf-8",
        )
        if vtt:
            (tmp_path / f"{project.id}/{project.id}.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nShared caption\n",
                encoding="utf-8",
            )


def _create_share_link(project, owner, token="raw-share-token", *, expires_at=None, revoked_at=None):
    return LessonShareLink.objects.create(
        project=project,
        owner=owner,
        token_hash=views._share_link_token_hash(token),
        expires_at=expires_at or (timezone.now() + timedelta(hours=24)),
        revoked_at=revoked_at,
    )


def _stream_token(url: str) -> str:
    match = re.search(r"/api/v1/stream/([^/]+)/", url)
    assert match, url
    return match.group(1)


def _stream_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or url


@pytest.mark.django_db
def test_owner_can_create_share_link_and_token_hash_is_stored(tmp_path):
    teacher = _make_teacher("share_owner")
    project, _job = _make_ready_lesson(teacher)
    client = APIClient()
    client.force_authenticate(user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path), ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        response = client.post(f"/api/v1/projects/{project.id}/share-links/", {}, format="json")

    assert response.status_code == 201
    assert response.data["token"]
    assert response.data["share_path"].startswith("/share/")
    share_link = LessonShareLink.objects.get(project=project)
    assert share_link.token_hash == views._share_link_token_hash(response.data["token"])
    assert response.data["token"] not in share_link.token_hash


@pytest.mark.django_db
def test_non_owner_cannot_create_share_link(tmp_path):
    owner = _make_teacher("share_owner_forbidden")
    other = _make_teacher("share_other_forbidden")
    project, _job = _make_ready_lesson(owner)
    client = APIClient()
    client.force_authenticate(user=other)

    response = client.post(f"/api/v1/projects/{project.id}/share-links/", {}, format="json")

    assert response.status_code == 403
    assert LessonShareLink.objects.count() == 0


@pytest.mark.django_db
def test_valid_share_token_returns_playback_metadata_and_protected_media(tmp_path):
    cache.clear()
    owner = _make_teacher("share_valid_owner")
    project, job = _make_ready_lesson(owner)
    _write_media_files(tmp_path, project, job)
    token = "valid-share-token"
    _create_share_link(project, owner, token=token)
    client = APIClient()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
        LESSON_SHARE_MEDIA_GRANT_TTL_SECONDS=900,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = client.get(f"/api/v1/share/{token}/")
        assert response.status_code == 200
        assert response.data["id"] == project.id
        assert response.data["stream_url"].startswith("http://testserver/api/v1/stream/")
        assert response.data["vtt_url"].startswith("http://testserver/api/v1/stream/")
        assert job.result_url not in response.data["stream_url"]
        assert response.data["playback_status"]["grant_active"] is True

        stream_response = client.get(_stream_path(response.data["stream_url"]))

    assert stream_response.status_code == 200
    assert b"fake-mp4" in b"".join(stream_response.streaming_content)


@pytest.mark.django_db
def test_invalid_expired_and_revoked_share_tokens_are_rejected(tmp_path):
    owner = _make_teacher("share_invalid_owner")
    project, _job = _make_ready_lesson(owner)
    _create_share_link(
        project,
        owner,
        token="expired-token",
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    _create_share_link(
        project,
        owner,
        token="revoked-token",
        revoked_at=timezone.now(),
    )
    client = APIClient()

    assert client.get("/api/v1/share/not-real/").status_code == 404
    expired = client.get("/api/v1/share/expired-token/")
    revoked = client.get("/api/v1/share/revoked-token/")

    assert expired.status_code == 410
    assert expired.data["reason"] == "expired"
    assert revoked.status_code == 410
    assert revoked.data["reason"] == "revoked"


@pytest.mark.django_db
def test_revoked_share_link_rejects_existing_media_stream(tmp_path):
    cache.clear()
    owner = _make_teacher("share_revoked_stream_owner")
    project, job = _make_ready_lesson(owner)
    _write_media_files(tmp_path, project, job)
    token = "revoked-after-issue"
    share_link = _create_share_link(project, owner, token=token)
    client = APIClient()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        metadata = client.get(f"/api/v1/share/{token}/")
        assert metadata.status_code == 200
        stream_path = _stream_path(metadata.data["stream_url"])
        share_link.revoked_at = timezone.now()
        share_link.save(update_fields=["revoked_at"])
        stream_response = client.get(stream_path)

    assert stream_response.status_code == 403


@pytest.mark.django_db
def test_expired_share_link_rejects_existing_media_stream(tmp_path):
    cache.clear()
    owner = _make_teacher("share_expired_stream_owner")
    project, job = _make_ready_lesson(owner)
    _write_media_files(tmp_path, project, job)
    token = "expired-after-issue"
    share_link = _create_share_link(project, owner, token=token)
    client = APIClient()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        metadata = client.get(f"/api/v1/share/{token}/")
        assert metadata.status_code == 200
        stream_path = _stream_path(metadata.data["stream_url"])
        share_link.expires_at = timezone.now() - timedelta(seconds=1)
        share_link.save(update_fields=["expires_at"])
        stream_response = client.get(stream_path)

    assert stream_response.status_code == 403


@pytest.mark.django_db
def test_share_token_for_lesson_a_cannot_access_lesson_b_media(tmp_path):
    cache.clear()
    owner = _make_teacher("share_cross_owner")
    project_a, job_a = _make_ready_lesson(owner, title="A")
    project_b, job_b = _make_ready_lesson(owner, title="B")
    _write_media_files(tmp_path, project_a, job_a)
    _write_media_files(tmp_path, project_b, job_b)
    token = "lesson-a-token"
    _create_share_link(project_a, owner, token=token)
    client = APIClient()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        metadata = client.get(f"/api/v1/share/{token}/")
        assert metadata.status_code == 200
        media_token = _stream_token(metadata.data["stream_url"])
        _job_id, file_type, _rel_path, grant_id, bind_key = views.validate_media_token(media_token)
        forged = views.generate_media_token(
            job_b.id,
            file_type,
            ttl_seconds=900,
            grant_id=grant_id,
            bind_key=bind_key,
        )
        forged_response = client.get(f"/api/v1/stream/{forged}/")

    assert forged_response.status_code == 403


@pytest.mark.django_db
def test_shared_playback_includes_avatar_overlay_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr("core.capabilities.avatar_enabled", lambda: True)
    monkeypatch.setattr(views, "avatar_enabled", lambda: True)
    owner = _make_teacher("share_avatar_owner")
    owner.profile.avatar_enabled = True
    owner.profile.avatar_consent_confirmed = True
    owner.profile.avatar_source_valid = True
    owner.profile.avatar_image_processed = f"profiles/{owner.id}/avatar.png"
    owner.profile.save()
    project, job = _make_ready_lesson(owner)
    project.avatar_processing_status = "ready"
    project.avatar_output_path = f"{project.id}/avatar.mp4"
    project.save(update_fields=["avatar_processing_status", "avatar_output_path"])
    _write_media_files(tmp_path, project, job)
    (tmp_path / project.avatar_output_path).write_bytes(b"avatar-video")
    token = "avatar-share-token"
    _create_share_link(project, owner, token=token)
    client = APIClient()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ENABLE_AVATAR=True,
        LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = client.get(f"/api/v1/share/{token}/")

    assert response.status_code == 200
    assert response.data["avatar_overlay"]["enabled"] is True
    assert response.data["avatar_overlay"]["stream_url"].startswith("http://testserver/api/v1/stream/")
    assert project.avatar_output_path not in response.data["avatar_overlay"]["stream_url"]
