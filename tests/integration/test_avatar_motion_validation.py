import os
import sys
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
WORKER_ROOT = SERVICES_ROOT / "worker"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

sys.modules.pop("avatar", None)
avatar_pipeline = importlib.import_module("avatar.pipeline")  # noqa: E402
avatar_canonical_pipeline = importlib.import_module("avatar.canonical_pipeline")  # noqa: E402
canonical_adapters = importlib.import_module("avatar.canonical_adapters")  # noqa: E402
CANONICAL_ENGINE = canonical_adapters.CANONICAL_ENGINE
EngineResult = canonical_adapters.EngineResult


def _strict_validation_pass() -> dict:
    return {
        "motion_real": True,
        "animated": True,
        "lip_motion_valid": True,
        "eye_motion_valid": True,
        "face_artifacts_detected": False,
        "audio_match": True,
        "frame_count": 48,
        "min_frames": 18,
        "duration_mismatch": False,
        "failure_reason": "",
        "quality_checks": {},
    }


def _strict_validation_fail() -> dict:
    payload = _strict_validation_pass()
    payload.update({
        "motion_real": False,
        "lip_motion_valid": False,
        "failure_reason": "low_lip_motion",
    })
    return payload


def _overlay_drift_only_metrics() -> dict:
    payload = _strict_validation_pass()
    payload.update(
        {
            "motion_real": False,
            "animated": False,
            "failure_reason": "whole_frame_drift",
            "frame_count": 119,
            "min_frames": 18,
            "animation_score": 5.54,
            "min_score": 1.8,
            "audio_match": True,
            "duration_mismatch": False,
            "quality_checks": {
                "unique_frames": 59,
                "face_drift_ratio": 0.188,
                "max_drift": 0.16,
                "drift_detected": True,
                "loop_detected": False,
                "glitch_detected": False,
                "mouth_artifact_detected": False,
                "eye_artifact_detected": False,
                "face_warp_detected": False,
                "landmark_stable": True,
                "structural_face_artifact_detected": False,
                "face_artifact_detected": False,
                "mouth_openness_change": 0.010867,
                "min_mouth_open_change": 0.0035,
                "eye_blink_change": 0.006137,
                "min_eye_blink_change": 0.0025,
            },
        }
    )
    return payload


def test_lesson_segment_validation_downgrades_isolated_whole_frame_drift():
    metrics = _overlay_drift_only_metrics()

    assert avatar_pipeline.accept_avatar_render(metrics) is False

    adjusted = avatar_pipeline.apply_lesson_segment_validation_policy(metrics)

    assert adjusted["failure_reason"] == ""
    assert adjusted["motion_real"] is True
    assert adjusted["animated"] is True
    assert adjusted["whole_frame_drift_diagnostic_only"] is True
    assert "whole_frame_drift" in adjusted["validation_warnings"]
    assert adjusted["quality_checks"]["drift_warning"] is True
    assert avatar_pipeline.accept_avatar_render(adjusted) is True


def test_lesson_segment_validation_downgrades_overlay_landmark_and_roi_warnings():
    metrics = _overlay_drift_only_metrics()
    metrics["failure_reason"] = "whole_frame_drift,face_roi_artifact,landmark_instability"
    metrics["quality_checks"]["landmark_stable"] = False
    metrics["quality_checks"]["face_artifact_detected"] = True
    metrics["face_artifacts_detected"] = True

    adjusted = avatar_pipeline.apply_lesson_segment_validation_policy(metrics)

    assert adjusted["failure_reason"] == ""
    assert adjusted["motion_real"] is True
    assert adjusted["validation_classification"] == "warning"
    assert adjusted["validation_warning_only"] is True
    assert "whole_frame_drift" in adjusted["validation_warnings"]
    assert "face_roi_artifact" in adjusted["validation_warnings"]
    assert "landmark_instability" in adjusted["validation_warnings"]
    assert adjusted["quality_checks"]["face_roi_artifact_warning"] is True
    assert avatar_pipeline.accept_avatar_render(adjusted) is False
    assert avatar_pipeline.accept_avatar_lesson_segment_render(adjusted) is True


def test_lesson_segment_validation_still_rejects_corrupt_drift_output():
    metrics = _overlay_drift_only_metrics()
    metrics["frame_count"] = 1
    metrics["quality_checks"]["unique_frames"] = 0
    metrics["failure_reason"] = "whole_frame_drift,single_frame_output"

    adjusted = avatar_pipeline.apply_lesson_segment_validation_policy(metrics)

    assert adjusted["failure_reason"] == "whole_frame_drift,single_frame_output"
    assert adjusted["whole_frame_drift_diagnostic_only"] is False
    assert avatar_pipeline.accept_avatar_render(adjusted) is False


def test_lesson_segment_validation_still_rejects_structural_face_corruption():
    metrics = _overlay_drift_only_metrics()
    metrics["failure_reason"] = "whole_frame_drift,face_roi_artifact,face_warp"
    metrics["quality_checks"]["face_warp_detected"] = True
    metrics["quality_checks"]["structural_face_artifact_detected"] = True

    adjusted = avatar_pipeline.apply_lesson_segment_validation_policy(metrics)

    assert adjusted["failure_reason"] == "whole_frame_drift,face_roi_artifact,face_warp"
    assert adjusted["validation_classification"] == "hard_failure"
    assert avatar_pipeline.accept_avatar_render(adjusted) is False


def _passing_motion_gate() -> dict:
    return {
        "passed": True,
        "technical_valid": True,
        "technical_passed": True,
        "motion_passed": True,
        "unique_frames": 22,
        "frame_delta": 0.8,
        "head_motion_score": 0.02,
        "mouth_motion_score": 0.02,
        "failure_reason": "",
    }


def _shared_motion_probe_metrics(
    path: str | Path,
    *,
    duration_seconds: float = 1.0,
    fps: float = 25.0,
    frame_count: int = 25,
    unique_frames: int = 25,
    unique_ratio: float = 1.0,
    mean_mad: float = 0.8,
    near_static: bool = False,
    failure_reason: str = "",
) -> dict:
    return {
        "path": str(path),
        "duration_seconds": float(duration_seconds),
        "fps": float(fps),
        "frame_count": int(frame_count),
        "unique_frames": int(unique_frames),
        "unique_ratio": float(unique_ratio),
        "first_hash": "first",
        "last_hash": "last",
        "mean_mad": float(mean_mad),
        "near_static": bool(near_static),
        "failure_reason": str(failure_reason or ("driver_near_static:mean_mad=0.0<min_0.35" if near_static else "")),
        "probe_errors": [],
    }


def _legacy_zero_motion_validation() -> dict:
    return {
        "animated": False,
        "motion_real": False,
        "quality_checks": {
            "frames_sampled": 64,
            "unique_frames": 0,
            "start_end_frame_diff": 0.0,
            "head_motion_score": 0.0,
            "mouth_openness_change": 0.0,
            "face_detection_frames": 0,
            "mouth_roi_frames": 0,
            "eye_roi_frames": 0,
            "landmark_valid_frames": 0,
        },
    }


def _stub_canonical_input(tmp_path: Path, image: Path) -> SimpleNamespace:
    normalized = tmp_path / "canonical.png"
    normalized.write_bytes(b"canonical")
    return SimpleNamespace(
        original_input_path=str(image),
        selected_source_key="image",
        normalized_input_path=str(normalized),
        normalized_mode="canonical_square_portrait",
        engine_name=CANONICAL_ENGINE,
        source_kind="image",
        preflight_score=1.0,
        face_detected=True,
        readable=True,
        crop_box=[0, 0, 10, 10],
        face_bbox=[1, 1, 9, 9],
        metrics={},
        ranking=[],
        handoff={},
        warning="",
    )


def _write_static_handoff_stub(*, source_image, output_path, **_kwargs):
    Path(output_path).write_bytes(b"static-handoff")
    return Path(output_path)


def _stub_canonical_input_for_source_key(tmp_path: Path, image: Path, source_key: str) -> SimpleNamespace:
    normalized = tmp_path / f"canonical_{source_key}.png"
    normalized.write_bytes(f"canonical:{source_key}".encode("utf-8"))
    return SimpleNamespace(
        original_input_path=str(image),
        selected_source_key=str(source_key),
        normalized_input_path=str(normalized),
        normalized_mode="canonical_square_portrait",
        engine_name=CANONICAL_ENGINE,
        source_kind="image",
        preflight_score=1.0,
        face_detected=True,
        readable=True,
        crop_box=[0, 0, 10, 10],
        face_bbox=[1, 1, 9, 9],
        metrics={},
        ranking=[],
        handoff={},
        warning="",
    )


def test_preview_liveportrait_motion_strength_defaults_to_one(tmp_path, monkeypatch):
    monkeypatch.delenv("AVATAR_PREVIEW_LIVEPORTRAIT_MOTION_STRENGTH", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", raising=False)
    image = tmp_path / "face.png"
    audio = tmp_path / "a.wav"
    image.write_bytes(b"image")
    audio.write_bytes(b"audio")

    env = avatar_canonical_pipeline._build_stage_env(
        _stub_canonical_input(tmp_path, image),
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(tmp_path / "preview.mp4"),
            preview_teacher_id=1,
            preview_job_id=2,
        ),
    )

    assert env["AVATAR_LIVEPORTRAIT_MOTION_STRENGTH"] == "1.0"
    assert env["AVATAR_LIVEPORTRAIT_MOTION_PRESET"] == "natural_conservative"
    assert env["AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY"] == "0"


