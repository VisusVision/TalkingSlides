"""Versioned, comparison-first evaluation reports for Digital Twin renders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any


EVALUATION_VERSION = "avatar-evaluation-v1"
REQUIRED_VARIANTS = ("generic", "personal", "prosody")
_QUALITY_REGRESSION_TOLERANCE = 0.05
_DURATION_REGRESSION_TOLERANCE_SECONDS = 0.10


class EvaluationContractError(ValueError):
    """Raised when a comparison would not be fair or reproducible."""


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(float(minimum), min(float(maximum), _finite(value)))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _signal(payload: Any) -> dict[str, Any]:
    signal = _mapping(payload)
    raw_score = signal.get("score")
    score = None if raw_score is None else round(_clamp(raw_score), 5)
    passed = signal.get("passed")
    return {
        "score": score,
        "passed": passed if isinstance(passed, bool) else None,
        "assurance": str(signal.get("assurance") or "unavailable").strip().lower(),
        "provider": str(signal.get("provider") or "").strip(),
    }


def _motion_score(validation: Mapping[str, Any]) -> float:
    explicit = validation.get("avatar_visual_motion_score")
    if explicit is not None:
        return round(_clamp(explicit), 5)
    quality = _mapping(validation.get("quality_checks"))

    def ratio(value: Any, target: float) -> float:
        return _clamp(_finite(value) / max(target, 1e-9))

    return round(
        ratio(quality.get("eye_blink_change"), 0.0023) * 0.35
        + ratio(quality.get("head_motion_score"), 0.0045) * 0.25
        + ratio(validation.get("global_frame_diff_mean"), 0.35) * 0.20
        + ratio(quality.get("eye_movement_score"), 3.0) * 0.20,
        5,
    )


def _variant_metrics(raw_variant: Mapping[str, Any]) -> dict[str, Any]:
    variant = dict(raw_variant)
    kind = str(variant.get("kind") or "").strip().lower()
    quality_report = _mapping(variant.get("quality_report"))
    validation = _mapping(variant.get("motion_validation"))
    checks = _mapping(validation.get("quality_checks"))
    technical = _mapping(quality_report.get("technical"))
    motion_plan = _mapping(variant.get("motion_plan"))
    execution = _mapping(motion_plan.get("execution"))
    runtime = _mapping(variant.get("runtime"))

    identity = _signal(quality_report.get("identity"))
    lip_sync = _signal(quality_report.get("lip_sync"))
    temporal = _signal(quality_report.get("temporal"))
    technical_pass = bool(
        technical.get("strict_validation_passed", validation.get("motion_real", False))
    )
    audio_match = bool(technical.get("audio_match", validation.get("audio_match", False)))
    duration_mismatch = bool(
        technical.get("duration_mismatch", validation.get("duration_mismatch", False))
    )
    duration_delta = max(_finite(validation.get("duration_delta_seconds")), 0.0)
    duration_tolerance = max(_finite(validation.get("duration_tolerance_seconds"), 0.45), 0.01)
    duration_score = _clamp(1.0 - duration_delta / duration_tolerance)
    artifact = bool(
        technical.get("artifact_detected")
        or validation.get("face_artifacts_detected")
        or checks.get("face_artifact_detected")
        or checks.get("structural_face_artifact_detected")
        or checks.get("face_warp_detected")
        or checks.get("glitch_detected")
        or checks.get("drift_detected")
    )
    loop_detected = bool(checks.get("loop_detected"))
    landmark_stable = bool(technical.get("landmark_stable", checks.get("landmark_stable", True)))
    motion_score = _motion_score(validation)
    identity_score = float(identity["score"] or 0.0)
    lip_sync_score = float(lip_sync["score"] or 0.0)
    temporal_score = float(temporal["score"] or 0.0)
    automated_quality_score = round(
        100.0
        * (
            identity_score * 0.20
            + lip_sync_score * 0.25
            + temporal_score * 0.25
            + duration_score * 0.15
            + motion_score * 0.15
        ),
        2,
    )
    personal_window = bool(motion_plan.get("personal_window_selected"))
    personal_execution_reported = "window_source" in execution
    explicit_personal_materialization = execution.get("personal_motion_materialized")
    legacy_window_source = str(execution.get("window_source") or "")
    personal_window_materialized = bool(
        personal_window
        and (
            bool(explicit_personal_materialization)
            if explicit_personal_materialization is not None
            else (
                not personal_execution_reported
                or legacy_window_source == "motion_style_v2"
                or legacy_window_source.startswith("motion_style_v2_")
            )
        )
    )
    prosody_materialized = bool(
        execution.get("prosody_timeline_materialized")
        or variant.get("prosody_timeline_materialized")
    )
    generic_isolation_reported = all(
        key in execution
        for key in (
            "personal_source_video_supplied",
            "renderer_driver_source",
            "renderer_reference_type",
        )
    )
    renderer_driver_source = str(execution.get("renderer_driver_source") or "").strip().lower()
    renderer_reference_type = str(execution.get("renderer_reference_type") or "").strip().lower()
    generic_baseline_isolated = bool(
        kind != "generic"
        or (
            generic_isolation_reported
            and not bool(execution.get("personal_source_video_supplied"))
            and renderer_driver_source != "source_video"
            and renderer_reference_type == "image"
        )
    )
    appearance_source_policy = str(execution.get("appearance_source_policy") or "").strip()
    identity_motion_evidence_reported = all(
        key in execution for key in ("appearance_source_policy", "identity_motion_decoupled")
    )
    identity_motion_decoupled = bool(
        kind in {"personal", "prosody"}
        and identity_motion_evidence_reported
        and bool(execution.get("identity_motion_decoupled"))
        and appearance_source_policy
        in {
            "personal_image_original_identity_video_motion_v1",
            "personal_image_processed_identity_video_motion_v1",
        }
    )
    capability_coverage = 0.0
    if personal_window:
        capability_coverage += 0.5
    if prosody_materialized:
        capability_coverage += 0.5
    expected_evidence = bool(
        (kind == "generic" and generic_baseline_isolated)
        or (kind == "personal" and personal_window_materialized and identity_motion_decoupled)
        or (
            kind == "prosody"
            and personal_window_materialized
            and prosody_materialized
            and identity_motion_decoupled
        )
    )
    missing_signals = [
        name
        for name, signal in (("identity", identity), ("lip_sync", lip_sync), ("temporal", temporal))
        if signal["score"] is None
    ]
    failed_signals = [
        name
        for name, signal in (("identity", identity), ("lip_sync", lip_sync), ("temporal", temporal))
        if signal["passed"] is not True
    ]
    ready = bool(
        technical_pass
        and audio_match
        and not duration_mismatch
        and not artifact
        and landmark_stable
        and not missing_signals
        and not failed_signals
    )
    return {
        "id": str(variant.get("id") or kind),
        "kind": kind,
        "input_fingerprint": str(variant.get("input_fingerprint") or "").strip(),
        "automated_quality_score": automated_quality_score,
        "identity": identity,
        "lip_sync": lip_sync,
        "temporal": temporal,
        "motion_score": motion_score,
        "duration_score": round(duration_score, 5),
        "duration_delta_seconds": round(duration_delta, 5),
        "technical_pass": technical_pass,
        "audio_match": audio_match,
        "duration_mismatch": duration_mismatch,
        "artifact_detected": artifact,
        "loop_detected": loop_detected,
        "landmark_stable": landmark_stable,
        "personal_window_selected": personal_window,
        "personal_window_materialized": personal_window_materialized,
        "prosody_timeline_materialized": prosody_materialized,
        "generic_baseline_isolated": generic_baseline_isolated if kind == "generic" else None,
        "appearance_source_policy": appearance_source_policy,
        "identity_motion_decoupled": identity_motion_decoupled if kind != "generic" else None,
        "capability_coverage": round(capability_coverage, 2),
        "expected_evidence_present": expected_evidence,
        "ready_for_comparison": ready,
        "missing_signals": missing_signals,
        "failed_signals": failed_signals,
        "runtime": {
            "render_seconds": round(max(_finite(runtime.get("render_seconds")), 0.0), 3),
            "peak_vram_mb": round(max(_finite(runtime.get("peak_vram_mb")), 0.0), 1),
        },
        "artifacts": _mapping(variant.get("artifacts")),
    }


def _regressions(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    regressions: list[str] = []
    for key, label in (
        ("identity", "identity_score_regressed"),
        ("lip_sync", "lip_sync_score_regressed"),
        ("temporal", "temporal_score_regressed"),
    ):
        candidate_score = _mapping(candidate.get(key)).get("score")
        baseline_score = _mapping(baseline.get(key)).get("score")
        if candidate_score is not None and baseline_score is not None:
            if float(candidate_score) < float(baseline_score) - _QUALITY_REGRESSION_TOLERANCE:
                regressions.append(label)
        candidate_passed = _mapping(candidate.get(key)).get("passed")
        baseline_passed = _mapping(baseline.get(key)).get("passed")
        if baseline_passed is True and candidate_passed is not True:
            regressions.append(f"{key}_gate_regressed")
    if float(candidate.get("automated_quality_score") or 0.0) < float(baseline.get("automated_quality_score") or 0.0) - 3.0:
        regressions.append("automated_quality_score_regressed")
    if float(candidate.get("duration_delta_seconds") or 0.0) > (
        float(baseline.get("duration_delta_seconds") or 0.0) + _DURATION_REGRESSION_TOLERANCE_SECONDS
    ):
        regressions.append("duration_alignment_regressed")
    if bool(candidate.get("artifact_detected")) and not bool(baseline.get("artifact_detected")):
        regressions.append("artifact_introduced")
    if not bool(candidate.get("technical_pass")) and bool(baseline.get("technical_pass")):
        regressions.append("technical_validation_regressed")
    return list(dict.fromkeys(regressions))


def _improvements(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    improvements: list[str] = []
    if float(candidate.get("capability_coverage") or 0.0) > float(baseline.get("capability_coverage") or 0.0):
        improvements.append("capability_coverage_increased")
    if float(candidate.get("motion_score") or 0.0) >= float(baseline.get("motion_score") or 0.0) + 0.03:
        improvements.append("measured_motion_increased")
    if float(candidate.get("automated_quality_score") or 0.0) >= float(baseline.get("automated_quality_score") or 0.0) + 3.0:
        improvements.append("automated_quality_score_increased")
    if float(candidate.get("runtime", {}).get("peak_vram_mb") or 0.0) > 0.0:
        baseline_vram = float(baseline.get("runtime", {}).get("peak_vram_mb") or 0.0)
        if baseline_vram > 0.0 and float(candidate["runtime"]["peak_vram_mb"]) < baseline_vram:
            improvements.append("peak_vram_reduced")
    return improvements


def _deltas(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, float | None]:
    def signal_delta(key: str) -> float | None:
        candidate_score = _mapping(candidate.get(key)).get("score")
        baseline_score = _mapping(baseline.get(key)).get("score")
        if candidate_score is None or baseline_score is None:
            return None
        return round(float(candidate_score) - float(baseline_score), 5)

    candidate_runtime = _mapping(candidate.get("runtime"))
    baseline_runtime = _mapping(baseline.get("runtime"))
    return {
        "automated_quality_points": round(
            float(candidate.get("automated_quality_score") or 0.0)
            - float(baseline.get("automated_quality_score") or 0.0),
            2,
        ),
        "identity_score": signal_delta("identity"),
        "lip_sync_score": signal_delta("lip_sync"),
        "temporal_score": signal_delta("temporal"),
        "motion_score": round(
            float(candidate.get("motion_score") or 0.0)
            - float(baseline.get("motion_score") or 0.0),
            5,
        ),
        "duration_delta_seconds": round(
            float(candidate.get("duration_delta_seconds") or 0.0)
            - float(baseline.get("duration_delta_seconds") or 0.0),
            5,
        ),
        "render_seconds": round(
            float(candidate_runtime.get("render_seconds") or 0.0)
            - float(baseline_runtime.get("render_seconds") or 0.0),
            3,
        ),
        "peak_vram_mb": round(
            float(candidate_runtime.get("peak_vram_mb") or 0.0)
            - float(baseline_runtime.get("peak_vram_mb") or 0.0),
            1,
        ),
    }


def evaluate_avatar_variants(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Compare generic/personal/prosody evidence under one fair input contract."""

    payload = dict(manifest or {})
    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, Sequence) or isinstance(raw_variants, (str, bytes)):
        raise EvaluationContractError("evaluation_variants_missing")
    variants = [_variant_metrics(raw) for raw in raw_variants if isinstance(raw, Mapping)]
    by_kind: dict[str, dict[str, Any]] = {}
    for variant in variants:
        kind = str(variant["kind"])
        if kind not in REQUIRED_VARIANTS:
            raise EvaluationContractError(f"evaluation_variant_kind_invalid:{kind or 'missing'}")
        if kind in by_kind:
            raise EvaluationContractError(f"evaluation_variant_duplicate:{kind}")
        by_kind[kind] = variant
    missing = [kind for kind in REQUIRED_VARIANTS if kind not in by_kind]
    if missing:
        raise EvaluationContractError(f"evaluation_variants_missing:{','.join(missing)}")
    fingerprints = {str(variant["input_fingerprint"]) for variant in by_kind.values()}
    if "" in fingerprints or len(fingerprints) != 1:
        raise EvaluationContractError("evaluation_input_fingerprint_mismatch")

    generic = by_kind["generic"]
    personal = by_kind["personal"]
    prosody = by_kind["prosody"]
    comparisons = {
        "personal_vs_generic": {
            "baseline": "generic",
            "candidate": "personal",
            "deltas": _deltas(personal, generic),
            "regressions": _regressions(personal, generic),
            "improvements": _improvements(personal, generic),
        },
        "prosody_vs_personal": {
            "baseline": "personal",
            "candidate": "prosody",
            "deltas": _deltas(prosody, personal),
            "regressions": _regressions(prosody, personal),
            "improvements": _improvements(prosody, personal),
        },
    }
    generic_eligible = bool(generic["ready_for_comparison"] and generic["expected_evidence_present"])
    personal_eligible = bool(
        generic_eligible
        and personal["ready_for_comparison"]
        and personal["expected_evidence_present"]
        and not comparisons["personal_vs_generic"]["regressions"]
    )
    prosody_eligible = bool(
        prosody["ready_for_comparison"]
        and prosody["expected_evidence_present"]
        and personal_eligible
        and not comparisons["prosody_vs_personal"]["regressions"]
    )
    generic["eligible"] = generic_eligible
    personal["eligible"] = personal_eligible
    prosody["eligible"] = prosody_eligible
    if prosody_eligible:
        recommendation = "prosody"
    elif personal_eligible:
        recommendation = "personal"
    elif generic_eligible:
        recommendation = "generic"
    else:
        recommendation = "inconclusive"

    heuristic_signals = sorted(
        {
            f"{kind}.{signal_name}"
            for kind, variant in by_kind.items()
            for signal_name in ("identity", "lip_sync", "temporal")
            if str(variant[signal_name]["assurance"]) not in {"strong", "biometric", "verified"}
        }
    )
    fair_comparison = bool(generic["generic_baseline_isolated"])
    automated_claims = {
        "fair_input_contract": fair_comparison,
        "generic_baseline_isolated": bool(generic["generic_baseline_isolated"]),
        "personal_motion_bound": bool(personal["personal_window_materialized"]),
        "personal_identity_motion_decoupled": bool(personal["identity_motion_decoupled"]),
        "prosody_timing_materialized": bool(prosody["prosody_timeline_materialized"]),
        "prosody_identity_motion_decoupled": bool(prosody["identity_motion_decoupled"]),
        "personal_quality_non_regression": not bool(comparisons["personal_vs_generic"]["regressions"]),
        "prosody_quality_non_regression": not bool(comparisons["prosody_vs_personal"]["regressions"]),
        "naturalness_improved": "manual_review_required",
        "emotion_fit_improved": "manual_review_required",
    }
    raw_context = _mapping(payload.get("context"))
    report_context: dict[str, Any] = {}
    for key in ("case_id", "script_hash", "seed", "quality_preset"):
        value = raw_context.get(key)
        if isinstance(value, (str, int, bool)) or (
            isinstance(value, float) and math.isfinite(value)
        ):
            report_context[key] = value
    model_versions = raw_context.get("model_versions")
    if isinstance(model_versions, Mapping):
        report_context["model_versions"] = {
            str(key): str(value)
            for key, value in model_versions.items()
            if isinstance(value, (str, int, bool))
            or (isinstance(value, float) and math.isfinite(value))
        }
    return {
        "version": EVALUATION_VERSION,
        "suite_id": str(payload.get("suite_id") or "avatar-evaluation").strip()[:120],
        "fair_comparison": fair_comparison,
        "input_fingerprint": next(iter(fingerprints)),
        "context": report_context,
        "recommendation": recommendation,
        "variants": [by_kind[kind] for kind in REQUIRED_VARIANTS],
        "comparisons": comparisons,
        "automated_claims": automated_claims,
        "manual_review": {
            "required": True,
            "blind_order_required": True,
            "scale": "1-5",
            "criteria": [
                "identity_consistency",
                "lip_sync_naturalness",
                "head_and_expression_naturalness",
                "transition_smoothness",
                "emotion_and_emphasis_fit",
            ],
            "minimum_reviewers": 3,
        },
        "warnings": (
            ["heuristic_or_unverified_signals_present"] if heuristic_signals else []
        ),
        "heuristic_signals": heuristic_signals,
        "limitations": [
            "Automated motion metrics do not prove human-perceived naturalness.",
            "Heuristic identity or lip-sync signals cannot support biometric-grade claims.",
            "This report does not establish parity with commercial avatar products.",
        ],
    }


