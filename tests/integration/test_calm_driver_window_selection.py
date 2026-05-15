from __future__ import annotations

import importlib
from pathlib import Path

import pytest

runner = importlib.import_module("scripts.liveportrait_runner")
canonical_pipeline = importlib.import_module("services.avatar.canonical_pipeline")


def test_select_calm_template_window_prefers_later_active_window(tmp_path, monkeypatch):
    template = tmp_path / "calm_driver.mp4"
    template.write_bytes(b"template")

    def fake_probe_duration(path, stream_selector="v:0"):
        return 40.0

    def fake_probe_segment(*, source_video, start_seconds, duration_seconds):
        if start_seconds <= 0.5:
            return 0.20
        if 8.0 <= start_seconds <= 10.0:
            return 0.52
        return 0.30

    monkeypatch.setattr(runner, "_probe_duration_seconds", fake_probe_duration)
    monkeypatch.setattr(runner, "_probe_clip_mean_mad_segment", fake_probe_segment)
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_ENABLED", "1")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_STEP_SECONDS", "2")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_MIN_MAD", "0.40")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_MAX_MAD", "0.70")

    choice = runner._select_calm_template_window_start(
        source_video=template,
        target_duration_seconds=7.6,
        segment_index=1,
        audio_hash="audio-a",
        page_key="slide-001",
    )

    assert choice["source"] == "sliding_window_probe"
    assert float(choice["start_seconds"]) >= 8.0
    assert float(choice["mean_mad"]) >= 0.40


def test_select_calm_template_window_is_deterministic_for_same_seed_inputs(tmp_path, monkeypatch):
    template = tmp_path / "calm_driver.mp4"
    template.write_bytes(b"template")

    monkeypatch.setattr(runner, "_probe_duration_seconds", lambda *_args, **_kwargs: 30.0)
    monkeypatch.setattr(
        runner,
        "_probe_clip_mean_mad_segment",
        lambda **_kwargs: 0.55 if float(_kwargs.get("start_seconds") or 0.0) >= 6.0 else 0.25,
    )
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_ENABLED", "1")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_STEP_SECONDS", "2")

    first = runner._select_calm_template_window_start(
        source_video=template,
        target_duration_seconds=7.6,
        segment_index=3,
        audio_hash="same-audio",
        page_key="page-7",
    )
    second = runner._select_calm_template_window_start(
        source_video=template,
        target_duration_seconds=7.6,
        segment_index=3,
        audio_hash="same-audio",
        page_key="page-7",
    )

    assert first == second


def test_musetalk_stage_cache_key_changes_when_handoff_hash_changes(tmp_path):
    class _Req:
        source_image_path = str(tmp_path / "face.png")
        source_image_original_path = str(tmp_path / "face.png")
        audio_path = str(tmp_path / "audio.wav")
        lipsync_engine = "liveportrait+musetalk"
        avatar_reference_type = "image"
        target_frame_count = 0
        target_duration_seconds = 7.6
        cache_text_hash = ""

    (tmp_path / "face.png").write_bytes(b"face")
    (tmp_path / "audio.wav").write_bytes(b"audio")

    first = canonical_pipeline._musetalk_stage_cache_keys(
        _Req(),
        "liveportrait+musetalk",
        handoff_video_hash="handoff-a",
    )
    second = canonical_pipeline._musetalk_stage_cache_keys(
        _Req(),
        "liveportrait+musetalk",
        handoff_video_hash="handoff-b",
    )

    assert first["musetalk_handoff_video_hash"] != second["musetalk_handoff_video_hash"]


def test_liveportrait_stage_cache_includes_calm_window_version(tmp_path, monkeypatch):
    class _Req:
        source_image_path = str(tmp_path / "face.png")
        source_image_original_path = str(tmp_path / "face.png")
        audio_path = str(tmp_path / "audio.wav")
        lipsync_engine = "liveportrait+musetalk"
        avatar_reference_type = "image"
        target_frame_count = 0
        target_duration_seconds = 7.6
        cache_text_hash = ""

    (tmp_path / "face.png").write_bytes(b"face")
    (tmp_path / "audio.wav").write_bytes(b"audio")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_CACHE_VERSION", "9")

    keys = canonical_pipeline._liveportrait_stage_cache_keys(_Req(), "liveportrait+musetalk")
    assert keys["liveportrait_calm_window_cache_version"] == "9"


def test_musetalk_history_timeout_ignores_outlier_samples(monkeypatch):
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_HISTORY_MAX_ELAPSED_SECONDS", "3600")
    estimate = canonical_pipeline._musetalk_history_timeout_estimate(
        records=[
            {
                "total_elapsed_seconds": 56483.0,
                "frame_count": 188,
                "audio_duration_seconds": 7.6,
                "chunk_count": 1,
                "gpu_total_mib": 4096,
            },
            {
                "total_elapsed_seconds": 120.0,
                "frame_count": 188,
                "audio_duration_seconds": 7.6,
                "chunk_count": 1,
                "gpu_total_mib": 4096,
            },
        ],
        duration_seconds=7.6,
        frame_count=188,
        chunk_count=1,
        gpu_total_mib=4096,
    )
    assert estimate["sample_count"] == 1
    assert estimate["max_seconds"] < 500.0
