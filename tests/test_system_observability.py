import io
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import django
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db.utils import OperationalError  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from core.models import Job, Project, RenderFollowUpIntent, UserProfile  # noqa: E402
from core import perf_metrics, system_observability  # noqa: E402
from core import storage_metrics_snapshot as storage_metrics_snapshot_module  # noqa: E402
from core.system_observability import build_system_observability_report  # noqa: E402
from core.storage_metrics_snapshot import (  # noqa: E402
    load_storage_metrics_snapshot,
    storage_metrics_snapshot_path,
    write_storage_metrics_snapshot,
)


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_default_storage_root(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)


def _make_project(username: str) -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title=f"Observability {username}", user=user, status="processing")


def _age_model(instance, *, hours: int = 4):
    old = timezone.now() - timedelta(hours=hours)
    type(instance).objects.filter(pk=instance.pk).update(created_at=old, updated_at=old)
    instance.refresh_from_db()
    return instance


def test_observability_metrics_generation_counts_render_intent_storage_and_recovery(tmp_path):
    project = _make_project("metrics")
    _age_model(Job.objects.create(project=project, job_type="video_export", status="pending"))
    Job.objects.create(project=project, job_type="video_export", status="running", celery_task_id="task-1")
    Job.objects.create(project=project, job_type="video_export", status="failed")
    _age_model(RenderFollowUpIntent.objects.create(project=project, status=RenderFollowUpIntent.STATUS_CLAIMED))
    (tmp_path / "tmp").mkdir()
    old_file = tmp_path / "tmp" / "old.tmp"
    old_file.write_bytes(b"old")
    old_epoch = (timezone.now() - timedelta(days=45)).timestamp()
    os.utime(old_file, (old_epoch, old_epoch))
    write_storage_metrics_snapshot(storage_root=tmp_path, older_than_days=30)

    report = build_system_observability_report(storage_root=tmp_path, retention_older_than_days=30, recovery_max_age_hours=2)

    assert report["mode"] == "read-only/report-only"
    assert report["render"]["metrics"]["active_render_count"] == 2
    assert report["render"]["metrics"]["pending_render_count"] == 1
    assert report["render"]["metrics"]["running_render_count"] == 1
    assert report["render"]["metrics"]["failed_render_count"] == 1
    assert report["render"]["metrics"]["oldest_active_render_age_seconds"] > 0
    assert report["follow_up_intents"]["metrics"]["claimed_intent_count"] == 1
    assert report["follow_up_intents"]["metrics"]["oldest_intent_age_seconds"] > 0
    assert report["storage"]["metrics"]["retention_candidate_count"] == 1
    assert report["storage"]["metrics"]["reclaimable_bytes_estimate"] >= len(b"old")
    assert report["storage_backend"]["metrics"]["effective_storage_backend"] == "filesystem"
    assert report["storage_backend"]["metrics"]["adapter_class"] == "FilesystemStorageAdapter"
    assert report["recovery"]["metrics"]["recovery_candidate_count"] >= 1
    assert report["recovery"]["metrics"]["stale_render_count"] >= 1
    assert report["recovery"]["metrics"]["stale_intent_count"] >= 1


