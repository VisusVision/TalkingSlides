import importlib
import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from core.models import DigitalTwin, DigitalTwinAuditEvent, DigitalTwinRender
from worker import digital_twin_tasks


pytestmark = pytest.mark.django_db


def _motion_package() -> dict:
    interval = {
        "start_seconds": 8.0,
        "end_seconds": 16.0,
        "duration_seconds": 8.0,
        "score": 0.82,
        "motion_intensity": 0.8,
        "expression_intensity": 0.76,
        "head_activity": 0.4,
        "face_coverage": 1.0,
        "landmark_coverage": 0.98,
    }
    return {
        "version": "motion-style-v2",
        "source_hash": "source-sha",
        "accepted": True,
        "usable_for_motion_planning": True,
        "duration_seconds": 30.0,
        "selected_intervals": {
            "calm": [{**interval, "score": 0.2}],
            "natural": [{**interval, "score": 0.5}],
            "expressive": [interval],
        },
        "coverage": {"face_presence": 1.0, "landmarks": 0.98},
        "motion": {"p90_intensity": 0.82},
        "expression": {"p90_intensity": 0.76},
        "gaze": {"camera_contact_ratio": 0.8},
        "blink": {"per_minute": 12.0},
    }


def test_render_binds_personal_motion_plan_to_liveportrait(tmp_path, settings, monkeypatch):
    settings.STORAGE_ROOT = str(tmp_path)
    owner = User.objects.create_user(username="motion-render-owner")
    portrait_relative = "avatars/portrait.png"
    source_relative = "avatars/performance.mp4"
    for relative_path in (portrait_relative, source_relative):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"source")
    twin = DigitalTwin.objects.create(
        owner=owner,
        display_name="Motion Render Twin",
        status="ready",
        consent_status="approved",
        identity_package={
            "processed_portrait_path": portrait_relative,
            "source_video_path": source_relative,
        },
        voice_package={"voice_id": "digital-twin-motion"},
        motion_style_package=_motion_package(),
        model_versions={"motion": "motion-style-v2"},
    )
    render = DigitalTwinRender.objects.create(
        twin=twin,
        request_payload={
            "script": "Bu plan kişisel hareket kullanır.",
            "language": "tr-TR",
            "emotion": "happy",
            "motion_intensity": 0.8,
            "quality_preset": "high",
        },
    )
    captured: dict = {}
    hardware = SimpleNamespace(warnings=(), as_dict=lambda: {"name": "ada_laptop_8gb", "warnings": []})
    quality = SimpleNamespace(
        publish_allowed=True,
        decision="passed",
        reasons=(),
        as_dict=lambda: {"decision": "passed", "publish_allowed": True, "reasons": []},
    )
    hardware_module = importlib.import_module("avatar.digital_twin.hardware")
    quality_module = importlib.import_module("avatar.digital_twin.render_quality")
    pipeline_module = importlib.import_module("avatar.pipeline")
    tts_module = importlib.import_module("scripts.tts_client")
    monkeypatch.setattr(hardware_module, "apply_local_inference_profile", lambda: hardware)
    monkeypatch.setattr(quality_module, "evaluate_render_quality", lambda **_kwargs: quality)

    def fake_tts(_voice_id, _script, output_path, **_kwargs):
        path = tmp_path / str(output_path).replace(str(tmp_path), "").lstrip("/\\")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return {"provider": "test-tts", "duration": 6.0}

    def fake_render(request):
        captured["request"] = request
        output = tmp_path / str(request.output_path).replace(str(tmp_path), "").lstrip("/\\")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"v" * 2048)
        return {
            "engine_used": "liveportrait+musetalk",
            "stage_paths": {
                "liveportrait_performance_window_source": "motion_style_v2_expressive",
                "liveportrait_performance_window_style": "expressive",
                "liveportrait_performance_window_start": 8.0,
                "liveportrait_performance_window_profile_score": 0.82,
            },
        }

    monkeypatch.setattr(tts_module, "synthesize_text_with_metadata", fake_tts)
    monkeypatch.setattr(pipeline_module, "render_avatar_segment_local", fake_render)
    monkeypatch.setattr(
        digital_twin_tasks,
        "_apply_ai_watermark",
        lambda _source, target: target.write_bytes(b"w" * 2048),
    )

    result = digital_twin_tasks.render_digital_twin.run(render_id=str(render.id))

    render.refresh_from_db()
    request = captured["request"]
    assert result["status"] == "ready"
    assert render.status == "ready"
    assert request.motion_preset == "natural_visible"
    assert request.performance_window["source"] == "motion_style_v2"
    assert request.performance_window["start_seconds"] == 8.0
    assert render.motion_plan["version"] == "motion-plan-v2"
    assert render.motion_plan["style"] == "expressive"
    assert render.motion_plan["personal_window_selected"] is True
    assert render.motion_plan["execution"]["window_source"] == "motion_style_v2_expressive"
    assert render.engine_trace[2]["stage"] == "motion_plan"
    render_dir = tmp_path / "digital_twins" / str(twin.id) / "renders" / str(render.id)
    provenance = json.loads((render_dir / "provenance.json").read_text())
    assert provenance["motion_plan"]["version"] == "motion-plan-v2"
    stored_plan = json.loads((render_dir / "motion-plan.json").read_text())
    assert stored_plan == render.motion_plan
    audit = DigitalTwinAuditEvent.objects.get(twin=twin, event="render.completed")
    assert audit.payload["personal_window_selected"] is True
