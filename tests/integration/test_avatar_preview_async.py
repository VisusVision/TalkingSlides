import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.db import connection
from django.conf import settings
REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import Job, UserProfile, VoiceProfile  # noqa: E402

pytestmark = pytest.mark.django_db


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def test_avatar_preview_regenerate_enqueues_fast_job(monkeypatch):
    if not _table_has_column("core_userprofile", "avatar_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    user = User.objects.create_user(username=f"teacher_async_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_image_processed="avatars/1/hash/processed.png",
        avatar_image_original="avatars/1/hash/original.png",
        avatar_last_preview_path="avatars/old/preview/preview.mp4",
        avatar_preview_video="avatars/old/preview/preview.mp4",
        avatar_consent_confirmed=True,
        avatar_enabled=True,
    )
    processed_abs = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / profile.avatar_image_processed
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.write_bytes(b"test")
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_teacher_async_{suffix}")

    def fake_refresh_avatar_source_validation(profile_arg, **kwargs):
        profile_arg.avatar_source_valid = True
        profile_arg.avatar_source_validation_error = ""
        profile_arg.avatar_source_hash = "source-hash"
        profile_arg.avatar_preview_stale = False
        profile_arg.avatar_moderation_status = "approved"
        if kwargs.get("persist", True):
            profile_arg.save(
                update_fields=[
                    "avatar_source_valid",
                    "avatar_source_validation_error",
                    "avatar_source_hash",
                    "avatar_preview_stale",
                    "avatar_moderation_status",
                    "updated_at",
                ]
            )
        return {
            "valid": True,
            "validation_current": True,
            "source_hash": "source-hash",
            "preview_stale": False,
            "error": "",
        }

    monkeypatch.setattr(views, "refresh_avatar_source_validation", fake_refresh_avatar_source_validation)
    monkeypatch.setattr(
        views,
        "_avatar_preview_readiness",
        lambda profile_arg, voice_profile, *, storage_root: {
            "ready": True,
            "missing_requirements": [],
            "checks": {"avatar_source_valid": True},
        },
    )

    sent = {}

    def fake_send_task(name, kwargs=None, args=None):
        sent["name"] = name
        sent["kwargs"] = kwargs or {}
        return SimpleNamespace(id="celery-preview-123")

    monkeypatch.setattr(views, "_celery_app", SimpleNamespace(send_task=fake_send_task))

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/preview/")
    force_authenticate(request, user=user)
    response = views.AvatarPreviewRegenerateView.as_view()(request, user_id=user.id)

    assert response.status_code == 202
    assert response.data["status"] == "queued"
    assert response.data["job_id"]
    assert sent["name"] == "worker.tasks.render_avatar_preview"
    profile.refresh_from_db()
    assert profile.avatar_last_preview_status == "queued"
    assert profile.avatar_last_preview_path == ""
    assert profile.avatar_preview_video == ""


def test_avatar_preview_regenerate_rejects_missing_voice_profile():
    if not _table_has_column("core_userprofile", "avatar_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    os.environ.setdefault("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    os.environ.setdefault("AVATAR_MUSETALK_CMD", "echo musetalk")
    user = User.objects.create_user(username=f"teacher_missing_voice_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_image_processed="avatars/1/hash/processed.png",
        avatar_image_original="avatars/1/hash/original.png",
        avatar_consent_confirmed=True,
        avatar_enabled=True,
    )
    processed_abs = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / profile.avatar_image_processed
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.write_bytes(b"test")

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/preview/")
    force_authenticate(request, user=user)
    response = views.AvatarPreviewRegenerateView.as_view()(request, user_id=user.id)

    assert response.status_code == 400
    assert response.data.get("error_code") == "setup_not_prepared"
    assert "missing_voice_profile" in (response.data.get("missing_requirements") or [])


def test_avatar_preview_status_polling_returns_job_state():
    if not _table_has_column("core_userprofile", "avatar_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    os.environ.setdefault("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    os.environ.setdefault("AVATAR_MUSETALK_CMD", "echo musetalk")
    user = User.objects.create_user(username=f"teacher_status_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_last_preview_status="done",
        avatar_last_preview_job_id="",
        avatar_last_preview_path="avatars/2/preview/preview.mp4",
    )
    job = Job.objects.create(job_type="avatar_render", status="done", progress=100, result_url=profile.avatar_last_preview_path)
    profile.avatar_last_preview_job_id = str(job.id)
    profile.save(update_fields=["avatar_last_preview_job_id"])

    factory = APIRequestFactory()
    request = factory.get(f"/api/v1/users/{user.id}/avatar/preview/{job.id}/")
    force_authenticate(request, user=user)
    response = views.AvatarPreviewStatusView.as_view()(request, user_id=user.id, job_id=job.id)

    assert response.status_code == 200
    assert response.data["status"] == "done"
    assert response.data["preview_rel_path"] == "avatars/2/preview/preview.mp4"
    assert "preview_readiness" in response.data
    assert response.data["normalized_engine"] == "liveportrait+musetalk"
    assert response.data["avatar_engine_selected"] == "liveportrait+musetalk"


def test_avatar_preview_status_hides_preview_path_for_non_current_job():
    if not _table_has_column("core_userprofile", "avatar_image_original"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    os.environ.setdefault("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    os.environ.setdefault("AVATAR_MUSETALK_CMD", "echo musetalk")
    user = User.objects.create_user(username=f"teacher_status_hidden_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_last_preview_status="rendering",
        avatar_last_preview_job_id="999999",
        avatar_last_preview_path="avatars/2/preview/preview.mp4",
        avatar_preview_video="avatars/2/preview/preview.mp4",
    )
    job = Job.objects.create(job_type="avatar_render", status="running", progress=40, result_url="avatars/2/preview/preview.mp4")

    factory = APIRequestFactory()
    request = factory.get(f"/api/v1/users/{user.id}/avatar/preview/{job.id}/")
    force_authenticate(request, user=user)
    response = views.AvatarPreviewStatusView.as_view()(request, user_id=user.id, job_id=job.id)

    assert response.status_code == 200
    assert response.data["preview_status"] == "rendering"
    assert response.data["preview_rel_path"] == ""
