from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .domain import (
    DigitalTwin,
    RenderMode,
    RenderRequest,
    RenderResult,
    TrainingRequest,
    TwinCapability,
    TwinStatus,
)
from .ports import (
    AuditSink,
    BodyRenderer,
    ConsentVerifier,
    IdentityRefiner,
    IdentityTrainer,
    MotionPlanner,
    MotionStyleTrainer,
    PortraitRenderer,
    ProvenanceMarker,
    ReferenceAnalyzer,
    RenderQualityGate,
    SpeechEngine,
    TwinRepository,
    TwinValidator,
    VoiceTrainer,
)
from .quality import apply_quality_thresholds


class TwinNotReadyError(RuntimeError):
    pass


class QualityGateError(RuntimeError):
    pass


@dataclass(slots=True)
class DigitalTwinOrchestrator:
    repository: TwinRepository
    consent_verifier: ConsentVerifier
    reference_analyzer: ReferenceAnalyzer
    identity_trainer: IdentityTrainer
    voice_trainer: VoiceTrainer
    motion_style_trainer: MotionStyleTrainer
    twin_validator: TwinValidator
    speech_engine: SpeechEngine
    motion_planner: MotionPlanner
    portrait_renderer: PortraitRenderer
    body_renderer: BodyRenderer
    identity_refiner: IdentityRefiner
    render_quality_gate: RenderQualityGate
    provenance_marker: ProvenanceMarker
    audit: AuditSink

    def train(self, request: TrainingRequest) -> DigitalTwin:
        if request.idempotency_key:
            existing = self.repository.find_by_idempotency_key(request.idempotency_key)
            if existing is not None:
                return existing

        twin = self.repository.save(DigitalTwin.create(request))
        self.audit.record("twin.created", twin.id, {"owner_id": twin.owner_id})
        try:
            twin = self.repository.save(twin.transition(TwinStatus.VERIFYING_CONSENT))
            consent = self.consent_verifier.verify(request)
            if not consent.approved:
                rejected = twin.transition(
                    TwinStatus.REJECTED,
                    consent=consent,
                    failure_code="consent_rejected",
                    failure_message=consent.reason or "Consent verification failed.",
                )
                self.audit.record("twin.consent_rejected", twin.id, {"reason": consent.reason})
                return self.repository.save(rejected)

            twin = self.repository.save(
                twin.transition(TwinStatus.ANALYZING_REFERENCES, consent=consent)
            )
            analysis = self.reference_analyzer.analyze(request)
            if not analysis.accepted:
                rejected = twin.transition(
                    TwinStatus.REJECTED,
                    reference_analysis=analysis,
                    failure_code="reference_rejected",
                    failure_message=analysis.rejection_reason or "Reference quality is insufficient.",
                )
                self.audit.record(
                    "twin.reference_rejected",
                    twin.id,
                    {"reason": analysis.rejection_reason, "warnings": list(analysis.warnings)},
                )
                return self.repository.save(rejected)

            twin = self.repository.save(
                twin.transition(TwinStatus.TRAINING, reference_analysis=analysis)
            )
            identity_package = self.identity_trainer.train(twin, request)
            voice_package = self.voice_trainer.train(twin, request)
            motion_style_package = self.motion_style_trainer.train(twin, request)
            twin = self.repository.save(
                twin.transition(
                    TwinStatus.VALIDATING,
                    identity_package=identity_package,
                    voice_package=voice_package,
                    motion_style_package=motion_style_package,
                )
            )
            training_quality = apply_quality_thresholds(
                self.twin_validator.validate(twin),
                require_provenance=False,
            )
            if not training_quality.passed:
                failed = twin.transition(
                    TwinStatus.FAILED,
                    failure_code="training_quality_failed",
                    failure_message=";".join(training_quality.failure_reasons),
                )
                self.audit.record(
                    "twin.training_quality_failed",
                    twin.id,
                    {"reasons": list(training_quality.failure_reasons)},
                )
                return self.repository.save(failed)

            ready = self.repository.save(twin.transition(TwinStatus.READY))
            self.audit.record("twin.ready", twin.id, {"capabilities": sorted(ready.capabilities)})
            return ready
        except Exception as exc:
            failed = twin.transition(
                TwinStatus.FAILED,
                failure_code="training_exception",
                failure_message=str(exc),
            )
            self.repository.save(failed)
            self.audit.record("twin.training_failed", twin.id, {"error": str(exc)})
            raise

    def render(self, request: RenderRequest) -> RenderResult:
        twin = self.repository.get(request.twin_id)
        if twin.status is not TwinStatus.READY or not twin.consent or not twin.consent.approved:
            raise TwinNotReadyError(f"Twin {twin.id} is not ready for rendering.")
        if not request.script.strip() and request.audio is None:
            raise ValueError("A script or driving audio is required.")

        requires_body = request.mode in {RenderMode.UPPER_BODY, RenderMode.FULL_BODY}
        if request.mode is RenderMode.UPPER_BODY and not (
            {TwinCapability.UPPER_BODY, TwinCapability.FULL_BODY} & twin.capabilities
        ):
            raise TwinNotReadyError(f"Twin {twin.id} has no upper-body capability.")
        if request.mode is RenderMode.FULL_BODY and TwinCapability.FULL_BODY not in twin.capabilities:
            raise TwinNotReadyError(f"Twin {twin.id} has no full-body capability.")

        audio = request.audio or self.speech_engine.synthesize(twin, request)
        motion = self.motion_planner.plan(twin, request, audio)
        if requires_body:
            base_video = self.body_renderer.render(twin, request, audio, motion)
            engine_trace = ["speech", "motion_plan", "body_renderer"]
        else:
            base_video = self.portrait_renderer.render(twin, request, audio, motion)
            engine_trace = ["speech", "motion_plan", "portrait_renderer"]

        refined_video = self.identity_refiner.refine(twin, request, base_video)
        marked_video = self.provenance_marker.apply(twin, request, refined_video)
        quality = apply_quality_thresholds(
            self.render_quality_gate.evaluate(twin, request, audio, marked_video)
        )
        if not quality.passed:
            self.audit.record(
                "render.quality_failed",
                twin.id,
                {"reasons": list(quality.failure_reasons)},
            )
            raise QualityGateError(";".join(quality.failure_reasons))

        result = RenderResult(
            render_id=f"render_{uuid4().hex}",
            twin_id=twin.id,
            output=marked_video,
            quality=quality,
            engine_trace=tuple(engine_trace + ["identity_refiner", "provenance", "quality_gate"]),
        )
        self.audit.record(
            "render.ready",
            twin.id,
            {"render_id": result.render_id, "output": result.output.uri},
        )
        return result

    def revoke(self, twin_id: str, *, reason: str) -> DigitalTwin:
        twin = self.repository.get(twin_id)
        revoked = self.repository.save(
            twin.transition(
                TwinStatus.REVOKED,
                failure_code="owner_revoked",
                failure_message=reason,
            )
        )
        self.audit.record("twin.revoked", twin.id, {"reason": reason})
        return revoked
