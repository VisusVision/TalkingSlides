import builtins
import json
import os
import sys
import importlib
import uuid
from pathlib import Path

import django
import pytest
from urllib.error import URLError

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from core import views  # noqa: E402

def test_tts_client_import_isolation(monkeypatch):
    """
    Ensure that importing scripts.tts_client uses the isolated `tts_preprocess`
    package and does not accidentally resolve `preprocess` to `avatar.preprocess`
    when `/app/avatar` or similar is on sys.path.
    """
    # 1. Simulate the worker environment by adding avatar/ to sys.path
    # We do a mock add by putting it at the front of sys.path.
    repo_root = Path(__file__).resolve().parents[2]
    avatar_dir = repo_root / "services" / "avatar"
    
    # Pre-add the avatar dir to sys.path
    monkeypatch.syspath_prepend(str(avatar_dir))
    
    # 2. Add tts_service to sys.path as it would be normally or via tts_client fallback
    tts_service_dir = repo_root / "services" / "tts_service"
    monkeypatch.syspath_prepend(str(tts_service_dir))
    
    scripts_dir = repo_root / "services" / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    
    # Remove any cached imports to ensure we test the import behavior cleanly
    for mod in list(sys.modules.keys()):
        if mod == "tts_client" or mod == "tts_preprocess" or mod == "preprocess":
            del sys.modules[mod]
            
    # 3. Import tts_client
    try:
        tts_client = importlib.import_module("tts_client")
    except ImportError as e:
        pytest.fail(f"Could not import tts_client: {e}")

    # 4. Verify that prepare_text_for_tts is imported correctly from tts_preprocess
    # rather than avatar.preprocess
    assert hasattr(tts_client, "prepare_text_for_tts"), "prepare_text_for_tts missing in tts_client"
    
    # Verify module origins
    assert "tts_preprocess" in sys.modules, "tts_preprocess module not loaded"
    preprocess_module = sys.modules["tts_preprocess"]
    
    # Check it came from the correct path, not the avatar path
    assert "tts_service" in str(preprocess_module.__file__)
    
    # Confirm it's the TTS preprocessing module, not the avatar preprocessing module
    assert not hasattr(preprocess_module, "AvatarValidationError"), "tts_preprocess incorrectly resolved to avatar preprocessing"
    assert hasattr(preprocess_module, "prepare_text_for_tts"), "tts_preprocess is missing expected TTS preprocessing functions"


def test_preview_helper_exists_and_uses_tts_preprocess(monkeypatch):
    """
    Phase 1 — Confirm preview_tts_text_with_metadata is exported by tts_client
    and that importing it does not pull in the old bare 'preprocess' module.
    This test simulates the worker environment (avatar dir on sys.path).
    """
    repo_root = Path(__file__).resolve().parents[2]
    avatar_dir = repo_root / "services" / "avatar"
    tts_service_dir = repo_root / "services" / "tts_service"
    scripts_dir = repo_root / "services" / "scripts"

    monkeypatch.syspath_prepend(str(avatar_dir))
    monkeypatch.syspath_prepend(str(tts_service_dir))
    monkeypatch.syspath_prepend(str(scripts_dir))

    for mod in list(sys.modules.keys()):
        if mod in ("tts_client", "tts_preprocess", "preprocess"):
            del sys.modules[mod]

    tts_client = importlib.import_module("tts_client")

    # 1. The helper must be exported
    assert hasattr(tts_client, "preview_tts_text_with_metadata"), (
        "preview_tts_text_with_metadata missing from tts_client"
    )

    # 2. The helper must be callable
    assert callable(tts_client.preview_tts_text_with_metadata)

    # 3. The old bare 'preprocess' module must not be in sys.modules
    assert "preprocess" not in sys.modules, (
        "Old 'preprocess' module was imported — import isolation violated for preview"
    )

    # 4. tts_preprocess must have been loaded from the correct path
    assert "tts_preprocess" in sys.modules
    pp_mod = sys.modules["tts_preprocess"]
    assert "tts_service" in str(pp_mod.__file__), (
        f"tts_preprocess resolved to wrong path: {pp_mod.__file__}"
    )


