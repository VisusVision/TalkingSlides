from hashlib import sha256
from pathlib import Path
import subprocess

import avatar.digital_twin.lipsync_syncnet as syncnet_module
import avatar.digital_twin.render_quality as quality_module
from avatar.digital_twin.lipsync_syncnet import evaluate_syncnet_lipsync
from avatar.digital_twin.verification import VerificationSignal


def _inputs(tmp_path, monkeypatch):
    video = tmp_path / "avatar.mp4"
    audio = tmp_path / "speech.wav"
    model = tmp_path / "syncnet.model"
    s3fd = tmp_path / "torch" / "hub" / "checkpoints" / "s3fd.pth"
    home = tmp_path / "latentsync"
    evaluator = home / "eval" / "eval_sync_conf.py"
    evaluator.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    model.write_bytes(b"model")
    s3fd.parent.mkdir(parents=True)
    s3fd.write_bytes(b"s3fd")
    evaluator.write_text("# test\n", encoding="utf-8")
    monkeypatch.setattr(syncnet_module, "SYNCNET_SHA256", sha256(b"model").hexdigest())
    monkeypatch.setattr(syncnet_module, "S3FD_SHA256", sha256(b"s3fd").hexdigest())
    monkeypatch.setattr(syncnet_module, "_runtime_revision", lambda _home: syncnet_module.LATENTSYNC_CODE_REVISION)
    return video, audio, model, s3fd, home


def _provider(monkeypatch, *, confidence="6.40", offset="-1", returncode=0):
    def run(command, **_kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"evaluation")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(
            command,
            returncode,
            f"Input video: evaluation.mp4\nSyncNet confidence: {confidence}\nAV offset: {offset}\n",
            "",
        )

    monkeypatch.setattr(syncnet_module.subprocess, "run", run)


def test_syncnet_lipsync_returns_strong_confidence_and_offset_evidence(tmp_path, monkeypatch):
    video, audio, model, s3fd, home = _inputs(tmp_path, monkeypatch)
    _provider(monkeypatch, confidence="6.40", offset="-1")

    signal = evaluate_syncnet_lipsync(
        output_video=video,
        audio_path=audio,
        runtime_home=home,
        checkpoint=model,
        s3fd_checkpoint=s3fd,
    )

    assert signal.passed is True
    assert signal.score == 0.9275
    assert signal.assurance == "strong"
    assert signal.provider == "latentsync_syncnet"
    assert signal.details["model_hash_verified"] is True
    assert signal.details["runtime_revision_verified"] is True
    assert signal.details["syncnet_confidence"] == 6.4
    assert signal.details["av_offset_frames"] == -1
    assert signal.details["av_offset_milliseconds"] == -40.0


def test_syncnet_lipsync_rejects_excessive_offset_even_with_high_confidence(tmp_path, monkeypatch):
    video, audio, model, s3fd, home = _inputs(tmp_path, monkeypatch)
    _provider(monkeypatch, confidence="8.20", offset="3")

    signal = evaluate_syncnet_lipsync(
        output_video=video,
        audio_path=audio,
        runtime_home=home,
        checkpoint=model,
        s3fd_checkpoint=s3fd,
    )

    assert signal.passed is False
    assert signal.details["confidence_passed"] is True
    assert signal.details["offset_passed"] is False
    assert signal.error == "lipsync_confidence_or_offset_failed"


def test_syncnet_lipsync_fails_closed_on_checkpoint_mismatch(tmp_path, monkeypatch):
    video, audio, model, s3fd, home = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(syncnet_module, "SYNCNET_SHA256", "0" * 64)

    signal = evaluate_syncnet_lipsync(
        output_video=video,
        audio_path=audio,
        runtime_home=home,
        checkpoint=model,
        s3fd_checkpoint=s3fd,
    )

    assert signal.passed is False
    assert signal.error == "lipsync_model_checksum_mismatch"


