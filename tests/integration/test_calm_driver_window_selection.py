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


def test_liveportrait_stage_cache_key_changes_when_calm_window_version_changes(tmp_path, monkeypatch):
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

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_CACHE_VERSION", "1")
    first = canonical_pipeline._liveportrait_stage_cache_keys(_Req(), "liveportrait+musetalk")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_CACHE_VERSION", "2")
    second = canonical_pipeline._liveportrait_stage_cache_keys(_Req(), "liveportrait+musetalk")

    assert first["liveportrait_calm_window_cache_version"] == "1"
    assert second["liveportrait_calm_window_cache_version"] == "2"
    assert first != second


def _calm_stage_cache_keys() -> dict[str, str]:
    return {
        "avatar_stage_cache_version": "1",
        "audio_hash": "audio",
        "source_image_hash": "image",
        "source_image_original_hash": "image-original",
        "request_source_key": "image",
        "target_frame_count": "190",
        "target_duration_seconds": "7.608000",
        "liveportrait_motion_preset": "natural_conservative",
        "liveportrait_driver_source_policy": "calm_template_for_image",
        "liveportrait_calm_template_hash": "calm-hash",
        "liveportrait_calm_template_basename": "calm_driver.mp4",
        "liveportrait_calm_template_path_marker": "path-marker",
        "liveportrait_calm_template_min_mad": "0.32",
        "liveportrait_calm_window_cache_version": "1",
        "liveportrait_vetted_template_fallback_allowed": "1",
        "liveportrait_composer_fallback_allowed": "0",
        "liveportrait_vetted_image_template_hash": "d11-hash",
        "liveportrait_template_motion_strength": "",
        "liveportrait_template_temporal_smoothing": "",
        "liveportrait_template_speed": "1.0",
    }


def _valid_calm_stage_paths(**overrides) -> dict:
    payload = {
        "liveportrait_driver_source_policy": "calm_template_for_image",
        "liveportrait_driver_source": "template",
        "liveportrait_template_used": "calm_driver.mp4",
        "liveportrait_calm_template_path": "/app/storage_local/avatar_templates/review/calm_driver.mp4",
        "liveportrait_calm_template_used": True,
        "liveportrait_vetted_template_fallback_used": False,
        "liveportrait_composer_used": False,
        "liveportrait_composer_fallback_used": False,
        "liveportrait_calm_template_window_start": 46.0,
        "liveportrait_calm_template_window_duration": 7.608,
        "liveportrait_calm_template_window_mean_mad": 0.631,
        "liveportrait_calm_template_window_materialized_mean_mad": 0.631,
        "liveportrait_calm_template_min_mad": 0.32,
        "liveportrait_calm_template_window_accepted_by_profile": True,
        "musetalk_source_kind": "liveportrait",
    }
    payload.update(overrides)
    return payload


def test_d11_liveportrait_stage_cache_is_rejected_for_calm_request(tmp_path):
    artifact = tmp_path / "avatar.liveportrait.mp4"
    artifact.write_bytes(b"old-d11-liveportrait")
    keys = _calm_stage_cache_keys()

    canonical_pipeline._write_stage_cache_meta(
        artifact_path=artifact,
        stage="liveportrait",
        cache_keys=keys,
        stage_paths=_valid_calm_stage_paths(
            liveportrait_template_used="d11.mp4",
            liveportrait_calm_template_used=False,
            liveportrait_vetted_template_fallback_used=True,
            liveportrait_calm_template_path="",
        ),
    )

    assert not canonical_pipeline._stage_cache_matches(
        artifact_path=artifact,
        cache_keys=keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )


def test_blank_liveportrait_stage_cache_metadata_is_rejected_for_calm_request(tmp_path):
    artifact = tmp_path / "avatar.liveportrait.mp4"
    artifact.write_bytes(b"blank-provenance")
    keys = _calm_stage_cache_keys()

    canonical_pipeline._write_stage_cache_meta(
        artifact_path=artifact,
        stage="liveportrait",
        cache_keys=keys,
    )

    assert not canonical_pipeline._stage_cache_matches(
        artifact_path=artifact,
        cache_keys=keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )


