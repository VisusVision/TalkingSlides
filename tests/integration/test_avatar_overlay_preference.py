import os
import sys
import json
import uuid
from pathlib import Path

import django
import pytest
from django.db import connection
REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.test.utils import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.avatar_runtime_settings import normalize_safe_avatar_motion_preset  # noqa: E402
from core.models import AvatarOverlayPreference, AvatarRenderJob, Job, Project, UserProfile  # noqa: E402

pytestmark = pytest.mark.django_db


def _with_session(request):
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _table_has_column(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
    return any(row[1] == column_name for row in rows)


def _frontend_source(*parts):
    return (REPO_ROOT / "services" / "frontend" / "src" / Path(*parts)).read_text(encoding="utf-8")


def test_frontend_video_stage_uses_separate_avatar_overlay_layer():
    source = _frontend_source("components", "player", "VideoStage.jsx")

    assert "import AvatarOverlayLayer" in source
    assert "<AvatarOverlayLayer" in source
    assert "src={lesson.stream_url}" in source
    assert "src={avatarStreamUrl}" in source
    assert source.count("<video") == 1


def test_frontend_video_stage_fullscreen_targets_player_shell():
    source = _frontend_source("components", "player", "VideoStage.jsx")

    assert "data-testid=\"player-fullscreen-shell\"" in source
    assert "playerShellRef.current" in source
    assert "target.requestFullscreen?.()" in source
    assert "activeVideoRef.current.requestFullscreen" not in source
    assert "controlsList=\"nodownload nofullscreen noplaybackrate noremoteplayback\"" in source
    assert "visus-shell-video::-webkit-media-controls-fullscreen-button" in source
    assert "data-testid=\"player-shell-fullscreen\"" in source
    assert "<WatermarkOverlay lesson={watermarkLesson} />" in source
    assert source.index("data-testid=\"player-fullscreen-shell\"") < source.index("<AvatarOverlayLayer")


def test_frontend_hls_player_uses_separate_avatar_overlay_layer():
    source = _frontend_source("components", "player", "HlsPlayer.jsx")

    assert "import AvatarOverlayLayer" in source
    assert "<AvatarOverlayLayer" in source
    assert "hls.loadSource(sourceUrl)" in source
    assert "src={avatarStreamUrl}" in source
    assert source.count("<video") == 1


def test_frontend_hls_player_fullscreen_targets_player_shell():
    source = _frontend_source("components", "player", "HlsPlayer.jsx")

    assert "data-testid=\"player-fullscreen-shell\"" in source
    assert "playerShellRef.current" in source
    assert "target.requestFullscreen?.()" in source
    assert "activeVideoRef.current.requestFullscreen" not in source
    assert "controlsList=\"nodownload nofullscreen noplaybackrate noremoteplayback\"" in source
    assert "visus-shell-video::-webkit-media-controls-fullscreen-button" in source
    assert "data-testid=\"player-shell-fullscreen\"" in source
    assert "<WatermarkOverlay lesson={watermarkLesson} />" in source
    assert source.index("data-testid=\"player-fullscreen-shell\"") < source.index("<AvatarOverlayLayer")


def test_frontend_caption_layer_stays_above_avatar_overlay_and_theater():
    layer_source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")
    video_stage_source = _frontend_source("components", "player", "VideoStage.jsx")
    hls_source = _frontend_source("components", "player", "HlsPlayer.jsx")

    assert "baseVideo: 0" in layer_source
    assert "watermark: 20" in layer_source
    assert "avatar: 25" in layer_source
    assert "avatarTheater: 30" in layer_source
    assert "avatarControls: 40" in layer_source
    assert "videoControls: 50" in layer_source
    assert "captions: 60" in layer_source
    assert "data-testid=\"player-caption-layer\"" in video_stage_source
    assert "style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.captions }}" in video_stage_source
    assert "data-testid=\"player-caption-layer\"" in hls_source
    assert "style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.captions }}" in hls_source
    assert "selectedTextTrack.mode = 'hidden'" in video_stage_source
    assert "selectedTextTrack.mode = 'hidden'" in hls_source


def test_frontend_avatar_controls_are_hidden_by_default():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "data-testid=\"avatar-overlay-controls\"" in source
    assert "data-controls-visible={controlsVisible || dragging ? 'true' : 'false'}" in source
    assert "visible ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'" in source
    assert "transition-opacity duration-200 ease-out" in source


def test_frontend_avatar_controls_appear_on_hover_focus_and_tap():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "onPointerEnter={handleFramePointerEnter}" in source
    assert "onPointerDown={handleFramePointerDown}" in source
    assert "onFocus={handleFrameFocus}" in source
    assert "showControls({ autoHide: event.pointerType !== 'mouse' })" in source
    assert "window.setTimeout" in source
    assert "2600" in source


def test_frontend_avatar_overlay_hide_show_persists_locally():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "storageKey(lessonId, 'visible')" in source
    assert "setAvatarVisible(true)" in source
    assert "setAvatarVisible(false)" in source
    assert "Show avatar" in source
    assert "Hide avatar" in source


def test_frontend_hidden_avatar_renders_compact_show_control():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "title=\"Show avatar\"" in source
    assert "aria-label=\"Show avatar\"" in source
    assert "right-3 top-3" in source
    assert "pointer-events-auto inline-flex items-center gap-2 rounded-full" in source


def test_frontend_avatar_drag_position_is_clamped():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "export function clampAvatarPlacement" in source
    assert "Math.max(0, 1 - width)" in source
    assert "window.addEventListener('pointermove'" in source
    assert "data-testid=\"avatar-drag-handle\"" in source
    assert "writeStoredPlacement(lessonId, clamped)" in source


def test_frontend_avatar_reset_restores_default_position():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "Reset avatar position" in source
    assert "setCurrentPlacement(defaultPlacement)" in source
    assert "clearStoredPlacement(lessonId)" in source


def test_frontend_avatar_theater_does_not_persist_and_keeps_caption_contract():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "data-testid=\"avatar-theater-overlay\"" in source
    assert "pointer-events-none absolute inset-x-3 top-3 bottom-16" in source
    assert "data-avatar-theater-frame=\"true\"" in source
    assert "fixed inset-0" not in source
    assert "setTheaterOpen(false)" in source
    assert "avatarTheater: 30" in source
    assert "videoControls: 50" in source
    assert "captions: 60" in source
    assert "storageKey(lessonId, 'theater')" not in source


def test_frontend_avatar_theater_uses_larger_safe_crop():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "data-avatar-video-mode={theater ? 'theater' : 'pip'}" in source
    assert "theater ? 'scale-110 object-[50%_32%]' : ''" in source
    assert "max-w-[96%]" in source
    assert "aspect-video max-h-full w-full" in source


def test_frontend_watch_exposes_single_focus_mode_for_study_layout():
    source = _frontend_source("pages", "Watch.jsx")

    assert "Focus Mode" in source
    assert source.count("Focus Mode") == 1
    assert "focusModeKey" in source
    assert "Study Mode" not in source
    assert "handleStudyModeToggle" not in source
    assert "setStudyMode" not in source
    assert "xl:grid-cols-[minmax(0,4fr)_minmax(16rem,1fr)]" in source
    assert "mode=\"study-panel\"" in source
    assert "data-testid=\"study-mode-panel\"" in source
    assert "data-testid=\"study-mode-notes\"" in source
    assert "avatarOverlayMode={focusMode ? 'disabled' : 'floating'}" in source


def test_frontend_studio_hides_advanced_avatar_runtime_and_placement_controls():
    source = _frontend_source("pages", "Studio.jsx")

    assert "Motion style" not in source
    assert "Restoration" not in source
    assert "LivePortrait" not in source
    assert "Save avatar settings" not in source
    assert "Save placement" not in source
    assert "avatarPlacement" not in source
    assert "const [avatarRuntimeSettings" not in source
    assert "setAvatarRuntimeSettings" not in source


def test_frontend_studio_keeps_avatar_only_rerender_button():
    source = _frontend_source("pages", "Studio.jsx")

    assert "rerenderProjectAvatar" in source
    assert "handleAvatarOnlyRerender" in source
    assert "Rerender avatar only" in source


def test_frontend_secure_playback_path_keeps_hls_and_heartbeat():
    watch_source = _frontend_source("pages", "Watch.jsx")
    hls_source = _frontend_source("components", "player", "HlsPlayer.jsx")

    assert "usePlaybackHeartbeat" in watch_source
    assert "PLAYER_MODES.SECURE_HLS" in watch_source
    assert "<HlsPlayer" in watch_source
    assert "import Hls from 'hls.js'" in hls_source
    assert "hls.loadSource(sourceUrl)" in hls_source
    assert "new Hls({ enableWorker: true })" in hls_source
    assert "avatarOverlayMode = 'floating'" in hls_source
    assert "avatarOverlay?.enabled && avatarStreamUrl" in hls_source


def test_frontend_no_avatar_lesson_does_not_render_overlay_layer():
    source = _frontend_source("components", "player", "AvatarOverlayLayer.jsx")

    assert "if (!enabled || !src) return null;" in source


def test_avatar_overlay_preference_persists_per_user_and_lesson():
    if not _table_has_column("core_project", "avatar_enabled_override"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"owner_{suffix}", password="pass")
    student = User.objects.create_user(username=f"student_{suffix}", password="pass")
    lesson = Project.objects.create(
        title="With Avatar",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100)

    factory = APIRequestFactory()
    request = factory.put(
        f"/api/v1/projects/{lesson.id}/avatar-overlay/",
        {
            "anchor": "custom",
            "x_percent": 61.5,
            "y_percent": 14.0,
            "width_percent": 27.0,
            "visible": True,
            "pinned": False,
        },
        format="json",
    )
    force_authenticate(request, user=student)
    response = views.AvatarOverlayPreferenceView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert float(response.data["x_percent"]) == 61.5
    assert response.data["anchor"] == "custom"

    get_request = factory.get(f"/api/v1/projects/{lesson.id}/avatar-overlay/")
    force_authenticate(get_request, user=student)
    get_response = views.AvatarOverlayPreferenceView.as_view()(get_request, project_id=lesson.id)

    assert get_response.status_code == 200
    assert float(get_response.data["width_percent"]) == 27.0
    assert get_response.data["pinned"] is False


def test_avatar_overlay_default_placement_is_top_right_medium():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"default_place_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(
        title="Default Placement",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100, result_url="default.mp4")

    factory = APIRequestFactory()
    request = _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/"))
    with override_settings(LESSON_PROTECTION_DEFAULT_MODE="public"):
        response = views.CatalogDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    placement = response.data["avatar_overlay"]["placement"]
    assert placement["position"] == "top-right"
    assert placement["size"] == "medium"
    assert placement["x"] == 0.72
    assert placement["y"] == 0.08
    assert placement["width"] == 0.24


