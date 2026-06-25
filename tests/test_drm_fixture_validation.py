import io
import json
import os
import sys
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

from core.drm_fixture_validation import build_drm_fixture_validation_report  # noqa: E402
from core.models import Project, UserProfile  # noqa: E402


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_storage_and_drm_settings(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)
    settings.DRM_ENABLED = True
    settings.DRM_PROVIDER_NAME = "external"
    settings.DRM_PREFERRED_SYSTEM = "widevine"
    settings.DRM_KEY_SYSTEM = ""
    settings.DRM_LICENSE_URL = ""
    settings.DRM_CERTIFICATE_URL = ""
    settings.DRM_WIDEVINE_ENABLED = True
    settings.DRM_WIDEVINE_KEY_SYSTEM = "com.widevine.alpha"
    settings.DRM_WIDEVINE_LICENSE_URL = "https://license.example.test/widevine"
    settings.DRM_WIDEVINE_CERTIFICATE_URL = ""
    settings.DRM_WIDEVINE_CONTENT_TYPE = "application/vnd.apple.mpegurl"
    settings.DRM_PLAYREADY_ENABLED = False
    settings.DRM_PLAYREADY_KEY_SYSTEM = "com.microsoft.playready"
    settings.DRM_PLAYREADY_LICENSE_URL = ""
    settings.DRM_PLAYREADY_CERTIFICATE_URL = ""
    settings.DRM_FAIRPLAY_ENABLED = False
    settings.DRM_FAIRPLAY_KEY_SYSTEM = "com.apple.fps.1_0"
    settings.DRM_FAIRPLAY_LICENSE_URL = ""
    settings.DRM_FAIRPLAY_CERTIFICATE_URL = ""


def _project(username="teacher") -> Project:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return Project.objects.create(title=f"Project {username}", user=user)


def _write_sidecar(root: Path, project: Project, payload: dict) -> None:
    path = root / str(project.id) / "playback_assets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_sidecar(project: Project) -> dict:
    return {
        "asset_id": f"lesson-{project.id}",
        "content_id": f"project-{project.id}",
        "protection_mode": "drm_protected",
        "hls": {
            "enabled": True,
            "manifest_rel_path": f"{project.id}/fixtures/widevine/index.m3u8",
            "encrypted": True,
            "packaging_status": "external_fixture",
            "drm_scheme": "widevine-cenc-cmaf",
        },
    }


def _write_manifest(root: Path, project: Project) -> None:
    path = root / str(project.id) / "fixtures" / "widevine" / "index.m3u8"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#EXTM3U\n", encoding="utf-8")


def test_missing_sidecar_reports_blocker(tmp_path):
    project = _project()

    report = build_drm_fixture_validation_report(project_id=project.id, storage_root=tmp_path)

    assert "missing_playback_sidecar" in report["blockers"]
    assert report["sidecar"]["exists"] is False
    assert report["summary"]["ready_for_staging_fixture_attempt"] is False


def test_non_drm_protection_mode_reports_blocker(tmp_path):
    project = _project()
    payload = _valid_sidecar(project)
    payload["protection_mode"] = "secure_stream"
    _write_sidecar(tmp_path, project, payload)
    _write_manifest(tmp_path, project)

    report = build_drm_fixture_validation_report(project_id=project.id, storage_root=tmp_path)

    assert "protection_mode_not_drm_protected" in report["blockers"]
    assert report["sidecar"]["protection_mode"] == "secure_stream"


def test_missing_license_url_reports_blocker(settings, tmp_path):
    project = _project()
    _write_sidecar(tmp_path, project, _valid_sidecar(project))
    _write_manifest(tmp_path, project)
    settings.DRM_WIDEVINE_LICENSE_URL = ""

    report = build_drm_fixture_validation_report(project_id=project.id, storage_root=tmp_path)

    assert "missing_license_url" in report["blockers"]
    assert report["drm"]["license_url"] == ""


def test_relative_license_url_reports_blocker(settings, tmp_path):
    project = _project()
    _write_sidecar(tmp_path, project, _valid_sidecar(project))
    _write_manifest(tmp_path, project)
    settings.DRM_WIDEVINE_LICENSE_URL = "/widevine/license"

    report = build_drm_fixture_validation_report(project_id=project.id, storage_root=tmp_path)

    assert "license_url_not_absolute" in report["blockers"]
    assert report["drm"]["license_url_absolute"] is False


def test_missing_hls_manifest_reports_blocker(tmp_path):
    project = _project()
    _write_sidecar(tmp_path, project, _valid_sidecar(project))

    report = build_drm_fixture_validation_report(project_id=project.id, storage_root=tmp_path)

    assert "missing_hls_manifest_file" in report["blockers"]
    assert report["hls"]["manifest_exists"] is False


def test_valid_fixture_report_shape(tmp_path):
    project = _project()
    _write_sidecar(tmp_path, project, _valid_sidecar(project))
    _write_manifest(tmp_path, project)

    report = build_drm_fixture_validation_report(project_id=project.id, storage_root=tmp_path)

    assert report["mode"] == "staging-read-only/report-only"
    assert report["summary"]["ready_for_staging_fixture_attempt"] is True
    assert report["blockers"] == []
    assert report["mp4_fallback_expected"] is False
    assert report["hls"]["manifest_exists"] is True
    assert report["drm"]["preferred_system"] == "widevine"
    assert report["drm"]["key_system"] == "com.widevine.alpha"
    assert report["drm"]["license_url"] == "https://license.example.test/widevine"
    assert report["drm"]["systems"]["widevine"]["ready"] is True


def test_json_management_command_output_is_deterministic(tmp_path):
    project = _project()
    _write_sidecar(tmp_path, project, _valid_sidecar(project))
    _write_manifest(tmp_path, project)

    first = io.StringIO()
    second = io.StringIO()
    call_command("drm_fixture_validation", "--project-id", str(project.id), "--storage-root", str(tmp_path), "--json", stdout=first)
    call_command("drm_fixture_validation", "--project-id", str(project.id), "--storage-root", str(tmp_path), "--json", stdout=second)

    assert first.getvalue() == second.getvalue()
    payload = json.loads(first.getvalue())
    assert payload["summary"]["ready_for_staging_fixture_attempt"] is True
