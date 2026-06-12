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

from core.models import AvatarRenderJob, Job, Project, TranslatedSubtitleTrack, UserProfile, VoiceProfile  # noqa: E402
from core.storage_reference_inventory import build_storage_reference_inventory  # noqa: E402


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolate_storage_root(settings, tmp_path):
    settings.STORAGE_ROOT = str(tmp_path)


def _write(root: Path, rel_path: str, payload: bytes = b"x") -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _project(username="teacher") -> tuple[User, Project]:
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    project = Project.objects.create(title=f"Project {username}", user=user)
    return user, project


def _paths(report):
    return {entry["path"]: entry for entry in report["references"]}


def test_empty_inventory_reports_zero_references(tmp_path):
    report = build_storage_reference_inventory(storage_root=tmp_path)

    assert report["mode"] == "read-only/report-only"
    assert report["summary"]["total_references"] == 0
    assert report["references"] == []


def test_project_upload_source_reference_from_project_namespace(tmp_path):
    _user, project = _project()
    _write(tmp_path, f"uploads/{project.id}/source.pptx", b"source")

    report = build_storage_reference_inventory(storage_root=tmp_path)
    entry = _paths(report)[f"uploads/{project.id}/source.pptx"]

    assert entry["category"] == "uploads"
    assert entry["owner_model"] == "Project"
    assert entry["owner_id"] == str(project.id)
    assert entry["field"] == "uploads_directory"
    assert entry["exists"] is True
    assert entry["size_bytes"] == len(b"source")
    assert entry["criticality"] == "critical"


def test_final_render_mp4_reference_from_job(tmp_path):
    _user, project = _project()
    _write(tmp_path, f"{project.id}/{project.id}.mp4", b"video")
    job = Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/{project.id}.mp4")

    report = build_storage_reference_inventory(storage_root=tmp_path)
    entry = next(item for item in report["references"] if item["owner_model"] == "Job" and item["owner_id"] == str(job.id))

    assert entry["category"] == "render_outputs"
    assert entry["field"] == "result_url"
    assert entry["exists"] is True
    assert entry["criticality"] == "critical"


def test_playback_assets_sidecar_and_referenced_paths(tmp_path):
    _user, project = _project()
    sidecar = {
        "mp4_rel_path": f"{project.id}/{project.id}.mp4",
        "srt_rel_path": f"{project.id}/{project.id}.srt",
        "vtt_rel_path": f"{project.id}/{project.id}.vtt",
        "tts_audio": [f"{project.id}/audio/slide_001.mp3"],
        "final_segments": [{"part_rel_path": f"{project.id}/parts/part_001.mp4"}],
        "hls": {
            "enabled": True,
            "manifest_rel_path": f"{project.id}/drm/hls/index.m3u8",
            "segment_glob": f"{project.id}/drm/hls/seg_*.ts",
            "encrypted": True,
        },
    }
    _write(tmp_path, f"{project.id}/playback_assets.json", json.dumps(sidecar).encode("utf-8"))
    _write(tmp_path, f"{project.id}/{project.id}.mp4", b"video")
    _write(tmp_path, f"{project.id}/drm/hls/index.m3u8", b"#EXTM3U")
    _write(tmp_path, f"{project.id}/drm/hls/seg_00001.ts", b"seg")
    _write(tmp_path, f"{project.id}/drm/hls/enc.key", b"key")

    report = build_storage_reference_inventory(storage_root=tmp_path)
    paths = _paths(report)

    assert paths[f"{project.id}/playback_assets.json"]["category"] == "playback_sidecars"
    assert paths[f"{project.id}/{project.id}.mp4"]["category"] == "render_outputs"
    assert paths[f"{project.id}/{project.id}.srt"]["category"] == "subtitles"
    assert paths[f"{project.id}/audio/slide_001.mp3"]["category"] == "tts_voice_audio"
    assert paths[f"{project.id}/parts/part_001.mp4"]["category"] == "render_outputs"
    assert paths[f"{project.id}/drm/hls/index.m3u8"]["category"] == "hls_assets"
    assert paths[f"{project.id}/drm/hls/seg_*.ts"]["exists"] is True
    assert paths[f"{project.id}/drm/hls/enc.key"]["exists"] is True


def test_subtitle_srt_vtt_references_from_track(tmp_path):
    _user, project = _project()
    _write(tmp_path, f"{project.id}/subtitles/tr.srt", b"srt")
    track = TranslatedSubtitleTrack.objects.create(
        project=project,
        language_code="tr",
        status="ready",
        srt_path=f"{project.id}/subtitles/tr.srt",
        vtt_path=f"{project.id}/subtitles/tr.vtt",
    )

    report = build_storage_reference_inventory(storage_root=tmp_path)
    entries = [entry for entry in report["references"] if entry["owner_model"] == "TranslatedSubtitleTrack"]

    assert {entry["field"] for entry in entries} == {"srt_path", "vtt_path"}
    assert all(entry["owner_id"] == str(track.id) for entry in entries)
    assert any(entry["exists"] is True for entry in entries)
    assert any(entry["exists"] is False for entry in entries)