def test_avatar_overlay_placement_saves_and_returns_normalized_payload():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"place_owner_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(title="Placed Avatar", user=teacher, status="ready")
    Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100, result_url="placed.mp4")

    factory = APIRequestFactory()
    request = factory.put(
        f"/api/v1/projects/{lesson.id}/avatar-overlay/",
        {"avatar_placement": {"position": "bottom-left", "size": "large"}},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.AvatarOverlayPreferenceView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    placement = response.data["avatar_placement"]
    assert placement["position"] == "bottom-left"
    assert placement["size"] == "large"
    assert placement["width"] == 0.3
    assert placement["x"] == 0.04
    assert placement["y"] == pytest.approx(0.7513)


def test_avatar_overlay_custom_coordinates_are_clamped():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"place_clamp_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(title="Clamped Avatar", user=teacher, status="ready")

    factory = APIRequestFactory()
    request = factory.put(
        f"/api/v1/projects/{lesson.id}/avatar-overlay/",
        {"avatar_placement": {"position": "custom", "x": 2, "y": -5, "width": 0.6}},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.AvatarOverlayPreferenceView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    placement = response.data["avatar_placement"]
    assert placement["position"] == "custom"
    assert placement["size"] == "large"
    assert placement["width"] == 0.35
    assert placement["x"] == 0.65
    assert placement["y"] == 0


def test_avatar_overlay_invalid_position_is_normalized():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"place_invalid_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(title="Invalid Placement", user=teacher, status="ready")

    factory = APIRequestFactory()
    request = factory.put(
        f"/api/v1/projects/{lesson.id}/avatar-overlay/",
        {"avatar_placement": {"position": "center", "size": "medium"}},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.AvatarOverlayPreferenceView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["avatar_placement"]["position"] == "top-right"
    assert response.data["avatar_placement"]["size"] == "medium"


def test_project_patch_updates_avatar_placement_without_render_job():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"place_patch_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(
        title="Patch Placement",
        user=teacher,
        status="ready",
        avatar_processing_status="ready",
        avatar_visible=True,
    )
    job = Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100, result_url="patch.mp4")

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/projects/{lesson.id}/",
        {"avatar_placement": {"position": "top-left", "size": "small"}},
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    lesson.refresh_from_db()
    assert lesson.avatar_processing_status == "ready"
    assert lesson.avatar_visible is True
    assert list(Job.objects.filter(project=lesson).values_list("id", flat=True)) == [job.id]
    assert response.data["avatar_placement"]["position"] == "top-left"
    assert response.data["avatar_placement"]["size"] == "small"
    pref = AvatarOverlayPreference.objects.get(user=teacher, lesson=lesson)
    assert pref.anchor == "top-left"


def test_playback_token_includes_saved_avatar_placement(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"place_token_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(
        title="Placement Token",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
    )
    Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100, result_url=f"{lesson.id}/{lesson.id}.mp4")
    AvatarOverlayPreference.objects.create(
        user=teacher,
        lesson=lesson,
        anchor="custom",
        x_percent=62,
        y_percent=12,
        width_percent=22,
    )

    factory = APIRequestFactory()
    request = _with_session(factory.get(f"/api/v1/projects/{lesson.id}/playback-token/"))
    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        response = views.PlaybackTokenView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    placement = response.data["avatar_overlay"]["placement"]
    assert placement == {
        "position": "custom",
        "size": "medium",
        "x": 0.62,
        "y": 0.12,
        "width": 0.22,
    }


