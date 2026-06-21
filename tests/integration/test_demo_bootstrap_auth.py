import os
import sys
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command  # noqa: E402
from rest_framework.authtoken.models import Token  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402


pytestmark = pytest.mark.django_db

DEMO_USERNAME = "demo.tech.teacher@example.com"
DEMO_PASSWORD = "visus-demo-local"


def test_seeded_demo_teacher_can_authenticate_through_login_endpoint():
    call_command("seed_demo_data", "--reset-demo", "--without-analytics-activity")

    response = APIClient().post(
        "/api/v1/auth/login/",
        {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["token"]
    assert response.data["user"]["id"]
    assert response.data["user"]["username"] == DEMO_USERNAME
    assert response.data["user"]["email"] == DEMO_USERNAME
    assert response.data["user"]["auth_provider"] == "password"
    assert Token.objects.filter(
        user__username=DEMO_USERNAME,
        key=response.data["token"],
    ).exists()
