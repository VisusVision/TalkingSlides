import pytest
from django.core.exceptions import ImproperlyConfigured

from config import settings as project_settings


def _valid_production_env(**overrides):
    env = {
        "SECRET_KEY": "prod-secret-key-with-enough-entropy",
        "POSTGRES_HOST": "postgres.example.internal",
        "MEDIA_TOKEN_SECRET": "prod-media-token-secret-with-enough-entropy",
        "ALLOWED_HOSTS": "api.example.com",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
    }
    env.update(overrides)
    return env


def _validate(env):
    project_settings.validate_production_settings(env=env, debug=False)


def test_production_settings_accept_explicit_safe_env():
    _validate(_valid_production_env())


def test_production_settings_reject_default_secret_key():
    env = _valid_production_env(SECRET_KEY=project_settings.DEV_SECRET_KEY)

    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        _validate(env)


def test_production_settings_reject_missing_postgres_host():
    env = _valid_production_env(POSTGRES_HOST="")

    with pytest.raises(ImproperlyConfigured, match="POSTGRES_HOST"):
        _validate(env)


def test_production_settings_reject_default_media_token_secret():
    env = _valid_production_env(MEDIA_TOKEN_SECRET=project_settings.DEV_MEDIA_TOKEN_SECRET)

    with pytest.raises(ImproperlyConfigured, match="MEDIA_TOKEN_SECRET"):
        _validate(env)


def test_production_settings_reject_cors_allow_all():
    env = _valid_production_env(CORS_ALLOW_ALL_ORIGINS="true")

    with pytest.raises(ImproperlyConfigured, match="CORS_ALLOW_ALL_ORIGINS"):
        _validate(env)


def test_production_settings_require_explicit_cors_origins():
    env = _valid_production_env()
    env.pop("CORS_ALLOWED_ORIGINS")

    with pytest.raises(ImproperlyConfigured, match="CORS_ALLOWED_ORIGINS"):
        _validate(env)


def test_production_settings_reject_wildcard_allowed_hosts_by_default():
    env = _valid_production_env(ALLOWED_HOSTS="*")

    with pytest.raises(ImproperlyConfigured, match="ALLOWED_HOSTS='\\*'"):
        _validate(env)


def test_production_settings_allow_wildcard_hosts_only_when_explicit():
    env = _valid_production_env(ALLOWED_HOSTS="*", ALLOW_WILDCARD_HOSTS="true")

    _validate(env)


def test_storage_settings_accept_readable_writable_absolute_root(tmp_path):
    project_settings.validate_storage_settings(
        env={"STORAGE_ROOT": str(tmp_path)},
        debug=False,
        storage_root=str(tmp_path),
    )


def test_storage_settings_reject_missing_explicit_root():
    with pytest.raises(ImproperlyConfigured, match="STORAGE_ROOT"):
        project_settings.validate_storage_settings(env={}, debug=False, storage_root=None)


def test_storage_settings_reject_relative_root():
    with pytest.raises(ImproperlyConfigured, match="absolute"):
        project_settings.validate_storage_settings(
            env={"STORAGE_ROOT": "storage_local"},
            debug=False,
            storage_root="storage_local",
        )


def test_storage_settings_reject_nonexistent_root(tmp_path):
    missing_root = tmp_path / "missing"

    with pytest.raises(ImproperlyConfigured, match="must exist"):
        project_settings.validate_storage_settings(
            env={"STORAGE_ROOT": str(missing_root)},
            debug=False,
            storage_root=str(missing_root),
        )