def test_avatar_runtime_settings_default_to_safe_values(monkeypatch):
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_ENABLED", raising=False)
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"runtime_default_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(title="Runtime Defaults", user=teacher, status="ready")

    factory = APIRequestFactory()
    request = factory.get(f"/api/v1/projects/{lesson.id}/")
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["avatar_runtime_settings"] == {
        "motion_preset": "natural_conservative",
        "restoration_enabled": False,
        "liveportrait_enabled": True,
    }


def test_avatar_runtime_settings_patch_normalizes_unsafe_values_without_base_rerender():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"runtime_patch_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(title="Runtime Patch", user=teacher, status="ready")
    job = Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100, result_url="runtime.mp4")

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/projects/{lesson.id}/",
        {
            "avatar_runtime_settings": {
                "motion_preset": "expressive_debug",
                "restoration_enabled": True,
                "liveportrait_enabled": False,
            }
        },
        format="json",
    )
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["avatar_runtime_settings"] == {
        "motion_preset": "natural_conservative",
        "restoration_enabled": True,
        "liveportrait_enabled": False,
    }
    lesson.refresh_from_db()
    assert lesson.draft_data["metadata"]["dirty"] is False
    assert list(Job.objects.filter(project=lesson).values_list("id", flat=True)) == [job.id]


