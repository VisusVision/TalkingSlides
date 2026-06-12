import io
import json
import os
import sys
import time
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

from core.models import Job, Project, UserProfile  # noqa: E402
from core.storage_unreferenced_candidates import build_storage_unreferenced_candidates_report  # noqa: E402


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_storage_root(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)


def _write(root: Path, rel_path: str, payload: bytes = b"x", *, age_days: int | None = None) -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if age_days is not None:
        old_epoch = time.time() - (age_days * 24 * 60 * 60)
        os.utime(path, (old_epoch, old_epoch))
    return path


def _project(username="teacher") -> tuple[User, Project]:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    project = Project.objects.create(title=f"Project {username}", user=user)
    return user, project


def _candidate_paths(report):
    return {entry["path"]: entry for entry in report["candidates"]}


def test_empty_storage_root_reports_zero_candidates(tmp_path):
    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert report["mode"] == "read-only/report-only"
    assert report["summary"]["total_files_scanned"] == 0
    assert report["summary"]["total_candidates"] == 0
    assert report["candidates"] == []


def test_referenced_file_is_not_candidate(tmp_path):
    _user, project = _project()
    rel_path = f"{project.id}/{project.id}.mp4"
    _write(tmp_path, rel_path, b"video")
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=rel_path)

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert rel_path not in _candidate_paths(report)
    assert report["summary"]["total_files_scanned"] == 1
    assert report["summary"]["total_referenced_paths"] >= 1


def test_unreferenced_file_is_candidate(tmp_path):
    _write(tmp_path, "tmp/orphan.bin", b"orphan")

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)
    candidate = _candidate_paths(report)["tmp/orphan.bin"]

    assert candidate["reason"] == "not_found_in_reference_inventory"
    assert candidate["risk_level"] == "review_required"
    assert candidate["delete_eligible"] is False


def test_candidate_byte_totals(tmp_path):
    _write(tmp_path, "tmp/a.bin", b"aaa")
    _write(tmp_path, "tmp/b.bin", b"bbbbb")

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert report["summary"]["total_candidates"] == 2
    assert report["summary"]["total_candidate_bytes"] == 8


def test_older_than_days_filters_newer_candidates(tmp_path):
    _write(tmp_path, "tmp/old.bin", b"old", age_days=45)
    _write(tmp_path, "tmp/new.bin", b"new")

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path, older_than_days=30)

    assert set(_candidate_paths(report)) == {"tmp/old.bin"}
    assert report["summary"]["skipped_paths"] == 1


def test_symlink_not_followed(tmp_path):
    target = _write(tmp_path, "outside-target.bin", b"target")
    link = tmp_path / "tmp" / "linked.bin"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert "tmp/linked.bin" not in _candidate_paths(report)
    assert report["summary"]["skipped_paths"] >= 1


def test_deterministic_ordering(tmp_path):
    _write(tmp_path, "z/file.bin", b"z")
    _write(tmp_path, "a/file.bin", b"a")

    first = build_storage_unreferenced_candidates_report(storage_root=tmp_path)
    second = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert first["candidates"] == second["candidates"]
    assert [entry["path"] for entry in first["candidates"]] == ["a/file.bin", "z/file.bin"]


def test_missing_db_inventory_unavailable_behavior(tmp_path, monkeypatch):
    _write(tmp_path, "tmp/orphan.bin", b"orphan")

    def unavailable(**_kwargs):
        return {
            "db_available": False,
            "warnings": ["database_unavailable:OperationalError"],
            "references": [],
        }

    monkeypatch.setattr("core.storage_unreferenced_candidates.build_storage_reference_inventory", unavailable)

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert report["db_available"] is False
    assert report["warnings"] == ["database_unavailable:OperationalError"]
    assert report["summary"]["total_candidates"] == 1


def test_json_management_command_output_shape(tmp_path):
    _write(tmp_path, "tmp/orphan.bin", b"orphan")
    stdout = io.StringIO()

    call_command("storage_unreferenced_candidates", "--json", "--storage-root", str(tmp_path), stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "read-only/report-only"
    assert "summary" in payload
    assert "candidates" in payload
    assert payload["candidates"][0]["delete_eligible"] is False


def test_delete_eligible_is_always_false(tmp_path):
    _write(tmp_path, "tmp/orphan.bin", b"orphan")

    report = build_storage_unreferenced_candidates_report(storage_root=tmp_path)

    assert report["candidates"]
    assert all(candidate["delete_eligible"] is False for candidate in report["candidates"])
