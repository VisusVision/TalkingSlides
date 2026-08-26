from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised by the Python 3.10 avatar image
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10-compatible subset of enum.StrEnum."""

        def __str__(self) -> str:
            return str(self.value)


from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TwinStatus(StrEnum):
    DRAFT = "draft"
    VERIFYING_CONSENT = "verifying_consent"
    ANALYZING_REFERENCES = "analyzing_references"
    TRAINING = "training"
    VALIDATING = "validating"
    READY = "ready"
    REJECTED = "rejected"
    FAILED = "failed"
    REVOKED = "revoked"


class TwinCapability(StrEnum):
    TALKING_HEAD = "talking_head"
    UPPER_BODY = "upper_body"
    FULL_BODY = "full_body"
    MULTILINGUAL_VOICE = "multilingual_voice"
    EMOTION_CONTROL = "emotion_control"
    STREAMING = "streaming"


class RenderMode(StrEnum):
    PORTRAIT = "portrait"
    UPPER_BODY = "upper_body"
    FULL_BODY = "full_body"
    STREAMING = "streaming"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    kind: str
    uri: str
    sha256: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    approved: bool
    subject_match_score: float
    liveness_score: float
    spoken_challenge_score: float
    policy_version: str
    reason: str = ""
    evidence: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceAnalysis:
    accepted: bool
    face_presence_ratio: float
    single_subject_ratio: float
    identity_consistency: float
    visual_quality: float
    audio_quality: float
    motion_coverage: float
    expression_coverage: float
    gaze_coverage: float
    body_pose_coverage: float
    selected_intervals: tuple[tuple[float, float], ...] = ()
    selected_keyframes: tuple[ArtifactRef, ...] = ()
    warnings: tuple[str, ...] = ()
    rejection_reason: str = ""


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    owner_id: str
    display_name: str
    performance_video: ArtifactRef
    consent_video: ArtifactRef
    voice_reference: ArtifactRef | None = None
    capabilities: frozenset[TwinCapability] = frozenset(
        {TwinCapability.TALKING_HEAD, TwinCapability.MULTILINGUAL_VOICE}
    )
    locale: str = "tr-TR"
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class DigitalTwin:
    id: str
    owner_id: str
    display_name: str
    idempotency_key: str = ""
    status: TwinStatus = TwinStatus.DRAFT
    capabilities: frozenset[TwinCapability] = frozenset()
    consent: ConsentDecision | None = None
    reference_analysis: ReferenceAnalysis | None = None
    identity_package: ArtifactRef | None = None
    voice_package: ArtifactRef | None = None
    motion_style_package: ArtifactRef | None = None
    look_packages: tuple[ArtifactRef, ...] = ()
    model_versions: dict[str, str] = field(default_factory=dict)
    failure_code: str = ""
    failure_message: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, request: TrainingRequest) -> "DigitalTwin":
        return cls(
            id=f"twin_{uuid4().hex}",
            owner_id=request.owner_id,
            display_name=request.display_name,
            idempotency_key=request.idempotency_key,
            capabilities=request.capabilities,
        )

    def transition(self, status: TwinStatus, **changes: Any) -> "DigitalTwin":
        return replace(self, status=status, updated_at=utc_now(), **changes)


@dataclass(frozen=True, slots=True)
class MotionPlan:
    duration_seconds: float
    fps: int
    phoneme_timeline: tuple[dict[str, Any], ...] = ()
    expression_timeline: tuple[dict[str, Any], ...] = ()
    gaze_timeline: tuple[dict[str, Any], ...] = ()
    gesture_timeline: tuple[dict[str, Any], ...] = ()
    camera_timeline: tuple[dict[str, Any], ...] = ()
    seed: int = 0


@dataclass(frozen=True, slots=True)
class RenderRequest:
    twin_id: str
    script: str = ""
    audio: ArtifactRef | None = None
    locale: str = "tr-TR"
    mode: RenderMode = RenderMode.PORTRAIT
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 25
    emotion: str = "neutral"
    scene_prompt: str = "professional studio"
    look_id: str = ""
    watermark_required: bool = True
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class QualityReport:
    passed: bool
    identity_similarity: float
    voice_similarity: float
    lip_sync: float
    temporal_stability: float
    expression_naturalness: float
    gaze_naturalness: float
    body_anatomy: float
    visual_fidelity: float
    watermark_present: bool
    failure_reasons: tuple[str, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RenderResult:
    render_id: str
    twin_id: str
    output: ArtifactRef
    quality: QualityReport
    engine_trace: tuple[str, ...]
    created_at: datetime = field(default_factory=utc_now)
