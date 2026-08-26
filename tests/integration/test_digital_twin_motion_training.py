import json
from datetime import timedelta
import importlib
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import DigitalTwin, DigitalTwinAuditEvent, DigitalTwinConsentSession, DigitalTwinTrainingRun
from worker import digital_twin_tasks


pytestmark = pytest.mark.django_db


def test_training_persists_motion_style_v2_analysis(tmp_path, settings, monkeypatch):
    settings.STORAGE_ROOT = str(tmp_path)
    owner = User.objects.create_user(username="motion-owner")
    twin = DigitalTwin.objects.create(
        owner=owner,
        display_name="Motion Twin",
        status="training",
        consent_status="approved",
    )
    consent = DigitalTwinConsentSession.objects.create(
        twin=twin,
        challenge_text="challenge",
        challenge_nonce_hash="a" * 64,
        status="approved",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    performance_relative = "digital_twins/input/performance.mp4"
    performance_path = tmp_path / performance_relative
    performance_path.parent.mkdir(parents=True)
    performance_path.write_bytes(b"performance-video")
    run = DigitalTwinTrainingRun.objects.create(
        twin=twin,
        consent_session=consent,
        status="queued",
        stage="queued",
        input_manifest={"performance_video_path": performance_relative},
    )

    source_relative = "avatars/source.mp4"
    portrait_relative = "avatars/portrait.png"
    identity_manifest_relative = "avatars/identity.json"
    voice_relative = "voices/reference.wav"
    for relative_path in (source_relative, portrait_relative, identity_manifest_relative, voice_relative):
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"artifact")

    hardware = SimpleNamespace(
        warnings=("close_gpu_apps_before_render",),
        as_dict=lambda: {"name": "ada_laptop_8gb", "warnings": ["close_gpu_apps_before_render"]},
    )
    motion_report = {
        "version": "motion-style-v2",
        "status": "ready",
        "accepted": True,
        "usable_for_motion_planning": True,
        "duration_seconds": 30.0,
        "frames_sampled": 150,
        "analyzer": {"provider": "face_alignment", "available": True},
        "coverage": {"face_presence": 1.0, "landmarks": 0.98},
        "head_pose": {"yaw_range": [-12.0, 15.0]},
        "gaze": {"camera_contact_ratio": 0.82},
        "blink": {"count": 7},
        "expression": {"p90_intensity": 0.71},
        "motion": {"p90_intensity": 0.62},
        "selected_intervals": {"calm": [], "natural": [], "expressive": []},
        "warnings": ["expression_coverage_advisory"],
        "timeline": [{"timestamp": 0.0, "motion_intensity": 0.1}],
    }
    hardware_module = importlib.import_module("avatar.digital_twin.hardware")
    preprocess_module = importlib.import_module("avatar.preprocess")
    motion_module = importlib.import_module("avatar.digital_twin.motion_analysis")
    monkeypatch.setattr(hardware_module, "apply_local_inference_profile", lambda: hardware)
    monkeypatch.setattr(
        preprocess_module,
        "preprocess_avatar_video",
        lambda **_kwargs: {
            "source_hash": "sha256-test",
            "video_rel_path": source_relative,
            "processed_rel_path": portrait_relative,
            "identity_package_rel_path": identity_manifest_relative,
            "references_rel_paths": [portrait_relative],
            "warnings": ["portrait_warning"],
        },
    )
    monkeypatch.setattr(
        motion_module,
        "analyze_performance_motion",
        lambda _path: motion_report,
    )
    monkeypatch.setattr(
        digital_twin_tasks,
        "_extract_voice_reference",
        lambda _source, _twin_id: {
            "voice_id": "digital-twin-test",
            "reference_path": voice_relative,
            "sample_rate": 24000,
            "channels": 1,
        },
    )

    result = digital_twin_tasks.train_digital_twin.run(training_run_id=str(run.id))

    run.refresh_from_db()
    twin.refresh_from_db()
    assert result["status"] == "ready"
    assert run.status == "done"
    assert twin.status == "ready"
    assert twin.model_versions["motion"] == "motion-style-v2"
    assert twin.motion_style_package["version"] == "motion-style-v2"
    assert twin.motion_style_package["source_hash"] == "sha256-test"
    assert twin.reference_analysis["motion_style"]["coverage"]["landmarks"] == 0.98
    assert "timeline" not in twin.reference_analysis["motion_style"]
    assert twin.reference_analysis["warnings"] == [
        "portrait_warning",
        "close_gpu_apps_before_render",
        "expression_coverage_advisory",
    ]
    manifest_path = tmp_path / twin.motion_style_package["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["timeline"][0]["motion_intensity"] == 0.1
    assert run.output_manifest["motion_style_package"]["manifest_path"] == twin.motion_style_package["manifest_path"]
    audit = DigitalTwinAuditEvent.objects.get(twin=twin, event="training.completed")
    assert audit.payload["motion_status"] == "ready"
