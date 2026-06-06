import importlib

import pytest


def test_celery_reliability_defaults_are_conservative():
    from config import settings

    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] == 12 * 60 * 60
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT is None
    assert settings.CELERY_TASK_TIME_LIMIT is None
    assert settings.CELERY_RESULT_EXPIRES == 24 * 60 * 60


def test_worker_celery_app_loads_reliability_config():
    from worker.celery_app import app

    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.broker_transport_options["visibility_timeout"] == 12 * 60 * 60
    assert app.conf.task_soft_time_limit is None
    assert app.conf.task_time_limit is None
    assert app.conf.result_expires == 24 * 60 * 60


def test_celery_reliability_env_overrides(monkeypatch):
    from config import settings

    monkeypatch.setenv("CELERY_TASK_ACKS_LATE", "false")
    monkeypatch.setenv("CELERY_TASK_REJECT_ON_WORKER_LOST", "false")
    monkeypatch.setenv("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("CELERY_TASK_SOFT_TIME_LIMIT", "600")
    monkeypatch.setenv("CELERY_TASK_TIME_LIMIT", "720")
    monkeypatch.setenv("CELERY_RESULT_EXPIRES", "3600")

    reloaded = importlib.reload(settings)
    try:
        assert reloaded.CELERY_TASK_ACKS_LATE is False
        assert reloaded.CELERY_TASK_REJECT_ON_WORKER_LOST is False
        assert reloaded.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] == 900
        assert reloaded.CELERY_TASK_SOFT_TIME_LIMIT == 600
        assert reloaded.CELERY_TASK_TIME_LIMIT == 720
        assert reloaded.CELERY_RESULT_EXPIRES == 3600
    finally:
        monkeypatch.delenv("CELERY_TASK_ACKS_LATE", raising=False)
        monkeypatch.delenv("CELERY_TASK_REJECT_ON_WORKER_LOST", raising=False)
        monkeypatch.delenv("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("CELERY_TASK_SOFT_TIME_LIMIT", raising=False)
        monkeypatch.delenv("CELERY_TASK_TIME_LIMIT", raising=False)
        monkeypatch.delenv("CELERY_RESULT_EXPIRES", raising=False)
        importlib.reload(settings)


def test_worker_metric_foundation_exports_prometheus_text():
    from core import perf_metrics

    perf_metrics.increment_worker_failures()
    perf_metrics.increment_worker_retries()
    perf_metrics.observe_worker_task_duration_seconds(2.5)

    text = perf_metrics.prometheus_metrics_text()

    assert "worker_task_failures_total 1" in text
    assert "worker_task_retries_total 1" in text
    assert "worker_task_duration_seconds_count 1" in text
    assert 'worker_task_duration_seconds{quantile="0.95"} 2.5' in text


@pytest.mark.django_db
def test_prometheus_metrics_endpoint_exists(client, settings):
    settings.PROMETHEUS_METRICS_TOKEN = ""

    response = client.get("/api/v1/system/metrics/prometheus/")

    assert response.status_code == 200
    assert b"worker_task_failures_total" in response.content


@pytest.mark.django_db
def test_prometheus_metrics_endpoint_token_guard(client, settings):
    settings.PROMETHEUS_METRICS_TOKEN = "metrics-secret"

    denied = client.get("/api/v1/system/metrics/prometheus/")
    allowed = client.get("/api/v1/system/metrics/prometheus/", HTTP_X_METRICS_TOKEN="metrics-secret")

    assert denied.status_code == 401
    assert allowed.status_code == 200
