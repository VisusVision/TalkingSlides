import os
import sys
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
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import Project  # noqa: E402


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
    lesson = Project.objects.create(title="With Avatar", user=teacher)

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
