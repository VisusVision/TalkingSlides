import importlib.util
import sys
import uuid
from pathlib import Path


SETTINGS_PATH = Path(__file__).resolve().parents[1] / "services" / "api" / "config" / "settings.py"
PLAIN_STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
COMPRESSED_STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


def _load_settings(monkeypatch, *, debug: str, disable_static_compression: str | None = None):
    monkeypatch.setenv("DEBUG", debug)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("MEDIA_TOKEN_SECRET", "test-media-token-secret-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("ALLOWED_HOSTS", "localhost,127.0.0.1")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("STORAGE_ROOT", str((SETTINGS_PATH.parents[3] / "storage_local").resolve()))
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    if disable_static_compression is None:
        monkeypatch.delenv("DJANGO_DISABLE_STATIC_COMPRESSION", raising=False)
    else:
        monkeypatch.setenv("DJANGO_DISABLE_STATIC_COMPRESSION", disable_static_compression)

    module_name = f"_settings_under_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_staticfiles_storage_is_plain_when_compression_disabled(monkeypatch):
    settings = _load_settings(monkeypatch, debug="False", disable_static_compression="true")

    assert settings.STORAGES["staticfiles"]["BACKEND"] == PLAIN_STATICFILES_STORAGE


def test_staticfiles_storage_stays_compressed_for_production_default(monkeypatch):
    settings = _load_settings(monkeypatch, debug="False")

    assert settings.STORAGES["staticfiles"]["BACKEND"] == COMPRESSED_STATICFILES_STORAGE
