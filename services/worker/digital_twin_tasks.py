from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import traceback

from django.conf import settings
from django.utils import timezone

from worker.celery_app import app


def _storage_root() -> Path:
    return Path(settings.STORAGE_ROOT).resolve()


def _absolute(relative_path: str) -> Path:
    root = _storage_root()
    candidate = (root / str(relative_path or "")).resolve()
    if candidate != root and root not in candidate.parents:
        raise RuntimeError("digital_twin_path_outside_storage")
    return candidate


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(_storage_root())).replace("\\", "/")


def _run(command: list[str], error_code: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"{error_code}:{(result.stderr or '')[-800:]}")


def _extract_voice_reference(source_video: Path, twin_id: str) -> dict:
    voice_id = f"digital-twin-{twin_id}"
    output = _storage_root() / "voices" / f"{voice_id}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_video), "-map", "0:a:0", "-vn", "-t", "60",
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output),
        ],
        "voice_extraction_failed",
    )
    if not output.exists() or output.stat().st_size <= 44:
        raise RuntimeError("voice_extraction_empty")
    return {"voice_id": voice_id, "reference_path": _relative(output), "sample_rate": 24000, "channels": 1}


@app.task(bind=True, name="worker.digital_twin.verify_consent", acks_late=True)
def verify_digital_twin_consent(self, *, training_run_id: str) -> dict:
    from avatar.digital_twin.verification import verify_consent_evidence
    from core.models import DigitalTwinAuditEvent, DigitalTwinTrainingRun

    run = DigitalTwinTrainingRun.objects.select_related("twin", "consent_session").get(pk=training_run_id)
    twin = run.twin
    session = run.consent_session
    if twin.status == "revoked":
        run.status = "cancelled"
        run.stage = "revoked"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stage", "finished_at", "updated_at"])
        return {"decision": "cancelled", "reason": "revoked"}
    session.status = "pending"
    session.save(update_fields=["status", "updated_at"])
    run.stage = "verifying_consent"
    run.task_id = str(getattr(self.request, "id", "") or run.task_id)
    run.save(update_fields=["stage", "task_id", "updated_at"])
    try:
        report = verify_consent_evidence(
            performance_video=_absolute(run.input_manifest["performance_video_path"]),
            consent_video=_absolute(run.input_manifest["consent_video_path"]),
            challenge_text=session.challenge_text,
        )
        decision_payload = report.as_dict()
        decision_payload["automated"] = True
        decision_payload["verified_at"] = timezone.now().isoformat()
        session.status = report.decision
        session.decision = decision_payload
        session.verified_at = timezone.now() if report.decision in {"approved", "rejected"} else None
        session.save(update_fields=["status", "decision", "verified_at", "updated_at"])
        twin.consent_status = report.decision
        twin.consent_decision = decision_payload
        twin.status = "training" if report.decision == "approved" else ("failed" if report.decision == "rejected" else "verifying_consent")
        twin.failure_code = "consent_rejected" if report.decision == "rejected" else ""
        twin.save(update_fields=["consent_status", "consent_decision", "status", "failure_code", "updated_at"])
        if report.decision == "approved":
            run.status = "queued"
            run.stage = "queued"
            run.save(update_fields=["status", "stage", "updated_at"])
            task = app.send_task(
                "worker.digital_twin.train",
                kwargs={"training_run_id": str(run.id)},
                queue=str(os.environ.get("CELERY_DIGITAL_TWIN_TRAIN_QUEUE", "avatar-train") or "avatar-train"),
            )
            run.task_id = str(task.id or "")
            run.save(update_fields=["task_id", "updated_at"])
        elif report.decision == "rejected":
            run.status = "cancelled"
            run.stage = "consent_rejected"
            run.error_code = "consent_rejected"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "stage", "error_code", "finished_at", "updated_at"])
        else:
            run.stage = "waiting_for_manual_review"
            run.save(update_fields=["stage", "updated_at"])
        DigitalTwinAuditEvent.objects.create(
            twin=twin,
            event=f"consent.automated_{report.decision}",
            payload={"run_id": str(run.id), "reasons": list(report.reasons)},
        )
        return {"decision": report.decision, "run_id": str(run.id), "report": decision_payload}
    except Exception as exc:
        session.status = "pending_review"
        session.decision = {"decision": "pending_review", "automated": True, "error": str(exc)[:1000]}
        session.save(update_fields=["status", "decision", "updated_at"])
        twin.consent_status = "pending_review"
        twin.consent_decision = session.decision
        twin.save(update_fields=["consent_status", "consent_decision", "updated_at"])
        run.stage = "waiting_for_manual_review"
        run.error_code = "consent_verifier_unavailable"
        run.error_message = str(exc)[:1000]
        run.save(update_fields=["stage", "error_code", "error_message", "updated_at"])
        DigitalTwinAuditEvent.objects.create(
            twin=twin,
            event="consent.automated_error",
            payload={"run_id": str(run.id), "error": str(exc)[:500]},
        )
        return {"decision": "pending_review", "run_id": str(run.id), "error": str(exc)[:1000]}


