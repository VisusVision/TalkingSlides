import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "services") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "services"))

from avatar.pipeline import _validate_preview_motion_base  # noqa: E402  # type: ignore


def _stage_report(quality_checks: dict):
    return {
        "frame_count": 124,
        "validation": {
            "min_frames": 18,
            "quality_checks": quality_checks,
        },
    }


def test_liveportrait_base_gate_does_not_fail_for_lipsync_minima_only():
    quality = {
        "unique_frames": 56,
        "loop_detected": False,
        "drift_detected": False,
        "glitch_detected": False,
        "mouth_artifact_detected": False,
        "eye_artifact_detected": False,
        "face_warp_detected": False,
        "landmark_stable": True,
        "face_artifact_detected": True,  # can be true only due to low mouth/blink deltas
        "structural_face_artifact_detected": False,
        "mouth_openness_change": 0.00318,
        "min_mouth_open_change": 0.0035,
        "eye_blink_change": 0.002627,
        "min_eye_blink_change": 0.0025,
    }
    ok, reason = _validate_preview_motion_base(_stage_report(quality))
    assert ok is True
    assert reason == ""


def test_liveportrait_base_gate_fails_for_structural_artifacts():
    quality = {
        "unique_frames": 56,
        "loop_detected": False,
        "drift_detected": False,
        "glitch_detected": False,
        "mouth_artifact_detected": False,
        "eye_artifact_detected": False,
        "face_warp_detected": True,
        "landmark_stable": True,
        "face_artifact_detected": True,
        "structural_face_artifact_detected": True,
    }
    ok, reason = _validate_preview_motion_base(_stage_report(quality))
    assert ok is False
    assert reason == "liveportrait_face_warp"