def test_calm_liveportrait_stage_cache_requires_matching_path_hash_and_window_metadata(tmp_path):
    artifact = tmp_path / "avatar.liveportrait.mp4"
    artifact.write_bytes(b"calm-liveportrait")
    keys = _calm_stage_cache_keys()

    canonical_pipeline._write_stage_cache_meta(
        artifact_path=artifact,
        stage="liveportrait",
        cache_keys=keys,
        stage_paths=_valid_calm_stage_paths(),
    )

    assert canonical_pipeline._stage_cache_matches(
        artifact_path=artifact,
        cache_keys=keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )

    wrong_path_keys = dict(keys)
    wrong_path_keys["liveportrait_calm_template_path_marker"] = "other-path"
    assert not canonical_pipeline._stage_cache_matches(
        artifact_path=artifact,
        cache_keys=wrong_path_keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )

    wrong_hash_keys = dict(keys)
    wrong_hash_keys["liveportrait_calm_template_hash"] = "other-hash"
    assert not canonical_pipeline._stage_cache_matches(
        artifact_path=artifact,
        cache_keys=wrong_hash_keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )

    no_window_artifact = tmp_path / "avatar.no_window.liveportrait.mp4"
    no_window_artifact.write_bytes(b"calm-liveportrait-no-window")
    canonical_pipeline._write_stage_cache_meta(
        artifact_path=no_window_artifact,
        stage="liveportrait",
        cache_keys=keys,
        stage_paths=_valid_calm_stage_paths(liveportrait_calm_template_window_duration=0.0),
    )
    assert not canonical_pipeline._stage_cache_matches(
        artifact_path=no_window_artifact,
        cache_keys=keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )


def test_musetalk_stage_cache_rejects_changed_handoff_hash(tmp_path):
    artifact = tmp_path / "avatar.musetalk.mp4"
    artifact.write_bytes(b"musetalk")
    keys = {
        "audio_hash": "audio",
        "source_image_hash": "image",
        "source_image_original_hash": "image-original",
        "target_frame_count": "190",
        "target_duration_seconds": "7.608000",
        "musetalk_handoff_video_hash": "handoff-a",
    }

    canonical_pipeline._write_stage_cache_meta(
        artifact_path=artifact,
        stage="musetalk",
        cache_keys=keys,
        stage_paths=_valid_calm_stage_paths(),
    )

    changed = dict(keys)
    changed["musetalk_handoff_video_hash"] = "handoff-b"
    assert not canonical_pipeline._stage_cache_matches(
        artifact_path=artifact,
        cache_keys=changed,
        stage="musetalk",
    )


def test_stage_cache_provenance_is_restored_from_sidecar(tmp_path):
    artifact = tmp_path / "avatar.liveportrait.mp4"
    artifact.write_bytes(b"calm-liveportrait")
    keys = _calm_stage_cache_keys()
    canonical_pipeline._write_stage_cache_meta(
        artifact_path=artifact,
        stage="liveportrait",
        cache_keys=keys,
        stage_paths=_valid_calm_stage_paths(
            liveportrait_calm_template_window_start=12.0,
            liveportrait_calm_template_window_materialized_mean_mad=0.448,
        ),
    )
    meta_payload = canonical_pipeline._load_matching_stage_cache_meta(
        artifact_path=artifact,
        cache_keys=keys,
        stage="liveportrait",
        require_driver_provenance=True,
    )

    stage_paths = {}
    canonical_pipeline._apply_stage_cache_driver_provenance(stage_paths, meta_payload)

    assert stage_paths["liveportrait_calm_template_used"] is True
    assert stage_paths["liveportrait_template_used"] == "calm_driver.mp4"
    assert stage_paths["liveportrait_vetted_template_fallback_used"] is False
    assert stage_paths["liveportrait_composer_used"] is False
    assert stage_paths["liveportrait_calm_template_window_start"] == pytest.approx(12.0)
    assert stage_paths["liveportrait_calm_template_window_materialized_mean_mad"] == pytest.approx(0.448)


