# pyright: reportMissingImports=false
import json
import os
import sys
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
from core.models import Job, Project, UserProfile  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework import status  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_playback_cache():
    cache.clear()
    yield
    cache.clear()


def _make_user(username: str, role: str = "teacher") -> User:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role=role)
    return user


def _client(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _make_ready_video_lesson(tmp_path: Path, owner: User, *, title: str, is_published: bool) -> tuple[Project, Job]:
    project = Project.objects.create(
        title=title,
        user=owner,
        status="ready",
        moderation_status="approved",
        is_published=is_published,
    )
    video_rel_path = f"{project.id}/{project.id}.mp4"
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=video_rel_path,
    )
    (tmp_path / str(project.id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / video_rel_path).write_bytes(b"fake-mp4-preview")
    return project, job


def _stream_path(video_url: str) -> str:
    return urlparse(video_url).path


def _stream_token(video_url: str) -> str:
    return _stream_path(video_url).rstrip("/").rsplit("/", 1)[-1]


class _Session:
    def __init__(self, session_key: str):
        self.session_key = session_key

    def save(self):
        return None


class _GrantRequest:
    def __init__(self, user: User, session_key: str):
        self.user = user
        self.session = _Session(session_key)


@pytest.mark.django_db
class TestStudioPreviewToken:
    def test_owner_can_request_unpublished_preview_and_stream_video(self, tmp_path):
        owner = _make_user("preview_unpublished_owner")
        project, job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Draft Lesson",
            is_published=False,
        )

        client = _client(owner)
        with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="secure_stream"):
            response = client.get(f"/api/v1/projects/{project.id}/studio-preview-token/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_studio_preview"] is True
        assert response.data["video_url"].startswith("http://testserver/api/v1/stream/")
        assert response.data["playback_status"]["protection_mode"] == "secure_stream"
        assert response.data["session_binding_active"] is False

        token = response.data["video_url"].rstrip("/").split("/")[-1]
        token_job_id, file_type, _rel_path, grant_id, bind_key = views.validate_media_token(token)
        assert token_job_id == job.id
        assert file_type == "video"
        assert grant_id
        assert not bind_key

        with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="secure_stream"):
            stream_response = _client().get(_stream_path(response.data["video_url"]), HTTP_RANGE="bytes=0-3")

        assert stream_response.status_code in {status.HTTP_200_OK, status.HTTP_206_PARTIAL_CONTENT}
        assert stream_response.status_code != status.HTTP_403_FORBIDDEN

    def test_owner_can_request_published_preview_and_stream_video(self, tmp_path):
        owner = _make_user("preview_published_owner")
        project, _job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Published Lesson",
            is_published=True,
        )

        client = _client(owner)
        with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
            response = client.get(f"/api/v1/projects/{project.id}/studio-preview-token/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_studio_preview"] is True
        assert response.data["video_url"].startswith("http://testserver/api/v1/stream/")
        assert response.data["session_binding_active"] is False

        with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
            stream_response = _client().get(_stream_path(response.data["video_url"]), HTTP_RANGE="bytes=0-3")

        assert stream_response.status_code in {status.HTTP_200_OK, status.HTTP_206_PARTIAL_CONTENT}
        assert stream_response.status_code != status.HTTP_403_FORBIDDEN

    def test_studio_preview_does_not_trigger_public_playback_concurrency_error(self, tmp_path):
        owner = _make_user("preview_concurrency_owner")
        project, _job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Concurrency Test",
            is_published=False,
        )
        views._issue_playback_grant(
            project.id,
            _GrantRequest(owner, "other-browser-session"),
            "secure_stream",
            1200,
        )

        client = _client(owner)
        with override_settings(
            STORAGE_ROOT=str(tmp_path),
            LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
            LESSON_PROTECTION_CONCURRENCY_POLICY="deny_new",
        ):
            response = client.get(f"/api/v1/projects/{project.id}/studio-preview-token/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data.get("error") != "This lesson is already active in another browser session."

    def test_non_owner_cannot_request_unpublished_preview_token(self, tmp_path):
        owner = _make_user("preview_private_owner")
        other_user = _make_user("preview_private_other")
        project, _job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Private Draft",
            is_published=False,
        )

        client = _client(other_user)
        with override_settings(STORAGE_ROOT=str(tmp_path)):
            response = client.get(f"/api/v1/projects/{project.id}/studio-preview-token/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_public_catalog_excludes_unpublished_preview_lesson(self, tmp_path):
        owner = _make_user("preview_catalog_owner")
        project, _job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Catalog Hidden Draft",
            is_published=False,
        )

        response = _client().get("/api/v1/catalog/")

        assert response.status_code == status.HTTP_200_OK
        assert project.id not in {item["id"] for item in response.data}

    def test_studio_preview_payload_does_not_expose_raw_storage_path(self, tmp_path):
        owner = _make_user("preview_storage_owner")
        project, job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Storage Path Draft",
            is_published=False,
        )

        client = _client(owner)
        with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="secure_stream"):
            response = client.get(f"/api/v1/projects/{project.id}/studio-preview-token/")

        assert response.status_code == status.HTTP_200_OK
        payload_text = json.dumps(response.data)
        assert "/api/v1/stream/" in payload_text
        assert job.result_url not in payload_text
        assert str(tmp_path) not in payload_text
        assert "storage_local" not in payload_text

    def test_public_playback_stays_session_bound_and_concurrency_locked(self, tmp_path):
        owner = _make_user("preview_public_owner")
        watcher = _make_user("preview_public_watcher")
        project, _job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Public Secure Lesson",
            is_published=True,
        )

        first_client = _client(watcher)
        with override_settings(
            STORAGE_ROOT=str(tmp_path),
            LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
            LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=True,
            LESSON_PROTECTION_CONCURRENCY_POLICY="deny_new",
        ):
            token_response = first_client.get(f"/api/v1/projects/{project.id}/playback-token/")

        assert token_response.status_code == status.HTTP_200_OK
        assert token_response.data["session_binding_active"] is True
        assert token_response.data["video_url"].startswith("http://testserver/api/v1/stream/")

        with override_settings(
            STORAGE_ROOT=str(tmp_path),
            LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
            LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=True,
        ):
            fresh_stream_response = _client().get(_stream_path(token_response.data["video_url"]), HTTP_RANGE="bytes=0-3")

        assert fresh_stream_response.status_code == status.HTTP_403_FORBIDDEN

        second_client = _client(watcher)
        with override_settings(
            STORAGE_ROOT=str(tmp_path),
            LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
            LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=True,
            LESSON_PROTECTION_CONCURRENCY_POLICY="deny_new",
        ):
            second_token_response = second_client.get(f"/api/v1/projects/{project.id}/playback-token/")

        assert second_token_response.status_code == status.HTTP_409_CONFLICT
        assert second_token_response.data["reason"] == "concurrency_active_elsewhere"

    def test_public_playback_token_is_idempotent_for_same_client_session(self, tmp_path):
        owner = _make_user("preview_idempotent_owner")
        watcher = _make_user("preview_idempotent_watcher")
        project, _job = _make_ready_video_lesson(
            tmp_path,
            owner,
            title="Public Secure Lesson Same Session",
            is_published=True,
        )

        client = _client(watcher)
        with override_settings(
            STORAGE_ROOT=str(tmp_path),
            LESSON_PROTECTION_DEFAULT_MODE="secure_stream",
            LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=True,
            LESSON_PROTECTION_CONCURRENCY_POLICY="deny_new",
        ):
            first_response = client.get(f"/api/v1/projects/{project.id}/playback-token/")
            second_response = client.get(f"/api/v1/projects/{project.id}/playback-token/")

        assert first_response.status_code == status.HTTP_200_OK
        assert second_response.status_code == status.HTTP_200_OK
        first_token = _stream_token(first_response.data["video_url"])
        second_token = _stream_token(second_response.data["video_url"])
        _job_id, _file_type, _rel_path, first_grant_id, first_bind_key = views.validate_media_token(first_token)
        _job_id, _file_type, _rel_path, second_grant_id, second_bind_key = views.validate_media_token(second_token)
        assert first_grant_id
        assert first_grant_id == second_grant_id
        assert first_bind_key
        assert first_bind_key == second_bind_key

    def test_studio_preview_token_no_ready_video(self):
        owner = _make_user("preview_no_video_owner")
        project = Project.objects.create(title="No Video", user=owner, is_published=False)

        client = _client(owner)
        response = client.get(f"/api/v1/projects/{project.id}/studio-preview-token/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "No ready video" in response.data["error"]
