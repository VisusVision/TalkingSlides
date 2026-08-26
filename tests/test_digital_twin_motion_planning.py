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
