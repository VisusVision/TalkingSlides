import os
import sys
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.views import MediaServeView  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402


@pytest.mark.django_db
def test_raw_media_endpoint_rejects_non_staff_users(tmp_path):
    media_file = tmp_path / "debug.txt"
    media_file.write_text("debug-only", encoding="utf-8")
    user = User.objects.create_user(username="media-user", password="pass")
    request = APIRequestFactory().get("/api/v1/media/debug.txt")
    force_authenticate(request, user=user)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = MediaServeView.as_view()(request, filepath="debug.txt")

    assert response.status_code == 403


@pytest.mark.django_db
def test_raw_media_endpoint_allows_staff_debug_access(tmp_path):
    media_file = tmp_path / "debug.txt"
    media_file.write_bytes(b"debug-only")
    staff = User.objects.create_user(username="media-staff", password="pass", is_staff=True)
    request = APIRequestFactory().get("/api/v1/media/debug.txt")
    force_authenticate(request, user=staff)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = MediaServeView.as_view()(request, filepath="debug.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert b"".join(response.streaming_content) == b"debug-only"