def test_system_observability_report_json_output(tmp_path):
    stdout = io.StringIO()
    write_storage_metrics_snapshot(storage_root=tmp_path)

    call_command("system_observability_report", "--json", "--storage-root", str(tmp_path), stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["render"]["metrics"]["active_render_count"] == 0
    assert payload["storage"]["metrics"]["total_storage_size_bytes"] == 0
    assert payload["storage_backend"]["metrics"]["effective_storage_backend"] == "filesystem"
    assert payload["storage_backend"]["metrics"]["runtime_media_migration_implied"] is False
    assert payload["mode"] == "read-only/report-only"


def test_system_observability_report_pretty_output(tmp_path):
    stdout = io.StringIO()
    write_storage_metrics_snapshot(storage_root=tmp_path)

    call_command("system_observability_report", "--pretty", "--storage-root", str(tmp_path), stdout=stdout)

    output = stdout.getvalue()
    assert "System observability report" in output
    assert "Render" in output
    assert "Follow-up intents" in output
    assert "Storage" in output
    assert "Storage backend readiness" in output
    assert "runtime_media_migration_implied: False" in output
    assert "Recovery" in output
    assert "read-only/report-only" in output


def test_system_observability_storage_backend_reports_local_alias_as_filesystem(tmp_path):
    with override_settings(_RAW_STORAGE_BACKEND="local", STORAGE_BACKEND="filesystem"):
        report = build_system_observability_report(storage_root=tmp_path)

    metrics = report["storage_backend"]["metrics"]
    assert metrics["configured_storage_backend"] == "local"
    assert metrics["effective_storage_backend"] == "filesystem"
    assert metrics["adapter_class"] == "FilesystemStorageAdapter"
    assert metrics["legacy_local_alias_normalized"] is True
    assert metrics["filesystem_root_status"] == "ok"


def test_system_observability_storage_backend_reports_s3_metadata_without_network(tmp_path):
    with override_settings(
        _RAW_STORAGE_BACKEND="s3",
        STORAGE_BACKEND="s3",
        S3_ENDPOINT_URL="http://minio.local:9000",
        S3_BUCKET_NAME="visus",
        S3_ACCESS_KEY_ID="access",
        S3_SECRET_ACCESS_KEY="secret",
        S3_REGION_NAME="us-east-1",
        S3_KEY_PREFIX="readiness",
        S3_USE_SSL=False,
        S3_VERIFY_SSL=False,
    ):
        report = build_system_observability_report(storage_root=tmp_path)

    metrics = report["storage_backend"]["metrics"]
    assert report["storage_backend"]["available"] is True
    assert metrics["configured_storage_backend"] == "s3"
    assert metrics["effective_storage_backend"] == "s3"
    assert metrics["adapter_class"] == "S3StorageAdapter"
    assert metrics["s3_endpoint_url_configured"] is True
    assert metrics["s3_bucket_name_configured"] is True
    assert metrics["s3_access_key_id_configured"] is True
    assert metrics["s3_secret_access_key_configured"] is True
    assert metrics["s3_key_prefix_configured"] is True
    assert metrics["s3_network_probe_performed"] is False
    assert metrics["s3_listing_enabled"] is False
    assert metrics["s3_range_reads_enabled"] is False
    assert metrics["s3_signed_urls_enabled"] is False
    assert metrics["s3_public_urls_enabled"] is False


def test_system_observability_storage_backend_reports_missing_s3_config(tmp_path):
    with override_settings(
        _RAW_STORAGE_BACKEND="s3",
        STORAGE_BACKEND="s3",
        S3_ENDPOINT_URL=None,
        S3_BUCKET_NAME="",
        S3_ACCESS_KEY_ID="",
        S3_SECRET_ACCESS_KEY="",
        S3_REGION_NAME=None,
        S3_KEY_PREFIX="",
    ):
        report = build_system_observability_report(storage_root=tmp_path)

    assert report["storage_backend"]["available"] is False
    assert (
        "s3_backend_missing_required_config:S3_BUCKET_NAME,S3_ACCESS_KEY_ID,S3_SECRET_ACCESS_KEY"
        in report["storage_backend"]["warnings"]
    )


def test_system_observability_storage_backend_readiness_does_not_mutate_storage(tmp_path):
    existing = tmp_path / "existing.txt"
    existing.write_text("keep", encoding="utf-8")
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    build_system_observability_report(storage_root=tmp_path)

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before
    assert existing.read_text(encoding="utf-8") == "keep"


def test_observability_report_degrades_when_database_is_unavailable(tmp_path, monkeypatch):
    def raise_db_error():
        raise OperationalError("database unavailable")

    write_storage_metrics_snapshot(storage_root=tmp_path)
    monkeypatch.setattr(system_observability, "_render_metrics", raise_db_error)

    report = build_system_observability_report(storage_root=tmp_path)

    assert report["render"]["available"] is False
    assert report["storage"]["available"] is True
    assert any("render_database_unavailable:OperationalError" == warning for warning in report["warnings"])


def test_observability_report_degrades_when_optional_helper_is_unavailable(tmp_path, monkeypatch):
    def raise_import_error(*_args, **_kwargs):
        raise ImportError("optional helper unavailable")

    monkeypatch.setattr(system_observability, "load_storage_metrics_snapshot", raise_import_error)

    report = build_system_observability_report(storage_root=tmp_path)

    assert report["storage"]["available"] is False
    assert report["render"]["available"] is True
    assert any("storage_unavailable:ImportError:optional helper unavailable" == warning for warning in report["warnings"])


def test_prometheus_observability_metrics_output_contains_expected_names():
    text = perf_metrics.prometheus_metrics_text()

    expected_names = [
        "system_observability_render_active_count",
        "system_observability_render_pending_count",
        "system_observability_render_running_count",
        "system_observability_render_failed_count",
        "system_observability_render_oldest_active_age_seconds",
        "system_observability_followup_pending_count",
        "system_observability_followup_claimed_count",
        "system_observability_followup_dispatched_count",
        "system_observability_followup_oldest_age_seconds",
        "system_observability_storage_total_bytes",
        "system_observability_storage_snapshot_available",
        "system_observability_storage_snapshot_age_seconds",
        "system_observability_storage_snapshot_generated_timestamp",
        "system_observability_storage_retention_candidate_count",
        "system_observability_storage_orphan_candidate_count",
        "system_observability_storage_reclaimable_bytes_estimate",
        "system_observability_recovery_candidate_count",
        "system_observability_recovery_stale_render_count",
        "system_observability_recovery_stale_intent_count",
    ]
    for name in expected_names:
        assert name in text


def test_prometheus_observability_degrades_when_database_is_unavailable(monkeypatch):
    def raise_db_error():
        raise OperationalError("database unavailable")

    monkeypatch.setattr(system_observability, "_render_metrics", raise_db_error)

    text = perf_metrics.prometheus_metrics_text()

    assert "system_observability_render_available 0" in text
    assert "system_observability_render_active_count 0" in text


def test_prometheus_observability_degrades_when_storage_is_unavailable(monkeypatch):
    text = perf_metrics.prometheus_metrics_text()

    assert "system_observability_storage_available 0" in text
    assert "system_observability_storage_scan_skipped 1" in text
    assert "system_observability_storage_total_bytes 0" in text


def test_prometheus_observability_does_not_mutate_database(tmp_path):
    project = _make_project("prometheus-side-effect")
    Job.objects.create(project=project, job_type="video_export", status="pending")
    before = {
        "jobs": Job.objects.count(),
        "intents": RenderFollowUpIntent.objects.count(),
        "projects": Project.objects.count(),
    }

    perf_metrics.prometheus_metrics_text()

    after = {
        "jobs": Job.objects.count(),
        "intents": RenderFollowUpIntent.objects.count(),
        "projects": Project.objects.count(),
    }
    assert after == before


def test_prometheus_observability_avoids_duplicate_metric_registration():
    first = perf_metrics.prometheus_metrics_text()
    second = perf_metrics.prometheus_metrics_text()

    assert first.count("# HELP system_observability_render_active_count ") == 1
    assert second.count("# HELP system_observability_render_active_count ") == 1
    assert second.count("# TYPE system_observability_render_active_count gauge") == 1


def test_prometheus_observability_does_not_trigger_storage_scan(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("storage scan should not run during prometheus scrape")

    monkeypatch.setattr("core.storage_metrics_snapshot.build_storage_report", fail_if_called)

    text = perf_metrics.prometheus_metrics_text()

    assert "system_observability_storage_scan_skipped 1" in text


def test_storage_metrics_snapshot_command_generates_snapshot(tmp_path):
    old_file = tmp_path / "tmp" / "old.tmp"
    old_file.parent.mkdir()
    old_file.write_bytes(b"temp")
    old_epoch = (timezone.now() - timedelta(days=45)).timestamp()
    os.utime(old_file, (old_epoch, old_epoch))
    stdout = io.StringIO()

    call_command("storage_metrics_snapshot", "--json", "--storage-root", str(tmp_path), stdout=stdout)

    payload = json.loads(stdout.getvalue())
    snapshot_path = storage_metrics_snapshot_path(tmp_path)
    assert snapshot_path.exists()
    assert payload["snapshot_path"] == str(snapshot_path)
    assert payload["total_storage_bytes"] >= len(b"temp")
    assert payload["retention_candidate_count"] == 1
    written_files = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    assert written_files == {"observability/storage_metrics_snapshot.json", "tmp/old.tmp"}
    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    assert snapshot_text.endswith("\n")
    assert snapshot_text == json.dumps(json.loads(snapshot_text), ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def test_storage_metrics_snapshot_write_uses_shared_json_helper(tmp_path, monkeypatch):
    calls = []
    real_write = storage_metrics_snapshot_module.write_json_metadata_file

    def tracking_write(**kwargs):
        calls.append(kwargs)
        return real_write(**kwargs)

    monkeypatch.setattr(storage_metrics_snapshot_module, "write_json_metadata_file", tracking_write)

    snapshot = write_storage_metrics_snapshot(storage_root=tmp_path)

    snapshot_path = storage_metrics_snapshot_path(tmp_path)
    assert snapshot_path == tmp_path / "observability" / "storage_metrics_snapshot.json"
    assert snapshot_path.read_text(encoding="utf-8") == json.dumps(snapshot, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    assert len(calls) == 1
    assert calls[0]["storage_root"] == tmp_path
    assert calls[0]["relative_path"].as_posix() == "observability/storage_metrics_snapshot.json"
    assert calls[0]["payload"] == snapshot
    assert calls[0]["sort_keys"] is True
    assert calls[0]["trailing_newline"] is True


def test_storage_metrics_snapshot_loads_existing_snapshot(tmp_path):
    write_storage_metrics_snapshot(storage_root=tmp_path)

    snapshot = load_storage_metrics_snapshot(storage_root=tmp_path)

    assert snapshot.available is True
    assert snapshot.metrics["total_storage_bytes"] == 0
    assert snapshot.metrics["generated_at"]
    assert snapshot.metrics["generated_timestamp"] > 0
    assert snapshot.metrics["age_seconds"] >= 0


def test_storage_metrics_snapshot_missing_degrades_to_zero_values(tmp_path):
    report = build_system_observability_report(storage_root=tmp_path)

    assert report["storage"]["available"] is False
    assert report["storage"]["metrics"]["total_storage_size_bytes"] == 0
    assert "storage_metrics_snapshot_missing" in report["storage"]["warnings"]


def test_storage_metrics_snapshot_corrupted_degrades_to_zero_values(tmp_path):
    snapshot_path = storage_metrics_snapshot_path(tmp_path)
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("{not-json", encoding="utf-8")

    report = build_system_observability_report(storage_root=tmp_path)

    assert report["storage"]["available"] is False
    assert report["storage"]["metrics"]["total_storage_size_bytes"] == 0
    assert any(warning.startswith("storage_metrics_snapshot_unavailable:JSONDecodeError") for warning in report["storage"]["warnings"])


def test_storage_metrics_snapshot_invalid_values_degrade_to_zero_values(tmp_path):
    snapshot_path = storage_metrics_snapshot_path(tmp_path)
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "total_storage_bytes": "not-a-number",
                "retention_candidate_count": 0,
                "orphan_candidate_count": 0,
                "reclaimable_bytes_estimate": 0,
                "generated_at": timezone.now().isoformat(),
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_storage_metrics_snapshot(storage_root=tmp_path)

    assert snapshot.available is False
    assert snapshot.metrics["total_storage_bytes"] == 0
    assert snapshot.warnings == ["storage_metrics_snapshot_invalid:nonnumeric_total_storage_bytes"]


def test_prometheus_observability_reads_snapshot_values(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)
    snapshot_path = storage_metrics_snapshot_path(tmp_path)
    snapshot_path.parent.mkdir(parents=True)
    generated_at = timezone.now() - timedelta(hours=2)
    snapshot_path.write_text(
        json.dumps(
            {
                "total_storage_bytes": 123,
                "retention_candidate_count": 2,
                "orphan_candidate_count": 3,
                "reclaimable_bytes_estimate": 99,
                "generated_at": generated_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    text = perf_metrics.prometheus_metrics_text()

    assert "system_observability_storage_available 1" in text
    assert "system_observability_storage_snapshot_available 1" in text
    assert "system_observability_storage_total_bytes 123" in text
    assert "system_observability_storage_retention_candidate_count 2" in text
    assert "system_observability_storage_orphan_candidate_count 3" in text
    assert "system_observability_storage_reclaimable_bytes_estimate 99" in text
    assert "system_observability_storage_snapshot_generated_timestamp " in text
    age_line = next(line for line in text.splitlines() if line.startswith("system_observability_storage_snapshot_age_seconds "))
    age_seconds = float(age_line.rsplit(" ", 1)[1])
    assert 7100 <= age_seconds <= 7300


def test_prometheus_observability_missing_snapshot_zeroes_values(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)

    text = perf_metrics.prometheus_metrics_text()

    assert "system_observability_storage_available 0" in text
    assert "system_observability_storage_snapshot_available 0" in text
    assert "system_observability_storage_total_bytes 0" in text
    assert "system_observability_storage_snapshot_age_seconds 0" in text
    assert "system_observability_storage_snapshot_generated_timestamp 0" in text


def test_prometheus_observability_corrupt_snapshot_zeroes_freshness(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)
    snapshot_path = storage_metrics_snapshot_path(tmp_path)
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("{not-json", encoding="utf-8")

    text = perf_metrics.prometheus_metrics_text()

    assert "system_observability_storage_available 0" in text
    assert "system_observability_storage_snapshot_available 0" in text
    assert "system_observability_storage_snapshot_age_seconds 0" in text
    assert "system_observability_storage_snapshot_generated_timestamp 0" in text


def test_prometheus_observability_does_not_mutate_outside_snapshot(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    perf_metrics.prometheus_metrics_text()

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before