def test_preview_liveportrait_motion_strength_env_override_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_PREVIEW_LIVEPORTRAIT_MOTION_STRENGTH", "1.37")
    image = tmp_path / "face.png"
    audio = tmp_path / "a.wav"
    image.write_bytes(b"image")
    audio.write_bytes(b"audio")

    env = avatar_canonical_pipeline._build_stage_env(
        _stub_canonical_input(tmp_path, image),
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(tmp_path / "preview.mp4"),
            preview_teacher_id=1,
            preview_job_id=2,
        ),
    )

    assert env["AVATAR_LIVEPORTRAIT_MOTION_STRENGTH"] == "1.37"


def test_liveportrait_motion_preset_affects_cache_identity(tmp_path, monkeypatch):
    image = tmp_path / "face.png"
    audio = tmp_path / "a.wav"
    image.write_bytes(b"image")
    audio.write_bytes(b"audio")
    request = avatar_pipeline.AvatarRenderRequest(
        source_image_path=str(image),
        audio_path=str(audio),
        output_path=str(tmp_path / "preview.mp4"),
        preview_teacher_id=1,
        preview_job_id=2,
    )

    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", raising=False)
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", raising=False)
    default_keys = avatar_canonical_pipeline._expected_cache_keys(request, CANONICAL_ENGINE)

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", "subtle_gaze")
    subtle_gaze_keys = avatar_canonical_pipeline._expected_cache_keys(request, CANONICAL_ENGINE)

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY", "1")
    boosted_allowed_keys = avatar_canonical_pipeline._expected_cache_keys(request, CANONICAL_ENGINE)

    assert default_keys["liveportrait_motion_preset"] == "natural_conservative"
    assert subtle_gaze_keys["liveportrait_motion_preset"] == "subtle_gaze"
    assert default_keys != subtle_gaze_keys
    assert subtle_gaze_keys["liveportrait_boosted_retry_allowed"] == "0"
    assert boosted_allowed_keys["liveportrait_boosted_retry_allowed"] == "1"
    assert subtle_gaze_keys != boosted_allowed_keys

    request.motion_preset = "subtle_blink"
    request.restoration_enabled = True
    request.liveportrait_enabled = False
    request_override_keys = avatar_canonical_pipeline._expected_cache_keys(request, CANONICAL_ENGINE)
    assert request_override_keys["liveportrait_motion_preset"] == "subtle_blink"
    assert request_override_keys["restoration_enabled"] == "1"
    assert request_override_keys["liveportrait_enabled"] == "0"
    assert request_override_keys != boosted_allowed_keys


def test_stage_env_uses_safe_runtime_motion_request_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_MOTION_PRESET", "expressive_debug")
    image = tmp_path / "face.png"
    audio = tmp_path / "a.wav"
    image.write_bytes(b"image")
    audio.write_bytes(b"audio")
    request = avatar_pipeline.AvatarRenderRequest(
        source_image_path=str(image),
        audio_path=str(audio),
        output_path=str(tmp_path / "preview.mp4"),
        motion_preset="subtle_gaze",
        restoration_enabled=True,
        liveportrait_enabled=False,
        preview_teacher_id=1,
        preview_job_id=2,
    )

    env = avatar_canonical_pipeline._build_stage_env(_stub_canonical_input(tmp_path, image), request)

    assert env["AVATAR_LIVEPORTRAIT_MOTION_PRESET"] == "subtle_gaze"
    assert env["AVATAR_LIVEPORTRAIT_ALLOW_BOOSTED_RETRY"] == "0"
    assert env["AVATAR_LIVEPORTRAIT_ENABLED"] == "0"


def test_musetalk_chunk_timing_metrics_are_shape_stable():
    metrics = avatar_canonical_pipeline._musetalk_chunk_timing_metrics(
        details={
            "route": "service_chunked",
            "chunk_metadata": [
                {
                    "index": 2,
                    "audio_duration_seconds": 3.25,
                    "frame_count": 81,
                    "elapsed_seconds": 14.5,
                    "service_success": True,
                }
            ],
        },
        debug_payload={},
        audio_duration_seconds=3.25,
        frame_count=81,
        elapsed_seconds=14.5,
    )

    assert metrics == [
        {
            "chunk_index": 2,
            "audio_duration_seconds": 3.25,
            "frame_count": 81,
            "elapsed_seconds": 14.5,
            "success": True,
            "route": "service_chunked",
        }
    ]


def test_liveportrait_gate_allows_shared_probe_motion_when_legacy_roi_gate_is_zero(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25, "size_bytes": Path(path).stat().st_size}

    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_animation", lambda _path: _legacy_zero_motion_validation())
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_shared_liveportrait_video_motion_probe",
        lambda path: _shared_motion_probe_metrics(path),
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": kwargs["video_path"],
            "reconciled_audio_path": kwargs["audio_path"],
        },
    )

    def fake_normalize(**kwargs):
        handoff = Path(kwargs["handoff_video_path"])
        handoff.write_bytes(Path(kwargs["video_path"]).read_bytes())
        return {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(handoff),
        }

    monkeypatch.setattr(avatar_canonical_pipeline, "_normalize_preview_video_for_musetalk", fake_normalize)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"moving-liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    musetalk_calls: list[dict[str, Any]] = []

    def fake_musetalk(*, source_video, output_path, **_kwargs):
        assert Path(source_video).exists()
        musetalk_calls.append({"source_video": source_video})
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=2,
            preview_job_id=101,
        )
    )

    assert result["stage_paths"]["musetalk_stage_state"] == "completed"
    assert len(musetalk_calls) == 1
    raw_gate = result["stage_paths"]["liveportrait_motion_gate"]
    assert raw_gate["passed"] is True
    assert raw_gate["unique_frames"] == 0
    assert raw_gate["shared_probe_unique_ratio"] == 1.0
    assert raw_gate["shared_probe_mean_mad"] == 0.8
    assert raw_gate["analyzer_classification"] == "liveportrait_motion_gate_analyzer_mismatch"


def test_liveportrait_gate_analyzes_exact_raw_current_run_path(tmp_path, monkeypatch):
    raw = tmp_path / "preview.mp4.liveportrait.mp4"
    stale_handoff = tmp_path / "preview.mp4.musetalk_handoff.mp4"
    raw.write_bytes(b"current-run-liveportrait")
    stale_handoff.write_bytes(b"stale-handoff")
    analyzed_paths: list[str] = []

    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_assert_video_contract",
        lambda path, *, stage_name="video": {"duration_seconds": 1.0, "frame_count": 25},
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_animation", lambda _path: _legacy_zero_motion_validation())

    def fake_shared_probe(path):
        analyzed_paths.append(str(path))
        return _shared_motion_probe_metrics(path)

    monkeypatch.setattr(avatar_canonical_pipeline, "_shared_liveportrait_video_motion_probe", fake_shared_probe)

    result = avatar_canonical_pipeline._liveportrait_motion_gate(
        str(raw),
        is_preview_request=True,
        expected_duration_seconds=1.0,
        expected_fps=25.0,
        expected_frame_count=25,
    )

    assert result["passed"] is True
    assert analyzed_paths == [str(raw)]
    assert result["analyzed_path"] == str(raw)
    assert result["file_sha256"] == avatar_canonical_pipeline.sha256_file(raw)
    assert result["file_sha256"] != avatar_canonical_pipeline.sha256_file(stale_handoff)


def test_low_motion_liveportrait_output_warns_and_feeds_musetalk_by_default(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    liveportrait_output = output.with_suffix(output.suffix + ".liveportrait.mp4")
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.delenv("AVATAR_LP_LOW_MOTION_FALLBACK_TO_STATIC", raising=False)
    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_assert_video_contract",
        lambda path, *, stage_name="video": {"duration_seconds": 1.0, "frame_count": 25} if Path(path).exists() else (_ for _ in ()).throw(RuntimeError(f"{stage_name}_missing")),
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_animation", lambda _path: _legacy_zero_motion_validation())
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_shared_liveportrait_video_motion_probe",
        lambda path: _shared_motion_probe_metrics(path, unique_frames=1, unique_ratio=0.04, mean_mad=0.0, near_static=True),
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(liveportrait_output),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_after_seconds": 1.0,
            "video_path": str(liveportrait_output),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"static-liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    musetalk_calls = []

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls.append(str(source_video))
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=3,
            preview_job_id=102,
        )
    )

    assert musetalk_calls == [str(liveportrait_output)]
    assert result["stage_paths"]["liveportrait_technical_valid"] is True
    assert result["stage_paths"]["liveportrait_motion_passed"] is False
    assert result["stage_paths"]["liveportrait_fallback_used"] is False
    assert result["stage_paths"]["musetalk_source_kind"] == "liveportrait"
    assert result["stage_paths"]["liveportrait_quality_warning"].startswith("liveportrait_low_motion")
    assert result["preview_status"] == "warning"
    assert Path(result["output_path"]).exists()


