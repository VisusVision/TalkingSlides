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

from django.core.management import call_command  # noqa: E402

from core.models import Job, Project  # noqa: E402
from core.storage_retention import build_storage_report  # noqa: E402


pytestmark = pytest.mark.django_db


def _write_file(path: Path, content: bytes = b"x", *, age_days: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if age_days is not None:
        old_epoch = time.time() - (age_days * 24 * 60 * 60)
        os.utime(path, (old_epoch, old_epoch))
    return path


def test_storage_retention_dry_run_reports_old_safe_area_files(tmp_path):
    old_frame = _write_file(tmp_path / "moderation" / "video_frames" / "1" / "old.png", b"old", age_days=45)
    _write_file(tmp_path / "moderation" / "video_frames" / "1" / "new.png", b"new", age_days=1)

    report = build_storage_report(storage_root=tmp_path, older_than_days=30)

    candidate_paths = {item["rel_path"] for item in report["retention_candidates"]}
    assert str(old_frame.relative_to(tmp_path)).replace("\\", "/") in candidate_paths
    assert "moderation/video_frames/1/new.png" not in candidate_paths
    assert report["retention_candidates"][0]["category"] == "moderation_video_frames"


def test_storage_orphan_detection_reports_missing_project_assets(tmp_path):
    project = Project.objects.create(title="Existing project")
    _write_file(tmp_path / str(project.id) / "current.mp4", b"current")
    _write_file(tmp_path / "9999" / "orphan.mp4", b"orphan")
    _write_file(tmp_path / "uploads" / "9999" / "source.pptx", b"source")

    report = build_storage_report(storage_root=tmp_path, older_than_days=30)

    orphans = {(item["category"], item["rel_path"]) for item in report["orphan_candidates"]}
    assert ("orphan_project_render_dir", "9999") in orphans
    assert ("orphan_upload_dir", "uploads/9999") in orphans
    assert ("orphan_project_render_dir", str(project.id)) not in orphans


def test_storage_capacity_report_groups_uploads_renders_subtitles_and_references(tmp_path):
    project = Project.objects.create(title="Capacity project")
    render_path = _write_file(tmp_path / str(project.id) / f"{project.id}.mp4", b"video")
    _write_file(tmp_path / str(project.id) / "subtitles" / "en.vtt", b"WEBVTT")
    _write_file(tmp_path / "uploads" / str(project.id) / "lesson.pptx", b"upload")
    _write_file(tmp_path / "avatars" / "123" / "preview.mp4", b"avatar")
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=str(render_path.relative_to(tmp_path)).replace("\\", "/"),
    )

    report = build_storage_report(storage_root=tmp_path, older_than_days=30)
    categories = report["capacity"]["categories"]

    assert categories["uploads"]["bytes"] == len(b"upload")
    assert categories["render_outputs"]["bytes"] >= len(b"video") + len(b"WEBVTT")
    assert categories["subtitles"]["bytes"] == len(b"WEBVTT")
    assert categories["avatars"]["bytes"] == len(b"avatar")
    assert report["capacity"]["referenced_existing_bytes"] == len(b"video")


def test_storage_retention_management_command_outputs_report(tmp_path, capsys):
    _write_file(tmp_path / "tmp" / "old.tmp", b"temp", age_days=45)

    call_command("storage_retention_check", storage_root=str(tmp_path), older_than_days=30, dry_run=True)

    captured = capsys.readouterr()
    assert "Storage retention check" in captured.out
    assert "mode: dry-run/report-only" in captured.out
    assert "Retention candidates: 1 files" in captured.out
    assert "No files were deleted" in captured.out


def test_storage_report_degrades_when_database_is_unavailable(tmp_path, monkeypatch):
    _write_file(tmp_path / "tmp" / "old.tmp", b"temp", age_days=45)

    def raise_db_error(*_args, **_kwargs):
        from django.db.utils import OperationalError

        raise OperationalError("schema missing")

    monkeypatch.setattr("core.storage_retention._orphan_candidates", raise_db_error)

    report = build_storage_report(storage_root=tmp_path, older_than_days=30)

    assert report["db_available"] is False
    assert report["orphan_candidates"] == []
    assert report["retention_candidates"]
    assert report["warnings"] == ["database_unavailable:OperationalError"]
