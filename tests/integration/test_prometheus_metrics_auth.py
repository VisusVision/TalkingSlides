# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest
from django.contrib.auth.models import User
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core import views  # noqa: E402


@pytest.mark.django_db
def test_prometheus_metrics_requires_auth_or_token(monkeypatch):
    monkeypatch.setattr(views, "_PROMETHEUS_AVAILABLE", False)
    request = APIRequestFactory().get("/api/v1/system/metrics/prometheus/")
    response = views.PrometheusMetricsView.as_view()(request)
    assert response.status_code == 401


@pytest.mark.django_db
def test_prometheus_metrics_accepts_configured_token(monkeypatch):
    monkeypatch.setattr(views, "_PROMETHEUS_AVAILABLE", False)
    request = APIRequestFactory().get("/api/v1/system/metrics/prometheus/", HTTP_X_METRICS_TOKEN="metrics-secret")
    with override_settings(PROMETHEUS_METRICS_TOKEN="metrics-secret"):
        response = views.PrometheusMetricsView.as_view()(request)
    assert response.status_code == 503


@pytest.mark.django_db
def test_prometheus_metrics_accepts_staff_user(monkeypatch):
    monkeypatch.setattr(views, "_PROMETHEUS_AVAILABLE", False)
    staff = User.objects.create_superuser(username="metrics_admin", password="pass", email="admin@example.com")
    request = APIRequestFactory().get("/api/v1/system/metrics/prometheus/")
    force_authenticate(request, user=staff)
    response = views.PrometheusMetricsView.as_view()(request)
    assert response.status_code == 503
