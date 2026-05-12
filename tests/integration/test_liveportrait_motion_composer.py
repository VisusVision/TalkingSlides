from services.scripts import liveportrait_motion_composer as motion_composer
from services.scripts.liveportrait_motion_composer import (
    _DIRECTIONS,
    _build_motion_recipe,
    _build_schedule,
    profile_sequence_for_preset,
    resolve_motion_preset,
)


def test_default_motion_preset_is_natural_conservative(monkeypatch):
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", raising=False)

    recipe = _build_motion_recipe(8.0, seed=123)

    assert resolve_motion_preset(None) == "natural_conservative"
    assert recipe["motion_preset"] == "natural_conservative"
    assert recipe["whole_frame_drift_guard"] is True
    assert profile_sequence_for_preset(motion_preset=recipe["motion_preset"]) == ["default"]


def test_natural_conservative_motion_floor_is_localized_and_drift_guarded(monkeypatch):
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", raising=False)

    recipe = _build_motion_recipe(4.0, seed=42, motion_preset="natural_conservative")

    assert recipe["motion_preset"] == "natural_conservative"
    assert recipe["motion_profile"] == "default"
    assert recipe["blink_duration_s"] == 0.32
    assert recipe["blink_shift_px"] == 0.48
    assert recipe["head_shift_max_px"] == 0.95
    assert recipe["base_sway_x_px"] <= 0.060
    assert recipe["base_sway_y_px"] <= 0.045
    assert recipe["recenter_enabled"] is True
    assert recipe["whole_frame_drift_guard"] is True
    assert profile_sequence_for_preset(motion_preset="natural_conservative") == ["default"]


def test_subtle_blink_preset_is_blink_dominant():
    recipe = _build_motion_recipe(4.0, seed=42, motion_preset="subtle_blink")

    assert recipe["motion_preset"] == "subtle_blink"
    assert recipe["gaze_enabled"] is False
    assert list(recipe.get("gaze_events") or []) == []
    assert list(recipe.get("blink_events_s") or [])
    assert float(recipe["head_shift_max_px"]) <= 0.12
    assert float(recipe["base_sway_x_px"]) <= 0.018


def test_subtle_gaze_preset_uses_small_recentering_side_glance():
    recipe = _build_motion_recipe(4.0, seed=42, motion_preset="subtle_gaze")
    gaze_events = list(recipe.get("gaze_events") or [])

    assert recipe["motion_preset"] == "subtle_gaze"
    assert recipe["recenter_enabled"] is True
    assert recipe["whole_frame_drift_guard"] is True
    assert gaze_events
    assert all(str(evt.get("direction")) in {"left", "right"} for evt in gaze_events)
    assert all(bool(evt.get("recenter_enabled")) for evt in gaze_events)
    assert max(abs(float(evt.get("dx_px") or 0.0)) for evt in gaze_events) <= 0.48


def test_expressive_debug_allows_boosted_profiles():
    assert profile_sequence_for_preset(motion_preset="expressive_debug") == ["default", "boosted", "boosted_strong"]

    recipe = _build_motion_recipe(4.0, seed=42, motion_preset="expressive_debug", motion_profile="boosted")

    assert recipe["motion_preset"] == "expressive_debug"
    assert recipe["boosted_profile"] is True
    assert recipe["whole_frame_drift_guard"] is False


def test_nod_behavior_is_disabled():
    recipe = _build_motion_recipe(300.0, seed=123)

    assert recipe.get("nods_enabled") is False
    assert recipe.get("continuous_eye_wander_enabled") is False

    schedule = _build_schedule(300.0, seed=123)
    for segment_name, _duration in schedule:
        assert "nod" not in segment_name


def test_eye_motion_is_not_continuous():
    recipe = _build_motion_recipe(300.0, seed=42)
    gaze_events = list(recipe.get("gaze_events") or [])

    active_gaze_seconds = sum(float(evt.get("duration_s") or 0.0) for evt in gaze_events)
    assert active_gaze_seconds <= 20.0

    if len(gaze_events) >= 2:
        starts = [float(evt.get("start_s") or 0.0) for evt in gaze_events]
        gaps = [starts[idx] - starts[idx - 1] for idx in range(1, len(starts))]
        assert min(gaps) >= 35.0


def test_blink_schedule_is_around_configured_interval():
    recipe = _build_motion_recipe(90.0, seed=77)
    blink_intervals = list(recipe.get("blink_intervals_s") or [])

    assert blink_intervals
    for interval_s in blink_intervals:
        assert motion_composer._BLINK_MIN - 0.20 <= float(interval_s) <= motion_composer._BLINK_MAX + 0.20


def test_gaze_events_are_sparse_and_random_among_allowed_directions():
    recipe_a = _build_motion_recipe(260.0, seed=10)
    recipe_b = _build_motion_recipe(260.0, seed=20)

    dirs_a = [str(evt.get("direction") or "") for evt in list(recipe_a.get("gaze_events") or [])]
    dirs_b = [str(evt.get("direction") or "") for evt in list(recipe_b.get("gaze_events") or [])]

    assert all(direction in _DIRECTIONS for direction in dirs_a)
    assert all(direction in _DIRECTIONS for direction in dirs_b)
    assert len(dirs_a) <= int(260.0 / motion_composer._GAZE_MIN) + 2
    assert dirs_a != dirs_b