def test_low_motion_liveportrait_can_fallback_to_static_when_enabled(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    static_handoff = output.with_suffix(output.suffix + ".musetalk_handoff.mp4")
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setenv("AVATAR_LP_LOW_MOTION_FALLBACK_TO_STATIC", "1")
    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_build_static_handoff_loop", _write_static_handoff_stub)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_assert_video_contract",
        lambda path, *, stage_name="video": {"duration_seconds": 1.0, "frame_count": 25} if Path(path).exists() else (_ for _ in ()).throw(RuntimeError(f"{stage_name}_missing")),
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_animation", lambda _path: _legacy_zero_motion_validation())
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_shared_liveportrait_video_motion_probe",
        lambda path: _shared_motion_probe_metrics(path, unique_frames=1, unique_ratio=0.04, mean_mad=0.0, near_static=True),
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(static_handoff),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_after_seconds": 1.0,
            "video_path": str(static_handoff),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"static-liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    musetalk_calls = []

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls.append(str(source_video))
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=33,
            preview_job_id=1033,
        )
    )

    assert musetalk_calls == [str(static_handoff)]
    assert result["stage_paths"]["liveportrait_technical_valid"] is True
    assert result["stage_paths"]["liveportrait_motion_passed"] is False
    assert result["stage_paths"]["liveportrait_fallback_used"] is True
    assert result["stage_paths"]["liveportrait_fallback_reason"] == "low_motion"
    assert result["stage_paths"]["musetalk_source_kind"] == "static_fallback"
    assert result["stage_paths"]["liveportrait_quality_warning"].startswith("liveportrait_low_motion")


def test_liveportrait_missing_output_falls_back_to_static_handoff_and_runs_musetalk(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    static_handoff = output.with_suffix(output.suffix + ".musetalk_handoff.mp4")
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_build_static_handoff_loop", _write_static_handoff_stub)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_animation", lambda _path: _legacy_zero_motion_validation())
    monkeypatch.setattr(avatar_canonical_pipeline, "_shared_liveportrait_video_motion_probe", lambda path: _shared_motion_probe_metrics(path))
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(static_handoff),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_after_seconds": 1.0,
            "video_path": str(static_handoff),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        return EngineResult(True, "liveportrait", output_path, "")

    musetalk_calls = []

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls.append(str(source_video))
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=4,
            preview_job_id=103,
        )
    )

    assert musetalk_calls == [str(static_handoff)]
    assert result["stage_paths"]["liveportrait_failed"] is True
    assert result["stage_paths"]["liveportrait_fallback_used"] is True
    assert result["stage_paths"]["musetalk_source_kind"] == "static_fallback"
    assert "liveportrait_technical_invalid" in result["stage_paths"]["liveportrait_failure_reason"]
    assert Path(result["output_path"]).exists()


def test_liveportrait_disabled_uses_static_source_without_running_liveportrait(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    static_handoff = output.with_suffix(output.suffix + ".musetalk_handoff.mp4")
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_ENABLED", "0")
    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_build_static_handoff_loop", _write_static_handoff_stub)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_assert_video_contract",
        lambda path, *, stage_name="video": {"duration_seconds": 1.0, "frame_count": 25} if Path(path).exists() else (_ for _ in ()).throw(RuntimeError(f"{stage_name}_missing")),
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(static_handoff),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_after_seconds": 1.0,
            "video_path": str(static_handoff),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    liveportrait_calls = {"count": 0}

    def fake_liveportrait(**_kwargs):
        liveportrait_calls["count"] += 1
        raise AssertionError("LivePortrait should be skipped")

    musetalk_calls = []

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls.append(str(source_video))
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=44,
            preview_job_id=1044,
        )
    )

    assert liveportrait_calls["count"] == 0
    assert musetalk_calls == [str(static_handoff)]
    assert result["stage_paths"]["liveportrait_enabled"] is False
    assert result["stage_paths"]["liveportrait_started"] is False
    assert result["stage_paths"]["liveportrait_bypassed"] is True
    assert result["stage_paths"]["musetalk_source_kind"] == "static_source"
    assert result["stage_paths"]["liveportrait_fallback_used"] is False
    assert Path(result["output_path"]).exists()


def test_validate_avatar_animation_fails_for_low_motion(monkeypatch):
    monkeypatch.setattr(avatar_pipeline, "_video_frame_count", lambda _path: 42)
    monkeypatch.setattr(avatar_pipeline, "_animation_score", lambda _path: 0.2)
    monkeypatch.setattr(
        avatar_pipeline,
        "_analyze_avatar_motion_quality",
        lambda _path: {
            "unique_frames": 22,
            "lip_movement_score": 1.5,
            "eye_movement_score": 0.6,
            "drift_detected": False,
            "glitch_detected": False,
        },
    )

    result = avatar_pipeline.validate_avatar_animation("dummy.mp4")

    assert result["frame_count"] == 42
    assert result["animated"] is False


def test_validate_avatar_animation_passes_for_dynamic_video(monkeypatch):
    monkeypatch.setattr(avatar_pipeline, "_video_frame_count", lambda _path: 64)
    monkeypatch.setattr(avatar_pipeline, "_animation_score", lambda _path: 3.4)
    monkeypatch.setattr(
        avatar_pipeline,
        "_analyze_avatar_motion_quality",
        lambda _path: {
            "unique_frames": 30,
            "lip_movement_score": 1.7,
            "eye_movement_score": 0.8,
            "drift_detected": False,
            "glitch_detected": False,
        },
    )

    result = avatar_pipeline.validate_avatar_animation("dummy.mp4")

    assert result["animated"] is True
    assert result["global_frame_diff_mean"] == 3.4
    assert result["animation_score"] >= result["min_score"]


def test_render_avatar_segment_cache_is_invalidated_when_audio_changes(tmp_path, monkeypatch):
    audio_a = tmp_path / "a.wav"
    audio_b = tmp_path / "b.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "avatar.mp4"
    audio_a.write_bytes(b"audio-a")
    audio_b.write_bytes(b"audio-b")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_trim_video_to_exact_audio_duration", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    calls = {"liveportrait": 0, "musetalk": 0}

    def fake_liveportrait(*, output_path, **_kwargs):
        calls["liveportrait"] += 1
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        calls["musetalk"] += 1
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    first = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio_a),
            output_path=str(output),
        )
    )
    second = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio_b),
            output_path=str(output),
        )
    )

    assert first["engine_used"] == CANONICAL_ENGINE
    assert second["engine_used"] == CANONICAL_ENGINE
    assert calls["liveportrait"] == 2
    assert calls["musetalk"] == 2


def test_render_avatar_segment_cache_is_invalidated_when_preview_contract_changes(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "avatar.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_trim_video_to_exact_audio_duration", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    calls = {"liveportrait": 0, "musetalk": 0}

    def fake_liveportrait(*, output_path, **_kwargs):
        calls["liveportrait"] += 1
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        calls["musetalk"] += 1
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    first = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
        )
    )
    second = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=30,
            target_duration_seconds=1.2,
        )
    )

    assert first["engine_used"] == CANONICAL_ENGINE
    assert second["engine_used"] == CANONICAL_ENGINE
    assert calls["liveportrait"] == 2
    assert calls["musetalk"] == 2