@app.task(bind=True, name="worker.digital_twin.train", acks_late=True)
def train_digital_twin(self, *, training_run_id: str) -> dict:
    from avatar.digital_twin.hardware import apply_local_inference_profile
    from avatar.preprocess import preprocess_avatar_video
    from core.models import DigitalTwinAuditEvent, DigitalTwinTrainingRun

    hardware_profile = apply_local_inference_profile()
    run = DigitalTwinTrainingRun.objects.select_related("twin", "consent_session", "twin__owner").get(pk=training_run_id)
    twin = run.twin
    if twin.status == "revoked" or twin.consent_status != "approved" or run.consent_session.status != "approved":
        run.status = "cancelled"
        run.stage = "consent_not_approved"
        run.error_code = "consent_not_approved"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stage", "error_code", "finished_at", "updated_at"])
        return {"status": "cancelled", "reason": "consent_not_approved"}

    run.status = "running"
    run.stage = "identity_package"
    run.started_at = timezone.now()
    run.task_id = str(getattr(self.request, "id", "") or run.task_id)
    run.save(update_fields=["status", "stage", "started_at", "task_id", "updated_at"])
    twin.status = "training"
    twin.failure_code = ""
    twin.failure_message = ""
    twin.save(update_fields=["status", "failure_code", "failure_message", "updated_at"])

    try:
        performance_path = _absolute(run.input_manifest["performance_video_path"])
        if not performance_path.exists():
            raise RuntimeError("performance_video_missing")
        result = preprocess_avatar_video(
            video_bytes=performance_path.read_bytes(),
            original_filename=performance_path.name,
            storage_root=str(_storage_root()),
            teacher_id=int(twin.owner_id),
            model_version="digital-twin-v2/liveportrait+musetalk",
        )
        identity_package = {
            "source_hash": result.get("source_hash", ""),
            "source_video_path": result.get("video_rel_path", ""),
            "processed_portrait_path": result.get("processed_rel_path", ""),
            "manifest_path": result.get("identity_package_rel_path", ""),
            "reference_paths": result.get("references_rel_paths", []),
        }

        run.stage = "voice_package"
        run.save(update_fields=["stage", "updated_at"])
        voice_source = _absolute(run.input_manifest.get("voice_reference_path") or run.input_manifest["performance_video_path"])
        voice_package = _extract_voice_reference(voice_source, str(twin.id))

        run.stage = "motion_style_package"
        run.save(update_fields=["stage", "updated_at"])
        motion_dir = _storage_root() / "digital_twins" / str(twin.id) / "packages"
        motion_dir.mkdir(parents=True, exist_ok=True)
        motion_manifest = motion_dir / "motion-style.json"
        motion_payload = {
            "version": "motion-style-v1",
            "reference_video_path": run.input_manifest["performance_video_path"],
            "motion_presets": ["natural", "expressive", "calm"],
            "note": "Current adapter selects learned performance windows; trainable motion encoder is a V3 component.",
        }
        motion_manifest.write_text(json.dumps(motion_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        motion_package = {**motion_payload, "manifest_path": _relative(motion_manifest)}

        twin.status = "validating"
        twin.identity_package = identity_package
        twin.voice_package = voice_package
        twin.motion_style_package = motion_package
        twin.reference_analysis = {
            "warnings": [*result.get("warnings", []), *hardware_profile.warnings],
            "reference_type": "video",
            "hardware_profile": hardware_profile.as_dict(),
        }
        twin.model_versions = {
            "identity": "avatar-preprocess-v2",
            "portrait_renderer": "liveportrait+musetalk:v1",
            "voice": "xtts-compatible-reference:v1",
            "motion": "performance-window:v1",
        }
        twin.save(update_fields=[
            "status", "identity_package", "voice_package", "motion_style_package",
            "reference_analysis", "model_versions", "updated_at",
        ])
        required = [identity_package.get("processed_portrait_path"), identity_package.get("source_video_path"), voice_package.get("reference_path")]
        if not all(value and _absolute(value).exists() for value in required):
            raise RuntimeError("package_validation_failed")
        twin.status = "ready"
        twin.save(update_fields=["status", "updated_at"])
        run.status = "done"
        run.stage = "ready"
        run.output_manifest = {
            "identity_package": identity_package,
            "voice_package": voice_package,
            "motion_style_package": motion_package,
            "hardware_profile": hardware_profile.as_dict(),
        }
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stage", "output_manifest", "finished_at", "updated_at"])
        DigitalTwinAuditEvent.objects.create(twin=twin, event="training.completed", payload={"run_id": str(run.id)})
        return {"status": "ready", "twin_id": str(twin.id), "training_run_id": str(run.id)}
    except Exception as exc:
        safe_error = str(exc)[:1000]
        run.status = "failed"
        run.stage = "failed"
        run.error_code = safe_error.split(":", 1)[0][:80] or "training_failed"
        run.error_message = safe_error
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stage", "error_code", "error_message", "finished_at", "updated_at"])
        twin.status = "failed"
        twin.failure_code = run.error_code
        twin.failure_message = safe_error
        twin.save(update_fields=["status", "failure_code", "failure_message", "updated_at"])
        DigitalTwinAuditEvent.objects.create(
            twin=twin,
            event="training.failed",
            payload={"run_id": str(run.id), "error_code": run.error_code, "trace": traceback.format_exc()[-2000:]},
        )
        raise


def _apply_ai_watermark(source: Path, target: Path) -> None:
    try:
        max_height = max(360, min(int(os.environ.get("DIGITAL_TWIN_DELIVERY_MAX_HEIGHT", "1080")), 2160))
    except ValueError:
        max_height = 1080
    video_filter = (
        f"scale=-2:min({max_height}\\,ih),"
        "drawbox=x=w-190:y=h-50:w=180:h=36:color=black@0.55:t=fill,"
        "drawtext=text='AI AVATAR':x=w-tw-22:y=h-th-18:fontcolor=white:fontsize=20"
    )
    _run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(target),
        ],
        "watermark_failed",
    )