def test_avatar_runtime_settings_allow_natural_visible_but_not_expressive_debug():
    assert normalize_safe_avatar_motion_preset("natural_visible") == "natural_visible"
    assert normalize_safe_avatar_motion_preset("expressive_debug") == "natural_conservative"


def test_per_project_runtime_settings_are_passed_to_avatar_options(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"runtime_opts_{suffix}", password="pass")
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed="avatars/runtime/processed.png",
        avatar_source_valid=True,
        avatar_moderation_status="approved",
    )
    lesson = Project.objects.create(
        title="Runtime Options",
        user=teacher,
        status="ready",
        draft_data={
            "metadata": {
                "dirty": False,
                "avatar_runtime_settings": {
                    "motion_preset": "subtle_gaze",
                    "restoration_enabled": True,
                    "liveportrait_enabled": False,
                },
            }
        },
    )
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")

    options = views._resolve_avatar_options_for_project(lesson, type("Req", (), {"data": {}})())

    assert options["motion_preset"] == "subtle_gaze"
    assert options["restoration_enabled"] is True
    assert options["liveportrait_enabled"] is False
    assert options["avatar_runtime_settings"]["motion_preset"] == "subtle_gaze"


def test_playback_token_includes_avatar_runtime_settings(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"runtime_token_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(
        title="Runtime Token",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        draft_data={
            "metadata": {
                "dirty": False,
                "avatar_runtime_settings": {
                    "motion_preset": "subtle_blink",
                    "restoration_enabled": True,
                    "liveportrait_enabled": True,
                },
            }
        },
    )
    Job.objects.create(project=lesson, job_type="video_export", status="done", progress=100, result_url=f"{lesson.id}/{lesson.id}.mp4")

    factory = APIRequestFactory()
    request = _with_session(factory.get(f"/api/v1/projects/{lesson.id}/playback-token/"))
    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        response = views.PlaybackTokenView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["avatar_runtime_settings"] == {
        "motion_preset": "subtle_blink",
        "restoration_enabled": True,
        "liveportrait_enabled": True,
    }


