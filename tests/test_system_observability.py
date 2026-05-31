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
from django.utils import timezone  # noqa: E402

from core.models import Job, Project, RenderFollowUpIntent, UserProfile  # noqa: E402
from core import system_observability  # noqa: E402
from core.system_observability import build_system_observability_report  # noqa: E402


pytestmark = pytest.mark.django_db


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
    assert report["recovery"]["metrics"]["recovery_candidate_count"] >= 1
    assert report["recovery"]["metrics"]["stale_render_count"] >= 1
    assert report["recovery"]["metrics"]["stale_intent_count"] >= 1


def test_system_observability_report_json_output(tmp_path):
    stdout = io.StringIO()

    call_command("system_observability_report", "--json", "--storage-root", str(tmp_path), stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["render"]["metrics"]["active_render_count"] == 0
    assert payload["storage"]["metrics"]["total_storage_size_bytes"] == 0
    assert payload["mode"] == "read-only/report-only"


def test_system_observability_report_pretty_output(tmp_path):
    stdout = io.StringIO()

    call_command("system_observability_report", "--pretty", "--storage-root", str(tmp_path), stdout=stdout)

    output = stdout.getvalue()
    assert "System observability report" in output
    assert "Render" in output
    assert "Follow-up intents" in output
    assert "Storage" in output
    assert "Recovery" in output
    assert "read-only/report-only" in output


def test_observability_report_degrades_when_database_is_unavailable(tmp_path, monkeypatch):
    def raise_db_error():
        raise OperationalError("database unavailable")

    monkeypatch.setattr(system_observability, "_render_metrics", raise_db_error)

    report = build_system_observability_report(storage_root=tmp_path)

    assert report["render"]["available"] is False
    assert report["storage"]["available"] is True
    assert any("render_database_unavailable:OperationalError" == warning for warning in report["warnings"])


def test_observability_report_degrades_when_optional_helper_is_unavailable(tmp_path, monkeypatch):
    def raise_import_error(*_args, **_kwargs):
        raise ImportError("optional helper unavailable")

    monkeypatch.setattr(system_observability, "build_storage_report", raise_import_error)

    report = build_system_observability_report(storage_root=tmp_path)

    assert report["storage"]["available"] is False
    assert report["render"]["available"] is True
    assert any("storage_unavailable:ImportError:optional helper unavailable" == warning for warning in report["warnings"])
