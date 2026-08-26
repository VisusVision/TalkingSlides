from avatar.digital_twin.motion_planning import build_personal_motion_plan


def _package(*, usable: bool = True) -> dict:
    def intervals(base_score: float) -> list[dict]:
        return [
            {
                "start_seconds": float(index * 8),
                "end_seconds": float(index * 8 + 8),
                "duration_seconds": 8.0,
                "score": base_score + index * 0.02,
                "motion_intensity": base_score,
                "expression_intensity": base_score,
                "head_activity": base_score / 2.0,
                "face_coverage": 1.0,
                "landmark_coverage": 0.95,
            }
            for index in range(3)
        ]

    return {
        "version": "motion-style-v2",
        "source_hash": "source-sha",
        "accepted": True,
        "usable_for_motion_planning": usable,
        "duration_seconds": 30.0,
        "selected_intervals": {
            "calm": intervals(0.20),
            "natural": intervals(0.50),
            "expressive": intervals(0.78),
        },
        "coverage": {"face_presence": 1.0, "landmarks": 0.95},
        "motion": {"p90_intensity": 0.78},
        "expression": {"p90_intensity": 0.74},
        "gaze": {"camera_contact_ratio": 0.8},
        "blink": {"per_minute": 14.0},
    }


def test_personal_motion_plan_maps_emotion_and_selects_a_semantic_window():
    plan = build_personal_motion_plan(
        _package(),
        {"emotion": "happy", "motion_intensity": 0.1},
        seed_material="render-1",
    )

    assert plan["version"] == "motion-plan-v2"
    assert plan["style"] == "expressive"
    assert plan["effective_intensity"] == 0.7
    assert plan["motion_preset"] == "natural_visible"
    assert plan["personal_window_selected"] is True
    assert plan["performance_window"]["source"] == "motion_style_v2"
    assert plan["selected_interval"]["style"] == "expressive"
    assert plan["fallback_reasons"] == []


def test_personal_motion_plan_is_deterministic_but_rotates_across_render_ids():
    package = _package()
    first = build_personal_motion_plan(package, {"motion_intensity": 0.5}, seed_material="same-render")
    second = build_personal_motion_plan(package, {"motion_intensity": 0.5}, seed_material="same-render")
    starts = {
        build_personal_motion_plan(package, {"motion_intensity": 0.5}, seed_material=f"render-{index}")[
            "selected_interval"
        ]["start_seconds"]
        for index in range(12)
    }

    assert first == second
    assert first["style"] == "natural"
    assert len(starts) > 1


def test_personal_motion_plan_uses_safe_fallback_for_limited_profile():
    plan = build_personal_motion_plan(
        _package(usable=False),
        {"emotion": "calm", "motion_intensity": 0.9},
        seed_material="render-limited",
    )

    assert plan["style"] == "calm"
    assert plan["motion_preset"] == "natural_conservative"
    assert plan["personal_window_selected"] is False
    assert plan["source"] == "performance_window_v1_fallback"
    assert plan["performance_window"]["enabled"] is False
    assert "motion_style_not_usable" in plan["fallback_reasons"]


def test_personal_motion_plan_rejects_invalid_candidate_intervals():
    package = _package()
    package["selected_intervals"]["natural"] = [
        {
            "start_seconds": 29.0,
            "end_seconds": 40.0,
            "duration_seconds": 11.0,
            "face_coverage": 1.0,
            "landmark_coverage": 1.0,
        }
    ]

    plan = build_personal_motion_plan(package, {}, seed_material="invalid-window")

    assert plan["style"] == "natural"
    assert plan["personal_window_selected"] is False
    assert plan["fallback_reasons"] == ["natural_interval_unavailable"]


def _prosody_profile() -> dict:
    return {
        "version": "prosody-v1",
        "status": "ready",
        "accepted": True,
        "duration_seconds": 6.0,
        "summary": {"speech_ratio": 0.7, "emphasis_count": 1},
        "warnings": [],
        "segments": [
            {"duration_seconds": 2.0, "style": "calm", "pause": True, "energy": 0.0},
            {"duration_seconds": 2.0, "style": "natural", "pause": False, "energy": 0.5},
            {
                "duration_seconds": 2.0,
                "style": "expressive",
                "pause": False,
                "energy": 0.9,
                "emphasis": True,
            },
        ],
    }


def test_personal_motion_plan_builds_audio_timed_personal_segments():
    plan = build_personal_motion_plan(
        _package(),
        {"emotion": "happy", "motion_intensity": 0.8},
        seed_material="prosody-render",
        prosody_profile=_prosody_profile(),
    )

    timeline = plan["performance_timeline"]
    assert plan["prosody_timeline_selected"] is True
    assert timeline["enabled"] is True
    assert timeline["source"] == "prosody_v1"
    assert timeline["duration_seconds"] == 6.0
    assert [segment["style"] for segment in timeline["segments"]] == [
        "calm",
        "natural",
        "expressive",
    ]
    assert [segment["output_start_seconds"] for segment in timeline["segments"]] == [0.0, 2.0, 4.0]
    assert plan["prosody_fallback_reasons"] == []


def test_unavailable_prosody_preserves_motion_planner_v2_fallback():
    plan = build_personal_motion_plan(
        _package(),
        {"motion_intensity": 0.5},
        seed_material="prosody-unavailable",
        prosody_profile={
            "version": "prosody-v1",
            "status": "unavailable",
            "accepted": False,
            "warnings": ["prosody_audio_near_silent"],
        },
    )

    assert plan["personal_window_selected"] is True
    assert plan["prosody_timeline_selected"] is False
    assert plan["performance_timeline"]["enabled"] is False
    assert plan["prosody_fallback_reasons"] == ["prosody_audio_near_silent"]


def test_long_prosody_region_is_split_to_stay_inside_personal_intervals():
    prosody = _prosody_profile()
    prosody["duration_seconds"] = 12.0
    prosody["segments"] = [
        {"duration_seconds": 10.0, "style": "expressive", "energy": 0.9, "emphasis": True},
        {"duration_seconds": 2.0, "style": "calm", "pause": True, "energy": 0.0},
    ]

    plan = build_personal_motion_plan(
        _package(),
        {"emotion": "happy", "motion_intensity": 0.8},
        seed_material="prosody-long",
        prosody_profile=prosody,
    )

    segments = plan["performance_timeline"]["segments"]
    assert plan["prosody_timeline_selected"] is True
    assert len(segments) == 3
    assert max(segment["duration_seconds"] for segment in segments) <= 7.85
    assert sum(segment["duration_seconds"] for segment in segments) == 12.0