class _UrlopenJsonResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_tts_preview_view_proxies_success(monkeypatch):
    factory = APIRequestFactory()
    user = User.objects.create_user(username=f"tts_preview_ok_{uuid.uuid4().hex[:8]}", password="pass")

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _UrlopenJsonResponse(
            {
                "original_text": "AI ve ChatGPT pipeline",
                "normalized_text": "AI ve ChatGPT pipeline",
                "spoken_text": "override text",
                "chunks": ["override text"],
                "chunk_pause_ms": [0],
                "tts_normalization_language": "tr",
                "tts_normalization_rules_applied": [],
                "normalization_enabled": True,
                "normalization_mode": "loose",
                "unknown_word_strategy": "keep",
                "applied_overrides": {},
                "warnings": [],
                "error": None,
                "fallback_used": False,
            }
        )

    monkeypatch.setattr(views, "urlopen", fake_urlopen)
    monkeypatch.setenv("TTS_SERVICE_URL", "http://tts-service-test:8001")

    request = factory.post(
        "/api/v1/tts/preview/",
        {
            "text": "AI ve ChatGPT pipeline",
            "normalization_enabled": True,
            "normalization_mode": "loose",
            "unknown_word_strategy": "keep",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    response = views.TTSPreviewView.as_view()(request)

    assert response.status_code == 200
    assert response.data["spoken_text"] == "override text"
    assert response.data["fallback_used"] is False
    assert captured["url"].endswith("/normalization/preview")
    assert captured["timeout"] == 5.0
    assert captured["body"]["text"] == "AI ve ChatGPT pipeline"


def test_tts_preview_view_fail_open_when_service_unavailable(monkeypatch):
    factory = APIRequestFactory()
    user = User.objects.create_user(username=f"tts_preview_fail_{uuid.uuid4().hex[:8]}", password="pass")

    def fake_urlopen(_req, timeout=0):
        raise URLError("connection refused")

    monkeypatch.setattr(views, "urlopen", fake_urlopen)
    monkeypatch.setenv("TTS_SERVICE_URL", "http://tts-service-test:8001")

    request = factory.post(
        "/api/v1/tts/preview/",
        {
            "text": "AI ve ChatGPT pipeline",
            "normalization_enabled": False,
            "normalization_mode": "strict",
            "unknown_word_strategy": "phonetic",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    response = views.TTSPreviewView.as_view()(request)

    assert response.status_code == 200
    assert response.data["original_text"] == "AI ve ChatGPT pipeline"
    assert response.data["normalized_text"] == "AI ve ChatGPT pipeline"
    assert response.data["spoken_text"] == "AI ve ChatGPT pipeline"
    assert response.data["fallback_used"] is True
    assert response.data["normalization_enabled"] is False
    assert response.data["normalization_mode"] == "strict"
    assert response.data["unknown_word_strategy"] == "phonetic"
    assert response.data["warnings"]


def test_tts_preview_view_fail_open_runs_local_d1_resolver(monkeypatch):
    factory = APIRequestFactory()
    user = User.objects.create_user(username=f"tts_preview_d1_{uuid.uuid4().hex[:8]}", password="pass")

    def fake_urlopen(_req, timeout=0):
        raise URLError("connection refused")

    monkeypatch.setattr(views, "urlopen", fake_urlopen)
    monkeypatch.setenv("TTS_SERVICE_URL", "http://tts-service-test:8001")

    request = factory.post(
        "/api/v1/tts/preview/",
        {
            "text": "ASP ve Pipeline HyperBeam açıklaması.",
            "language": "auto",
            "normalization_enabled": True,
            "normalization_mode": "loose",
            "unknown_word_strategy": "keep",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    response = views.TTSPreviewView.as_view()(request)

    assert response.status_code == 200
    assert response.data["fallback_used"] is True
    assert response.data["resolved_language"] == "tr"
    assert response.data["spoken_text"] == "ey es pi ve payp layn HyperBeam açıklaması."
    assert response.data["unknown_terms"] == ["HyperBeam"]
    rules = [
        (rule["rule"], rule["term"], rule["replacement"])
        for rule in response.data["tts_normalization_rules_applied"]
    ]
    assert ("acronym", "ASP", "ey es pi") in rules
    assert ("english_technical_fallback", "Pipeline", "payp layn") in rules


def test_tts_preview_view_does_not_import_tts_client(monkeypatch):
    factory = APIRequestFactory()
    user = User.objects.create_user(username=f"tts_preview_iso_{uuid.uuid4().hex[:8]}", password="pass")

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tts_client":
            raise AssertionError("TTSPreviewView must not import tts_client")
        return real_import(name, globals, locals, fromlist, level)

    def fake_urlopen(_req, timeout=0):
        return _UrlopenJsonResponse(
            {
                "original_text": "hello",
                "normalized_text": "hello",
                "spoken_text": "hello",
                "chunks": ["hello"],
                "chunk_pause_ms": [0],
                "tts_normalization_language": "en",
                "tts_normalization_rules_applied": [],
                "normalization_enabled": True,
                "normalization_mode": "loose",
                "unknown_word_strategy": "keep",
                "applied_overrides": {},
                "warnings": [],
                "error": None,
                "fallback_used": False,
            }
        )

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(views, "urlopen", fake_urlopen)
    monkeypatch.setenv("TTS_SERVICE_URL", "http://tts-service-test:8001")

    request = factory.post("/api/v1/tts/preview/", {"text": "hello"}, format="json")
    force_authenticate(request, user=user)
    response = views.TTSPreviewView.as_view()(request)

    assert response.status_code == 200
    assert response.data["spoken_text"] == "hello"
