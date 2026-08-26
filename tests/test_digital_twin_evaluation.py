import json
from pathlib import Path
import subprocess
import sys

import pytest

from avatar.digital_twin.evaluation import (
    EvaluationContractError,
    evaluate_avatar_variants,
    render_evaluation_markdown,
)


def _variant(
    kind: str,
    *,
    identity: float = 0.90,
    lip_sync: float = 0.88,
    temporal: float = 0.90,
    motion: float = 0.55,
    duration_delta: float = 0.03,
    technical: bool = True,
    artifact: bool = False,
) -> dict:
    personal = kind in {"personal", "prosody"}
    prosody = kind == "prosody"
    return {
        "id": f"case-{kind}",
        "kind": kind,
        "input_fingerprint": "same-input-fingerprint",
        "quality_report": {
            "decision": "passed" if technical else "failed",
            "identity": {"score": identity, "passed": True, "assurance": "strong", "provider": "test"},
            "lip_sync": {"score": lip_sync, "passed": True, "assurance": "strong", "provider": "test"},
            "temporal": {"score": temporal, "passed": technical, "assurance": "verified", "provider": "test"},
            "technical": {
                "strict_validation_passed": technical,
                "audio_match": True,
                "duration_mismatch": False,
                "artifact_detected": artifact,
                "landmark_stable": True,
            },
        },
        "motion_validation": {
            "motion_real": technical,
            "audio_match": True,
            "duration_mismatch": False,
            "duration_delta_seconds": duration_delta,
            "duration_tolerance_seconds": 0.45,
            "avatar_visual_motion_score": motion,
            "face_artifacts_detected": artifact,
            "quality_checks": {
                "landmark_stable": True,
                "loop_detected": False,
                "face_artifact_detected": artifact,
            },
        },
        "motion_plan": {
            "personal_window_selected": personal,
            "prosody_timeline_selected": prosody,
            "execution": {
                "prosody_timeline_materialized": prosody,
                "renderer_driver_source": "source_video" if personal else "vetted_template",
                "renderer_reference_type": "video" if personal else "image",
                "personal_source_video_supplied": personal,
            },
        },
        "runtime": {"render_seconds": 12.0, "peak_vram_mb": 5_500.0},
        "artifacts": {"video_path": f"artifacts/{kind}.mp4"},
    }


def _manifest() -> dict:
    return {
        "suite_id": "portfolio-comparison-001",
        "variants": [
            _variant("generic", motion=0.50),
            _variant("personal", motion=0.62),
            _variant("prosody", motion=0.70),
        ],
    }


def test_evaluation_recommends_prosody_only_without_quality_regression():
    report = evaluate_avatar_variants(_manifest())

    assert report["version"] == "avatar-evaluation-v1"
    assert report["fair_comparison"] is True
    assert report["recommendation"] == "prosody"
    assert report["comparisons"]["personal_vs_generic"]["regressions"] == []
    assert report["comparisons"]["prosody_vs_personal"]["regressions"] == []
    assert report["comparisons"]["personal_vs_generic"]["deltas"]["motion_score"] == 0.12
    assert report["automated_claims"]["personal_motion_bound"] is True
    assert report["automated_claims"]["prosody_timing_materialized"] is True
    assert report["automated_claims"]["naturalness_improved"] == "manual_review_required"
    assert report["manual_review"]["minimum_reviewers"] == 3


def test_prosody_regression_falls_back_to_personal_recommendation():
    manifest = _manifest()
    manifest["variants"][2] = _variant("prosody", temporal=0.70, motion=0.70)

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "personal"
    regressions = report["comparisons"]["prosody_vs_personal"]["regressions"]
    assert "temporal_score_regressed" in regressions
    assert report["variants"][2]["eligible"] is False


def test_missing_personal_evidence_prevents_unsupported_recommendation():
    manifest = _manifest()
    manifest["variants"][1]["motion_plan"] = {}

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "generic"
    assert report["variants"][1]["expected_evidence_present"] is False
    assert report["variants"][2]["eligible"] is False


@pytest.mark.parametrize(
    ("personal_source_video_supplied", "renderer_driver_source", "renderer_reference_type"),
    [
        (True, "source_video", "video"),
        (False, "source_video", "image"),
        (False, "vetted_template", "video"),
    ],
)
def test_generic_baseline_with_a_personal_video_driver_is_not_eligible(
    personal_source_video_supplied,
    renderer_driver_source,
    renderer_reference_type,
):
    manifest = _manifest()
    manifest["variants"][0]["motion_plan"]["execution"].update(
        {
            "renderer_driver_source": renderer_driver_source,
            "renderer_reference_type": renderer_reference_type,
            "personal_source_video_supplied": personal_source_video_supplied,
        }
    )

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "inconclusive"
    assert report["fair_comparison"] is False
    assert report["variants"][0]["generic_baseline_isolated"] is False
    assert report["variants"][0]["expected_evidence_present"] is False
    assert report["variants"][0]["eligible"] is False
    assert report["automated_claims"]["generic_baseline_isolated"] is False


def test_generic_baseline_without_isolation_evidence_is_not_eligible():
    manifest = _manifest()
    manifest["variants"][0]["motion_plan"]["execution"] = {}

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "inconclusive"
    assert report["fair_comparison"] is False
    assert report["variants"][0]["generic_baseline_isolated"] is False
    assert report["variants"][0]["expected_evidence_present"] is False