def test_syncnet_lipsync_fails_closed_on_unpinned_runtime(tmp_path, monkeypatch):
    video, audio, model, s3fd, home = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(syncnet_module, "_runtime_revision", lambda _home: "f" * 40)

    signal = evaluate_syncnet_lipsync(
        output_video=video,
        audio_path=audio,
        runtime_home=home,
        checkpoint=model,
        s3fd_checkpoint=s3fd,
    )

    assert signal.passed is False
    assert signal.error == "lipsync_runtime_revision_unverified"


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


def test_partial_builtin_syncnet_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(quality_module, "_identity_proxy", lambda *_args: 0.9)
    report = quality_module.evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={"DIGITAL_TWIN_SYNCNET_MODEL_PATH": "syncnet.model"},
    )

    assert report.decision == "failed"
    assert report.publish_allowed is False
    assert report.lip_sync.assurance == "strong"
    assert report.lip_sync.error == "lipsync_model_configuration_incomplete"


def test_default_runtime_paths_do_not_enable_syncnet_without_model(monkeypatch):
    monkeypatch.setattr(quality_module, "_identity_proxy", lambda *_args: 0.9)
    report = quality_module.evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={
            "DIGITAL_TWIN_SYNCNET_HOME": "/opt/latentsync",
            "DIGITAL_TWIN_SYNCNET_S3FD_MODEL_PATH": "s3fd.pth",
        },
    )

    assert report.decision == "review_required"
    assert report.lip_sync.assurance == "heuristic"


def test_invalid_syncnet_threshold_fails_closed_without_crashing(monkeypatch):
    monkeypatch.setattr(syncnet_module, "_file_sha256", lambda path: (
        syncnet_module.SYNCNET_SHA256 if "syncnet" in str(path) else syncnet_module.S3FD_SHA256
    ))
    monkeypatch.setattr(syncnet_module, "_runtime_revision", lambda _home: syncnet_module.LATENTSYNC_CODE_REVISION)
    report = quality_module.evaluate_render_quality(
        source_image="source.png",
        output_video="output.mp4",
        audio_path="audio.wav",
        render_info=_render_info(),
        environ={
            "DIGITAL_TWIN_SYNCNET_HOME": "/opt/latentsync",
            "DIGITAL_TWIN_SYNCNET_MODEL_PATH": "syncnet.model",
            "DIGITAL_TWIN_SYNCNET_S3FD_MODEL_PATH": "s3fd.pth",
            "DIGITAL_TWIN_SYNCNET_MIN_CONFIDENCE": "not-a-number",
        },
    )

    assert report.decision == "failed"
    assert report.lip_sync.error.startswith("lipsync_configuration_invalid")


def test_builtin_strong_identity_and_syncnet_release_strict_render(monkeypatch):
    monkeypatch.setattr(
        quality_module,
        "evaluate_sface_identity",
        lambda **_kwargs: VerificationSignal(
            "identity_similarity", True, 0.72, "strong", "opencv_yunet_sface", {}
        ),
    )
    monkeypatch.setattr(
        quality_module,
        "evaluate_syncnet_lipsync",
        lambda **_kwargs: VerificationSignal("lip_sync", True, 0.64, "strong", "latentsync_syncnet", {}),
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
            "DIGITAL_TWIN_SYNCNET_HOME": "/opt/latentsync",
            "DIGITAL_TWIN_SYNCNET_MODEL_PATH": "syncnet.model",
            "DIGITAL_TWIN_SYNCNET_S3FD_MODEL_PATH": "s3fd.pth",
        },
    )

    assert report.decision == "passed"
    assert report.publish_allowed is True
    assert report.lip_sync.provider == "latentsync_syncnet"


def test_avatar_worker_receives_syncnet_configuration():
    compose = (Path(__file__).resolve().parents[1] / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

    for name in (
        "DIGITAL_TWIN_SYNCNET_HOME",
        "DIGITAL_TWIN_SYNCNET_MODEL_PATH",
        "DIGITAL_TWIN_SYNCNET_S3FD_MODEL_PATH",
        "DIGITAL_TWIN_SYNCNET_MIN_CONFIDENCE",
        "DIGITAL_TWIN_SYNCNET_MAX_OFFSET_FRAMES",
        "DIGITAL_TWIN_SYNCNET_TIMEOUT_SECONDS",
    ):
        assert f"{name}:" in compose