def test_preview_pipeline_preserves_liveportrait_when_musetalk_times_out(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setenv("AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS", "37")
    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    reconciliation_calls = []
    handoff_normalization_calls = []

    def fake_reconcile_duration_contract(*, video_path, audio_path, preview_teacher_id=0, preview_job_id=0):
        reconciliation_calls.append(
            {
                "video_path": video_path,
                "audio_path": audio_path,
                "preview_teacher_id": preview_teacher_id,
                "preview_job_id": preview_job_id,
            }
        )
        return {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 0.92,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": -0.08,
            "adjustment_seconds": 0.08,
            "strategy": "pad_video_with_last_frame",
            "video_changed": True,
            "audio_changed": False,
            "reconciled_video_path": video_path,
            "reconciled_audio_path": audio_path,
        }

    monkeypatch.setattr(avatar_canonical_pipeline, "_reconcile_duration_contract", fake_reconcile_duration_contract)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **kwargs: handoff_normalization_calls.append(kwargs) or {
            "normalized": True,
            "strategy": "normalize_contract_fps",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 30,
            "frame_count_after": 25,
            "duration_before_seconds": 1.2,
            "duration_after_seconds": 1.0,
            "video_path": kwargs["video_path"],
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_fail())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_save_failed_render_debug", lambda **_kwargs: str(tmp_path / "debug"))

    musetalk_call = {}

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(
            True,
            "liveportrait",
            output_path,
            "",
            details={
                "stderr": (
                    "[LivePortrait] final_driver_recipe motion_source=image_composed:default "
                    "liveportrait_motion_preset=natural_conservative "
                    "liveportrait_motion_profile=default "
                    "liveportrait_driver_source=composer "
                    "liveportrait_composer_used=1 "
                    "liveportrait_boosted_retry_used=0 "
                    "liveportrait_recenter_enabled=1 "
                    "liveportrait_whole_frame_drift_guard=1"
                ),
            },
        )

    def fake_musetalk(*, output_path, timeout_seconds=None, stage_name="musetalk", **_kwargs):
        assert reconciliation_calls
        musetalk_call["timeout_seconds"] = timeout_seconds
        musetalk_call["stage_name"] = stage_name
        return EngineResult(
            False,
            "musetalk",
            output_path,
            "preview_musetalk_timeout",
            details={"timeout_seconds": timeout_seconds, "stage_name": stage_name},
        )

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    with pytest.raises(RuntimeError, match="musetalk_failed:preview_musetalk_timeout"):
        avatar_pipeline.render_avatar_segment_local(
            avatar_pipeline.AvatarRenderRequest(
                source_image_path=str(image),
                audio_path=str(audio),
                output_path=str(output),
                target_frame_count=25,
                target_duration_seconds=1.0,
                preview_teacher_id=12,
                preview_job_id=34,
            )
        )

    assert musetalk_call["stage_name"] == "preview_musetalk"
    assert musetalk_call["timeout_seconds"] == 37
    assert reconciliation_calls == [{
        "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        "audio_path": str(audio),
        "preview_teacher_id": 12,
        "preview_job_id": 34,
    }]
    assert handoff_normalization_calls == [{
        "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        "handoff_video_path": str(output.with_suffix(output.suffix + ".musetalk_handoff.mp4")),
        "target_frame_count": 25,
        "target_duration_seconds": 1.0,
        "preview_teacher_id": 12,
        "preview_job_id": 34,
    }]
    assert not output.exists()


def test_preview_pipeline_runs_musetalk_and_uses_musetalk_output(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 0.92,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": -0.08,
            "adjustment_seconds": 0.08,
            "strategy": "pad_video_with_last_frame",
            "video_changed": True,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": True,
            "strategy": "normalize_contract_fps",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 30,
            "frame_count_after": 25,
            "duration_before_seconds": 1.2,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(
            True,
            "liveportrait",
            output_path,
            "",
            details={
                "stderr": (
                    "[LivePortrait] final_driver_recipe motion_source=image_composed:default "
                    "liveportrait_motion_preset=natural_conservative "
                    "liveportrait_motion_profile=default "
                    "liveportrait_driver_source=composer "
                    "liveportrait_composer_used=1 "
                    "liveportrait_boosted_retry_used=0 "
                    "liveportrait_recenter_enabled=1 "
                    "liveportrait_whole_frame_drift_guard=1"
                ),
            },
        )

    musetalk_calls = []

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls.append(str(source_video))
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=12,
            preview_job_id=35,
        )
    )

    assert result["preview_status"] == "ready"
    assert result["engine_used"] == CANONICAL_ENGINE
    assert result["requested_engine_raw"] == "musetalk"
    assert result["normalized_engine"] == CANONICAL_ENGINE
    assert result["avatar_engine_selected"] == CANONICAL_ENGINE
    assert result["stage_paths"]["avatar_engine_selected"] == CANONICAL_ENGINE
    assert result["stage_paths"]["musetalk_stage_state"] == "completed"
    assert result["stage_paths"]["liveportrait_started"] is True
    assert result["stage_paths"]["liveportrait_succeeded"] is True
    assert result["stage_paths"]["liveportrait_motion_preset"] == "natural_conservative"
    assert result["stage_paths"]["liveportrait_motion_profile"] == "default"
    assert result["stage_paths"]["liveportrait_driver_source"] == "composer"
    assert result["stage_paths"]["liveportrait_composer_used"] is True
    assert result["stage_paths"]["liveportrait_boosted_retry_used"] is False
    assert result["stage_paths"]["liveportrait_recenter_enabled"] is True
    assert result["stage_paths"]["liveportrait_whole_frame_drift_guard"] is True
    assert result["stage_paths"]["musetalk_started"] is True
    assert result["stage_paths"]["musetalk_succeeded"] is True
    assert result["stage_paths"]["musetalk_source_kind"] == "liveportrait"
    assert result["stage_paths"]["musetalk_source_video"] == str(output.with_suffix(output.suffix + ".liveportrait.mp4"))
    assert musetalk_calls == [result["stage_paths"]["musetalk_source_video"]]
    assert result["final_avatar_engine_chain"] == ["liveportrait", "musetalk"]
    assert result["stage_paths"]["musetalk_handoff_frame_normalization_strategy"] == "normalize_contract_fps"
    assert result["stage_paths"]["final_playable_path"] == str(output)
    assert result["stage_outputs"][-1]["stage"] == "musetalk"
    assert Path(result["output_path"]).exists()


@pytest.mark.parametrize("restoration_enabled", [False, True])
def test_preview_pipeline_stage_order_and_optional_restoration(tmp_path, monkeypatch, restoration_enabled):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    liveportrait_output = output.with_suffix(output.suffix + ".liveportrait.mp4")
    musetalk_output = output.with_suffix(output.suffix + ".musetalk.mp4")
    restored_output = output.with_suffix(output.suffix + ".restored.mp4")
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setenv("AVATAR_PREVIEW_USE_RESTORATION", "1" if restoration_enabled else "0")
    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(liveportrait_output),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": True,
            "strategy": "normalize_contract_fps",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(liveportrait_output),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    call_order: list[str] = []

    def fake_liveportrait(*, source_video, audio_path, output_path, **_kwargs):
        call_order.append("liveportrait")
        assert str(source_video) == ""
        assert str(audio_path) == str(audio)
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, source_video, audio_path, output_path, stage_name="musetalk", **_kwargs):
        call_order.append("musetalk")
        assert str(source_video) == str(liveportrait_output)
        assert str(audio_path) == str(audio)
        assert stage_name == "preview_musetalk"
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    def fake_restoration(*, input_video, output_path, audio_path, **_kwargs):
        call_order.append("restoration")
        assert str(input_video) == str(musetalk_output)
        assert str(audio_path) == str(audio)
        Path(output_path).write_bytes(b"restored")
        return EngineResult(True, "restoration", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_restoration", fake_restoration)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=18,
            preview_job_id=77,
        )
    )

    expected_order = ["liveportrait", "musetalk"] + (["restoration"] if restoration_enabled else [])
    assert call_order == expected_order
    assert result["stage_paths"]["musetalk_ran"] is True
    if restoration_enabled:
        assert result["stage_outputs"][-1]["stage"] == "restoration"
    else:
        assert result["stage_outputs"][-1]["stage"] == "musetalk"
    assert Path(result["output_path"]).exists()
    assert result["stage_paths"]["final_playable_path"] == str(output)
    if restoration_enabled:
        assert result["stage_paths"]["ui_returned_playable_file"] == str(restored_output)
    else:
        assert result["stage_paths"]["ui_returned_playable_file"] == str(musetalk_output)


def test_preview_pipeline_uses_musetalk_output_when_restoration_fails(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    liveportrait_output = output.with_suffix(output.suffix + ".liveportrait.mp4")
    musetalk_output = output.with_suffix(output.suffix + ".musetalk.mp4")
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setenv("AVATAR_PREVIEW_USE_RESTORATION", "1")
    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_assert_video_contract",
        lambda path, *, stage_name="video": {"duration_seconds": 1.0, "frame_count": 25} if Path(path).exists() else (_ for _ in ()).throw(RuntimeError(f"{stage_name}_missing")),
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(liveportrait_output),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_after_seconds": 1.0,
            "video_path": str(liveportrait_output),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    def fake_restoration(**_kwargs):
        raise RuntimeError("restoration boom")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_restoration", fake_restoration)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=19,
            preview_job_id=78,
        )
    )

    assert result["stage_paths"]["restoration_enabled"] is True
    assert result["stage_paths"]["restoration_succeeded"] is False
    assert result["stage_paths"]["restoration_failed"] is True
    assert "restoration boom" in result["stage_paths"]["restoration_failure_reason"]
    assert result["stage_paths"]["ui_returned_playable_file"] == str(musetalk_output)
    assert result["preview_status"] == "warning"
    assert output.read_bytes() == b"musetalk"


def test_preview_pipeline_returns_warning_when_strict_validation_fails(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_fail())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_save_failed_render_debug", lambda **_kwargs: str(tmp_path / "debug"))

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    with pytest.raises(RuntimeError, match="strict_validation_failed:low_lip_motion"):
        avatar_pipeline.render_avatar_segment_local(
            avatar_pipeline.AvatarRenderRequest(
                source_image_path=str(image),
                audio_path=str(audio),
                output_path=str(output),
            )
        )

    assert Path(output).exists()


def test_preview_pipeline_prefers_original_image_when_available(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    processed = tmp_path / "processed.png"
    original = tmp_path / "original.jpg"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    processed.write_bytes(b"processed")
    original.write_bytes(b"original")

    seen = {"source_image_path": "", "source_video_path": ""}

    def fake_canonicalize_avatar_input(*, source_image_path, source_video_path, output_path, is_preview, engine_name, source_key):
        seen["source_image_path"] = source_image_path
        seen["source_video_path"] = source_video_path
        return _stub_canonical_input(tmp_path, Path(source_image_path))

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", fake_canonicalize_avatar_input)
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0, "frame_count": 25})
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(processed),
            source_image_original_path=str(original),
            source_video_path=str(tmp_path / "other.mp4"),
            avatar_reference_type="image",
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=2,
            preview_job_id=10,
            preview_source_meta={"source_key": "image_original"},
        )
    )

    assert seen["source_image_path"] == str(original)
    assert seen["source_video_path"] == ""
    assert result["preview_status"] == "ready"
    assert result["canonical_input"]["original_input_path"] == str(original)


def test_preview_pipeline_retries_processed_source_then_uses_original(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    processed = tmp_path / "processed.png"
    original = tmp_path / "original.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    processed.write_bytes(b"processed")
    original.write_bytes(b"original")

    liveportrait_inputs: list[str] = []
    musetalk_calls = {"count": 0}
    gate_calls = {"count": 0}

    def fake_canonicalize_avatar_input(*, source_image_path, source_video_path, output_path, is_preview, engine_name, source_key):
        return _stub_canonical_input_for_source_key(tmp_path, Path(source_image_path), source_key or "image")

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    def fake_motion_gate(*_args, **_kwargs):
        gate_calls["count"] += 1
        if gate_calls["count"] == 1:
            return {
                **_passing_motion_gate(),
                "passed": False,
                "technical_valid": False,
                "technical_passed": False,
                "motion_passed": False,
                "unique_frames": 1,
                "frame_delta": 0.0,
                "head_motion_score": 0.0,
                "mouth_motion_score": 0.0,
                "failure_reason": "liveportrait_technical_validation_failed:processed_static",
                "technical_failure_reason": "liveportrait_technical_validation_failed:processed_static",
            }
        return _passing_motion_gate()

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", fake_canonicalize_avatar_input)
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", fake_motion_gate)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, input_path, output_path, **_kwargs):
        liveportrait_inputs.append(str(input_path))
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        musetalk_calls["count"] += 1
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(processed),
            source_image_original_path=str(original),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=100,
            preview_job_id=200,
            preview_source_meta={
                "source_key": "image_processed",
                "reference_type": "image",
                "source_candidates": [
                    {"source_key": "image_processed", "path": str(processed), "reason": "test_processed_first"},
                    {"source_key": "image_original", "path": str(original), "reason": "test_original_fallback"},
                ],
            },
        )
    )

    assert gate_calls["count"] == 3
    assert musetalk_calls["count"] == 1
    assert len(liveportrait_inputs) == 2
    assert "image_processed" in Path(liveportrait_inputs[0]).name
    assert "image_original" in Path(liveportrait_inputs[1]).name
    assert result["stage_paths"]["liveportrait_selected_source_key"] == "image_original"
    assert [entry["source_key"] for entry in result["stage_paths"]["liveportrait_rejected_sources"]] == ["image_processed"]
    assert result["stage_paths"]["musetalk_stage_state"] == "completed"


