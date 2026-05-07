# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import RequestFactory  # noqa: E402
from core.internal_http import build_internal_request_headers  # noqa: E402


def test_internal_headers_forward_traceparent_and_request_id():
    rf = RequestFactory()
    request = rf.get(
        "/api/v1/tts/preview/",
        HTTP_X_REQUEST_ID="req-forward-1",
        HTTP_TRACEPARENT="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
    )
    request.request_id = "req-forward-1"
    request.trace_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    headers = build_internal_request_headers(request, {"Accept": "application/json"})
    assert headers["X-Request-ID"] == "req-forward-1"
    assert headers["traceparent"].startswith("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-")