def test_avatar_runtime_status_reports_static_fallback_warning():
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"runtime_status_{suffix}", password="pass")
    UserProfile.objects.create(user=teacher, role="teacher")
    lesson = Project.objects.create(title="Runtime Status", user=teacher, status="ready")
    AvatarRenderJob.objects.create(
        lesson=lesson,
        teacher=teacher,
        source_image_hash="i",
        tts_audio_hash="a",
        engine_used="liveportrait+musetalk",
        render_status="done",
        metadata={
            "avatar_engine_selected": "liveportrait+musetalk",
            "liveportrait_succeeded": False,
            "liveportrait_fallback_used": True,
            "musetalk_source_kind": "static_fallback",
        },
    )

    factory = APIRequestFactory()
    request = factory.get(f"/api/v1/projects/{lesson.id}/")
    force_authenticate(request, user=teacher)
    response = views.ProjectDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["avatar_runtime_status"]["static_fallback_used"] is True
    assert response.data["avatar_runtime_status"]["warning"] == "Avatar used static fallback because motion stage failed."


def test_avatar_visibility_hides_ready_artifact_without_deleting_it(tmp_path):
    if not _table_has_column("core_project", "avatar_visible"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"avatar_owner_{suffix}", password="pass")
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed="avatars/teacher/processed.png",
        avatar_source_valid=True,
        avatar_moderation_status="approved",
    )
    (tmp_path / "avatars" / "teacher").mkdir(parents=True)
    (tmp_path / "avatars" / "teacher" / "processed.png").write_bytes(b"source")

    lesson = Project.objects.create(
        title="Ready Avatar",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        avatar_enabled_override=True,
        avatar_processing_status="ready",
        avatar_visible=True,
        avatar_output_path="1/avatar/avatar_track.mp4",
    )
    lesson.avatar_output_path = f"{lesson.id}/avatar/avatar_track.mp4"
    lesson.save(update_fields=["avatar_output_path"])
    avatar_path = tmp_path / lesson.avatar_output_path
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"avatar-track")
    (tmp_path / str(lesson.id)).mkdir(parents=True, exist_ok=True)
    (tmp_path / str(lesson.id) / "playback_assets.json").write_text(
        json.dumps({"avatar": {"track_rel_path": lesson.avatar_output_path}}),
        encoding="utf-8",
    )
    Job.objects.create(
        project=lesson,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{lesson.id}/{lesson.id}.mp4",
    )

    factory = APIRequestFactory()
    request = factory.patch(
        f"/api/v1/projects/{lesson.id}/",
        {"avatar_visible": False},
        format="json",
    )
    force_authenticate(request, user=teacher)
    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectDetailView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    lesson.refresh_from_db()
    assert lesson.avatar_visible is False
    assert lesson.avatar_processing_status == "ready"
    assert avatar_path.exists()

    public_request = _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/"))
    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        public_response = views.CatalogDetailView.as_view()(public_request, project_id=lesson.id)

    assert public_response.status_code == 200
    assert public_response.data["avatar_overlay"]["enabled"] is False
    assert public_response.data["avatar_available"] is False


