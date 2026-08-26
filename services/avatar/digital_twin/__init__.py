"""Digital Twin V2 domain and orchestration contracts.

The package deliberately contains no model imports. GPU engines live behind
ports so local research models, hosted providers, and future in-house models can
be swapped without changing consent, lifecycle, or quality policy.
"""

from .domain import (
    ArtifactRef,
    ConsentDecision,
    DigitalTwin,
    MotionPlan,
    QualityReport,
    ReferenceAnalysis,
    RenderMode,
    RenderRequest,
    RenderResult,
    TrainingRequest,
    TwinCapability,
    TwinStatus,
)
from .orchestrator import DigitalTwinOrchestrator, QualityGateError, TwinNotReadyError
from .hardware import GpuSnapshot, InferenceProfile, apply_local_inference_profile, probe_nvidia_gpu
from .motion_analysis import MOTION_STYLE_VERSION, analyze_performance_motion, build_motion_style_profile
from .motion_planning import MOTION_PLAN_VERSION, build_personal_motion_plan
from .render_quality import RenderQualityReport, evaluate_render_quality
from .verification import ConsentVerificationReport, VerificationSignal, verify_consent_evidence

__all__ = [
    "ArtifactRef",
    "ConsentDecision",
    "DigitalTwin",
    "DigitalTwinOrchestrator",
    "MotionPlan",
    "MOTION_STYLE_VERSION",
    "MOTION_PLAN_VERSION",
    "QualityGateError",
    "QualityReport",
    "ReferenceAnalysis",
    "RenderMode",
    "RenderRequest",
    "RenderResult",
    "TrainingRequest",
    "TwinCapability",
    "TwinNotReadyError",
    "TwinStatus",
    "GpuSnapshot",
    "InferenceProfile",
    "apply_local_inference_profile",
    "analyze_performance_motion",
    "build_motion_style_profile",
    "build_personal_motion_plan",
    "probe_nvidia_gpu",
    "ConsentVerificationReport",
    "RenderQualityReport",
    "VerificationSignal",
    "evaluate_render_quality",
    "verify_consent_evidence",
]
