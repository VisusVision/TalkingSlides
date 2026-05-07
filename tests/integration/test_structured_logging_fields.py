# pyright: reportMissingImports=false

import json
import os
import sys
from pathlib import Path

import django
import pytest
from django.http import JsonResponse
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory

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
from core.middleware import StructuredRequestLoggingMiddleware  # noqa: E402


@pytest.mark.django_db
def test_structured_logging_helper_emits_standard_fields(caplog):
    rf = APIRequestFactory()
    request = rf.get("/api/v1/projects/1/jobs/2/", HTTP_X_REQUEST_ID="req-123", HTTP_TRACEPARENT="00-abc123def4567890-1111111111111111-01")
    with caplog.at_level("INFO"):
        views._log_with_standard_fields(
            "info",
            "test_structured_log",
            request=request,
            user_id=10,
            project_id=20,
            job_id=30,
            queue="render",
            stage="test",
            started_at=None,
        )
    assert caplog.records
    message = caplog.records[-1].message
    payload_raw = message.split("|", 1)[1].strip()
    payload = json.loads(payload_raw)
    for key in ("request_id", "trace_id", "user_id", "project_id", "job_id", "queue", "stage", "duration_ms"):
        assert key in payload


def test_structured_logging_middleware_logs_lifecycle_and_sets_response_header(caplog):
    rf = RequestFactory()
    middleware = StructuredRequestLoggingMiddleware(lambda _req: JsonResponse({"ok": True}, status=200))
    request = rf.get("/api/v1/health/", HTTP_X_REQUEST_ID="req-mw-001", HTTP_TRACEPARENT="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01")
    with caplog.at_level("INFO"):
        response = middleware(request)
    assert response.status_code == 200
    assert response["X-Request-ID"] == "req-mw-001"
    messages = [record.message for record in caplog.records if "|" in record.message]
    assert any("request_started" in msg for msg in messages)
    assert any("request_finished" in msg for msg in messages)


def test_structured_logging_middleware_generates_request_id_when_missing():
    rf = RequestFactory()
    middleware = StructuredRequestLoggingMiddleware(lambda _req: JsonResponse({"ok": True}, status=200))
    request = rf.get("/api/v1/health/")
    response = middleware(request)
    assert response.status_code == 200
    assert response["X-Request-ID"].startswith("req_")
