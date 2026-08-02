from __future__ import annotations

from typing import Protocol

from .domain import (
    ArtifactRef,
    ConsentDecision,
    DigitalTwin,
    MotionPlan,
    QualityReport,
    ReferenceAnalysis,
    RenderRequest,
    TrainingRequest,
)


class TwinRepository(Protocol):
    def save(self, twin: DigitalTwin) -> DigitalTwin: ...

    def get(self, twin_id: str) -> DigitalTwin: ...

    def find_by_idempotency_key(self, key: str) -> DigitalTwin | None: ...


class ConsentVerifier(Protocol):
    def verify(self, request: TrainingRequest) -> ConsentDecision: ...


class ReferenceAnalyzer(Protocol):
    def analyze(self, request: TrainingRequest) -> ReferenceAnalysis: ...


class IdentityTrainer(Protocol):
    def train(self, twin: DigitalTwin, request: TrainingRequest) -> ArtifactRef: ...


class VoiceTrainer(Protocol):
    def train(self, twin: DigitalTwin, request: TrainingRequest) -> ArtifactRef: ...


class MotionStyleTrainer(Protocol):
    def train(self, twin: DigitalTwin, request: TrainingRequest) -> ArtifactRef: ...


class TwinValidator(Protocol):
    def validate(self, twin: DigitalTwin) -> QualityReport: ...


class SpeechEngine(Protocol):
    def synthesize(self, twin: DigitalTwin, request: RenderRequest) -> ArtifactRef: ...


class MotionPlanner(Protocol):
    def plan(self, twin: DigitalTwin, request: RenderRequest, audio: ArtifactRef) -> MotionPlan: ...


class PortraitRenderer(Protocol):
    def render(
        self,
        twin: DigitalTwin,
        request: RenderRequest,
        audio: ArtifactRef,
        motion: MotionPlan,
    ) -> ArtifactRef: ...


class BodyRenderer(Protocol):
    def render(
        self,
        twin: DigitalTwin,
        request: RenderRequest,
        audio: ArtifactRef,
        motion: MotionPlan,
    ) -> ArtifactRef: ...


class IdentityRefiner(Protocol):
    def refine(self, twin: DigitalTwin, request: RenderRequest, video: ArtifactRef) -> ArtifactRef: ...


class RenderQualityGate(Protocol):
    def evaluate(
        self,
        twin: DigitalTwin,
        request: RenderRequest,
        audio: ArtifactRef,
        video: ArtifactRef,
    ) -> QualityReport: ...


class ProvenanceMarker(Protocol):
    def apply(self, twin: DigitalTwin, request: RenderRequest, video: ArtifactRef) -> ArtifactRef: ...


class AuditSink(Protocol):
    def record(self, event: str, twin_id: str, payload: dict) -> None: ...