def test_avatar_profile_project_and_render_job_references(tmp_path):
    user, project = _project()
    profile = user.profile
    profile.avatar_image_original = f"avatars/{user.id}/uploads/source.png"
    profile.avatar_last_preview_path = f"avatars/{user.id}/preview/preview.mp4"
    profile.save(update_fields=["avatar_image_original", "avatar_last_preview_path"])
    project.avatar_output_path = f"{project.id}/avatar/avatar_track.mp4"
    project.save(update_fields=["avatar_output_path"])
    avatar_job = AvatarRenderJob.objects.create(
        lesson=project,
        teacher=user,
        source_image_hash="a",
        tts_audio_hash="b",
        output_path=f"{project.id}/avatar_segments/avatar_001.mp4",
    )
    _write(tmp_path, profile.avatar_image_original, b"png")

    report = build_storage_reference_inventory(storage_root=tmp_path)
    paths = _paths(report)

    assert paths[profile.avatar_image_original]["category"] == "avatar_assets"
    assert paths[profile.avatar_image_original]["exists"] is True
    assert paths[profile.avatar_last_preview_path]["exists"] is False
    assert paths[project.avatar_output_path]["owner_model"] == "Project"
    assert paths[avatar_job.output_path]["owner_model"] == "AvatarRenderJob"


def test_profile_image_and_voice_references(tmp_path):
    user, project = _project()
    profile = user.profile
    profile.banner_image_processed = f"profiles/{user.id}/banner_processed.jpg"
    profile.logo_image_original = f"profiles/{user.id}/logo_original.png"
    profile.save(update_fields=["banner_image_processed", "logo_image_original"])
    VoiceProfile.objects.create(user=user, voice_id="voice_fixture")
    _write(tmp_path, profile.banner_image_processed, b"banner")
    _write(tmp_path, "voices/voice_fixture.wav", b"voice")

    report = build_storage_reference_inventory(storage_root=tmp_path)
    paths = _paths(report)

    assert paths[profile.banner_image_processed]["category"] == "profiles"
    assert paths[profile.logo_image_original]["exists"] is False
    assert paths["voices/voice_fixture.wav"]["category"] == "tts_voice_audio"
    assert paths["voices/voice_fixture.wav"]["exists"] is True


def test_missing_reference_counts_as_missing(tmp_path):
    _user, project = _project()
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/missing.mp4")

    report = build_storage_reference_inventory(storage_root=tmp_path)

    assert report["summary"]["missing_references"] >= 1
    assert _paths(report)[f"{project.id}/missing.mp4"]["exists"] is False


def test_unsafe_traversal_path_is_flagged_and_not_opened(tmp_path):
    _user, project = _project()
    outside = tmp_path.parent / "secret.mp4"
    outside.write_bytes(b"secret")
    Job.objects.create(project=project, job_type="video_export", status="done", result_url="../secret.mp4")

    report = build_storage_reference_inventory(storage_root=tmp_path)
    entry = _paths(report)["../secret.mp4"]

    assert entry["exists"] is False
    assert entry["size_bytes"] == 0
    assert "unsafe_path:traversal" in entry["notes"]


def test_deterministic_ordering(tmp_path):
    _user, first = _project("first")
    _user2, second = _project("second")
    Job.objects.create(project=second, job_type="video_export", status="done", result_url=f"{second.id}/{second.id}.mp4")
    Job.objects.create(project=first, job_type="video_export", status="done", result_url=f"{first.id}/{first.id}.mp4")

    first_report = build_storage_reference_inventory(storage_root=tmp_path)
    second_report = build_storage_reference_inventory(storage_root=tmp_path)

    assert first_report["references"] == second_report["references"]
    assert first_report["references"] == sorted(
        first_report["references"],
        key=lambda item: (item["category"], item["owner_model"], int(item["owner_id"]), item["field"], item["path"]),
    )


def test_json_management_command_output_shape(tmp_path):
    _user, project = _project()
    Job.objects.create(project=project, job_type="video_export", status="done", result_url=f"{project.id}/{project.id}.mp4")
    stdout = io.StringIO()

    call_command("storage_reference_inventory", "--json", "--storage-root", str(tmp_path), stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "read-only/report-only"
    assert payload["summary"]["total_references"] >= 1
    assert "references" in payload
    assert "references_by_category" in payload