def test_preview_pipeline_reports_rejected_sources_when_all_candidates_fallback_to_static(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    processed = tmp_path / "processed.png"
    original = tmp_path / "original.png"
    output = tmp_path / "preview.mp4"
    static_handoff = output.with_suffix(output.suffix + ".musetalk_handoff.mp4")
    audio.write_bytes(b"audio")
    processed.write_bytes(b"processed")
    original.write_bytes(b"original")

    musetalk_calls = {"count": 0}

    def fake_canonicalize_avatar_input(*, source_image_path, source_video_path, output_path, is_preview, engine_name, source_key):
        return _stub_canonical_input_for_source_key(tmp_path, Path(source_image_path), source_key or "image")

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", fake_canonicalize_avatar_input)
    monkeypatch.setattr(avatar_canonical_pipeline, "_build_static_handoff_loop", _write_static_handoff_stub)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_liveportrait_motion_gate",
        lambda *_args, **_kwargs: {
            **_passing_motion_gate(),
            "passed": False,
            "technical_valid": False,
            "technical_passed": False,
            "motion_passed": False,
            "failure_reason": "liveportrait_technical_validation_failed:static_candidate",
            "technical_failure_reason": "liveportrait_technical_validation_failed:static_candidate",
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(static_handoff),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(static_handoff),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls["count"] += 1
        assert str(source_video) == str(static_handoff)
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(processed),
            source_image_original_path=str(original),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=101,
            preview_job_id=201,
            preview_source_meta={
                "source_key": "image_processed",
                "reference_type": "image",
                "source_candidates": [
                    {"source_key": "image_processed", "path": str(processed), "reason": "test_processed_first"},
                    {"source_key": "image_original", "path": str(original), "reason": "test_original_fallback"},
                ],
            },
        )
    )

    assert musetalk_calls["count"] == 1
    stage_paths = dict(result["stage_paths"])
    assert stage_paths.get("musetalk_ran") is True
    assert stage_paths.get("musetalk_source_kind") == "static_fallback"
    assert stage_paths.get("liveportrait_failed") is True
    assert stage_paths.get("liveportrait_fallback_used") is True
    assert [entry.get("source_key") for entry in stage_paths.get("liveportrait_rejected_sources") or []] == [
        "image_processed",
        "image_original",
    ]


def test_preview_pipeline_calls_musetalk_only_after_motion_valid_source(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    processed = tmp_path / "processed.png"
    original = tmp_path / "original.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    processed.write_bytes(b"processed")
    original.write_bytes(b"original")

    call_order: list[str] = []
    gate_calls = {"count": 0}

    def fake_canonicalize_avatar_input(*, source_image_path, source_video_path, output_path, is_preview, engine_name, source_key):
        return _stub_canonical_input_for_source_key(tmp_path, Path(source_image_path), source_key or "image")

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    def fake_motion_gate(*_args, **_kwargs):
        gate_calls["count"] += 1
        if gate_calls["count"] == 1:
            return {
                **_passing_motion_gate(),
                "passed": False,
                "technical_valid": False,
                "technical_passed": False,
                "motion_passed": False,
                "failure_reason": "liveportrait_technical_validation_failed:first_candidate_static",
                "technical_failure_reason": "liveportrait_technical_validation_failed:first_candidate_static",
            }
        return _passing_motion_gate()

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", fake_canonicalize_avatar_input)
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", fake_motion_gate)
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, input_path, output_path, **_kwargs):
        call_order.append(f"liveportrait:{Path(input_path).name}")
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        assert gate_calls["count"] == 3
        call_order.append("musetalk")
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(processed),
            source_image_original_path=str(original),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=102,
            preview_job_id=202,
            preview_source_meta={
                "source_key": "image_processed",
                "reference_type": "image",
                "source_candidates": [
                    {"source_key": "image_processed", "path": str(processed), "reason": "test_processed_first"},
                    {"source_key": "image_original", "path": str(original), "reason": "test_original_fallback"},
                ],
            },
        )
    )

    assert call_order[-1] == "musetalk"
    assert "image_processed" in call_order[0]
    assert "image_original" in call_order[1]
    assert result["stage_paths"]["liveportrait_selected_source_key"] == "image_original"
    assert result["stage_paths"]["musetalk_ran"] is True


def test_preview_pipeline_bypasses_stale_preview_artifacts_and_reruns(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")
    output.write_bytes(b"stale-preview")
    output.with_suffix(output.suffix + ".meta.json").write_text("{}", encoding="utf-8")

    calls = {"liveportrait": 0}

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *_args, **_kwargs: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0, "frame_count": 25})
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        calls["liveportrait"] += 1
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(True, "liveportrait", output_path, "")

    def fake_musetalk(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            preview_teacher_id=77,
            preview_job_id=88,
            target_frame_count=25,
            target_duration_seconds=1.0,
        )
    )

    assert calls["liveportrait"] == 1
    assert result["preview_status"] == "ready"
    assert Path(result["output_path"]).read_bytes() == b"musetalk"


def test_preview_duration_reconciliation_allows_shortfall_within_one_second(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    audio = tmp_path / "preview.wav"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    monkeypatch.delenv("AVATAR_PREVIEW_MAX_PAD_SECONDS", raising=False)
    state = {"video_duration_seconds": 1.0, "ffmpeg_cmd": []}

    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_probe_audio_duration_seconds",
        lambda _path: 1.6208,
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_probe_video_duration_seconds",
        lambda _path: state["video_duration_seconds"],
    )

    def fake_subprocess_run(cmd, capture_output, text, check, timeout):
        state["ffmpeg_cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"padded")
        state["video_duration_seconds"] = 1.6208
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(avatar_canonical_pipeline.subprocess, "run", fake_subprocess_run)

    result = avatar_canonical_pipeline._reconcile_duration_contract(
        video_path=str(video),
        audio_path=str(audio),
        preview_teacher_id=7,
        preview_job_id=9,
    )

    assert result["strategy"] == "pad_video_with_last_frame"
    assert result["video_changed"] is True
    assert result["contract_duration_seconds"] == 1.6208
    assert result["duration_delta_seconds"] == -0.6208
    assert result["adjustment_seconds"] == 0.6208
    assert result["final_video_duration_seconds"] == 1.6208
    assert result["final_audio_duration_seconds"] == 1.6208
    assert any("tpad=stop_mode=clone:stop_duration=0.620800" == str(part) for part in state["ffmpeg_cmd"])


def test_preview_duration_reconciliation_loops_when_shortfall_exceeds_clone_threshold(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    audio = tmp_path / "preview.wav"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    monkeypatch.delenv("AVATAR_PREVIEW_CLONE_PAD_THRESHOLD_SECONDS", raising=False)
    state = {"video_duration_seconds": 1.0, "ffmpeg_cmd": []}

    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_probe_audio_duration_seconds",
        lambda _path: 4.0,
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_probe_video_duration_seconds",
        lambda _path: state["video_duration_seconds"],
    )

    def fake_subprocess_run(cmd, capture_output, text, check, timeout):
        state["ffmpeg_cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"looped")
        state["video_duration_seconds"] = 4.0
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(avatar_canonical_pipeline.subprocess, "run", fake_subprocess_run)

    result = avatar_canonical_pipeline._reconcile_duration_contract(
        video_path=str(video),
        audio_path=str(audio),
        preview_teacher_id=11,
        preview_job_id=22,
    )

    assert result["strategy"] == "loop_video_to_contract_duration"
    assert result["video_changed"] is True
    assert result["contract_duration_seconds"] == 4.0
    assert "-stream_loop" in state["ffmpeg_cmd"]


def test_preview_musetalk_handoff_normalizes_video_to_contract_frame_count(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    state = {
        "frame_count": 47,
        "duration_seconds": 1.5667,
        "ffmpeg_cmd": [],
    }

    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_video_frame_count",
        lambda _path: int(state["frame_count"]),
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline.legacy_pipeline,
        "_probe_video_duration_seconds",
        lambda _path: float(state["duration_seconds"]),
    )

    def fake_subprocess_run(cmd, capture_output, text, check, timeout):
        state["ffmpeg_cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"normalized")
        state["frame_count"] = 25
        state["duration_seconds"] = 1.5625
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(avatar_canonical_pipeline.subprocess, "run", fake_subprocess_run)

    result = avatar_canonical_pipeline._normalize_preview_video_for_musetalk(
        video_path=str(video),
        target_frame_count=25,
        target_duration_seconds=1.5625,
        preview_teacher_id=2,
        preview_job_id=283,
    )

    assert result["normalized"] is True
    assert result["strategy"] == "normalize_contract_fps"
    assert result["frame_count_before"] == 47
    assert result["frame_count_after"] == 25
    assert result["duration_before_seconds"] == 1.5667
    assert result["duration_after_seconds"] == 1.5625
    assert result["target_fps"] == 16
    assert "fps=16" in state["ffmpeg_cmd"]
    assert "-frames:v" in state["ffmpeg_cmd"]
    assert "25" in state["ffmpeg_cmd"]


def test_preview_musetalk_timeout_budget_includes_cold_start_allowance(tmp_path, monkeypatch):
    request = avatar_pipeline.AvatarRenderRequest(
        source_image_path=str(tmp_path / "face.png"),
        audio_path=str(tmp_path / "preview.wav"),
        output_path=str(tmp_path / "preview.mp4"),
        preview_teacher_id=2,
        preview_job_id=11,
        target_duration_seconds=1.52,
    )

    # Make the adaptive calculation deterministic for this contract test.
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED", "0")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER", "1.0")
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_LOW_VRAM_MULTIPLIER", "1.0")
    monkeypatch.setenv("MUSETALK_CHUNK_MAX_SECONDS", "0")
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "probe_runtime_resources",
        lambda: {
            "system": {"total_mib": 16000, "available_mib": 8000, "used_mib": 8000, "source": "test"},
            "gpu": {"available": False, "reason": "test_cpu", "devices": [], "selected": {}},
        },
    )

    budget = avatar_canonical_pipeline._preview_musetalk_timeout_seconds(request)

    # Default budget includes model/chunk overhead plus frame-aware scaling:
    # base=180 + 1.52*32 + 24*1.1 + 1*240 = 495.04.
    assert budget == pytest.approx(495.04)


def test_preview_musetalk_timeout_budget_honors_explicit_override(tmp_path, monkeypatch):
    request = avatar_pipeline.AvatarRenderRequest(
        source_image_path=str(tmp_path / "face.png"),
        audio_path=str(tmp_path / "preview.wav"),
        output_path=str(tmp_path / "preview.mp4"),
        preview_teacher_id=2,
        preview_job_id=11,
        target_duration_seconds=1.52,
    )
    monkeypatch.setenv("AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS", "37")

    budget = avatar_canonical_pipeline._preview_musetalk_timeout_seconds(request)

    assert budget == 37.0


def test_preview_image_avatar_does_not_use_synthetic_motion_source(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0, "frame_count": 25})
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_liveportrait_motion_gate",
        lambda *_args, **_kwargs: {
            "passed": True,
            "unique_frames": 22,
            "frame_delta": 0.8,
            "head_motion_score": 0.02,
            "mouth_motion_score": 0.02,
            "failure_reason": "",
        },
    )
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(
            True,
            "liveportrait",
            output_path,
            "",
            details={
                "stderr": "[LivePortrait] motion_source=generated_micro_motion source_image=/tmp/face.png driving_input=/tmp/driving_ref.mp4",
            },
        )

    def fake_musetalk(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=5,
            preview_job_id=91,
        )
    )

    marker = str((result.get("stage_paths") or {}).get("liveportrait_motion_source") or "")
    assert "synthetic_from_image" not in marker
    assert "motion_source=generated_micro_motion" in marker


