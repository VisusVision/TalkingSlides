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

from django.core.management import call_command  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from core.management.commands import seed_demo_data  # noqa: E402
from core.models import Job, Project  # noqa: E402


pytestmark = pytest.mark.django_db

FAKE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2demo-video"


def test_seeded_published_lessons_have_existing_streamable_video(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_demo_data, "_generate_demo_video_bytes", lambda: FAKE_MP4)

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        STORAGE_BACKEND="filesystem",
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        call_command("seed_demo_data", "--reset-demo", "--without-analytics-activity")

        published = list(Project.objects.filter(is_published=True).order_by("id"))
        assert published

        client = APIClient()
        catalog_response = client.get("/api/v1/catalog/")
        assert catalog_response.status_code == 200
        assert len(catalog_response.data) == len(published)

        for project in published:
            job = Job.objects.get(project=project, job_type="video_export", status="done")
            assert job.result_url
            assert (tmp_path / job.result_url).is_file()

            detail_response = client.get(f"/api/v1/catalog/{project.id}/")
            assert detail_response.status_code == 200
            assert detail_response.data["stream_url"]

            stream_path = urlparse(detail_response.data["stream_url"]).path
            stream_response = client.get(stream_path)
            assert stream_response.status_code == 200
            assert stream_response["Content-Type"].startswith("video/mp4")
            assert b"".join(stream_response.streaming_content) == FAKE_MP4


def test_seed_without_video_fixture_does_not_advertise_playback(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_demo_data, "_generate_demo_video_bytes", lambda: None)

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        STORAGE_BACKEND="filesystem",
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        call_command("seed_demo_data", "--reset-demo", "--without-analytics-activity")

        assert not Project.objects.filter(is_published=True).exists()
        assert not Job.objects.filter(job_type="video_export", status="done").exists()

        catalog_response = APIClient().get("/api/v1/catalog/")
        assert catalog_response.status_code == 200
        assert catalog_response.data == []