def test_gaze_duration_is_brief_then_returns_neutral():
    recipe = _build_motion_recipe(260.0, seed=91)
    gaze_events = list(recipe.get("gaze_events") or [])

    for evt in gaze_events:
        duration_s = float(evt.get("duration_s") or 0.0)
        assert 0.45 <= duration_s <= motion_composer._GAZE_DUR_MAX
        assert evt.get("recenter_enabled") is True
        assert float(evt.get("recenter_after_s") or 0.0) >= float(evt.get("start_s") or 0.0) + duration_s - 1e-4


def test_head_motion_stays_subtle():
    recipe = _build_motion_recipe(300.0, seed=99)
    gaze_events = list(recipe.get("gaze_events") or [])

    for evt in gaze_events:
        dx_px = abs(float(evt.get("dx_px") or 0.0))
        dy_px = abs(float(evt.get("dy_px") or 0.0))
        assert dx_px <= motion_composer._HEAD_SHIFT_MAX_PX + 1e-6
        assert dy_px <= motion_composer._HEAD_SHIFT_MAX_PX + 1e-6


def test_short_preview_bootstraps_early_motion():
    recipe = _build_motion_recipe(1.5, seed=42)
    gaze_events = list(recipe.get("gaze_events") or [])

    assert recipe.get("short_preview_mode") is True
    assert gaze_events
    assert list(recipe.get("blink_events_s") or [])
    assert float(gaze_events[0].get("start_s") or 0.0) <= 0.20


def test_motion_profile_boosted_increases_visible_motion_for_short_preview():
    default_recipe = _build_motion_recipe(2.2, seed=42, motion_profile="default")
    boosted_recipe = _build_motion_recipe(2.2, seed=42, motion_profile="boosted")

    assert float(boosted_recipe.get("head_shift_max_px") or 0.0) > float(default_recipe.get("head_shift_max_px") or 0.0)
    assert float(boosted_recipe.get("blink_shift_px") or 0.0) > float(default_recipe.get("blink_shift_px") or 0.0)
    assert float(boosted_recipe.get("base_sway_x_px") or 0.0) >= float(default_recipe.get("base_sway_x_px") or 0.0)
    assert boosted_recipe.get("motion_profile") == "boosted"


def test_compose_logs_motion_recipe_details(tmp_path, monkeypatch, capsys):
    src_image = tmp_path / "source.png"
    output_path = tmp_path / "out.mp4"
    src_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    def _fake_render(*, src_image, target_duration_s, fps, recipe, out):
        assert src_image.exists()
        assert target_duration_s >= 0.5
        assert fps > 0
        assert isinstance(recipe, dict)
        return True

    class _ProbeResult:
        returncode = 0
        stdout = '{"streams": [{"duration": "200.0", "nb_frames": "5000"}]}'
        stderr = ""

    monkeypatch.setattr(motion_composer, "_render_continuous_image_motion", _fake_render)
    monkeypatch.setattr(motion_composer.subprocess, "run", lambda *args, **kwargs: _ProbeResult())

    ok = motion_composer.compose(
        200.0,
        output_path,
        source_kind="image",
        source_image_path=src_image,
        seed=42,
        verbose=True,
    )

    assert ok is True
    stderr_text = capsys.readouterr().err
    assert "nods_disabled=1" in stderr_text
    assert "motion_preset=natural_conservative" in stderr_text
    assert "whole_frame_drift_guard=1" in stderr_text
    assert "blink_schedule_s=" in stderr_text
    assert "gaze_event index=" in stderr_text
    assert "motion_recipe=" in stderr_text


def test_compose_contract_logging_keeps_requested_duration_when_internal_fps_differs(tmp_path, monkeypatch, capsys):
    src_image = tmp_path / "source.png"
    output_path = tmp_path / "out.mp4"
    src_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    def _fake_render(*, src_image, target_duration_s, fps, recipe, out):
        assert src_image.exists()
        assert target_duration_s == 37.25
        assert fps == 25
        assert recipe.get("target_duration_s") == 37.25
        return True

    class _ProbeResult:
        returncode = 0
        stdout = '{"streams": [{"duration": "37.25", "nb_frames": "932"}]}'
        stderr = ""

    monkeypatch.setattr(motion_composer, "_render_continuous_image_motion", _fake_render)
    monkeypatch.setattr(motion_composer.subprocess, "run", lambda *args, **kwargs: _ProbeResult())

    ok = motion_composer.compose(
        37.25,
        output_path,
        source_kind="image",
        source_image_path=src_image,
        seed=42,
        verbose=True,
        requested_fps=16.0,
        target_frame_count=596,
        expected_duration_seconds=37.25,
        render_fps=25,
    )

    assert ok is True
    stderr_text = capsys.readouterr().err
    assert "requested_fps=16.0000" in stderr_text
    assert "internal_fps=25" in stderr_text
    assert "target_frame_count=596" in stderr_text
    assert "target_duration_seconds=37.2500" in stderr_text
    assert "expected_duration_seconds=37.2500" in stderr_text
