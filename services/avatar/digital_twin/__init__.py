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
from .evaluation import (
    EVALUATION_VERSION,
    EvaluationContractError,
    evaluate_avatar_variants,
    render_evaluation_markdown,
)
from .demo_pack import (
    DEMO_PACK_VERSION,
    DemoPackContractError,
    build_demo_input_fingerprint,
    build_demo_variant_plans,
    run_demo_pack,
    validate_strong_quality_configuration,
    validate_strong_quality_report,
)
from .orchestrator import DigitalTwinOrchestrator, QualityGateError, TwinNotReadyError
from .hardware import GpuSnapshot, InferenceProfile, apply_local_inference_profile, probe_nvidia_gpu
from .motion_analysis import MOTION_STYLE_VERSION, analyze_performance_motion, build_motion_style_profile
from .motion_planning import MOTION_PLAN_VERSION, build_personal_motion_plan
from .prosody import PROSODY_PROFILE_VERSION, analyze_audio_prosody, build_prosody_profile
from .render_quality import RenderQualityReport, evaluate_render_quality
from .identity_sface import evaluate_sface_identity
from .lipsync_syncnet import evaluate_syncnet_lipsync
from .verification import ConsentVerificationReport, VerificationSignal, verify_consent_evidence

__all__ = [
    "ArtifactRef",
    "ConsentDecision",
    "DigitalTwin",
    "DigitalTwinOrchestrator",
    "DEMO_PACK_VERSION",
    "DemoPackContractError",
    "EVALUATION_VERSION",
    "EvaluationContractError",
    "MotionPlan",
    "MOTION_STYLE_VERSION",
    "MOTION_PLAN_VERSION",
    "PROSODY_PROFILE_VERSION",
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
    "build_demo_input_fingerprint",
    "build_demo_variant_plans",
    "build_personal_motion_plan",
    "analyze_audio_prosody",
    "build_prosody_profile",
    "probe_nvidia_gpu",
    "ConsentVerificationReport",
    "RenderQualityReport",
    "VerificationSignal",
    "evaluate_render_quality",
    "evaluate_sface_identity",
    "evaluate_syncnet_lipsync",
    "evaluate_avatar_variants",
    "render_evaluation_markdown",
    "run_demo_pack",
    "validate_strong_quality_configuration",
    "validate_strong_quality_report",
    "verify_consent_evidence",
]