def render_evaluation_markdown(report: Mapping[str, Any]) -> str:
    """Render a stable, review-friendly Markdown report from evaluation JSON."""

    payload = dict(report or {})
    variants = [dict(item) for item in payload.get("variants") or [] if isinstance(item, Mapping)]
    lines = [
        f"# Avatar Evaluation Lab - {payload.get('suite_id') or 'evaluation'}",
        "",
        f"- Contract: `{payload.get('version') or ''}`",
        f"- Fair comparison: `{'yes' if payload.get('fair_comparison') else 'no'}`",
        f"- Input fingerprint: `{str(payload.get('input_fingerprint') or '')[:16]}`",
        f"- Automated recommendation: `{payload.get('recommendation') or 'inconclusive'}`",
        "",
        "## Automated evidence",
        "",
        "| Variant | Quality | Motion | Identity | Lip sync | Temporal | Duration delta | Identity/motion split | Personal exec | Prosody exec | Eligible |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|:---:|",
    ]
    for variant in variants:
        lines.append(
            "| {kind} | {quality:.2f} | {motion:.3f} | {identity} | {lip} | {temporal} | {duration:.3f}s | {identity_motion} | {personal} | {prosody} | {eligible} |".format(
                kind=str(variant.get("kind") or ""),
                quality=float(variant.get("automated_quality_score") or 0.0),
                motion=float(variant.get("motion_score") or 0.0),
                identity="n/a" if variant.get("identity", {}).get("score") is None else f"{float(variant['identity']['score']):.3f}",
                lip="n/a" if variant.get("lip_sync", {}).get("score") is None else f"{float(variant['lip_sync']['score']):.3f}",
                temporal="n/a" if variant.get("temporal", {}).get("score") is None else f"{float(variant['temporal']['score']):.3f}",
                duration=float(variant.get("duration_delta_seconds") or 0.0),
                identity_motion=(
                    "n/a"
                    if str(variant.get("kind") or "") == "generic"
                    else ("yes" if variant.get("identity_motion_decoupled") else "no")
                ),
                personal="yes" if variant.get("personal_window_materialized") else "no",
                prosody="yes" if variant.get("prosody_timeline_materialized") else "no",
                eligible="yes" if variant.get("eligible") else "no",
            )
        )
    lines.extend(["", "## Regression checks", ""])
    for name, comparison in dict(payload.get("comparisons") or {}).items():
        regressions = list(dict(comparison).get("regressions") or [])
        improvements = list(dict(comparison).get("improvements") or [])
        deltas = dict(dict(comparison).get("deltas") or {})
        lines.append(
            f"- `{name}`: quality_delta={float(deltas.get('automated_quality_points') or 0.0):+.2f}; "
            f"motion_delta={float(deltas.get('motion_score') or 0.0):+.3f}; "
            f"regressions={regressions or ['none']}; improvements={improvements or ['none']}"
        )
    lines.extend(
        [
            "",
            "## Manual blind review",
            "",
            "Rate every randomized clip from 1-5. Use at least three reviewers.",
            "",
            "| Criterion | Generic | Personal | Prosody | Notes |",
            "|---|:---:|:---:|:---:|---|",
        ]
    )
    for criterion in dict(payload.get("manual_review") or {}).get("criteria") or []:
        lines.append(f"| {str(criterion).replace('_', ' ')} |  |  |  |  |")
    lines.extend(["", "## Claim boundary", ""])
    for limitation in payload.get("limitations") or []:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)
