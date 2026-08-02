import avatar.digital_twin.render_quality as quality_module
import avatar.digital_twin.verification as verification_module
from avatar.digital_twin.render_quality import evaluate_render_quality
from avatar.digital_twin.verification import challenge_similarity, verify_consent_evidence


def _evidence(**overrides):
    payload = {
        "readable": True,
        "duration_seconds": 8.0,
        "frames_sampled": 24,
        "face_frames": 23,
        "face_presence_ratio": 0.96,
        "multiple_face_ratio": 0.0,
        "passive_motion_score": 0.4,
        "face_crops": [object()],
    }
    payload.update(overrides)
    return payload


def test_local_heuristics_never_auto_approve(monkeypatch):
    monkeypatch.setattr(verification_module, "analyze_video_evidence", lambda *_args, **_kwargs: _evidence())
    monkeypatch.setattr(verification_module, "_face_appearance_proxy", lambda *_args: 0.91)
    report = verify_consent_evidence(
        performance_video="performance.mp4",
        consent_video="consent.mp4",
        challenge_text="Ben Engin. Kod: abc123",
        environ={},
    )
    assert report.decision == "pending_review"
    assert report.face_match.assurance == "heuristic"
    assert report.liveness.assurance == "heuristic"


def test_strong_face_liveness_and_asr_can_auto_approve(monkeypatch):
    monkeypatch.setattr(verification_module, "analyze_video_evidence", lambda *_args, **_kwargs: _evidence())

    def provider(template, _replacements):
        if template == "asr":
            return {"transcript": "Ben Engin. Kod abc123", "assurance": "strong"}
        return {"passed": True, "score": 0.94, "assurance": "strong"}

    monkeypatch.setattr(verification_module, "_run_json_provider", provider)
    report = verify_consent_evidence(
        performance_video="performance.mp4",
        consent_video="consent.mp4",
        challenge_text="Ben Engin. Kod: abc123",
        environ={
            "DIGITAL_TWIN_FACE_VERIFY_CMD": "face",
            "DIGITAL_TWIN_LIVENESS_VERIFY_CMD": "live",
            "DIGITAL_TWIN_ASR_VERIFY_CMD": "asr",
            "DIGITAL_TWIN_CHALLENGE_MIN_SIMILARITY": "0.70",
        },
    )
    assert report.decision == "approved"
    assert report.challenge.details["nonce_match"] is True


def test_invalid_consent_video_is_rejected_before_manual_release(monkeypatch):
    responses = iter([_evidence(), _evidence(readable=False, frames_sampled=0, face_presence_ratio=0.0)])
    monkeypatch.setattr(verification_module, "analyze_video_evidence", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(verification_module, "_face_appearance_proxy", lambda *_args: None)
    report = verify_consent_evidence(
        performance_video="performance.mp4",
        consent_video="consent.mp4",
        challenge_text="Kod: abc123",
        environ={},
    )
    assert report.decision == "rejected"
    assert "consent_video_unreadable" in report.reasons


def test_low_biometric_score_rejects_even_when_provider_says_passed(monkeypatch):
    monkeypatch.setattr(verification_module, "analyze_video_evidence", lambda *_args, **_kwargs: _evidence())

    def provider(template, _replacements):
        if template == "face":
            return {"passed": True, "score": 0.2, "assurance": "strong"}
        if template == "asr":
            return {"transcript": "Kod abc123", "assurance": "strong"}
        return {"passed": True, "score": 0.95, "assurance": "strong"}

    monkeypatch.setattr(verification_module, "_run_json_provider", provider)
    report = verify_consent_evidence(
        performance_video="performance.mp4",
        consent_video="consent.mp4",
        challenge_text="Kod: abc123",
        environ={
            "DIGITAL_TWIN_FACE_VERIFY_CMD": "face",
            "DIGITAL_TWIN_LIVENESS_VERIFY_CMD": "live",
            "DIGITAL_TWIN_ASR_VERIFY_CMD": "asr",
        },
    )
    assert report.decision == "rejected"
    assert report.face_match.passed is False


def test_challenge_similarity_requires_session_nonce():
    score, nonce_ok = challenge_similarity(
        "Bu dijital ikizi kabul ediyorum. Kod: z9y8x7",
        "Bu dijital ikizi kabul ediyorum.",
    )
    assert score > 0.5
    assert nonce_ok is False


def _render_info(*, strict=True):
    return {
        "strict_validation_passed": strict,
        "motion_validation": {
            "audio_match": True,
            "duration_mismatch": False,
            "failure_reason": "" if strict else "invalid_lip_sync",
            "quality_checks": {
                "landmark_stable": True,
                "lip_movement_score": 0.004,
                "min_lip_movement": 0.002,
                "glitch_detected": False,
                "drift_detected": False,
                "face_artifact_detected": False,
            },
        },
    }


def test_quality_gate_allows_review_labeled_output_in_local_mode(monkeypatch):
    monkeypatch.setattr(quality_module, "_identity_proxy", lambda *_args: 0.9)
    report = evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={"DIGITAL_TWIN_STRICT_QUALITY_GATE": "0"},
    )
    assert report.decision == "review_required"
    assert report.publish_allowed is True


def test_quality_gate_blocks_pending_metrics_in_strict_production_mode(monkeypatch):
    monkeypatch.setattr(quality_module, "_identity_proxy", lambda *_args: 0.9)
    report = evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={"DIGITAL_TWIN_STRICT_QUALITY_GATE": "1"},
    )
    assert report.decision == "review_required"
    assert report.publish_allowed is False


def test_strong_identity_and_lipsync_providers_release_render(monkeypatch):
    monkeypatch.setattr(
        quality_module,
        "_run_json_provider",
        lambda *_args, **_kwargs: {"passed": True, "score": 0.93, "assurance": "strong"},
    )
    report = evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={
            "DIGITAL_TWIN_STRICT_QUALITY_GATE": "1",
            "DIGITAL_TWIN_IDENTITY_VERIFY_CMD": "identity",
            "DIGITAL_TWIN_LIPSYNC_VERIFY_CMD": "sync",
        },
    )
    assert report.decision == "passed"
    assert report.publish_allowed is True


def test_strong_provider_verdict_cannot_bypass_minimum_score(monkeypatch):
    monkeypatch.setattr(
        quality_module,
        "_run_json_provider",
        lambda *_args, **_kwargs: {"passed": True, "score": 0.25, "assurance": "strong"},
    )
    report = evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={
            "DIGITAL_TWIN_STRICT_QUALITY_GATE": "0",
            "DIGITAL_TWIN_IDENTITY_VERIFY_CMD": "identity",
            "DIGITAL_TWIN_LIPSYNC_VERIFY_CMD": "sync",
        },
    )
    assert report.decision == "failed"
    assert report.publish_allowed is False


def test_technical_render_failure_is_always_blocked(monkeypatch):
    monkeypatch.setattr(quality_module, "_identity_proxy", lambda *_args: 0.9)
    report = evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(strict=False),
        environ={"DIGITAL_TWIN_STRICT_QUALITY_GATE": "0"},
    )
    assert report.decision == "failed"
    assert report.publish_allowed is False