def test_planned_but_unmaterialized_personal_window_is_not_eligible():
    manifest = _manifest()
    manifest["variants"][1]["motion_plan"]["execution"]["window_source"] = ""

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "generic"
    assert report["variants"][1]["personal_window_selected"] is True
    assert report["variants"][1]["personal_window_materialized"] is False
    assert report["automated_claims"]["personal_motion_bound"] is False


def test_explicit_personal_materialization_evidence_overrides_legacy_source_label():
    manifest = _manifest()
    execution = manifest["variants"][1]["motion_plan"]["execution"]
    execution["window_source"] = "motion_style_v2_natural"
    execution["personal_motion_materialized"] = True

    report = evaluate_avatar_variants(manifest)

    assert report["variants"][1]["personal_window_materialized"] is True
    assert report["automated_claims"]["personal_motion_bound"] is True


def test_explicit_failed_materialization_cannot_be_hidden_by_legacy_source_label():
    manifest = _manifest()
    execution = manifest["variants"][1]["motion_plan"]["execution"]
    execution["window_source"] = "motion_style_v2_natural"
    execution["personal_motion_materialized"] = False

    report = evaluate_avatar_variants(manifest)

    assert report["variants"][1]["personal_window_materialized"] is False
    assert report["automated_claims"]["personal_motion_bound"] is False


def test_missing_quality_signal_prevents_unsupported_recommendation():
    manifest = _manifest()
    manifest["variants"][1]["quality_report"]["identity"] = {}

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "generic"
    assert report["variants"][1]["missing_signals"] == ["identity"]
    assert report["variants"][1]["ready_for_comparison"] is False


def test_failed_quality_gate_is_a_regression_even_with_a_high_score():
    manifest = _manifest()
    manifest["variants"][2]["quality_report"]["identity"]["passed"] = False

    report = evaluate_avatar_variants(manifest)

    assert report["recommendation"] == "personal"
    assert "identity_gate_regressed" in report["comparisons"]["prosody_vs_personal"]["regressions"]
    assert report["variants"][2]["failed_signals"] == ["identity"]


def test_evaluation_rejects_unfair_input_fingerprints():
    manifest = _manifest()
    manifest["variants"][2]["input_fingerprint"] = "different-input"

    with pytest.raises(EvaluationContractError, match="evaluation_input_fingerprint_mismatch"):
        evaluate_avatar_variants(manifest)


def test_markdown_keeps_automated_and_human_claims_separate():
    rendered = render_evaluation_markdown(evaluate_avatar_variants(_manifest()))

    assert "| prosody |" in rendered
    assert "## Manual blind review" in rendered
    assert "transition smoothness" in rendered
    assert "do not prove human-perceived naturalness" in rendered
    assert "Automated recommendation: `prosody`" in rendered


def test_cli_resolves_evidence_files_and_writes_json_and_markdown(tmp_path):
    variants = []
    for kind, motion in (("generic", 0.50), ("personal", 0.62), ("prosody", 0.70)):
        evidence_path = tmp_path / f"{kind}.json"
        evidence_path.write_text(json.dumps(_variant(kind, motion=motion)), encoding="utf-8")
        variants.append(
            {
                "kind": kind,
                "id": f"cli-{kind}",
                "input_fingerprint": "same-input-fingerprint",
                "evidence_path": evidence_path.name,
            }
        )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"suite_id": "cli-suite", "variants": variants}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "report"
    script = Path(__file__).resolve().parents[1] / "services" / "scripts" / "evaluate_avatar_variants.py"

    process = subprocess.run(
        [sys.executable, str(script), str(manifest_path), "--output-dir", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["recommendation"] == "prosody"
    report = json.loads((output_dir / "avatar-evaluation.json").read_text(encoding="utf-8"))
    assert report["suite_id"] == "cli-suite"
    assert (output_dir / "avatar-evaluation.md").exists()


def test_cli_reports_missing_evidence_as_contract_error(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "variants": [
                    {"kind": "generic", "evidence_path": "missing.json"},
                    {"kind": "personal", "evidence_path": "missing.json"},
                    {"kind": "prosody", "evidence_path": "missing.json"},
                ]
            }
        ),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[1] / "services" / "scripts" / "evaluate_avatar_variants.py"

    process = subprocess.run(
        [sys.executable, str(script), str(manifest_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert process.returncode == 2
    assert json.loads(process.stderr)["error"].startswith("evaluation_file_missing:")


def test_cli_can_fail_ci_on_a_detected_regression(tmp_path):
    manifest = _manifest()
    manifest["variants"][2] = _variant("prosody", temporal=0.60, motion=0.70)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "services" / "scripts" / "evaluate_avatar_variants.py"

    process = subprocess.run(
        [sys.executable, str(script), str(manifest_path), "--fail-on-regression"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert process.returncode == 3
    result = json.loads(process.stdout)
    report = json.loads(Path(result["json_report"]).read_text(encoding="utf-8"))
    assert report["recommendation"] == "personal"
    assert "temporal_score_regressed" in report["comparisons"]["prosody_vs_personal"]["regressions"]