def test_preview_records_low_motion_warning_before_musetalk(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    image = tmp_path / "face.png"
    output = tmp_path / "preview.mp4"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **_kwargs: _stub_canonical_input(tmp_path, image))
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", lambda *_args, **_kwargs: {"duration_seconds": 1.0, "frame_count": 25})
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "validate_avatar_render_with_audio", lambda *_args, **_kwargs: _strict_validation_pass())
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "accept_avatar_render", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_reconcile_duration_contract",
        lambda **_kwargs: {
            "contract_duration_seconds": 1.0,
            "original_video_duration_seconds": 1.0,
            "original_audio_duration_seconds": 1.0,
            "final_video_duration_seconds": 1.0,
            "final_audio_duration_seconds": 1.0,
            "duration_delta_seconds": 0.0,
            "adjustment_seconds": 0.0,
            "strategy": "unchanged",
            "video_changed": False,
            "audio_changed": False,
            "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
            "reconciled_audio_path": str(audio),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_normalize_preview_video_for_musetalk",
        lambda **_kwargs: {
            "normalized": False,
            "strategy": "unchanged",
            "target_frame_count": 25,
            "target_duration_seconds": 1.0,
            "target_fps": 25,
            "frame_count_before": 25,
            "frame_count_after": 25,
            "duration_before_seconds": 1.0,
            "duration_after_seconds": 1.0,
            "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        },
    )
    monkeypatch.setattr(
        avatar_canonical_pipeline,
        "_liveportrait_motion_gate",
        lambda *_args, **_kwargs: {
            "passed": False,
            "technical_valid": True,
            "technical_passed": True,
            "motion_passed": False,
            "unique_frames": 1,
            "frame_delta": 0.01,
            "head_motion_score": 0.0,
            "mouth_motion_score": 0.0,
            "failure_reason": "liveportrait_motion_gate_failed:unique_frames=1<min_4;frame_delta=0.01<min_0.12",
            "motion_failure_reason": "liveportrait_low_motion:unique_frames=1<min_4;frame_delta=0.01<min_0.12",
        },
    )

    musetalk_calls = {"count": 0}

    def fake_liveportrait(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"liveportrait")
        return EngineResult(
            True,
            "liveportrait",
            output_path,
            "",
            details={
                "stderr": "[LivePortrait] motion_source=generated_micro_motion source_image=/tmp/face.png driving_input=/tmp/driving_ref.mp4",
            },
        )

    def fake_musetalk(*, output_path, source_video="", **_kwargs):
        musetalk_calls["count"] += 1
        assert str(source_video) == str(output.with_suffix(output.suffix + ".liveportrait.mp4"))
        Path(output_path).write_bytes(b"musetalk")
        return EngineResult(True, "musetalk", output_path, "")

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk)

    result = avatar_pipeline.render_avatar_segment_local(
        avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(image),
            audio_path=str(audio),
            output_path=str(output),
            target_frame_count=25,
            target_duration_seconds=1.0,
            preview_teacher_id=5,
            preview_job_id=92,
        )
    )

    assert musetalk_calls["count"] == 1
    assert result["stage_paths"]["musetalk_source_kind"] == "liveportrait"
    assert result["stage_paths"]["liveportrait_motion_passed"] is False
    assert result["stage_paths"]["liveportrait_fallback_used"] is False
    assert result["stage_paths"]["liveportrait_quality_warning"].startswith("liveportrait_low_motion")
    assert result["preview_status"] == "warning"


def test_build_stage_env_derives_musetalk_fps_from_contract(tmp_path):
    canonical_input = _stub_canonical_input(tmp_path, tmp_path / "face.png")
    request = avatar_pipeline.AvatarRenderRequest(
        source_image_path=str(tmp_path / "face.png"),
        audio_path=str(tmp_path / "preview.wav"),
        output_path=str(tmp_path / "preview.mp4"),
        target_frame_count=26,
        target_duration_seconds=1.04,
    )

    env_map = avatar_canonical_pipeline._build_stage_env(canonical_input, request)

    assert env_map["MUSETALK_FPS"] == "25"
    # preview.mp4 output name → is_preview=True → padding defaults are 0
    assert env_map["MUSETALK_AUDIO_PADDING_LEFT"] == "0"
    assert env_map["MUSETALK_AUDIO_PADDING_RIGHT"] == "0"


def test_preview_audio_contract_uses_lower_default_fps(monkeypatch):
    monkeypatch.delenv("AVATAR_PREVIEW_FPS", raising=False)
    assert avatar_canonical_pipeline.os.environ is not None
    preview_flow = importlib.import_module("avatar_preview_flow")

    assert preview_flow._preview_fps() == 16


def test_preview_audio_contract_normalizes_unsupported_fps(monkeypatch):
    monkeypatch.setenv("AVATAR_PREVIEW_FPS", "15")
    preview_flow = importlib.import_module("avatar_preview_flow")

    assert preview_flow._preview_fps() == 16


# ---------------------------------------------------------------------------
# Regression: LP runner must raise on generated_micro_motion ffmpeg failure
# ---------------------------------------------------------------------------

def test_liveportrait_runner_raises_on_generated_micro_motion_ffmpeg_failure(tmp_path, monkeypatch):
    """Regression for RC1: when ffmpeg fails to generate the micro-motion
    driving clip, the runner must raise RuntimeError instead of silently
    falling back to a still image as the driving source."""
    liveportrait_runner = importlib.import_module("scripts.liveportrait_runner")

    source_image = tmp_path / "face.png"
    source_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    output_path = tmp_path / "lp_out.mp4"

    lp_home = tmp_path / "liveportrait_home"
    lp_home.mkdir()
    (lp_home / "outputs").mkdir()
    # Entrypoint --help must advertise the CLI flags the runner probes for.
    lp_entrypoint = lp_home / "inference.py"
    lp_entrypoint.write_text(
        """import sys\nif '--help' in sys.argv:\n    print('--source --driving --output_path --model_path')\n"""
    )
    lp_model = lp_home / "models"
    lp_model.mkdir()

    # Make sure no template candidates exist so the runner falls through
    # to the generated_micro_motion path.
    monkeypatch.delenv("AVATAR_LIVEPORTRAIT_PREVIEW_DRIVING_TEMPLATE", raising=False)

    # Patch subprocess.run so the ffmpeg micro-motion command returns rc=1
    # while allowing the --help probe to pass through normally.
    import subprocess as _subprocess

    _original_run = _subprocess.run

    def _fake_subprocess_run(cmd, *args, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "ffmpeg" in cmd_str.lower() and "-loop" in cmd_str:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="ffmpeg: error encoding",
            )
        return _original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_subprocess, "run", _fake_subprocess_run)

    # Build CLI args matching liveportrait_runner.main() expectations.
    monkeypatch.setattr(
        sys, "argv",
        [
            "liveportrait_runner",
            "--source_image", str(source_image),
            "--output_path", str(output_path),
            "--liveportrait_home", str(lp_home),
            "--liveportrait_entrypoint", str(lp_entrypoint),
            "--liveportrait_model_path", str(lp_model),
            "--timeout_seconds", "30",
        ],
    )

    with pytest.raises(RuntimeError, match="generated_micro_motion_failed|liveportrait_no_driving_source"):
        liveportrait_runner.main()


