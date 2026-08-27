from hashlib import sha256
from pathlib import Path

import avatar.digital_twin.identity_sface as identity_module
import avatar.digital_twin.render_quality as quality_module
from avatar.digital_twin.identity_sface import evaluate_sface_identity
from avatar.digital_twin.verification import VerificationSignal


class _Image:
    shape = (512, 512, 3)


class _Cv2:
    __version__ = "test"
    FaceRecognizerSF_FR_COSINE = 0

    @staticmethod
    def imread(_path):
        return _Image()


class _Recognizer:
    def __init__(self, scores):
        self.scores = iter(scores)

    def match(self, _source, _target, _distance):
        return next(self.scores)


def _models(tmp_path, monkeypatch):
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    source = tmp_path / "source.png"
    video = tmp_path / "output.mp4"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    source.write_bytes(b"source")
    video.write_bytes(b"video")
    monkeypatch.setattr(identity_module, "YUNET_SHA256", sha256(b"yunet").hexdigest())
    monkeypatch.setattr(identity_module, "SFACE_SHA256", sha256(b"sface").hexdigest())
    monkeypatch.setattr(identity_module, "cv2", _Cv2())
    monkeypatch.setattr(identity_module, "_largest_face", lambda *_args: object())
    monkeypatch.setattr(identity_module, "_feature", lambda *_args: object())
    monkeypatch.setattr(identity_module, "_sample_video_frames", lambda *_args, **_kwargs: ([_Image()] * 10, 10))
    return source, video, yunet, sface


def test_sface_identity_returns_strong_p10_score_with_provenance(tmp_path, monkeypatch):
    source, video, yunet, sface = _models(tmp_path, monkeypatch)
    recognizer = _Recognizer([0.82, 0.81, 0.79, 0.80, 0.78, 0.77, 0.76, 0.75, 0.74, 0.10])
    monkeypatch.setattr(identity_module, "_load_models", lambda *_args: (object(), recognizer))

    signal = evaluate_sface_identity(
        source_image=source,
        output_video=video,
        yunet_model=yunet,
        sface_model=sface,
    )

    assert signal.assurance == "strong"
    assert signal.provider == "opencv_yunet_sface"
    assert signal.passed is True
    assert signal.score == 0.676
    assert signal.details["aggregation"] == "cosine_p10"
    assert signal.details["model_hashes_verified"] is True
    assert signal.details["face_coverage"] == 1.0


def test_sface_identity_rejects_identity_drift_in_lower_tail(tmp_path, monkeypatch):
    source, video, yunet, sface = _models(tmp_path, monkeypatch)
    recognizer = _Recognizer([0.8] * 8 + [0.2, 0.1])
    monkeypatch.setattr(identity_module, "_load_models", lambda *_args: (object(), recognizer))

    signal = evaluate_sface_identity(
        source_image=source,
        output_video=video,
        yunet_model=yunet,
        sface_model=sface,
    )

    assert signal.passed is False
    assert signal.error == "identity_cosine_below_threshold"
    assert signal.details["cosine_median"] == 0.8
    assert signal.score == 0.19


def test_sface_identity_fails_closed_on_insufficient_face_coverage(tmp_path, monkeypatch):
    source, video, yunet, sface = _models(tmp_path, monkeypatch)
    monkeypatch.setattr(identity_module, "_largest_face", lambda *_args: object())
    monkeypatch.setattr(identity_module, "_sample_video_frames", lambda *_args, **_kwargs: ([_Image()] * 10, 10))
    calls = iter([object()] + [object()] * 5 + [None] * 5)
    monkeypatch.setattr(identity_module, "_largest_face", lambda *_args: next(calls))
    monkeypatch.setattr(identity_module, "_load_models", lambda *_args: (object(), _Recognizer([0.9] * 5)))

    signal = evaluate_sface_identity(
        source_image=source,
        output_video=video,
        yunet_model=yunet,
        sface_model=sface,
        min_face_frames=6,
    )

    assert signal.passed is False
    assert signal.assurance == "strong"
    assert signal.error == "identity_face_coverage_below_threshold"


def test_sface_identity_rejects_unpinned_model_hashes(tmp_path, monkeypatch):
    source, video, yunet, sface = _models(tmp_path, monkeypatch)
    monkeypatch.setattr(identity_module, "SFACE_SHA256", "0" * 64)

    signal = evaluate_sface_identity(
        source_image=source,
        output_video=video,
        yunet_model=yunet,
        sface_model=sface,
    )

    assert signal.passed is False
    assert signal.error == "identity_model_checksum_mismatch"
    assert signal.details["model_hashes_verified"] is False


def _render_info():
    return {
        "strict_validation_passed": True,
        "motion_validation": {
            "audio_match": True,
            "duration_mismatch": False,
            "quality_checks": {
                "landmark_stable": True,
                "lip_movement_score": 0.004,
                "min_lip_movement": 0.002,
            },
        },
    }


def test_partial_builtin_identity_configuration_fails_closed():
    report = quality_module.evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={"DIGITAL_TWIN_YUNET_MODEL_PATH": "yunet.onnx"},
    )

    assert report.decision == "failed"
    assert report.publish_allowed is False
    assert report.identity.assurance == "strong"
    assert report.identity.error == "identity_model_configuration_incomplete"


def test_builtin_strong_identity_can_release_with_strong_lipsync(monkeypatch):
    monkeypatch.setattr(
        quality_module,
        "evaluate_sface_identity",
        lambda **_kwargs: VerificationSignal(
            "identity_similarity", True, 0.71, "strong", "opencv_yunet_sface", {"model_hashes_verified": True}
        ),
    )
    monkeypatch.setattr(
        quality_module,
        "_run_json_provider",
        lambda *_args, **_kwargs: {"passed": True, "score": 0.93, "assurance": "strong"},
    )

    report = quality_module.evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={
            "DIGITAL_TWIN_STRICT_QUALITY_GATE": "1",
            "DIGITAL_TWIN_YUNET_MODEL_PATH": "yunet.onnx",
            "DIGITAL_TWIN_SFACE_MODEL_PATH": "sface.onnx",
            "DIGITAL_TWIN_LIPSYNC_VERIFY_CMD": "sync",
        },
    )

    assert report.decision == "passed"
    assert report.publish_allowed is True
    assert report.identity.provider == "opencv_yunet_sface"


def test_avatar_worker_receives_builtin_identity_configuration():
    compose = (Path(__file__).resolve().parents[1] / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

    for name in (
        "DIGITAL_TWIN_YUNET_MODEL_PATH",
        "DIGITAL_TWIN_SFACE_MODEL_PATH",
        "DIGITAL_TWIN_IDENTITY_MIN_COSINE",
        "DIGITAL_TWIN_IDENTITY_MIN_FACE_COVERAGE",
        "DIGITAL_TWIN_IDENTITY_MIN_FACE_FRAMES",
        "DIGITAL_TWIN_IDENTITY_MAX_SAMPLES",
    ):
        assert f"{name}:" in compose
