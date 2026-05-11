import json
import os
import re
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

from core import views  # noqa: E402
from core.models import Job, Project, UserProfile  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402
from worker import tasks as worker_tasks  # noqa: E402


class _DummyRequest:
    class _DummyUser:
        is_authenticated = False
        is_active = False

    class _DummySession:
        session_key = "abc12345session"

        def save(self):
            return None

    user = _DummyUser()
    session = _DummySession()

    def build_absolute_uri(self, path: str) -> str:
        return f"http://testserver{path}"


class _DummyJob:
    def __init__(self, job_id: int, project_id: int, result_url: str, srt_url: str = ""):
        self.id = job_id
        self.project_id = project_id
        self.result_url = result_url
        self.srt_url = srt_url


class _DummyJobsCollection:
    def __init__(self, job):
        self._job = job

    def filter(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._job


class _DummyAvatarRenderJobsCollection:
    def exclude(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None


def _extract_stream_tokens(text: str) -> list[str]:
    return re.findall(r"/api/v1/stream/([^/]+)/", text)


def _make_teacher(username: str):
    user = User.objects.create_user(username=username, password="pass")
    UserProfile.objects.create(user=user, role="teacher")
    return user


def test_worker_hls_sidecar_public_mode_does_not_package_without_streaming_config(tmp_path):
    calls = []

    def fake_package_hls_stream(*args, **kwargs):
        calls.append((args, kwargs))
        return {"encrypted": False}

    payload = worker_tasks._package_hls_assets_for_playback(
        project_id=101,
        final_video=str(tmp_path / "101.mp4"),
        output_dir=tmp_path,
        output_rel_prefix="101",
        protection_mode="public",
        package_hls_stream_func=fake_package_hls_stream,
        streaming_enabled=False,
    )

    assert calls == []
    assert payload == {
        "enabled": False,
        "manifest_rel_path": "",
        "encrypted": False,
        "packaging_status": "not_required",
        "warnings": [],
    }


def test_worker_hls_sidecar_secure_stream_packages_manifest_and_filters_keys(tmp_path):
    calls = []
    raw_key = "00112233445566778899aabbccddeeff"

    def fake_package_hls_stream(input_video_path, output_dir, **kwargs):
        calls.append({"input_video_path": input_video_path, "output_dir": output_dir, "kwargs": kwargs})
        return {
            "playlist": str(Path(output_dir) / "index.m3u8"),
            "encrypted": True,
            "key_hex": raw_key,
            "key_file": str(Path(output_dir) / "enc.key"),
        }

    payload = worker_tasks._package_hls_assets_for_playback(
        project_id=202,
        final_video=str(tmp_path / "202.mp4"),
        output_dir=tmp_path,
        output_rel_prefix="202",
        protection_mode="secure_stream",
        package_hls_stream_func=fake_package_hls_stream,
        streaming_enabled=False,
        hls_encryption_enabled=True,
        hls_key_hex=raw_key,
    )

    assert len(calls) == 1
    assert calls[0]["kwargs"]["encrypt"] is True
    assert payload["enabled"] is True
    assert payload["manifest_rel_path"] == "202/drm/hls/index.m3u8"
    assert payload["encrypted"] is True
    assert payload["packaging_status"] == "packaged"
    assert payload["warnings"] == []
    serialized = json.dumps(payload)
    assert raw_key not in serialized
    assert "key_hex" not in serialized
    assert "key_file" not in serialized
    assert "key_rel_path" not in serialized


def test_worker_hls_sidecar_secure_stream_packaging_failure_records_required_warning(tmp_path):
    def fake_package_hls_stream(*args, **kwargs):
        raise RuntimeError("ffmpeg failed")

    payload = worker_tasks._package_hls_assets_for_playback(
        project_id=303,
        final_video=str(tmp_path / "303.mp4"),
        output_dir=tmp_path,
        output_rel_prefix="303",
        protection_mode="secure_stream",
        package_hls_stream_func=fake_package_hls_stream,
        streaming_enabled=False,
    )

    assert payload["enabled"] is False
    assert payload["manifest_rel_path"] == ""
    assert payload["packaging_status"] == "failed"
    assert "hls_packaging_failed" in payload["warnings"]
    assert "hls_required_but_missing" in payload["warnings"]


def test_worker_hls_sidecar_drm_protected_records_missing_encryption_and_metadata(tmp_path):
    def fake_package_hls_stream(input_video_path, output_dir, **kwargs):
        return {"playlist": str(Path(output_dir) / "index.m3u8"), "encrypted": False}

    with override_settings(
        DRM_ENABLED=False,
        LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM=True,
        LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM=True,
    ):
        payload = worker_tasks._package_hls_assets_for_playback(
            project_id=404,
            final_video=str(tmp_path / "404.mp4"),
            output_dir=tmp_path,
            output_rel_prefix="404",
            protection_mode="drm_protected",
            package_hls_stream_func=fake_package_hls_stream,
            streaming_enabled=False,
            hls_encryption_enabled=False,
        )

    assert payload["enabled"] is True
    assert payload["manifest_rel_path"] == "404/drm/hls/index.m3u8"
    assert payload["encrypted"] is False
    assert payload["packaging_status"] == "packaged"
    assert "drm_hls_encryption_required_but_disabled" in payload["warnings"]
    assert "drm_metadata_required_but_missing" in payload["warnings"]


def test_media_token_roundtrip_with_and_without_rel_path():
    plain = views.generate_media_token(12, "video")
    job_id, file_type, rel_path, grant_id, bind_key = views.validate_media_token(plain)
    assert (job_id, file_type, rel_path) == (12, "video", "")
    assert grant_id is None
    assert bind_key is None

    hls = views.generate_media_token(34, "hls_manifest", rel_path="34/drm/hls/index.m3u8")
    job_id, file_type, rel_path, grant_id, bind_key = views.validate_media_token(hls)
    assert job_id == 34
    assert file_type == "hls_manifest"
    assert rel_path == "34/drm/hls/index.m3u8"
    assert grant_id is None
    assert bind_key is None


def test_vtt_content_type_and_fallback():
    assert views._content_type_for_resource("vtt") == "text/vtt; charset=utf-8"
    assert views._content_type_for_resource("srt") == "text/vtt; charset=utf-8"

    # Check that error response for VTT still returns text/vtt
    resp = views._stream_error_response(file_type="vtt", status_code=200, reason="subtitle_missing")
    assert resp["Content-Type"] == "text/vtt; charset=utf-8"
    assert b"WEBVTT" in resp.content

def test_media_token_expiry(monkeypatch):
    base = 1_700_000_000
    monkeypatch.setattr(views.time, "time", lambda: base)
    token = views.generate_media_token(99, "video")

    monkeypatch.setattr(views.time, "time", lambda: base + views._token_ttl() + 10)
    with pytest.raises(ValueError, match="expired"):
        views.validate_media_token(token)


def test_hls_manifest_rewrite_tokenizes_segments_and_key(tmp_path):
    storage_root = tmp_path
    project_dir = storage_root / "101" / "drm" / "hls"
    project_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = project_dir / "index.m3u8"
    manifest_text = "\n".join(
        [
            "#EXTM3U",
            '#EXT-X-KEY:METHOD=AES-128,URI="enc.key"',
            "#EXTINF:6.0,",
            "seg_00001.ts",
            "#EXTINF:6.0,",
            "seg_00002.ts",
        ]
    )
    manifest_path.write_text(manifest_text, encoding="utf-8")

    rewritten = views._rewrite_hls_manifest_with_tokens(
        manifest_text,
        request=_DummyRequest(),
        job_id=555,
        manifest_path=manifest_path,
        storage_root=storage_root,
    )

    # Ensure no raw segment/key refs remain in rewritten manifest body.
    assert 'URI="enc.key"' not in rewritten
    assert "\nseg_00001.ts\n" not in rewritten
    assert "\nseg_00002.ts\n" not in rewritten

    tokens = _extract_stream_tokens(rewritten)
    assert len(tokens) == 3

    decoded = [views.validate_media_token(tok) for tok in tokens]
    types = sorted(item[1] for item in decoded)
    assert types == ["hls_key", "hls_segment", "hls_segment"]
    for _, _, rel_path, _, _ in decoded:
        assert rel_path.startswith("101/")


def test_hls_manifest_rewrite_rejects_parent_path_reference(tmp_path):
    storage_root = tmp_path
    project_dir = storage_root / "101" / "drm" / "hls"
    project_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = project_dir / "index.m3u8"
    manifest_text = "\n".join(["#EXTM3U", "../../../../outside.ts"])
    manifest_path.write_text(manifest_text, encoding="utf-8")

    with pytest.raises(ValueError):
        views._rewrite_hls_manifest_with_tokens(
            manifest_text,
            request=_DummyRequest(),
            job_id=555,
            manifest_path=manifest_path,
            storage_root=storage_root,
        )


def test_playback_sidecar_loading_and_debug_payload(tmp_path):
    storage_root = tmp_path
    project_id = 777
    sidecar_path = storage_root / str(project_id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        '{"asset_id":"lesson-777","content_id":"project-777","hls":{"manifest_rel_path":"777/drm/hls/index.m3u8","encrypted":true}}',
        encoding="utf-8",
    )

    sidecar = views._playback_sidecar_for_job(str(storage_root), project_id)
    assert sidecar.get("asset_id") == "lesson-777"
    assert sidecar.get("hls", {}).get("manifest_rel_path") == "777/drm/hls/index.m3u8"

    class _Project:
        id = project_id
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    class _Job:
        id = 888

    payload = views._playback_payload(
        _DummyRequest(),
        _Project(),
        _Job(),
        video_token="videoTok",
        srt_token=None,
        hls_manifest_token="hlsTok",
        hls_encrypted=True,
    )
    dbg = payload.get("playback_debug", {})
    assert dbg.get("secure_playback_active") is True
    assert dbg.get("selected_mode") == "hls"
    assert dbg.get("hls_available") is True


def test_playback_payload_includes_safe_drm_contract_fields():
    class _Project:
        id = 1201
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    class _Job:
        id = 2202

    with override_settings(
        DRM_ENABLED=True,
        DRM_PROVIDER_NAME="external",
        DRM_PREFERRED_SYSTEM="widevine",
        DRM_WIDEVINE_ENABLED=True,
        DRM_WIDEVINE_LICENSE_URL="https://license.example.test/widevine",
        DRM_WIDEVINE_CONTENT_TYPE="video/mp4",
        LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=True,
    ):
        payload = views._playback_payload(
            _DummyRequest(),
            _Project(),
            _Job(),
            video_token="videoTok",
            srt_token=None,
            hls_manifest_token="hlsTok",
            hls_encrypted=True,
            protection_mode="drm_protected",
            allow_mp4_fallback=False,
            playback_session_id="playback-2202-abcdef123456",
            session_binding_active=True,
        )

    drm = payload["drm"]
    assert drm["enabled"] is True
    assert drm["ready"] is True
    assert drm["provider"] == "external"
    assert drm["preferred_system"] == "widevine"
    assert drm["key_system"] == "com.widevine.alpha"
    assert drm["license_url"] == "https://license.example.test/widevine"
    assert drm["certificate_url"] == ""
    assert drm["manifest_url"].endswith("/api/v1/stream/hlsTok/")
    assert drm["asset_id"] == "lesson-1201"
    assert drm["content_id"] == "project-1201"
    assert drm["playback_session_id"] == "playback-2202-abcdef123456"
    assert drm["session_binding_active"] is True
    assert drm["fallback_allowed"] is False
    assert drm["systems"]["widevine"]["ready"] is True
    assert "raw_path" not in drm


def test_playback_payload_drm_protected_disables_mp4_fallback():
    class _Project:
        id = 1001
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    class _Job:
        id = 2002

    payload = views._playback_payload(
        _DummyRequest(),
        _Project(),
        _Job(),
        video_token="videoTok",
        srt_token=None,
        hls_manifest_token="hlsTok",
        hls_encrypted=True,
        protection_mode="drm_protected",
        allow_mp4_fallback=False,
    )

    assert payload["protection_mode"] == "drm_protected"
    assert payload["video_url"] == ""
    assert payload["allow_mp4_fallback"] is False
    assert payload["streaming"]["fallback"] is None
    assert payload["streaming"]["hls"]["enabled"] is True
    assert payload["watermark"]["forced"] is True
    assert payload["playback_status"]["protection_mode"] == "drm_protected"
    assert payload["playback_status"]["grant_active"] is False
    assert payload["playback_status"]["token_renewal_enabled"] is False


def test_playback_payload_secure_stream_keeps_mp4_fallback():
    class _Project:
        id = 1003
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    class _Job:
        id = 2004

    payload = views._playback_payload(
        _DummyRequest(),
        _Project(),
        _Job(),
        video_token="videoTok",
        srt_token=None,
        hls_manifest_token=None,
        hls_encrypted=False,
        protection_mode="secure_stream",
        allow_mp4_fallback=True,
    )

    assert payload["protection_mode"] == "secure_stream"
    assert payload["video_url"].endswith("/api/v1/stream/videoTok/")
    assert payload["allow_mp4_fallback"] is True

def test_effective_mode_prefers_public_env_over_legacy_sidecar_secure():
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public", DEBUG=True):
        effective, mode_debug = views._resolve_effective_protection_mode({"protection_mode": "secure_stream"})

    assert effective == "public"
    assert mode_debug["source"] == "env_default_public_override"
    assert mode_debug["env_default_mode"] == "public"
    assert mode_debug["sidecar_mode"] == "secure_stream"

def test_effective_mode_uses_sidecar_when_env_not_public():
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="secure_stream", DEBUG=False):
        effective, mode_debug = views._resolve_effective_protection_mode({"protection_mode": "drm_protected"})

    assert effective == "drm_protected"
    assert mode_debug["source"] == "sidecar"
    assert mode_debug["sidecar_override_applied"] is True

def test_playback_payload_mode_debug_reflects_effective_mode_source():
    class _Project:
        id = 1401
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    class _Job:
        id = 2402

    payload = views._playback_payload(
        _DummyRequest(),
        _Project(),
        _Job(),
        video_token="videoTok",
        srt_token=None,
        hls_manifest_token=None,
        protection_mode="public",
        mode_debug={"source": "env_default_public_override", "effective_mode": "public"},
        allow_mp4_fallback=True,
        playback_session_id=None,
        session_binding_active=False,
    )

    assert payload["playback_status"]["protection_mode"] == "public"
    assert payload["playback_status"]["mode_source"] == "env_default_public_override"
    assert payload["playback_status"]["token_renewal_enabled"] is False
    assert payload["mode_debug"]["effective_mode"] == "public"

def test_public_mode_allows_grantless_stream_access():
    allowed = views._check_grant_access(
        _DummyRequest(),
        lesson_id=321,
        grant_id=None,
        bind_key=None,
        mode="public",
        file_type="video",
    )
    assert allowed is True


def test_playback_token_view_rejects_drm_protected_lesson_without_drm_config(tmp_path, monkeypatch):
    project_id = 901
    job = _DummyJob(902, project_id, result_url=f"{project_id}/{project_id}.mp4")

    class _Project:
        id = project_id
        jobs = _DummyJobsCollection(job)
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    sidecar_path = tmp_path / str(project_id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        '{"asset_id":"lesson-901","content_id":"project-901","protection_mode":"drm_protected","hls":{"manifest_rel_path":"901/drm/hls/index.m3u8","encrypted":true}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(views.Project.objects, "get", lambda pk: _Project())

    request = APIRequestFactory().get(f"/api/v1/projects/{project_id}/playback-token/")
    request.user = _DummyRequest._DummyUser()
    request.session = _DummyRequest._DummySession()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        DRM_ENABLED=True,
        DRM_PREFERRED_SYSTEM="widevine",
        DRM_WIDEVINE_ENABLED=False,
        LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM=True,
    ):
        response = views.PlaybackTokenView.as_view()(request, project_id=project_id)

    assert response.status_code == 409
    assert response.data["error"] == "DRM-protected lesson requires DRM metadata configuration."


def test_playback_token_view_rejects_drm_protected_lesson_without_hls_manifest(tmp_path, monkeypatch):
    project_id = 905
    job = _DummyJob(906, project_id, result_url=f"{project_id}/{project_id}.mp4")

    class _Project:
        id = project_id
        jobs = _DummyJobsCollection(job)
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    sidecar_path = tmp_path / str(project_id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        json.dumps(
            {
                "asset_id": "lesson-905",
                "content_id": "project-905",
                "protection_mode": "drm_protected",
                "hls": {
                    "enabled": False,
                    "manifest_rel_path": "",
                    "encrypted": False,
                    "packaging_status": "failed",
                    "warnings": ["hls_required_but_missing"],
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(views.Project.objects, "get", lambda pk: _Project())

    request = APIRequestFactory().get(f"/api/v1/projects/{project_id}/playback-token/")
    request.user = _DummyRequest._DummyUser()
    request.session = _DummyRequest._DummySession()

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.PlaybackTokenView.as_view()(request, project_id=project_id)

    assert response.status_code == 409
    assert response.data["error"] == "DRM-protected lesson requires HLS manifest."


def test_playback_token_view_returns_hls_payload_for_drm_protected_lesson(tmp_path, monkeypatch):
    project_id = 911
    job = _DummyJob(912, project_id, result_url=f"{project_id}/{project_id}.mp4")

    class _Project:
        id = project_id
        jobs = _DummyJobsCollection(job)
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    sidecar_path = tmp_path / str(project_id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        '{"asset_id":"lesson-911","content_id":"project-911","protection_mode":"drm_protected","hls":{"manifest_rel_path":"911/drm/hls/index.m3u8","encrypted":true}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(views.Project.objects, "get", lambda pk: _Project())

    request = APIRequestFactory().get(f"/api/v1/projects/{project_id}/playback-token/")
    request.user = _DummyRequest._DummyUser()
    request.session = _DummyRequest._DummySession()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        DRM_ENABLED=True,
        DRM_PROVIDER_NAME="external",
        DRM_PREFERRED_SYSTEM="widevine",
        DRM_WIDEVINE_ENABLED=True,
        DRM_WIDEVINE_LICENSE_URL="https://license.example.test/widevine",
        DRM_WIDEVINE_CONTENT_TYPE="application/vnd.apple.mpegurl",
        LESSON_PROTECTION_REQUIRE_DRM_METADATA_FOR_DRM=True,
        LESSON_PROTECTION_REQUIRE_HLS_ENCRYPTION_FOR_DRM=True,
    ):
        response = views.PlaybackTokenView.as_view()(request, project_id=project_id)

    assert response.status_code == 200
    assert response.data["protection_mode"] == "drm_protected"
    assert response.data["video_url"] == ""
    assert response.data["allow_mp4_fallback"] is False
    assert response.data["streaming"]["hls"]["enabled"] is True
    assert response.data["streaming"]["hls"]["manifest_url"].endswith("/api/v1/stream/") is False
    assert "/api/v1/stream/" in response.data["streaming"]["hls"]["manifest_url"]
    assert response.data["drm"]["ready"] is True


def test_playback_token_view_returns_multi_system_drm_contract(tmp_path, monkeypatch):
    project_id = 911
    job = _DummyJob(912, project_id, result_url=f"{project_id}/{project_id}.mp4", srt_url=f"{project_id}/{project_id}.srt")

    class _Project:
        id = project_id
        jobs = _DummyJobsCollection(job)
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    sidecar_path = tmp_path / str(project_id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        '{"asset_id":"lesson-911","content_id":"project-911","protection_mode":"drm_protected","hls":{"manifest_rel_path":"911/drm/hls/index.m3u8","encrypted":true}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(views.Project.objects, "get", lambda pk: _Project())

    request = APIRequestFactory().get(f"/api/v1/projects/{project_id}/playback-token/")
    request.user = _DummyRequest._DummyUser()
    request.session = _DummyRequest._DummySession()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        DRM_ENABLED=True,
        DRM_PROVIDER_NAME="external",
        DRM_PREFERRED_SYSTEM="widevine",
        DRM_WIDEVINE_ENABLED=True,
        DRM_WIDEVINE_LICENSE_URL="https://license.example.test/widevine",
        DRM_PLAYREADY_ENABLED=True,
        DRM_PLAYREADY_LICENSE_URL="https://license.example.test/playready",
        LESSON_PROTECTION_BIND_PLAYBACK_TO_SESSION=True,
    ):
        response = views.PlaybackTokenView.as_view()(request, project_id=project_id)

    assert response.status_code == 200
    payload = response.data
    assert payload["protection_mode"] == "drm_protected"
    assert payload["video_url"] == ""
    assert payload["allow_mp4_fallback"] is False
    assert payload["session_binding_active"] is True
    assert payload["drm"]["enabled"] is True
    assert payload["drm"]["ready"] is True
    assert payload["drm"]["preferred_system"] == "widevine"
    assert payload["drm"]["systems"]["widevine"]["ready"] is True
    assert payload["drm"]["systems"]["playready"]["ready"] is True
    assert payload["drm"]["manifest_url"].startswith("http://testserver/api/v1/stream/")
    assert payload["drm"]["playback_session_id"].startswith("playback-912-")


def test_playback_grant_rotation_invalidates_previous_grant(monkeypatch):
    # Minimal in-memory cache stub
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    req = _DummyRequest()

    class _AuthUser:
        is_authenticated = True
        id = 77
        username = "student77"

    req.user = _AuthUser()
    job_id = 5151
    mode = "secure_stream"
    ttl = 300

    old_grant, _ = views._issue_playback_grant(job_id, req, mode, ttl)
    bind_key = views._bind_key_for_request(req)

    assert views._check_grant_access(
        req,
        lesson_id=job_id,
        grant_id=old_grant,
        bind_key=bind_key,
        mode=mode,
        file_type="hls_manifest",
    ) is True

    # Re-issuing from same session reuses grant to keep UX stable.
    new_grant, _ = views._issue_playback_grant(job_id, req, mode, ttl)
    assert new_grant == old_grant

    req_other = _DummyRequest()
    req_other.user = _AuthUser()

    class _OtherSession:
        session_key = "different-session"

        def save(self):
            return None

    req_other.session = _OtherSession()
    rotated_grant, _ = views._issue_playback_grant(job_id, req_other, mode, ttl)
    assert rotated_grant != old_grant

    assert views._check_grant_access(
        req,
        lesson_id=job_id,
        grant_id=old_grant,
        bind_key=bind_key,
        mode=mode,
        file_type="hls_manifest",
    ) is False

    assert views._check_grant_access(
        req_other,
        lesson_id=job_id,
        grant_id=rotated_grant,
        bind_key=views._bind_key_for_request(req_other),
        mode=mode,
        file_type="hls_manifest",
    ) is True


def test_playback_grant_allows_rewatch_and_seek_within_same_session(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    req = _DummyRequest()
    lesson_id = 8080
    mode = "secure_stream"
    grant_id, _ = views._issue_playback_grant(lesson_id, req, mode, 1200)
    bind_key = views._bind_key_for_request(req)

    # Simulate normal player actions: manifest fetch, repeated segment requests,
    # and rewind that requests the same segment again.
    for file_type in ["hls_manifest", "hls_segment", "hls_segment", "hls_segment", "hls_key", "hls_segment"]:
        assert views._check_grant_access(
            req,
            lesson_id=lesson_id,
            grant_id=grant_id,
            bind_key=bind_key,
            mode=mode,
            file_type=file_type,
        ) is True


def test_logout_epoch_revokes_existing_playback_grant(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    req = _DummyRequest()

    class _AuthUser:
        is_authenticated = True
        id = 41
        username = "student41"

    req.user = _AuthUser()
    lesson_id = 9090
    mode = "secure_stream"
    grant_id, _ = views._issue_playback_grant(lesson_id, req, mode, 1200)
    bind_key = views._bind_key_for_request(req)

    assert views._check_grant_access(
        req,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=bind_key,
        mode=mode,
        file_type="hls_manifest",
    ) is True

    store[views._logout_epoch_key_for(views._playback_identity(req))] = int(time.time())

    assert views._check_grant_access(
        req,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=bind_key,
        mode=mode,
        file_type="hls_segment",
    ) is False


def test_inactivity_revokes_only_after_grace(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    now_ref = {"v": 1_700_000_000}
    monkeypatch.setattr(views.time, "time", lambda: now_ref["v"])

    req = _DummyRequest()
    lesson_id = 10010
    mode = "secure_stream"
    grant_id, _ = views._issue_playback_grant(lesson_id, req, mode, 1800)
    bind_key = views._bind_key_for_request(req)

    monkeypatch.setattr(views, "_playback_inactivity_ttl", lambda: 120)

    now_ref["v"] += 90
    assert views._check_grant_access(
        req,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=bind_key,
        mode=mode,
        file_type="hls_segment",
    ) is True

    now_ref["v"] += 130
    assert views._check_grant_access(
        req,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=bind_key,
        mode=mode,
        file_type="hls_segment",
    ) is False


def test_hidden_grace_revokes_only_after_grace(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    now_ref = {"v": 1_700_000_000}
    monkeypatch.setattr(views.time, "time", lambda: now_ref["v"])
    monkeypatch.setattr(views, "_playback_hidden_grace_ttl", lambda: 60)

    req = _DummyRequest()
    lesson_id = 12012
    mode = "secure_stream"
    grant_id, _ = views._issue_playback_grant(lesson_id, req, mode, 1800)

    payload = store[views._grant_key_for(grant_id)]
    views._touch_grant_activity(grant_id=grant_id, grant_payload=payload, ttl_seconds=1800, hidden=True)

    now_ref["v"] += 40
    assert views._check_grant_access(
        req,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=views._bind_key_for_request(req),
        mode=mode,
        file_type="hls_manifest",
    ) is True

    payload = store[views._grant_key_for(grant_id)]
    views._touch_grant_activity(grant_id=grant_id, grant_payload=payload, ttl_seconds=1800, hidden=True)
    now_ref["v"] += 61

    assert views._check_grant_access(
        req,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=views._bind_key_for_request(req),
        mode=mode,
        file_type="hls_manifest",
    ) is False


def test_multi_device_policy_deny_new_blocks_second_session(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    class _AuthUser:
        is_authenticated = True
        id = 88
        username = "student88"

    req_one = _DummyRequest()
    req_one.user = _AuthUser()
    req_two = _DummyRequest()
    req_two.user = _AuthUser()

    class _SessionTwo:
        session_key = "session-two"

        def save(self):
            return None

    req_two.session = _SessionTwo()

    lesson_id = 7878
    mode = "secure_stream"
    ttl = 1800

    first_grant, _ = views._issue_playback_grant(lesson_id, req_one, mode, ttl)
    assert first_grant

    with override_settings(LESSON_PROTECTION_CONCURRENCY_POLICY="deny_new"):
        allowed, reason = views._enforce_playback_concurrency(lesson_id, req_two, mode)

    assert allowed is False
    assert reason == "concurrency_active_elsewhere"


def test_multi_device_policy_rotate_old_revokes_previous_session(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    class _AuthUser:
        is_authenticated = True
        id = 89
        username = "student89"

    req_one = _DummyRequest()
    req_one.user = _AuthUser()
    req_two = _DummyRequest()
    req_two.user = _AuthUser()

    class _SessionTwo:
        session_key = "session-two"

        def save(self):
            return None

    req_two.session = _SessionTwo()

    lesson_id = 7979
    mode = "secure_stream"
    ttl = 1800

    first_grant, _ = views._issue_playback_grant(lesson_id, req_one, mode, ttl)
    assert first_grant

    with override_settings(LESSON_PROTECTION_CONCURRENCY_POLICY="rotate_old"):
        allowed, reason = views._enforce_playback_concurrency(lesson_id, req_two, mode)

    assert allowed is True
    assert reason is None
    assert (store.get(views._grant_key_for(first_grant)) or {}).get("revoked") is True


def test_grant_activity_touch_extends_last_seen(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    now_ref = {"v": 1_700_000_000}
    monkeypatch.setattr(views.time, "time", lambda: now_ref["v"])

    req = _DummyRequest()
    lesson_id = 6767
    mode = "secure_stream"
    grant_id, _ = views._issue_playback_grant(lesson_id, req, mode, 900)
    initial_last_seen = (store.get(views._grant_key_for(grant_id)) or {}).get("last_seen_at")

    now_ref["v"] += 30
    payload = store.get(views._grant_key_for(grant_id))
    views._touch_grant_activity(grant_id=grant_id, grant_payload=payload, ttl_seconds=900, hidden=False)
    updated_last_seen = (store.get(views._grant_key_for(grant_id)) or {}).get("last_seen_at")

    assert updated_last_seen > initial_last_seen


def test_playback_session_heartbeat_renews_active_grant(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    now_ref = {"v": 1_700_000_000}
    monkeypatch.setattr(views.time, "time", lambda: now_ref["v"])

    project_id = 4321
    job = _DummyJob(8765, project_id, result_url=f"{project_id}/{project_id}.mp4")

    class _Project:
        id = project_id
        jobs = _DummyJobsCollection(job)
        avatar_render_jobs = _DummyAvatarRenderJobsCollection()

    monkeypatch.setattr(views.Project.objects, "get", lambda pk: _Project())

    req = _DummyRequest()
    grant_id, _ = views._issue_playback_grant(project_id, req, "secure_stream", 1200)
    before = (store.get(views._grant_key_for(grant_id)) or {}).get("last_seen_at")

    rf = APIRequestFactory()
    heartbeat_request = rf.post(
        f"/api/v1/projects/{project_id}/playback-session/heartbeat/",
        {"visibility": "visible"},
        format="json",
    )
    heartbeat_request.user = req.user
    heartbeat_request.session = req.session

    now_ref["v"] += 25
    response = views.PlaybackSessionHeartbeatView.as_view()(heartbeat_request, project_id=project_id)

    assert response.status_code == 200
    assert response.data["active"] is True
    assert response.data["renewed"] is True
    after = (store.get(views._grant_key_for(grant_id)) or {}).get("last_seen_at")
    assert after > before


def test_old_token_invalidated_after_grant_rotation_stream_view(tmp_path, monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    project_id = 606
    job_id = 607
    rel = f"{project_id}/{project_id}.mp4"

    (tmp_path / f"{project_id}").mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(b"fake-mp4")

    fake_job = _DummyJob(job_id, project_id, result_url=rel)
    monkeypatch.setattr(views.Job.objects, "get", lambda pk: fake_job)

    class _AuthUser:
        is_authenticated = True
        is_active = True
        id = 121
        username = "student121"

    req1 = _DummyRequest()
    req1.user = _AuthUser()
    req2 = _DummyRequest()
    req2.user = _AuthUser()

    class _SessionTwo:
        session_key = "second-browser-session"

        def save(self):
            return None

    req2.session = _SessionTwo()

    with override_settings(LESSON_PROTECTION_CONCURRENCY_POLICY="rotate_old"):
        grant1, _ = views._issue_playback_grant(project_id, req1, "secure_stream", 1200)
        token1 = views.generate_media_token(job_id, "video", ttl_seconds=1200, grant_id=grant1, bind_key=views._bind_key_for_request(req1))
        allowed, _ = views._enforce_playback_concurrency(project_id, req2, "secure_stream")
        assert allowed is True
        grant2, _ = views._issue_playback_grant(project_id, req2, "secure_stream", 1200)
        token2 = views.generate_media_token(job_id, "video", ttl_seconds=1200, grant_id=grant2, bind_key=views._bind_key_for_request(req2))

    with override_settings(STORAGE_ROOT=str(tmp_path), ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        rf = APIRequestFactory()

        request_old = rf.get(f"/api/v1/stream/{token1}/")
        request_old.user = req1.user
        request_old.session = req1.session
        old_resp = views.MediaStreamView.as_view()(request_old, token=token1)
        assert old_resp.status_code == 403

        request_new = rf.get(f"/api/v1/stream/{token2}/")
        request_new.user = req2.user
        request_new.session = req2.session
        new_resp = views.MediaStreamView.as_view()(request_new, token=token2)
        assert new_resp.status_code == 200


def test_client_risk_signals_do_not_flag_chrome_like_hls_request():
    rf = APIRequestFactory()
    req = rf.get(
        "/api/v1/stream/fake-token/",
        HTTP_USER_AGENT=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        HTTP_ACCEPT="application/vnd.apple.mpegurl,application/json;q=0.9,*/*;q=0.8",
        HTTP_SEC_FETCH_DEST="video",
        HTTP_SEC_FETCH_MODE="cors",
        HTTP_REFERER="http://localhost:5173/lesson/22",
    )
    score, reasons = views._client_risk_signals(req, grant_id="g1", file_type="hls_manifest", mode="drm_protected")
    assert score < views._risk_medium_threshold()
    assert reasons == []


def test_client_risk_signals_suspicious_automation_pattern():
    rf = APIRequestFactory()
    req = rf.get(
        "/api/v1/stream/fake-token/",
        HTTP_USER_AGENT="Wget/1.21.4",
        HTTP_ACCEPT="*/*",
    )
    score, reasons = views._client_risk_signals(req, grant_id="g2", file_type="hls_manifest", mode="drm_protected")
    assert score >= 2
    assert "ua_automation_pattern" in reasons


def test_risk_policy_medium_manifest_requires_fresh_grant():
    decision = views._risk_policy_decision(mode="secure_stream", file_type="hls_manifest", risk_score=views._risk_medium_threshold())
    assert decision == "fresh_grant_required"


def test_repeated_bind_mismatch_revokes_grant(monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    class _AuthUser:
        is_authenticated = True
        id = 91
        username = "student91"

    req_a = _DummyRequest()
    req_a.user = _AuthUser()
    req_b = _DummyRequest()
    req_b.user = _AuthUser()

    class _SessionB:
        session_key = "session-b"

        def save(self):
            return None

    req_b.session = _SessionB()

    lesson_id = 2222
    mode = "secure_stream"
    grant_id, _ = views._issue_playback_grant(lesson_id, req_a, mode, 1200)
    bind_a = views._bind_key_for_request(req_a)

    # First mismatch attempt is denied but not yet revoked.
    assert views._check_grant_access(
        req_b,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=bind_a,
        mode=mode,
        file_type="hls_manifest",
    ) is False
    assert (store.get(views._grant_key_for(grant_id)) or {}).get("revoked") is not True

    # Repeated mismatch triggers revocation.
    assert views._check_grant_access(
        req_b,
        lesson_id=lesson_id,
        grant_id=grant_id,
        bind_key=bind_a,
        mode=mode,
        file_type="hls_manifest",
    ) is False
    assert (store.get(views._grant_key_for(grant_id)) or {}).get("revoked") is True


def test_media_stream_view_serves_mp4_hls_manifest_segment_and_key(tmp_path, monkeypatch):
    project_id = 202
    job_id = 303
    root = tmp_path

    video_rel = f"{project_id}/{project_id}.mp4"
    srt_rel = f"{project_id}/{project_id}.srt"
    vtt_rel = f"{project_id}/{project_id}.vtt"
    manifest_rel = f"{project_id}/drm/hls/index.m3u8"
    seg_rel = f"{project_id}/drm/hls/seg_00001.ts"
    key_rel = f"{project_id}/drm/hls/enc.key"

    (root / f"{project_id}").mkdir(parents=True, exist_ok=True)
    (root / video_rel).write_bytes(b"fake-mp4")
    (root / srt_rel).write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    (root / vtt_rel).write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n", encoding="utf-8")

    hls_dir = root / f"{project_id}" / "drm" / "hls"
    hls_dir.mkdir(parents=True, exist_ok=True)
    (root / seg_rel).write_bytes(b"fake-segment")
    (root / key_rel).write_bytes(b"0123456789ABCDEF")
    (root / manifest_rel).write_text(
        "\n".join(
            [
                "#EXTM3U",
                '#EXT-X-KEY:METHOD=AES-128,URI="enc.key"',
                "#EXTINF:6.0,",
                "seg_00001.ts",
            ]
        ),
        encoding="utf-8",
    )

    fake_job = _DummyJob(job_id, project_id, result_url=video_rel, srt_url=srt_rel)
    monkeypatch.setattr(views.Job.objects, "get", lambda pk: fake_job)

    with override_settings(STORAGE_ROOT=str(root), ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        rf = APIRequestFactory()
        stream_view = views.MediaStreamView.as_view()

        # MP4 fallback stream
        video_token = views.generate_media_token(job_id, "video")
        resp = stream_view(rf.get(f"/api/v1/stream/{video_token}/"), token=video_token)
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("video/mp4")

        # HLS manifest stream with rewritten tokenized children
        manifest_token = views.generate_media_token(job_id, "hls_manifest", rel_path=manifest_rel)
        manifest_resp = stream_view(rf.get(f"/api/v1/stream/{manifest_token}/"), token=manifest_token)
        assert manifest_resp.status_code == 200
        assert manifest_resp["Content-Type"].startswith("application/vnd.apple.mpegurl")
        body = manifest_resp.content.decode("utf-8")
        assert "/api/v1/stream/" in body
        assert "seg_00001.ts" not in body
        assert 'URI="enc.key"' not in body

        # Direct tokenized segment stream
        seg_token = views.generate_media_token(job_id, "hls_segment", rel_path=seg_rel)
        seg_resp = stream_view(rf.get(f"/api/v1/stream/{seg_token}/"), token=seg_token)
        assert seg_resp.status_code == 200
        assert seg_resp["Content-Type"].startswith("video/mp2t")

        # Direct tokenized key stream
        key_token = views.generate_media_token(job_id, "hls_key", rel_path=key_rel)
        key_resp = stream_view(rf.get(f"/api/v1/stream/{key_token}/"), token=key_token)
        assert key_resp.status_code == 200
        assert key_resp["Content-Type"].startswith("application/octet-stream")

        # Subtitle stream is converted to VTT for browser track compatibility.
        srt_token = views.generate_media_token(job_id, "srt")
        srt_resp = stream_view(rf.get(f"/api/v1/stream/{srt_token}/"), token=srt_token)
        assert srt_resp.status_code == 200
        assert srt_resp["Content-Type"].startswith("text/vtt")
        srt_body = srt_resp.content.decode("utf-8")
        assert srt_body.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:01.000" in srt_body

        # WebVTT sidecar stream is served directly for native tracks/fallback fetch.
        vtt_token = views.generate_media_token(job_id, "vtt", rel_path=vtt_rel)
        vtt_resp = stream_view(rf.get(f"/api/v1/stream/{vtt_token}/"), token=vtt_token)
        assert vtt_resp.status_code == 200
        assert vtt_resp["Content-Type"].startswith("text/vtt")
        assert b"WEBVTT" in b"".join(vtt_resp.streaming_content)


@pytest.mark.django_db
def test_catalog_detail_public_lesson_includes_tokenized_subtitle_url(tmp_path):
    teacher = _make_teacher("public_caption_teacher")
    project = Project.objects.create(
        title="Published captions",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    (tmp_path / str(project.id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{project.id}/{project.id}.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nPublic caption\n",
        encoding="utf-8",
    )

    request = APIRequestFactory().get(f"/api/v1/catalog/{project.id}/")
    request.session = _DummyRequest._DummySession()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.CatalogDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["has_srt"] is True
    assert response.data["srt_url"].startswith("http://testserver/api/v1/stream/")
    assert response.data["has_vtt"] is True
    assert response.data["vtt_url"].startswith("http://testserver/api/v1/stream/")
    assert response.data["subtitle_vtt_url"] == response.data["vtt_url"]
    assert job.srt_url not in response.data["srt_url"]
    assert f"{project.id}/{project.id}.vtt" not in response.data["vtt_url"]


@pytest.mark.django_db
def test_catalog_detail_old_srt_only_lesson_has_no_vtt_until_rerender(tmp_path):
    teacher = _make_teacher("old_caption_teacher")
    project = Project.objects.create(
        title="Old captions",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    (tmp_path / str(project.id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{project.id}/{project.id}.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nLegacy caption\n",
        encoding="utf-8",
    )

    request = APIRequestFactory().get(f"/api/v1/catalog/{project.id}/")
    request.session = _DummyRequest._DummySession()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        response = views.CatalogDetailView.as_view()(request, project_id=project.id)

    assert response.status_code == 200
    assert response.data["has_srt"] is True
    assert response.data["has_vtt"] is False
    assert response.data["srt_url"].startswith("http://testserver/api/v1/stream/")
    assert response.data["vtt_url"] is None
    assert response.data["subtitle_vtt_url"] is None


@pytest.mark.django_db
def test_owner_draft_preview_subtitle_uses_grant_and_blocks_grantless_stream(tmp_path):
    cache.clear()
    teacher = _make_teacher("draft_caption_teacher")
    project = Project.objects.create(title="Draft captions", user=teacher, is_published=False)
    job = Job.objects.create(
        project=project,
        job_type="video_export",
        status="done",
        result_url=f"{project.id}/{project.id}.mp4",
        srt_url=f"{project.id}/{project.id}.srt",
    )
    (tmp_path / str(project.id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / job.srt_url).write_text("1\n00:00:00,000 --> 00:00:01,000\nDraft caption\n", encoding="utf-8")
    (tmp_path / f"{project.id}/{project.id}.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nDraft caption\n",
        encoding="utf-8",
    )

    class _Session:
        session_key = f"draft-caption-session-{project.id}"

        def save(self):
            return None

    factory = APIRequestFactory()
    anonymous_response = views.CatalogDetailView.as_view()(
        factory.get(f"/api/v1/catalog/{project.id}/"),
        project_id=project.id,
    )
    assert anonymous_response.status_code == 404

    owner_request = factory.get(f"/api/v1/catalog/{project.id}/")
    force_authenticate(owner_request, user=teacher)
    owner_request.user = teacher
    owner_request.session = _Session()

    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        owner_response = views.CatalogDetailView.as_view()(owner_request, project_id=project.id)

    assert owner_response.status_code == 200
    assert owner_response.data["has_srt"] is True
    assert owner_response.data["srt_url"].startswith("http://testserver/api/v1/stream/")
    assert owner_response.data["has_vtt"] is True
    assert owner_response.data["vtt_url"].startswith("http://testserver/api/v1/stream/")
    assert owner_response.data["subtitle_vtt_url"] == owner_response.data["vtt_url"]
    assert job.srt_url not in owner_response.data["srt_url"]
    assert f"{project.id}/{project.id}.vtt" not in owner_response.data["vtt_url"]
    assert owner_response.data["playback_status"]["protection_mode"] == "secure_stream"
    assert owner_response.data["mode_debug"]["draft_preview_forced_secure_stream"] is True

    token = _extract_stream_tokens(owner_response.data["srt_url"])[0]
    token_job_id, file_type, _rel_path, grant_id, bind_key = views.validate_media_token(token)
    assert token_job_id == job.id
    assert file_type == "srt"
    assert grant_id
    assert bind_key

    grantless_token = views.generate_media_token(job.id, "srt")
    grantless_response = views.MediaStreamView.as_view()(
        factory.get(f"/api/v1/stream/{grantless_token}/"),
        token=grantless_token,
    )
    assert grantless_response.status_code == 403

    vtt_token = _extract_stream_tokens(owner_response.data["vtt_url"])[0]
    token_job_id, file_type, rel_path, grant_id, bind_key = views.validate_media_token(vtt_token)
    assert token_job_id == job.id
    assert file_type == "vtt"
    assert rel_path == f"{project.id}/{project.id}.vtt"
    assert grant_id
    assert bind_key

    grantless_vtt_token = views.generate_media_token(job.id, "vtt", rel_path=f"{project.id}/{project.id}.vtt")
    grantless_vtt_response = views.MediaStreamView.as_view()(
        factory.get(f"/api/v1/stream/{grantless_vtt_token}/"),
        token=grantless_vtt_token,
    )
    assert grantless_vtt_response.status_code == 403

    stream_request = factory.get(f"/api/v1/stream/{token}/")
    force_authenticate(stream_request, user=teacher)
    stream_request.user = teacher
    stream_request.session = owner_request.session
    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        stream_response = views.MediaStreamView.as_view()(stream_request, token=token)

    assert stream_response.status_code == 200
    assert stream_response["Content-Type"].startswith("text/vtt")
    body = stream_response.content.decode("utf-8")
    assert body.startswith("WEBVTT")
    assert "Draft caption" in body

    vtt_stream_request = factory.get(f"/api/v1/stream/{vtt_token}/")
    force_authenticate(vtt_stream_request, user=teacher)
    vtt_stream_request.user = teacher
    vtt_stream_request.session = owner_request.session
    with override_settings(
        STORAGE_ROOT=str(tmp_path),
        LESSON_PROTECTION_DEFAULT_MODE="public",
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
    ):
        vtt_stream_response = views.MediaStreamView.as_view()(vtt_stream_request, token=vtt_token)

    assert vtt_stream_response.status_code == 200
    assert vtt_stream_response["Content-Type"].startswith("text/vtt")
    assert b"Draft caption" in b"".join(vtt_stream_response.streaming_content)


def test_media_stream_view_rejects_expired_token(tmp_path, monkeypatch):
    project_id = 401
    job_id = 402
    rel = f"{project_id}/{project_id}.mp4"

    (tmp_path / f"{project_id}").mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(b"fake-mp4")

    fake_job = _DummyJob(job_id, project_id, result_url=rel)
    monkeypatch.setattr(views.Job.objects, "get", lambda pk: fake_job)

    now = 1_700_000_000
    monkeypatch.setattr(views.time, "time", lambda: now)
    token = views.generate_media_token(job_id, "video")

    monkeypatch.setattr(views.time, "time", lambda: now + views._token_ttl() + 1)

    with override_settings(STORAGE_ROOT=str(tmp_path), ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        rf = APIRequestFactory()
        resp = views.MediaStreamView.as_view()(rf.get(f"/api/v1/stream/{token}/"), token=token)
        assert resp.status_code == 403
        assert resp["Content-Type"].startswith("video/mp4")
        assert resp.content == b""


def test_media_stream_view_missing_subtitle_returns_empty_vtt(tmp_path, monkeypatch):
    project_id = 731
    job_id = 732
    rel = f"{project_id}/{project_id}.mp4"

    (tmp_path / f"{project_id}").mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(b"fake-mp4")

    fake_job = _DummyJob(job_id, project_id, result_url=rel, srt_url=f"{project_id}/{project_id}.srt")
    monkeypatch.setattr(views.Job.objects, "get", lambda pk: fake_job)

    token = views.generate_media_token(job_id, "srt")

    with override_settings(STORAGE_ROOT=str(tmp_path), ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        rf = APIRequestFactory()
        resp = views.MediaStreamView.as_view()(rf.get(f"/api/v1/stream/{token}/"), token=token)
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/vtt")
        assert resp.content.decode("utf-8").startswith("WEBVTT")


def test_media_stream_invalid_token_does_not_return_json_or_html():
    rf = APIRequestFactory()
    resp = views.MediaStreamView.as_view()(rf.get("/api/v1/stream/not-a-real-token/"), token="not-a-real-token")
    assert resp.status_code == 403
    assert resp["Content-Type"].startswith("video/mp4")
    assert resp.content == b""


def test_media_stream_view_allows_browser_like_grant_bound_request(tmp_path, monkeypatch):
    store = {}

    class _CacheStub:
        @staticmethod
        def set(key, value, timeout=None):
            store[key] = value

        @staticmethod
        def get(key):
            return store.get(key)

    monkeypatch.setattr(views, "cache", _CacheStub)

    project_id = 510
    job_id = 511
    rel = f"{project_id}/{project_id}.mp4"

    (tmp_path / f"{project_id}").mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(b"fake-mp4")

    fake_job = _DummyJob(job_id, project_id, result_url=rel)
    monkeypatch.setattr(views.Job.objects, "get", lambda pk: fake_job)

    req_ctx = _DummyRequest()
    grant_id, _ = views._issue_playback_grant(project_id, req_ctx, "secure_stream", 1200)
    bind_key = views._bind_key_for_request(req_ctx)
    token = views.generate_media_token(job_id, "video", ttl_seconds=1200, grant_id=grant_id, bind_key=bind_key)

    with override_settings(STORAGE_ROOT=str(tmp_path), ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        rf = APIRequestFactory()
        request = rf.get(
            f"/api/v1/stream/{token}/",
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
            ),
            HTTP_ACCEPT="video/mp4,*/*;q=0.9",
            HTTP_SEC_FETCH_DEST="video",
            HTTP_SEC_FETCH_MODE="cors",
            HTTP_REFERER="http://localhost:5173/lesson/510",
        )
        request.user = req_ctx.user
        request.session = req_ctx.session
        response = views.MediaStreamView.as_view()(request, token=token)

    assert response.status_code == 200
