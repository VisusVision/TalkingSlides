import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import django
import pytest
from django.conf import settings
REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import Job, UserProfile, VoiceProfile  # noqa: E402
from tests.integration.schema_skip import skip_if_column_missing  # noqa: E402
from worker import avatar_preview_flow  # noqa: E402

pytestmark = pytest.mark.django_db


def _make_preview_ready_user(monkeypatch, username_prefix="teacher_preview_dedupe"):
    skip_if_column_missing("core_userprofile", "avatar_image_original")

    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    user = User.objects.create_user(username=f"{username_prefix}_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_image_processed=f"avatars/{suffix}/processed.png",
        avatar_image_original=f"avatars/{suffix}/original.png",
        avatar_consent_confirmed=True,
        avatar_enabled=True,
        avatar_source_valid=True,
        avatar_source_validation_error="",
        avatar_source_hash="source-hash",
        avatar_preview_stale=False,
        avatar_moderation_status="approved",
    )
    processed_abs = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / profile.avatar_image_processed
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.write_bytes(b"test")
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_{suffix}")

    monkeypatch.setattr(
        views,
        "refresh_avatar_source_validation",
        lambda profile_arg, **_kwargs: {
            "valid": True,
            "validation_current": True,
            "source_hash": "source-hash",
            "preview_stale": False,
            "error": "",
        },
    )
    monkeypatch.setattr(
        views,
        "_avatar_preview_readiness",
        lambda profile_arg, voice_profile, *, storage_root: {
            "ready": True,
            "missing_requirements": [],
            "checks": {"avatar_source_valid": True},
        },
    )
    return user, profile


def _post_preview_regenerate(user):
    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/preview/")
    force_authenticate(request, user=user)
    return views.AvatarPreviewRegenerateView.as_view()(request, user_id=user.id)


def test_avatar_preview_regenerate_enqueues_fast_job(monkeypatch):
    skip_if_column_missing("core_userprofile", "avatar_image_original")

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
    assert response.data["avatar_setup_status"]["state"] == "preparing"
    assert sent["name"] == "worker.tasks.render_avatar_preview"
    profile.refresh_from_db()
    assert profile.avatar_last_preview_status == "queued"
    assert profile.avatar_last_preview_path == ""
    assert profile.avatar_preview_video == ""


def test_avatar_preview_regenerate_reuses_pending_preview_job(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_pending_dedupe")
    sent = []

    def fake_send_task(name, kwargs=None, args=None):
        sent.append({"name": name, "kwargs": kwargs or {}})
        return SimpleNamespace(id=f"celery-preview-{len(sent)}")

    monkeypatch.setattr(views, "_celery_app", SimpleNamespace(send_task=fake_send_task))

    first = _post_preview_regenerate(user)
    second = _post_preview_regenerate(user)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["status"] == "queued"
    assert second.data["status"] == "queued"
    assert second.data["job_id"] == first.data["job_id"]
    assert second.data["task_id"] == "celery-preview-1"
    assert len(sent) == 1
    assert Job.objects.filter(job_type="avatar_render", status="pending").count() == 1
    profile.refresh_from_db()
    assert profile.avatar_last_preview_job_id == str(first.data["job_id"])


def test_avatar_preview_regenerate_reuses_running_preview_job(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_running_dedupe")
    job = Job.objects.create(job_type="avatar_render", status="running", progress=40, celery_task_id="celery-running")
    profile.avatar_image_status = "processing"
    profile.avatar_last_preview_status = "rendering"
    profile.avatar_last_preview_job_id = str(job.id)
    profile.save(update_fields=["avatar_image_status", "avatar_last_preview_status", "avatar_last_preview_job_id"])
    monkeypatch.setattr(
        views,
        "_dispatch_celery_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate preview task should not be queued")),
    )

    response = _post_preview_regenerate(user)

    assert response.status_code == 202
    assert response.data["status"] == "queued"
    assert response.data["job_id"] == job.id
    assert response.data["task_id"] == "celery-running"
    assert Job.objects.filter(job_type="avatar_render", status__in=["pending", "running"]).count() == 1


