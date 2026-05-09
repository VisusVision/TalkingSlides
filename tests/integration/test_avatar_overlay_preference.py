import os
import sys
import json
import uuid
from pathlib import Path

import django
import pytest
from django.db import connection
REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402

pytestmark = pytest.mark.django_db


def _with_session(request):
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def test_avatar_overlay_preference_persists_per_user_and_lesson():
    if not _table_has_column("core_project", "avatar_enabled_override"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"owner_{suffix}", password="pass")
    student = User.objects.create_user(username=f"student_{suffix}", password="pass")
    lesson = Project.objects.create(
        title="With Avatar",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100)

    factory = APIRequestFactory()
    request = factory.put(
        f"/api/v1/projects/{lesson.id}/avatar-overlay/",
        {
            "anchor": "custom",
            "x_percent": 61.5,
            "y_percent": 14.0,
            "width_percent": 27.0,
            "visible": True,
            "pinned": False,
        },
        format="json",
    )
    force_authenticate(request, user=student)
    response = views.AvatarOverlayPreferenceView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert float(response.data["x_percent"]) == 61.5
    assert response.data["anchor"] == "custom"

    get_request = factory.get(f"/api/v1/projects/{lesson.id}/avatar-overlay/")
    force_authenticate(get_request, user=student)
    get_response = views.AvatarOverlayPreferenceView.as_view()(get_request, project_id=lesson.id)

    assert get_response.status_code == 200
    assert float(get_response.data["width_percent"]) == 27.0
    assert get_response.data["pinned"] is False


def test_avatar_visibility_hides_ready_artifact_without_deleting_it(tmp_path):
    if not _table_has_column("core_project", "avatar_visible"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"avatar_owner_{suffix}", password="pass")
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed="avatars/teacher/processed.png",
        avatar_source_valid=True,
        avatar_moderation_status="approved",
    )
    (tmp_path / "avatars" / "teacher").mkdir(parents=True)
    (tmp_path / "avatars" / "teacher" / "processed.png").write_bytes(b"source")

    lesson = Project.objects.create(
        title="Ready Avatar",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        avatar_enabled_override=True,
        avatar_processing_status="ready",
        avatar_visible=True,
        avatar_output_path="1/avatar/avatar_track.mp4",
    )
    lesson.avatar_output_path = f"{lesson.id}/avatar/avatar_track.mp4"
    lesson.save(update_fields=["avatar_output_path"])
    avatar_path = tmp_path / lesson.avatar_output_path
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"avatar-track")
    (tmp_path / str(lesson.id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / str(lesson.id) / "playback_assets.json").write_text(
        json.dumps({"avatar": {"track_rel_path": lesson.avatar_output_path}}),
        encoding="utf-8",
    )
    Job.objects.create(
        project=lesson,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{lesson.id}/{lesson.id}.mp4",
    )

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/projects/{lesson.id}/",
        {"avatar_visible": False},
        format="json",
    )
    force_authenticate(request, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    lesson.refresh_from_db()
    assert lesson.avatar_visible is False
    assert lesson.avatar_processing_status == "ready"
    assert avatar_path.exists()

    public_request = _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/"))
    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        public_response = views.CatalogDetailView.as_view()(public_request, project_id=lesson.id)

    assert public_response.status_code == 200
    assert public_response.data["avatar_overlay"]["enabled"] is False
    assert public_response.data["avatar_available"] is False


def test_watch_payload_exposes_avatar_only_when_visible_and_ready(tmp_path):
    if not _table_has_column("core_project", "avatar_processing_status"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"watch_avatar_{suffix}", password="pass")
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed="avatars/watch/processed.png",
        avatar_source_valid=True,
        avatar_moderation_status="approved",
    )
    (tmp_path / "avatars" / "watch").mkdir(parents=True)
    (tmp_path / "avatars" / "watch" / "processed.png").write_bytes(b"source")

    lesson = Project.objects.create(
        title="Watch Avatar",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        avatar_enabled_override=True,
        avatar_processing_status="queued",
        avatar_visible=True,
        avatar_processing_message="Avatar is still processing and will be added when ready.",
    )
    Job.objects.create(
        project=lesson,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{lesson.id}/{lesson.id}.mp4",
    )
    sidecar_path = tmp_path / str(lesson.id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_text(json.dumps({"avatar": {"track_rel_path": f"{lesson.id}/avatar/avatar_track.mp4"}}), encoding="utf-8")

    factory = APIRequestFactory()
    request = _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/"))
    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        queued_response = views.CatalogDetailView.as_view()(request, project_id=lesson.id)

    assert queued_response.status_code == 200
    assert queued_response.data["avatar_processing_status"] == "queued"
    assert queued_response.data["avatar_overlay"]["enabled"] is False

    lesson.avatar_processing_status = "ready"
    lesson.avatar_output_path = f"{lesson.id}/avatar/avatar_track.mp4"
    lesson.save(update_fields=["avatar_processing_status", "avatar_output_path"])
    avatar_path = tmp_path / lesson.avatar_output_path
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"avatar-track")

    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        ready_response = views.CatalogDetailView.as_view()(
            _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/")),
            project_id=lesson.id,
        )

    assert ready_response.status_code == 200
    assert ready_response.data["avatar_available"] is True
    assert ready_response.data["avatar_overlay"]["enabled"] is True
