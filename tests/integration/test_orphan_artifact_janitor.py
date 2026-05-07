# pyright: reportMissingImports=false

import os
import sys
import time
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
SERVICES_ROOT = REPO_ROOT / "services"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402

from core.models import Job, Project, UserProfile  # noqa: E402
from worker import tasks  # noqa: E402


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def _touch_old(path: Path, *, age_hours: int = 8):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    past = time.time() - (age_hours * 3600)
    os.utime(path, (past, past))


@pytest.mark.django_db
def test_orphan_artifact_janitor_skips_active_projects_and_cleans_inactive(tmp_path, monkeypatch):
    teacher = _make_teacher("janitor_teacher")
    active_project = Project.objects.create(title="Active project", user=teacher)
    inactive_project = Project.objects.create(title="Inactive project", user=teacher)

    Job.objects.create(project=active_project, job_type="video_export", status="running")
    Job.objects.create(project=inactive_project, job_type="video_export", status="failed")

    storage_root = tmp_path / "storage"
    active_root = storage_root / str(active_project.id)
    inactive_root = storage_root / str(inactive_project.id)

    _touch_old(active_root / "parts" / "part_001.mp4")
    _touch_old(inactive_root / "parts" / "part_001.mp4")
    _touch_old(inactive_root / "audio" / "slide_001.mp3")
    _touch_old(inactive_root / "parts" / "part_001.overlay.png")

    monkeypatch.setattr(tasks, "STORAGE_ROOT", str(storage_root))
    result = tasks.cleanup_orphan_render_artifacts.apply(kwargs={"min_age_hours": 1}).result

    assert result["status"] == "done"
    assert int(result["removed_files"]) >= 3
    assert (active_root / "parts" / "part_001.mp4").exists()
    assert not (inactive_root / "parts" / "part_001.mp4").exists()
    assert not (inactive_root / "audio" / "slide_001.mp3").exists()
    assert not (inactive_root / "parts" / "part_001.overlay.png").exists()
