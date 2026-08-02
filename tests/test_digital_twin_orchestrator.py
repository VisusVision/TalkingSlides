from __future__ import annotations

from dataclasses import replace

import pytest

from services.avatar.digital_twin import (
    ArtifactRef,
    ConsentDecision,
    DigitalTwinOrchestrator,
    MotionPlan,
    QualityGateError,
    QualityReport,
    ReferenceAnalysis,
    RenderMode,
    RenderRequest,
    TrainingRequest,
    TwinCapability,
    TwinNotReadyError,
    TwinStatus,
)


def artifact(kind: str) -> ArtifactRef:
    return ArtifactRef(kind=kind, uri=f"memory://{kind}", sha256=f"hash-{kind}")


def passing_quality(*, watermark: bool = True) -> QualityReport:
    return QualityReport(
        passed=True,
        identity_similarity=0.94,
        voice_similarity=0.91,
        lip_sync=0.92,
        temporal_stability=0.93,
        expression_naturalness=0.88,
        gaze_naturalness=0.86,
        body_anatomy=0.90,
        visual_fidelity=0.92,
        watermark_present=watermark,
    )


class Repository:
    def __init__(self):
        self.items = {}

    def save(self, twin):
        self.items[twin.id] = twin
        return twin

    def get(self, twin_id):
        return self.items[twin_id]

    def find_by_idempotency_key(self, key):
        return next((item for item in self.items.values() if item.idempotency_key == key), None)


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event, twin_id, payload):
        self.events.append((event, twin_id, payload))


class Consent:
    approved = True

    def verify(self, _request):
        return ConsentDecision(
            approved=self.approved,
            subject_match_score=0.98,
            liveness_score=0.96,
            spoken_challenge_score=0.95,
            policy_version="consent-v2",
            reason="" if self.approved else "subject_mismatch",
        )


class Analyzer:
    accepted = True

    def analyze(self, _request):
        return ReferenceAnalysis(
            accepted=self.accepted,
            face_presence_ratio=0.98,
            single_subject_ratio=1.0,
            identity_consistency=0.96,
            visual_quality=0.92,
            audio_quality=0.90,
            motion_coverage=0.88,
            expression_coverage=0.84,
            gaze_coverage=0.80,
            body_pose_coverage=0.77,
            rejection_reason="" if self.accepted else "multiple_people",
        )


class Trainer:
    def __init__(self, kind):
        self.kind = kind

    def train(self, _twin, _request):
        return artifact(self.kind)


class Validator:
    def validate(self, _twin):
        return passing_quality(watermark=False)


class Speech:
    def synthesize(self, _twin, _request):
        return artifact("speech")


class Planner:
    def plan(self, _twin, _request, _audio):
        return MotionPlan(duration_seconds=4.0, fps=25)


class Renderer:
    def __init__(self, kind):
        self.kind = kind
        self.calls = 0

    def render(self, _twin, _request, _audio, _motion):
        self.calls += 1
        return artifact(self.kind)


class Refiner:
    def refine(self, _twin, _request, _video):
        return artifact("refined-video")


class Marker:
    def apply(self, _twin, _request, _video):
        return artifact("marked-video")


class RenderGate:
    report = passing_quality()

    def evaluate(self, _twin, _request, _audio, _video):
        return self.report


@pytest.fixture
def system():
    repository = Repository()
    consent = Consent()
    analyzer = Analyzer()
    portrait = Renderer("portrait-video")
    body = Renderer("body-video")
    gate = RenderGate()
    audit = Audit()
    orchestrator = DigitalTwinOrchestrator(
        repository=repository,
        consent_verifier=consent,
        reference_analyzer=analyzer,
        identity_trainer=Trainer("identity-package"),
        voice_trainer=Trainer("voice-package"),
        motion_style_trainer=Trainer("motion-package"),
        twin_validator=Validator(),
        speech_engine=Speech(),
        motion_planner=Planner(),
        portrait_renderer=portrait,
        body_renderer=body,
        identity_refiner=Refiner(),
        render_quality_gate=gate,
        provenance_marker=Marker(),
        audit=audit,
    )
    return orchestrator, repository, consent, analyzer, portrait, body, gate, audit


def request(**changes):
    base = TrainingRequest(
        owner_id="user-42",
        display_name="Teacher Twin",
        performance_video=artifact("performance-video"),
        consent_video=artifact("consent-video"),
        idempotency_key="create-user-42-v1",
    )
    return replace(base, **changes)


def test_training_requires_approved_live_consent(system):
    orchestrator, _repository, consent, *_rest = system
    consent.approved = False

    twin = orchestrator.train(request())

    assert twin.status is TwinStatus.REJECTED
    assert twin.failure_code == "consent_rejected"
    assert twin.identity_package is None


def test_training_builds_separate_identity_voice_and_motion_packages(system):
    orchestrator, *_rest = system

    twin = orchestrator.train(request())
    duplicate = orchestrator.train(request())

    assert twin.status is TwinStatus.READY
    assert twin.identity_package.kind == "identity-package"
    assert twin.voice_package.kind == "voice-package"
    assert twin.motion_style_package.kind == "motion-package"
    assert duplicate.id == twin.id


def test_ready_twin_renders_through_motion_refinement_and_quality(system):
    orchestrator, _repository, _consent, _analyzer, portrait, body, _gate, audit = system
    twin = orchestrator.train(request())

    result = orchestrator.render(
        RenderRequest(twin_id=twin.id, script="Merhaba, bugün fiziği anlatacağız.")
    )

    assert result.output.kind == "marked-video"
    assert result.quality.passed is True
    assert portrait.calls == 1
    assert body.calls == 0
    assert result.engine_trace == (
        "speech",
        "motion_plan",
        "portrait_renderer",
        "identity_refiner",
        "provenance",
        "quality_gate",
    )
    assert audit.events[-1][0] == "render.ready"


def test_render_quality_gate_blocks_identity_drift(system):
    orchestrator, _repository, _consent, _analyzer, _portrait, _body, gate, _audit = system
    twin = orchestrator.train(request())
    gate.report = replace(passing_quality(), identity_similarity=0.41)

    with pytest.raises(QualityGateError, match="identity_similarity"):
        orchestrator.render(RenderRequest(twin_id=twin.id, script="Test"))


def test_full_body_render_requires_trained_capability(system):
    orchestrator, *_rest = system
    twin = orchestrator.train(request())

    with pytest.raises(TwinNotReadyError, match="full-body"):
        orchestrator.render(
            RenderRequest(twin_id=twin.id, script="Test", mode=RenderMode.FULL_BODY)
        )


def test_full_body_capability_routes_to_body_engine(system):
    orchestrator, _repository, _consent, _analyzer, portrait, body, *_rest = system
    twin = orchestrator.train(
        request(
            idempotency_key="create-full-body-v1",
            capabilities=frozenset(
                {
                    TwinCapability.TALKING_HEAD,
                    TwinCapability.FULL_BODY,
                    TwinCapability.MULTILINGUAL_VOICE,
                }
            ),
        )
    )

    result = orchestrator.render(
        RenderRequest(twin_id=twin.id, script="Test", mode=RenderMode.FULL_BODY)
    )

    assert result.quality.passed is True
    assert portrait.calls == 0
    assert body.calls == 1