def test_calm_window_choice_order_tries_higher_probe_mad_before_lower(tmp_path, monkeypatch):
    template = tmp_path / "calm_driver.mp4"
    template.write_bytes(b"template")

    monkeypatch.setattr(runner, "_probe_duration_seconds", lambda *_args, **_kwargs: 40.0)
    monkeypatch.setattr(
        runner,
        "_probe_clip_mean_mad_segment",
        lambda **_kwargs: 0.52 if float(_kwargs.get("start_seconds") or 0.0) >= 8.0 else 0.41,
    )
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_ENABLED", "1")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_STEP_SECONDS", "2")

    order = runner._calm_template_window_choice_order(
        source_video=template,
        target_duration_seconds=7.6,
        segment_index=1,
        audio_hash="audio-a",
        page_key="slide-001",
    )

    assert float(order[0]["start_seconds"]) >= 8.0
    assert float(order[0]["mean_mad"]) >= 0.40


def test_calm_window_choice_order_prefers_ideal_band_before_subtle_passable_window(tmp_path, monkeypatch):
    template = tmp_path / "calm_driver.mp4"
    template.write_bytes(b"template")

    monkeypatch.setattr(runner, "_probe_duration_seconds", lambda *_args, **_kwargs: 60.0)
    monkeypatch.setattr(
        runner,
        "_probe_clip_mean_mad_segment",
        lambda **_kwargs: 0.631 if float(_kwargs.get("start_seconds") or 0.0) >= 46.0 else 0.328,
    )
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_ENABLED", "1")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_STEP_SECONDS", "2")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_MIN_MAD", "0.40")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_WINDOW_MAX_MAD", "0.70")

    order = runner._calm_template_window_choice_order(
        source_video=template,
        target_duration_seconds=7.6,
        segment_index=1,
        audio_hash="audio-a",
        page_key="slide-001",
    )

    assert float(order[0]["start_seconds"]) >= 46.0
    assert float(order[0]["mean_mad"]) == pytest.approx(0.631)


def test_calm_template_validation_profile_accepts_sub_global_mad(monkeypatch):
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_TEMPLATE_MIN_MAD", "0.32")
    metrics = {
        "unique_frames": 188,
        "unique_ratio": 1.0,
        "mean_mad": 0.328,
        "near_static": True,
        "valid": False,
        "technical_valid": True,
        "technical_failure_reason": "",
        "failure_reason": "driver_near_static:mean_mad=0.328<min_0.35",
        "validation_failure_reason": "driver_invalid:driver_near_static:mean_mad=0.328<min_0.35",
    }

    adjusted = runner._apply_calm_template_validation_profile(metrics)

    assert adjusted["valid"] is True
    assert adjusted["near_static"] is False
    assert adjusted["liveportrait_calm_template_min_mad"] == pytest.approx(0.32)
    assert adjusted["liveportrait_calm_template_window_accepted_by_profile"] is True
    assert adjusted["liveportrait_driver_near_static_threshold_profile"] == "calm_template_materialized_motion"


def test_calm_template_validation_profile_rejects_below_calm_min(monkeypatch):
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CALM_TEMPLATE_MIN_MAD", "0.32")
    metrics = {
        "unique_frames": 188,
        "unique_ratio": 1.0,
        "mean_mad": 0.299,
        "near_static": True,
        "valid": False,
        "technical_valid": True,
        "technical_failure_reason": "",
        "failure_reason": "driver_near_static:mean_mad=0.299<min_0.35",
        "validation_failure_reason": "driver_invalid:driver_near_static:mean_mad=0.299<min_0.35",
    }

    adjusted = runner._apply_calm_template_validation_profile(metrics)

    assert adjusted["valid"] is False
    assert adjusted["near_static"] is True
    assert adjusted["liveportrait_calm_template_window_accepted_by_profile"] is False


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