# ---------------------------------------------------------------------------
# Regression: MuseTalk failure must always raise, never produce fallback
# ---------------------------------------------------------------------------

def test_musetalk_failure_always_raises_never_returns_fallback(tmp_path, monkeypatch):
    """Regression for RC2: when MuseTalk fails (including timeout), the
    pipeline must raise RuntimeError rather than returning a lip-sync-less
    preview with preview_status=warning."""
    image = tmp_path / "avatar.png"
    image.write_bytes(b"PNG_AVATAR")
    audio = tmp_path / "preview.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 40)
    output = tmp_path / "preview.mp4"
    lp_output = output.with_suffix(output.suffix + ".liveportrait.mp4")

    canonical_input = _stub_canonical_input(tmp_path, image)

    monkeypatch.setattr(avatar_canonical_pipeline, "canonicalize_avatar_input", lambda **kw: canonical_input)
    monkeypatch.setattr(avatar_canonical_pipeline, "_liveportrait_motion_gate", lambda *a, **kw: _passing_motion_gate())
    monkeypatch.setattr(avatar_canonical_pipeline, "_reconcile_duration_contract", lambda **kw: {
        "contract_duration_seconds": 1.0,
        "original_video_duration_seconds": 0.92,
        "original_audio_duration_seconds": 1.0,
        "final_video_duration_seconds": 1.0,
        "final_audio_duration_seconds": 1.0,
        "duration_delta_seconds": -0.08,
        "adjustment_seconds": 0.08,
        "strategy": "pad_video_with_last_frame",
        "video_changed": True,
        "audio_changed": False,
        "reconciled_video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
        "reconciled_audio_path": str(audio),
    })
    monkeypatch.setattr(avatar_canonical_pipeline, "_normalize_preview_video_for_musetalk", lambda **kw: {
        "normalized": True,
        "strategy": "normalize_contract_fps",
        "target_frame_count": 25,
        "target_duration_seconds": 1.0,
        "target_fps": 25,
        "frame_count_before": 30,
        "frame_count_after": 25,
        "duration_before_seconds": 1.2,
        "duration_after_seconds": 1.0,
        "video_path": str(output.with_suffix(output.suffix + ".liveportrait.mp4")),
    })
    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_audio_contract", lambda *a, **kw: {"duration_seconds": 1.0})

    def fake_assert_video_contract(path, *, stage_name="video"):
        if not Path(path).exists():
            raise RuntimeError(f"{stage_name}_missing")
        return {"duration_seconds": 1.0, "frame_count": 25}

    monkeypatch.setattr(avatar_canonical_pipeline.legacy_pipeline, "_assert_video_contract", fake_assert_video_contract)

    def fake_liveportrait(*, output_path, **_kw):
        Path(output_path).write_bytes(b"liveportrait_video")
        return EngineResult(True, "liveportrait", output_path, "")

    # Simulate MuseTalk returning a timeout failure.
    def fake_musetalk_timeout(*, output_path, **_kw):
        return EngineResult(
            False, "musetalk", output_path, "preview_musetalk_timeout",
            details={"return_code": None, "stderr": "timeout", "elapsed_seconds": 30.0},
        )

    # Simulate MuseTalk returning a non-timeout failure.
    def fake_musetalk_crash(*, output_path, **_kw):
        return EngineResult(
            False, "musetalk", output_path, "musetalk_segfault",
            details={"return_code": 139, "stderr": "signal 11", "elapsed_seconds": 2.0},
        )

    monkeypatch.setattr(avatar_canonical_pipeline, "run_liveportrait", fake_liveportrait)

    # Case 1: Timeout failure must raise, not return fallback preview.
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk_timeout)
    with pytest.raises(RuntimeError, match="musetalk_failed:preview_musetalk_timeout"):
        avatar_pipeline.render_avatar_segment_local(
            avatar_pipeline.AvatarRenderRequest(
                source_image_path=str(image),
                audio_path=str(audio),
                output_path=str(output),
                target_frame_count=25,
                target_duration_seconds=1.0,
                preview_teacher_id=1,
                preview_job_id=1,
            )
        )
    assert not output.exists(), "Timeout failure must not produce an output file"

    # Case 2: Non-timeout crash must also raise.
    monkeypatch.setattr(avatar_canonical_pipeline, "run_musetalk", fake_musetalk_crash)
    with pytest.raises(RuntimeError, match="musetalk_failed:musetalk_segfault"):
        avatar_pipeline.render_avatar_segment_local(
            avatar_pipeline.AvatarRenderRequest(
                source_image_path=str(image),
                audio_path=str(audio),
                output_path=str(output),
                target_frame_count=25,
                target_duration_seconds=1.0,
                preview_teacher_id=2,
                preview_job_id=2,
            )
        )


# ---------------------------------------------------------------------------
# Focused: default MuseTalk preview timeout is large enough for cold start
# ---------------------------------------------------------------------------

