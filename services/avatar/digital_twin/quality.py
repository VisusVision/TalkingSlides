from __future__ import annotations

from dataclasses import dataclass

from .domain import QualityReport


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    identity_similarity: float = 0.82
    voice_similarity: float = 0.78
    lip_sync: float = 0.80
    temporal_stability: float = 0.82
    expression_naturalness: float = 0.72
    gaze_naturalness: float = 0.68
    body_anatomy: float = 0.76
    visual_fidelity: float = 0.78


def apply_quality_thresholds(
    report: QualityReport,
    thresholds: QualityThresholds | None = None,
    *,
    require_provenance: bool = True,
) -> QualityReport:
    limits = thresholds or QualityThresholds()
    scores = {
        "identity_similarity": (report.identity_similarity, limits.identity_similarity),
        "voice_similarity": (report.voice_similarity, limits.voice_similarity),
        "lip_sync": (report.lip_sync, limits.lip_sync),
        "temporal_stability": (report.temporal_stability, limits.temporal_stability),
        "expression_naturalness": (report.expression_naturalness, limits.expression_naturalness),
        "gaze_naturalness": (report.gaze_naturalness, limits.gaze_naturalness),
        "body_anatomy": (report.body_anatomy, limits.body_anatomy),
        "visual_fidelity": (report.visual_fidelity, limits.visual_fidelity),
    }
    failures = [
        f"{name}:{score:.4f}<{minimum:.4f}"
        for name, (score, minimum) in scores.items()
        if score < minimum
    ]
    if require_provenance and not report.watermark_present:
        failures.append("provenance_marker_missing")
    return QualityReport(
        passed=not failures,
        identity_similarity=report.identity_similarity,
        voice_similarity=report.voice_similarity,
        lip_sync=report.lip_sync,
        temporal_stability=report.temporal_stability,
        expression_naturalness=report.expression_naturalness,
        gaze_naturalness=report.gaze_naturalness,
        body_anatomy=report.body_anatomy,
        visual_fidelity=report.visual_fidelity,
        watermark_present=report.watermark_present,
        failure_reasons=tuple(failures),
        metrics=dict(report.metrics),
    )