def test_watch_payload_exposes_avatar_only_when_visible_and_ready(tmp_path):
    if not _table_has_column("core_project", "avatar_processing_status"):
        pytest.skip("Local DB schema is stale; run migrations to execute this test.")

    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"watch_avatar_{suffix}", password="pass")
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed="avatars/watch/processed.png",
        avatar_source_valid=True,
        avatar_moderation_status="approved",
    )
    (tmp_path / "avatars" / "watch").mkdir(parents=True)
    (tmp_path / "avatars" / "watch" / "processed.png").write_bytes(b"source")

    lesson = Project.objects.create(
        title="Watch Avatar",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        avatar_enabled_override=True,
        avatar_processing_status="queued",
        avatar_visible=True,
        avatar_processing_message="Avatar is still processing and will be added when ready.",
    )
    Job.objects.create(
        project=lesson,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{lesson.id}/{lesson.id}.mp4",
    )
    sidecar_path = tmp_path / str(lesson.id) / "playback_assets.json"
    sidecar_path.parent.mkdir(parents=True)
    sidecar_path.write_text(json.dumps({"avatar": {"track_rel_path": f"{lesson.id}/avatar/avatar_track.mp4"}}), encoding="utf-8")

    factory = APIRequestFactory()
    request = _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/"))
    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        queued_response = views.CatalogDetailView.as_view()(request, project_id=lesson.id)

    assert queued_response.status_code == 200
    assert queued_response.data["avatar_processing_status"] == "queued"
    assert queued_response.data["avatar_overlay"]["enabled"] is False

    lesson.avatar_processing_status = "ready"
    lesson.avatar_output_path = f"{lesson.id}/avatar/avatar_track.mp4"
    lesson.save(update_fields=["avatar_processing_status", "avatar_output_path"])
    avatar_path = tmp_path / lesson.avatar_output_path
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"avatar-track")

    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        ready_response = views.CatalogDetailView.as_view()(
            _with_session(factory.get(f"/api/v1/catalog/{lesson.id}/")),
            project_id=lesson.id,
        )

    assert ready_response.status_code == 200
    assert ready_response.data["avatar_available"] is True
    assert ready_response.data["avatar_overlay"]["enabled"] is True


