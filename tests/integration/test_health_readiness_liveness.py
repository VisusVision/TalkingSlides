# pyright: reportMissingImports=false

import os
import sys
from pathlib import Path

import django
import pytest
from django.test import RequestFactory

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from config import urls as root_urls  # noqa: E402


class _Cursor:
    def execute(self, _query):
        return None

    def fetchone(self):
        return (1,)


class _CursorContext:
    def __enter__(self):
        return _Cursor()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _ConnectionOk:
    def cursor(self):
        return _CursorContext()


class _ConnectionFail:
    def cursor(self):
        raise RuntimeError("db_down")


class _CacheOk:
    def __init__(self):
        self._store = {}

    def set(self, key, value, timeout=None):
        self._store[key] = value
        return True

    def get(self, key):
        return self._store.get(key)


class _CacheFail:
    def set(self, key, value, timeout=None):
        raise RuntimeError("cache_down")

    def get(self, key):
        raise RuntimeError("cache_down")


def test_health_live_and_ready_ok(monkeypatch):
    rf = RequestFactory()
    monkeypatch.setattr(root_urls, "connection", _ConnectionOk())
    monkeypatch.setattr(root_urls, "cache", _CacheOk())

    health_resp = root_urls.health(rf.get("/health/"))
    assert health_resp.status_code == 200

    live_resp = root_urls.live(rf.get("/live/"))
    assert live_resp.status_code == 200

    ready_resp = root_urls.ready(rf.get("/ready/"))
    assert ready_resp.status_code == 200
    assert b"\"status\": \"ready\"" in ready_resp.content


def test_ready_returns_503_when_dependency_fails(monkeypatch):
    rf = RequestFactory()
    monkeypatch.setattr(root_urls, "connection", _ConnectionFail())
    monkeypatch.setattr(root_urls, "cache", _CacheFail())

    ready_resp = root_urls.ready(rf.get("/ready/"))
    assert ready_resp.status_code == 503
    assert b"\"status\": \"not_ready\"" in ready_resp.content
