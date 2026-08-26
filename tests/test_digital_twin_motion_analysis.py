import json

from avatar.digital_twin.motion_analysis import build_motion_style_profile


def _performance_observations() -> list[dict]:
    observations = []
    for index in range(60):
        expressive = index >= 40
        observations.append(
            {
                "timestamp": index * 0.5,
                "face_present": True,
                "landmark_present": True,
                "frame_motion": 0.15 if expressive else (0.06 if index >= 20 else 0.01),
                "yaw": -15.0 + index * 0.5,
                "pitch": -4.0 + index * 0.12,
                "roll": -2.0 + index * 0.06,
                "gaze_x": 0.58 if index in {20, 21, 22} else 0.0,
                "gaze_y": 0.02,
                "eye_aspect_ratio": 0.08 if index in {10, 30} else 0.28,
                "mouth_open": 0.45 if expressive else 0.08,
                "smile_width": 0.55 if expressive else 0.34,
                "brow_raise": 0.30 if expressive else 0.16,
            }
        )
    return observations


def test_motion_style_profile_extracts_behavior_and_ranked_intervals():
    report = build_motion_style_profile(
        _performance_observations(),
        duration_seconds=30.0,
        source_fps=30.0,
        analyzer={"provider": "test", "available": True},
    )

    assert report["version"] == "motion-style-v2"
    assert report["status"] == "ready"
    assert report["accepted"] is True
    assert report["usable_for_motion_planning"] is True
    assert report["coverage"]["landmarks"] == 1.0
    assert report["head_pose"]["yaw_range"][1] - report["head_pose"]["yaw_range"][0] > 20.0
    assert report["blink"]["count"] == 2
    assert report["gaze"]["side_glance_events"] == [10.0]
    assert report["expression"]["p90_intensity"] > 0.5
    assert report["selected_intervals"]["calm"]
    assert report["selected_intervals"]["natural"]
    assert report["selected_intervals"]["expressive"]
    assert (
        report["selected_intervals"]["calm"][0]["score"]
        < report["selected_intervals"]["expressive"][0]["score"]
    )
    json.dumps(report, allow_nan=False)


def test_motion_style_profile_is_explicitly_limited_without_landmarks():
    observations = [
        {
            "timestamp": index * 2.5,
            "face_present": True,
            "landmark_present": False,
            "frame_motion": 0.02,
        }
        for index in range(10)
    ]

    report = build_motion_style_profile(
        observations,
        duration_seconds=25.0,
        source_fps=30.0,
        analyzer={"provider": "disabled", "available": False},
    )

    assert report["status"] == "limited"
    assert report["accepted"] is True
    assert report["usable_for_motion_planning"] is False
    assert "landmark_coverage_low" in report["warnings"]
    assert "behavioral_signals_unavailable" in report["warnings"]


def test_motion_style_profile_rejects_empty_observations():
    report = build_motion_style_profile([], duration_seconds=0.0, source_fps=0.0)

    assert report["status"] == "failed"
    assert report["accepted"] is False
    assert report["frames_sampled"] == 0
    assert report["selected_intervals"] == {"calm": [], "natural": [], "expressive": []}