def _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch, runtime_settings=None):
    suffix = uuid.uuid4().hex[:8]
    teacher = User.objects.create_user(username=f"avatar_only_{suffix}", password="pass")
    source_rel = f"avatars/{suffix}/processed.png"
    UserProfile.objects.create(
        user=teacher,
        role="teacher",
        avatar_enabled=True,
        avatar_consent_confirmed=True,
        avatar_image_processed=source_rel,
        avatar_source_valid=True,
        avatar_source_hash="source-hash",
        avatar_moderation_status="approved",
    )
    source_path = tmp_path / source_rel
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"avatar-source")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", "echo liveportrait")
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo musetalk")
    monkeypatch.setattr(
        views,
        "stored_avatar_source_state",
        lambda *_args, **_kwargs: {
            "valid": True,
            "validation_current": True,
            "error": "",
            "source_hash": "source-hash",
            "preview_stale": False,
            "preview_source_hash": "preview-hash",
        },
    )

    metadata = {"dirty": False}
    if runtime_settings:
        metadata["avatar_runtime_settings"] = runtime_settings
    lesson = Project.objects.create(
        title=f"Avatar only {suffix}",
        user=teacher,
        status="ready",
        moderation_status="approved",
        is_published=True,
        avatar_enabled_override=True,
        avatar_processing_status="ready",
        avatar_visible=True,
        draft_data={"metadata": metadata},
    )
    base_dir = tmp_path / str(lesson.id)
    audio_path = base_dir / "audio" / "slide_001.mp3"
    slide_path = base_dir / "images" / "slide_001.png"
    video_path = base_dir / f"{lesson.id}.mp4"
    for path in [audio_path, slide_path, video_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    sidecar = {
        "mp4_rel_path": f"{lesson.id}/{lesson.id}.mp4",
        "hls": {"enabled": False, "packaging_status": "not_required"},
        "avatar": None,
        "final_segments": [
            {
                "index": 0,
                "slide": f"{lesson.id}/images/slide_001.png",
                "transcript": "Avatar only segment.",
                "tts_audio": f"{lesson.id}/audio/slide_001.mp3",
                "duration": 1.25,
                "pause_seconds": 0.0,
                "part_rel_path": f"{lesson.id}/parts/part_001.mp4",
            }
        ],
    }
    (base_dir / "playback_assets.json").write_text(json.dumps(sidecar), encoding="utf-8")
    base_job = Job.objects.create(
        project=lesson,
        job_type="video_export",
        status="done",
        progress=100,
        result_url=f"{lesson.id}/{lesson.id}.mp4",
    )
    return teacher, lesson, base_job


def test_avatar_only_rerender_enqueues_avatar_task_without_base_rerender(tmp_path, monkeypatch):
    runtime_settings = {
        "motion_preset": "subtle_gaze",
        "restoration_enabled": True,
        "liveportrait_enabled": False,
    }
    teacher, lesson, base_job = _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch, runtime_settings)
    pref = AvatarOverlayPreference.objects.create(
        user=teacher,
        lesson=lesson,
        anchor="bottom-left",
        x_percent=4,
        y_percent=74,
        width_percent=30,
    )
    captured = {}

    def fake_dispatch(task_name, *, args=None, kwargs=None, queue=None):
        captured["task_name"] = task_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        captured["queue"] = queue
        return type("AsyncResult", (), {"id": "avatar-only-task-1"})()

    monkeypatch.setattr(views, "_dispatch_celery_task", fake_dispatch)
    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/projects/{lesson.id}/avatar/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path), CELERY_AVATAR_QUEUE="avatar"):
        response = views.ProjectAvatarRerenderView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 202
    assert response.data["avatar_processing_status"] == "queued"
    assert response.data["avatar_job_id"]
    assert captured["task_name"] == "worker.tasks.render_lesson_avatar_overlay"
    assert captured["queue"] == "avatar"
    assert Job.objects.filter(project=lesson, job_type="video_export").count() == 1
    avatar_job = Job.objects.get(project=lesson, job_type="avatar_render")
    assert avatar_job.celery_task_id == "avatar-only-task-1"
    task_kwargs = captured["kwargs"]
    assert task_kwargs["base_job_id"] == base_job.id
    assert "render_results" not in task_kwargs
    assert "avatar_options" not in task_kwargs

    manifest = json.loads(Path(task_kwargs["handoff_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["base_job_id"] == base_job.id
    assert manifest["avatar_job_id"] == avatar_job.id
    assert manifest["avatar_settings"]["motion_preset"] == "subtle_gaze"
    assert manifest["avatar_settings"]["restoration_enabled"] is True
    assert manifest["avatar_settings"]["liveportrait_enabled"] is False
    assert manifest["avatar_settings"]["avatar_runtime_settings"] == runtime_settings
    assert Path(manifest["ordered_results"][0]["tts_audio_path"]).exists()
    assert manifest["render_metadata"]["avatar_only_rerender"] is True

    lesson.refresh_from_db()
    assert lesson.avatar_processing_status == "queued"
    assert lesson.avatar_last_job_id == str(avatar_job.id)
    pref.refresh_from_db()
    assert pref.anchor == "bottom-left"
    assert float(pref.width_percent) == 30.0


def test_avatar_only_rerender_rejects_missing_playback_assets(tmp_path, monkeypatch):
    teacher, lesson, _base_job = _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch)
    (tmp_path / str(lesson.id) / "playback_assets.json").unlink()
    monkeypatch.setattr(
        views,
        "_dispatch_celery_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("avatar task should not be queued")),
    )

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/projects/{lesson.id}/avatar/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectAvatarRerenderView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 409
    assert response.data["error"] == "Playback assets are not ready."
    assert Job.objects.filter(project=lesson, job_type="avatar_render").count() == 0


def test_avatar_only_rerender_rejects_missing_base_render(tmp_path, monkeypatch):
    teacher, lesson, base_job = _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch)
    base_job.delete()
    monkeypatch.setattr(
        views,
        "_dispatch_celery_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("avatar task should not be queued")),
    )

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/projects/{lesson.id}/avatar/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectAvatarRerenderView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 409
    assert response.data["error"] == "Base lesson render is not ready."
    assert Job.objects.filter(project=lesson, job_type="avatar_render").count() == 0


def test_avatar_only_rerender_does_not_duplicate_active_avatar_job(tmp_path, monkeypatch):
    teacher, lesson, _base_job = _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch)
    lesson.avatar_processing_status = "processing"
    lesson.avatar_last_job_id = "existing-avatar-job"
    lesson.save(update_fields=["avatar_processing_status", "avatar_last_job_id"])
    monkeypatch.setattr(
        views,
        "_dispatch_celery_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate avatar task should not be queued")),
    )

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/projects/{lesson.id}/avatar/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectAvatarRerenderView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["avatar_processing_status"] == "processing"
    assert response.data["avatar_job_id"] == "existing-avatar-job"
    assert Job.objects.filter(project=lesson, job_type="avatar_render").count() == 0


