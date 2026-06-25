import os
import shutil
import subprocess
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
from core.models import Job, LessonProgress, Project  # noqa: E402


pytestmark = pytest.mark.django_db

FAKE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2demo-video"


def test_seeded_published_lessons_have_existing_streamable_video(tmp_path, monkeypatch):
    requested_durations = []

    def fake_video(duration_seconds):
        requested_durations.append(duration_seconds)
        return FAKE_MP4

    monkeypatch.setattr(seed_demo_data, "_generate_demo_video_bytes", fake_video)

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
            transcript_end = project.transcript_pages.order_by("-end_seconds").values_list(
                "end_seconds", flat=True
            ).first()
            assert transcript_end
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
            assert detail_response.data["duration_seconds"] == int(transcript_end)
            assert detail_response.data["duration_minutes"] == int((transcript_end + 59) // 60)

        assert sorted(set(requested_durations)) == sorted(
            {
                float(
                    project.transcript_pages.order_by("-end_seconds").values_list(
                        "end_seconds", flat=True
                    ).first()
                )
                for project in published
            }
        )


def test_seed_without_video_fixture_does_not_advertise_playback(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_demo_data, "_generate_demo_video_bytes", lambda _duration: None)

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


def test_seeded_catalog_feed_detail_and_profile_use_consistent_duration_and_views(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_demo_data, "_generate_demo_video_bytes", lambda _duration: FAKE_MP4)

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        STORAGE_BACKEND="filesystem",
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        call_command("seed_demo_data", "--reset-demo", "--with-analytics-activity")

        project = Project.objects.get(title="Introduction to Photosynthesis")
        expected_duration_seconds = int(
            project.transcript_pages.order_by("-end_seconds").values_list("end_seconds", flat=True).first()
        )
        expected_duration_minutes = (expected_duration_seconds + 59) // 60
        expected_views = LessonProgress.objects.filter(project=project).count()
        client = APIClient()

        catalog_row = next(
            row for row in client.get("/api/v1/catalog/").data if row["id"] == project.id
        )
        feed_response = client.get("/api/v1/catalog/feed/?limit=24")
        feed_rows = [
            row
            for section in feed_response.data["sections"]
            for row in section["items"]
            if row["id"] == project.id
        ]
        detail_row = client.get(f"/api/v1/catalog/{project.id}/").data
        profile_rows = client.get(f"/api/v1/users/{project.user_id}/lessons/").data["results"]
        profile_row = next(row for row in profile_rows if row["id"] == project.id)

        for row in [catalog_row, detail_row, profile_row, *feed_rows]:
            assert row["duration_seconds"] == expected_duration_seconds
            assert row["duration_minutes"] == expected_duration_minutes
            assert row["view_count"] == expected_views


def test_generated_demo_video_duration_matches_requested_contract(tmp_path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg/ffprobe are required for the generated fixture duration contract")

    payload = seed_demo_data._generate_demo_video_bytes(3.0)
    assert payload
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(payload)
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert float(probe.stdout.strip()) == pytest.approx(3.0, abs=0.1)