def test_avatar_preview_regenerate_creates_new_job_after_terminal_preview(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_terminal_new")
    old_job = Job.objects.create(job_type="avatar_render", status="done", progress=100, celery_task_id="celery-done")
    profile.avatar_last_preview_status = "done"
    profile.avatar_last_preview_job_id = str(old_job.id)
    profile.save(update_fields=["avatar_last_preview_status", "avatar_last_preview_job_id"])
    sent = []

    def fake_send_task(name, kwargs=None, args=None):
        sent.append({"name": name, "kwargs": kwargs or {}})
        return SimpleNamespace(id="celery-preview-new")

    monkeypatch.setattr(views, "_celery_app", SimpleNamespace(send_task=fake_send_task))

    response = _post_preview_regenerate(user)

    assert response.status_code == 202
    assert response.data["status"] == "queued"
    assert response.data["job_id"] != old_job.id
    assert len(sent) == 1
    assert Job.objects.filter(job_type="avatar_render").count() == 2
    assert Job.objects.filter(job_type="avatar_render", status__in=["pending", "running"]).count() == 1
    profile.refresh_from_db()
    assert profile.avatar_last_preview_job_id == str(response.data["job_id"])


def test_stale_avatar_preview_success_does_not_overwrite_current_profile(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_stale_success")
    stale_job = Job.objects.create(job_type="avatar_render", status="running", progress=80)
    current_job = Job.objects.create(job_type="avatar_render", status="running", progress=20)
    profile.avatar_last_preview_status = "rendering"
    profile.avatar_last_preview_job_id = str(current_job.id)
    profile.avatar_last_preview_path = "avatars/current/preview.mp4"
    profile.avatar_preview_video = "avatars/current/preview.mp4"
    profile.avatar_preview_error = ""
    profile.avatar_image_status = "processing"
    profile.save(
        update_fields=[
            "avatar_last_preview_status",
            "avatar_last_preview_job_id",
            "avatar_last_preview_path",
            "avatar_preview_video",
            "avatar_preview_error",
            "avatar_image_status",
        ]
    )

    updated = avatar_preview_flow._set_current_avatar_preview_profile_state(
        profile_id=profile.id,
        job_id=stale_job.id,
        status="ready",
        error="",
        preview_rel_path="avatars/stale/preview.mp4",
        image_status="ready",
        preview_source_hash="stale-source-hash",
    )

    assert updated is False
    profile.refresh_from_db()
    assert profile.avatar_last_preview_job_id == str(current_job.id)
    assert profile.avatar_last_preview_status == "rendering"
    assert profile.avatar_last_preview_path == "avatars/current/preview.mp4"
    assert profile.avatar_preview_video == "avatars/current/preview.mp4"
    assert profile.avatar_preview_source_hash == ""


def test_current_avatar_preview_success_updates_profile(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_current_success")
    current_job = Job.objects.create(job_type="avatar_render", status="running", progress=80)
    profile.avatar_last_preview_status = "rendering"
    profile.avatar_last_preview_job_id = str(current_job.id)
    profile.avatar_last_preview_path = ""
    profile.avatar_preview_video = ""
    profile.avatar_preview_error = ""
    profile.avatar_image_status = "processing"
    profile.save(
        update_fields=[
            "avatar_last_preview_status",
            "avatar_last_preview_job_id",
            "avatar_last_preview_path",
            "avatar_preview_video",
            "avatar_preview_error",
            "avatar_image_status",
        ]
    )

    updated = avatar_preview_flow._set_current_avatar_preview_profile_state(
        profile_id=profile.id,
        job_id=current_job.id,
        status="ready",
        error="",
        preview_rel_path="avatars/current/preview.mp4",
        image_status="ready",
        preview_source_hash="current-source-hash",
    )

    assert updated is True
    profile.refresh_from_db()
    assert profile.avatar_last_preview_job_id == str(current_job.id)
    assert profile.avatar_last_preview_status == "ready"
    assert profile.avatar_last_preview_path == "avatars/current/preview.mp4"
    assert profile.avatar_preview_video == "avatars/current/preview.mp4"
    assert profile.avatar_preview_source_hash == "current-source-hash"
    assert profile.avatar_image_status == "ready"


def test_stale_avatar_preview_failure_does_not_overwrite_current_profile(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_stale_failure")
    stale_job = Job.objects.create(job_type="avatar_render", status="running", progress=80)
    current_job = Job.objects.create(job_type="avatar_render", status="running", progress=20)
    profile.avatar_last_preview_status = "rendering"
    profile.avatar_last_preview_job_id = str(current_job.id)
    profile.avatar_last_preview_path = "avatars/current/preview.mp4"
    profile.avatar_preview_video = "avatars/current/preview.mp4"
    profile.avatar_preview_error = ""
    profile.avatar_image_status = "processing"
    profile.save(
        update_fields=[
            "avatar_last_preview_status",
            "avatar_last_preview_job_id",
            "avatar_last_preview_path",
            "avatar_preview_video",
            "avatar_preview_error",
            "avatar_image_status",
        ]
    )

    updated = avatar_preview_flow._set_current_avatar_preview_profile_state(
        profile_id=profile.id,
        job_id=stale_job.id,
        status="failed",
        error="stale failure",
        image_status="ready",
        clear_preview=True,
    )

    assert updated is False
    profile.refresh_from_db()
    assert profile.avatar_last_preview_job_id == str(current_job.id)
    assert profile.avatar_last_preview_status == "rendering"
    assert profile.avatar_preview_error == ""
    assert profile.avatar_last_preview_path == "avatars/current/preview.mp4"
    assert profile.avatar_preview_video == "avatars/current/preview.mp4"


def test_current_avatar_preview_failure_updates_profile(monkeypatch):
    user, profile = _make_preview_ready_user(monkeypatch, "teacher_preview_current_failure")
    current_job = Job.objects.create(job_type="avatar_render", status="running", progress=80)
    profile.avatar_last_preview_status = "rendering"
    profile.avatar_last_preview_job_id = str(current_job.id)
    profile.avatar_last_preview_path = "avatars/current/preview.mp4"
    profile.avatar_preview_video = "avatars/current/preview.mp4"
    profile.avatar_preview_error = ""
    profile.avatar_image_status = "processing"
    profile.save(
        update_fields=[
            "avatar_last_preview_status",
            "avatar_last_preview_job_id",
            "avatar_last_preview_path",
            "avatar_preview_video",
            "avatar_preview_error",
            "avatar_image_status",
        ]
    )

    updated = avatar_preview_flow._set_current_avatar_preview_profile_state(
        profile_id=profile.id,
        job_id=current_job.id,
        status="failed",
        error="current failure",
        image_status="ready",
        clear_preview=True,
    )

    assert updated is True
    profile.refresh_from_db()
    assert profile.avatar_last_preview_job_id == str(current_job.id)
    assert profile.avatar_last_preview_status == "failed"
    assert profile.avatar_preview_error == "current failure"
    assert profile.avatar_last_preview_path == ""
    assert profile.avatar_preview_video == ""
    assert profile.avatar_image_status == "ready"


def test_avatar_preview_regenerate_rejects_missing_voice_profile():
    skip_if_column_missing("core_userprofile", "avatar_image_original")

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
    assert response.data["avatar_setup_status"]["state"] == "missing_voice"
    assert response.data["action_required"] == "upload_voice"
    assert "missing_voice_profile" in (response.data.get("missing_requirements") or [])


def test_avatar_preview_regenerate_blocks_without_consent():
    skip_if_column_missing("core_userprofile", "avatar_image_original")

    suffix = uuid.uuid4().hex[:8]
    os.environ.setdefault("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    os.environ.setdefault("AVATAR_MUSETALK_CMD", "echo musetalk")
    user = User.objects.create_user(username=f"teacher_no_consent_{suffix}", password="pass")
    profile = UserProfile.objects.create(
        user=user,
        role="teacher",
        avatar_image_processed="avatars/1/hash/processed.png",
        avatar_image_original="avatars/1/hash/original.png",
        avatar_consent_confirmed=False,
        avatar_enabled=False,
    )
    processed_abs = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / profile.avatar_image_processed
    processed_abs.parent.mkdir(parents=True, exist_ok=True)
    processed_abs.write_bytes(b"test")
    VoiceProfile.objects.create(user=user, provider="xtts_v2", voice_id=f"voice_no_consent_{suffix}")

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/users/{user.id}/avatar/preview/")
    force_authenticate(request, user=user)
    response = views.AvatarPreviewRegenerateView.as_view()(request, user_id=user.id)

    assert response.status_code == 400
    assert response.data["avatar_setup_status"]["state"] == "missing_consent"
    assert response.data["action_required"] == "confirm_consent"


def test_avatar_preview_status_polling_returns_job_state():
    skip_if_column_missing("core_userprofile", "avatar_image_original")

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
    assert "avatar_setup_status" in response.data
    assert response.data["action_required"] == response.data["avatar_setup_status"]["action_required"]
    assert response.data["normalized_engine"] == "liveportrait+musetalk"
    assert response.data["avatar_engine_selected"] == "liveportrait+musetalk"


def test_avatar_preview_status_hides_preview_path_for_non_current_job():
    skip_if_column_missing("core_userprofile", "avatar_image_original")

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