@app.task(bind=True, name="worker.digital_twin.render", acks_late=True)
def render_digital_twin(self, *, render_id: str) -> dict:
    from avatar.digital_twin.hardware import apply_local_inference_profile
    from avatar.digital_twin.render_quality import evaluate_render_quality
    from avatar.pipeline import AvatarRenderRequest, render_avatar_segment_local
    from core.models import DigitalTwinAuditEvent, DigitalTwinRender
    from scripts.tts_client import synthesize_text_with_metadata

    hardware_profile = apply_local_inference_profile()
    render = DigitalTwinRender.objects.select_related("twin").get(pk=render_id)
    twin = render.twin
    if twin.status != "ready" or twin.consent_status != "approved":
        render.status = "cancelled"
        render.error_code = "digital_twin_not_ready"
        render.finished_at = timezone.now()
        render.save(update_fields=["status", "error_code", "finished_at", "updated_at"])
        return {"status": "cancelled", "reason": "digital_twin_not_ready"}
    render.status = "running"
    render.started_at = timezone.now()
    render.task_id = str(getattr(self.request, "id", "") or render.task_id)
    render.save(update_fields=["status", "started_at", "task_id", "updated_at"])
    try:
        if render.render_mode == "full_body":
            raise RuntimeError("full_body_renderer_not_configured")
        output_dir = _storage_root() / "digital_twins" / str(twin.id) / "renders" / str(render.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / "speech.mp3"
        raw_path = output_dir / "raw.mp4"
        final_path = output_dir / "avatar.mp4"
        tts = synthesize_text_with_metadata(
            str(twin.voice_package["voice_id"]),
            str(render.request_payload["script"]),
            str(audio_path),
            mode="service",
            lang=str(render.request_payload.get("language") or twin.locale),
        )
        request = AvatarRenderRequest(
            source_image_path=str(_absolute(twin.identity_package["processed_portrait_path"])),
            source_image_original_path=str(_absolute(twin.identity_package["processed_portrait_path"])),
            source_video_path=str(_absolute(twin.identity_package["source_video_path"])),
            avatar_reference_type="video",
            audio_path=str(audio_path),
            output_path=str(raw_path),
            motion_preset="natural",
            quality_preset=str(render.request_payload.get("quality_preset") or "high"),
            lipsync_engine="musetalk",
            restoration_enabled=True,
            liveportrait_enabled=True,
            enforce_exact_audio_duration=True,
        )
        info = render_avatar_segment_local(request)
        if not raw_path.exists() or raw_path.stat().st_size <= 1024:
            raise RuntimeError("renderer_output_missing")
        quality_report = evaluate_render_quality(
            source_image=request.source_image_path,
            output_video=raw_path,
            audio_path=audio_path,
            render_info=info,
        )
        quality = quality_report.as_dict()
        quality["checks"] = {
            "output_non_empty": True,
            "watermark_applied": False,
            "consent_active": True,
        }
        render.quality_report = quality
        render.save(update_fields=["quality_report", "updated_at"])
        if not quality_report.publish_allowed:
            raise RuntimeError(f"quality_gate_failed:{quality_report.decision}:{','.join(quality_report.reasons)}")
        _apply_ai_watermark(raw_path, final_path)
        quality["checks"]["watermark_applied"] = True
        quality["output_non_empty"] = bool(final_path.exists() and final_path.stat().st_size > 1024)
        if not quality["output_non_empty"]:
            raise RuntimeError("quality_gate_failed")
        provenance = {
            "generated": True,
            "watermark": "AI AVATAR",
            "twin_id": str(twin.id),
            "render_id": str(render.id),
            "model_versions": twin.model_versions,
            "quality_decision": quality_report.decision,
            "quality_review_required": quality_report.decision == "review_required",
        }
        (output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        render.status = "ready"
        render.output_path = _relative(final_path)
        render.quality_report = quality
        render.engine_trace = [
            {"stage": "hardware", "profile": hardware_profile.as_dict()},
            {"stage": "tts", "provider": tts.get("provider")},
            {"stage": "portrait", "engine": info.get("engine_used", "liveportrait+musetalk")},
            {"stage": "provenance", "watermark": "AI AVATAR"},
        ]
        render.motion_plan = {
            "emotion": render.request_payload.get("emotion", "neutral"),
            "intensity": render.request_payload.get("motion_intensity", 0.5),
            "source": "personal_performance_window",
        }
        render.finished_at = timezone.now()
        render.save(update_fields=[
            "status", "output_path", "quality_report", "engine_trace", "motion_plan", "finished_at", "updated_at",
        ])
        DigitalTwinAuditEvent.objects.create(twin=twin, event="render.completed", payload={"render_id": str(render.id)})
        return {"status": "ready", "render_id": str(render.id), "output_path": render.output_path}
    except Exception as exc:
        render.status = "quality_failed" if str(exc).startswith("quality_gate_failed") else "failed"
        render.error_code = str(exc).split(":", 1)[0][:80] or "render_failed"
        render.error_message = str(exc)[:1000]
        render.finished_at = timezone.now()
        render.save(update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"])
        DigitalTwinAuditEvent.objects.create(
            twin=twin, event="render.failed", payload={"render_id": str(render.id), "error_code": render.error_code}
        )
        raise