def test_avatar_only_rerender_rejects_invalid_avatar_prerequisites(tmp_path, monkeypatch):
    teacher, lesson, _base_job = _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch)
    monkeypatch.setattr(
        views,
        "_resolve_avatar_options_for_project",
        lambda *_args, **_kwargs: {
            "requested": True,
            "enabled": False,
            "disabled_reason": "avatar_source_invalid",
            "teacher_id": teacher.id,
        },
    )
    monkeypatch.setattr(
        views,
        "_dispatch_celery_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("invalid avatar should not be queued")),
    )

    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/projects/{lesson.id}/avatar/rerender/", {}, format="json")
    force_authenticate(request, user=teacher)

    with override_settings(STORAGE_ROOT=str(tmp_path)):
        response = views.ProjectAvatarRerenderView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 400
    assert response.data["error"] == "avatar_source_invalid"
    assert Job.objects.filter(project=lesson, job_type="avatar_render").count() == 0
    lesson.refresh_from_db()
    assert lesson.avatar_processing_status == "failed"


def test_playback_token_uses_base_video_job_while_avatar_rerender_is_queued(tmp_path, monkeypatch):
    teacher, lesson, base_job = _setup_avatar_only_rerender_lesson(tmp_path, monkeypatch)
    Job.objects.create(
        project=lesson,
        job_type="avatar_render",
        status="pending",
        progress=0,
        result_url=f"{lesson.id}/avatar/avatar_track.mp4",
    )
    lesson.avatar_processing_status = "queued"
    lesson.avatar_last_job_id = "avatar-job"
    lesson.save(update_fields=["avatar_processing_status", "avatar_last_job_id"])
    issued_tokens = []

    def fake_generate_media_token(job_id, file_type, **kwargs):
        issued_tokens.append({"job_id": job_id, "file_type": file_type, **kwargs})
        return f"{file_type}-token"

    monkeypatch.setattr(views, "generate_media_token", fake_generate_media_token)
    factory = APIRequestFactory()
    request = _with_session(factory.get(f"/api/v1/projects/{lesson.id}/playback-token/"))

    with override_settings(STORAGE_ROOT=str(tmp_path), LESSON_PROTECTION_DEFAULT_MODE="public"):
        response = views.PlaybackTokenView.as_view()(request, project_id=lesson.id)

    assert response.status_code == 200
    assert response.data["video_url"]
    assert response.data["avatar_processing_status"] == "queued"
    video_tokens = [token for token in issued_tokens if token["file_type"] == "video"]
    assert video_tokens
    assert video_tokens[0]["job_id"] == base_job.id