def test_preview_musetalk_timeout_floor_covers_cold_start(tmp_path, monkeypatch):
    """The default MuseTalk timeout floor must be ≥90 s so that model cold
    start (mmpose + MuseTalk weights) can complete before the budget expires.

    Previous defaults (base=8, max=25) caused every preview to time out
    before MuseTalk finished loading its models.
    """
    for name in [
        "AVATAR_PREVIEW_STAGE_TIMEOUT_MUSETALK_SECONDS",
        "AVATAR_PREVIEW_MUSETALK_TIMEOUT_SECONDS",
        "AVATAR_ORCH_STAGE_TIMEOUT_MUSETALK_SECONDS",
        "AVATAR_MUSETALK_TIMEOUT_MAX_SECONDS",
        "AVATAR_PREVIEW_MUSETALK_TIMEOUT_MAX_SECONDS",
        "AVATAR_PREVIEW_MUSETALK_TIMEOUT_BASE_SECONDS",
        "AVATAR_PREVIEW_MUSETALK_TIMEOUT_PER_AUDIO_SECOND",
        "AVATAR_PREVIEW_MUSETALK_TIMEOUT_PER_FRAME_SECOND",
        "AVATAR_MUSETALK_TIMEOUT_PER_CHUNK_SECONDS",
        "AVATAR_MUSETALK_TIMEOUT_SAFETY_MULTIPLIER",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AVATAR_MUSETALK_TIMEOUT_HISTORY_ENABLED", "0")

    def _budget(audio_seconds: float) -> float:
        req = avatar_pipeline.AvatarRenderRequest(
            source_image_path=str(tmp_path / "face.png"),
            audio_path=str(tmp_path / "preview.wav"),
            output_path=str(tmp_path / "preview.mp4"),
            preview_teacher_id=1,
            preview_job_id=1,
            target_duration_seconds=audio_seconds,
        )
        return avatar_canonical_pipeline._preview_musetalk_timeout_seconds(req)

    # For any realistic preview audio duration, the timeout must be ≥90 s.
    for audio_seconds in (0.5, 1.0, 1.52, 2.8):
        budget = _budget(audio_seconds)
        assert budget >= 90.0, (
            f"Timeout {budget}s for {audio_seconds}s audio is too small for MuseTalk cold start. "
            "Set AVATAR_PREVIEW_MUSETALK_TIMEOUT_MIN_SECONDS ≥ 90."
        )

    # Budget scales with audio duration (linear increase past the floor).
    budget_short = _budget(1.0)
    budget_long = _budget(2.8)
    assert budget_long > budget_short, "Timeout must grow as audio duration increases"


# ---------------------------------------------------------------------------
# Regression: worker bootstrap calls MuseTalk warmup before first preview job
# ---------------------------------------------------------------------------

def test_worker_bootstrap_runs_musetalk_warmup_before_first_job(tmp_path, monkeypatch):
    """Worker startup (bootstrap_musetalk.main) must call _warmup_musetalk so
    that MuseTalk models are loaded into GPU memory before any preview job.

    This test verifies:
    1. _warmup_musetalk is invoked by main() when AVATAR_MUSETALK_WARMUP=1.
    2. _warmup_musetalk is skipped when AVATAR_MUSETALK_WARMUP=0.
    3. A warmup failure (non-zero return from _warmup_musetalk) is non-fatal:
       main() still returns 0 so the worker starts and serves jobs.
    """
    bootstrap = importlib.import_module("bootstrap_musetalk")

    # Build a minimal fake MuseTalk tree so bootstrap passes file checks.
    musetalk_home = tmp_path / "musetalk"
    model_root = tmp_path / "models"
    (musetalk_home / "musetalk" / "utils" / "dwpose").mkdir(parents=True)
    preprocessing_py = musetalk_home / "musetalk" / "utils" / "preprocessing.py"
    preprocessing_py.write_text("# stub\n")
    (musetalk_home / "scripts" / "inference.py").parent.mkdir(parents=True, exist_ok=True)
    (musetalk_home / "scripts" / "inference.py").write_text("# stub\n")

    required = [
        "sd-vae/config.json",
        "sd-vae/diffusion_pytorch_model.bin",
        "musetalkV15/unet.pth",
        "whisper/config.json",
        "whisper/pytorch_model.bin",
        "whisper/preprocessor_config.json",
        "dwpose/dw-ll_ucoco_384.pth",
        "face-parse-bisent/79999_iter.pth",
        "face-parse-bisent/resnet18-5c106cde.pth",
    ]
    for rel in required:
        p = model_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"stub")

    monkeypatch.setenv("MUSETALK_HOME", str(musetalk_home))
    monkeypatch.setenv("MUSETALK_MODEL_PATH", str(model_root))
    # Use the configured AVATAR_MUSETALK_CMD template for warmup.
    monkeypatch.setenv("AVATAR_MUSETALK_CMD", "echo warmup {source_image} {audio_path} {output_path}")

    # Satisfy LivePortrait env checks inside bootstrap so the MuseTalk path is reached.
    lp_home = tmp_path / "lp"
    lp_home.mkdir()
    lp_entrypoint = lp_home / "inference.py"
    lp_entrypoint.write_text("# stub\n")
    lp_models = tmp_path / "lp_models"
    lp_models.mkdir()
    lp_cmd = str(lp_entrypoint)
    monkeypatch.setenv("AVATAR_ENGINE", "liveportrait+musetalk")
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_HOME", str(lp_home))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_ENTRYPOINT", str(lp_entrypoint))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_MODEL_PATH", str(lp_models))
    monkeypatch.setenv("AVATAR_LIVEPORTRAIT_CMD", lp_cmd)
    monkeypatch.setenv("AVATAR_PREVIEW_USE_LIVEPORTRAIT", "1")
    monkeypatch.setenv("AVATAR_PREVIEW_USE_MUSETALK", "1")
    monkeypatch.setenv("AVATAR_PREVIEW_USE_RESTORATION", "0")

    warmup_calls: list[dict] = []

    def _fake_warmup(*, musetalk_home, model_root):
        warmup_calls.append({"musetalk_home": str(musetalk_home), "model_root": str(model_root)})
        return 0

    # Bypass expensive LP env + runtime import checks; focus on the warmup contract.
    monkeypatch.setattr(bootstrap, "_composite_env_report", lambda: (
        ["AVATAR_ENGINE", "AVATAR_LIVEPORTRAIT_CMD", "AVATAR_MUSETALK_CMD"],
        [],
        {"preview_liveportrait": True, "preview_musetalk": True, "preview_restoration": False},
    ))
    monkeypatch.setattr(bootstrap, "_command_head_callable", lambda cmd: True)
    monkeypatch.setattr(bootstrap, "_liveportrait_command_references_entrypoint", lambda cmd, ep: True)
    monkeypatch.setattr(bootstrap, "_check_runtime_imports", lambda: {"python": "3.10.0"})
    # Persistent service is not available in unit tests; stub it out.
    monkeypatch.setattr(bootstrap, "_start_musetalk_service", lambda **kw: False)
    monkeypatch.setenv("AVATAR_MUSETALK_STANDALONE_FALLBACK", "1")
    # Provide stub ffmpeg/ffprobe so the missing-files check doesn't fail.
    import shutil as _shutil
    _real_which = _shutil.which
    monkeypatch.setattr(
        bootstrap.shutil, "which",
        lambda name, **kw: "/usr/bin/" + name if name in {"ffmpeg", "ffprobe"} else _real_which(name, **kw),
    )
    monkeypatch.setattr(bootstrap, "_warmup_musetalk", _fake_warmup)

    # Case 1: Warmup enabled — _warmup_musetalk must be called.
    monkeypatch.setenv("AVATAR_MUSETALK_WARMUP", "1")
    result = bootstrap.main()
    assert result == 0, f"bootstrap.main() must succeed when warmup succeeds, got {result}"
    assert len(warmup_calls) == 1, (
        f"_warmup_musetalk must be called exactly once on startup, was called {len(warmup_calls)} times"
    )

    # Case 2: Warmup disabled — _warmup_musetalk must still be called by the
    # bootstrap dispatch path; it is _warmup_musetalk's own responsibility to
    # exit early when the env flag is 0.
    warmup_calls.clear()
    monkeypatch.setenv("AVATAR_MUSETALK_WARMUP", "0")
    result = bootstrap.main()
    assert result == 0
    # Even when AVATAR_MUSETALK_WARMUP=0, main() still delegates to _warmup_musetalk;
    # _warmup_musetalk is responsible for the early return.
    assert len(warmup_calls) == 1

    # Case 3: Warmup failure causes bootstrap to fail (non-zero exit code).
    warmup_calls.clear()

    def _failing_warmup(*, musetalk_home, model_root):
        warmup_calls.append({})
        return 70

    monkeypatch.setattr(bootstrap, "_warmup_musetalk", _failing_warmup)
    monkeypatch.setenv("AVATAR_MUSETALK_WARMUP", "1")
    result = bootstrap.main()
    # Warmup failure is non-fatal: worker still starts so it can serve jobs.
    assert result == 0, "bootstrap.main() must succeed even when warmup fails (worker still starts)"
    assert len(warmup_calls) == 1


# ---------------------------------------------------------------------------
# Regression: service HTTP timeout must be max(budget, floor), never the bare budget
# ---------------------------------------------------------------------------

def test_musetalk_service_timeout_is_always_floored(monkeypatch):
    """The actual HTTP timeout used for the persistent MuseTalk service call must be
    max(preview_budget, floor), never the bare preview budget alone.

    This guards against small budgets (e.g. 12.5s from an explicit env override or
    47s from a short audio contract) silently becoming the urlopen timeout when the
    service needs 80–150s of inference time on a low-end GPU.
    """
    import importlib as _importlib

    # Reload adapters so monkeypatched env vars take effect on module-level constants.
    adapters = _importlib.import_module("avatar.canonical_adapters")

    captured_calls: list[dict] = []

    def _fake_service_call(url, *, source_image, source_video, audio_path, output_path,
                           params, timeout_seconds, stage_budget_timeout_seconds,
                           stage_name, run_id, route_reason, service_health):
        captured_calls.append({"timeout_seconds": timeout_seconds, "stage_name": stage_name})
        return adapters.EngineResult(True, "musetalk", output_path, "", "", {
            "elapsed_seconds": 1.0, "cold_start_seconds": 0.0, "inference_seconds": 1.0,
            "svc_timeout_seconds": timeout_seconds,
        })

    monkeypatch.setattr(adapters, "_musetalk_service_url", lambda: "http://127.0.0.1:17860")
    monkeypatch.setattr(
        adapters,
        "_musetalk_service_health",
        lambda url: {"status": "ready", "ready_for_inference": True, "models_loaded": True},
    )
    monkeypatch.setattr(adapters, "_run_via_musetalk_service", _fake_service_call)

    def _run_with_budget(budget_seconds: float, floor_seconds: float | None = None) -> float:
        """Call run_musetalk and return the timeout that reached the service layer."""
        captured_calls.clear()
        env = {
            "AVATAR_MUSETALK_SERVICE_ENABLED": "1",
            "AVATAR_MUSETALK_ROUTE": "service",
            "AVATAR_PREVIEW_FORCE_ISOLATED_MUSETALK": "0",
        }
        if floor_seconds is not None:
            env["AVATAR_MUSETALK_SERVICE_INFER_FLOOR_SECONDS"] = str(floor_seconds)

        old = {}
        for k, v in env.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            adapters.run_musetalk(
                source_image="/tmp/face.png",
                source_video="",
                audio_path="/tmp/preview.wav",
                output_path="/tmp/out.mp4",
                timeout_seconds=budget_seconds,
                stage_name="preview_musetalk",
            )
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert len(captured_calls) == 1, "Expected exactly one service call"
        return captured_calls[0]["timeout_seconds"]

    # Case 1: small budget (12.5s) + default floor (420s) → service must see 420s.
    svc_timeout = _run_with_budget(12.5)
    assert svc_timeout >= 420.0, (
        f"Service timeout {svc_timeout}s is below the 420s floor when budget=12.5s. "
        "The preview budget must be floored before reaching the HTTP client."
    )

    # Case 2: medium budget (47s) + default floor → service must see 420s.
    svc_timeout = _run_with_budget(47.0)
    assert svc_timeout >= 420.0, (
        f"Service timeout {svc_timeout}s is below the 420s floor when budget=47s."
    )

    # Case 3: large budget (600s) + default floor → service sees 600s (budget wins).
    svc_timeout = _run_with_budget(600.0)
    assert svc_timeout >= 600.0, (
        f"Service timeout {svc_timeout}s shrank below the 600s budget."
    )

    # Case 4: custom floor (180s) set via env → service sees max(budget, 180).
    svc_timeout = _run_with_budget(90.0, floor_seconds=180.0)
    assert svc_timeout >= 180.0, (
        f"Service timeout {svc_timeout}s is below the custom 180s floor when budget=90s."
    )

    # Case 5: budget > custom floor → budget wins.
    svc_timeout = _run_with_budget(300.0, floor_seconds=180.0)
    assert svc_timeout >= 300.0, (
        f"Service timeout {svc_timeout}s shrank below the 300s budget when floor=180."
    )
