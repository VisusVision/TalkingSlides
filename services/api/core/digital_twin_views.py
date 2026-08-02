from __future__ import annotations

from datetime import timedelta
import hashlib
import os
from pathlib import Path
import secrets

from celery import Celery
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.digital_twin_serializers import (
    DigitalTwinConsentSessionSerializer,
    DigitalTwinRenderSerializer,
    DigitalTwinSerializer,
    DigitalTwinTrainingRunSerializer,
)
from core.models import (
    DigitalTwin,
    DigitalTwinAuditEvent,
    DigitalTwinConsentSession,
    DigitalTwinRender,
    DigitalTwinTrainingRun,
)


_celery = Celery(broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"))
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


def _queue(name: str, fallback: str) -> str:
    return str(getattr(settings, name, fallback) or fallback).strip() or fallback


def _owner_twin(request, twin_id):
    query = DigitalTwin.objects.filter(pk=twin_id)
    if not request.user.is_staff:
        query = query.filter(owner=request.user)
    return query.first()


def _idempotency(request, scope: str) -> str | None:
    raw = str(request.headers.get("Idempotency-Key") or "").strip()
    return f"{request.user.pk}:{scope}:{raw}"[:128] if raw else None


def _audit(*, twin, actor, event: str, payload=None):
    DigitalTwinAuditEvent.objects.create(twin=twin, actor=actor, event=event, payload=payload or {})


def _motion_intensity(value) -> float:
    try:
        parsed = float(value if value is not None else 0.5)
    except (TypeError, ValueError):
        raise ValueError("motion_intensity_must_be_numeric")
    return max(0.0, min(1.0, parsed))


def _save_upload(upload, target_dir: Path, stem: str, allowed: set[str], max_bytes: int) -> str:
    size = int(getattr(upload, "size", 0) or 0)
    suffix = Path(str(getattr(upload, "name", "") or "")).suffix.lower()
    if suffix not in allowed:
        raise ValueError(f"unsupported_file_type:{suffix or 'missing'}")
    if size <= 0:
        raise ValueError("empty_upload")
    if size > max_bytes:
        raise ValueError("upload_too_large")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}{suffix}"
    temporary = target.with_suffix(target.suffix + ".uploading")
    with open(temporary, "wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)
    os.replace(temporary, target)
    return str(target.relative_to(Path(settings.STORAGE_ROOT))).replace("\\", "/")


class DigitalTwinCollectionView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def get(self, request):
        twins = DigitalTwin.objects.filter(owner=request.user).order_by("-created_at")
        return Response(DigitalTwinSerializer(twins, many=True).data)

    def post(self, request):
        display_name = str(request.data.get("display_name") or "").strip()
        if not display_name:
            return Response({"error": "display_name_required"}, status=status.HTTP_400_BAD_REQUEST)
        capabilities = request.data.get("capabilities") or ["portrait", "voice", "motion_style"]
        if not isinstance(capabilities, list):
            return Response({"error": "capabilities_must_be_a_list"}, status=status.HTTP_400_BAD_REQUEST)
        allowed = {"portrait", "full_body", "voice", "motion_style"}
        capabilities = list(dict.fromkeys(str(value) for value in capabilities if str(value) in allowed))
        key = _idempotency(request, "create")
        if key:
            existing = DigitalTwin.objects.filter(owner=request.user, idempotency_key=key).first()
            if existing:
                return Response(DigitalTwinSerializer(existing).data)
        try:
            twin = DigitalTwin.objects.create(
                owner=request.user,
                display_name=display_name[:120],
                capabilities=capabilities,
                locale=str(request.data.get("locale") or "tr-TR")[:16],
                idempotency_key=key,
            )
        except IntegrityError:
            twin = DigitalTwin.objects.get(idempotency_key=key)
        _audit(twin=twin, actor=request.user, event="digital_twin.created")
        return Response(DigitalTwinSerializer(twin).data, status=status.HTTP_201_CREATED)


class DigitalTwinDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, twin_id):
        twin = _owner_twin(request, twin_id)
        if not twin:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(DigitalTwinSerializer(twin).data)


class DigitalTwinConsentSessionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, twin_id):
        twin = _owner_twin(request, twin_id)
        if not twin or twin.status == "revoked":
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        nonce = secrets.token_urlsafe(24)
        challenge = (
            f"Ben {twin.display_name}. Bu dijital ikizin yalnızca benim açık iznimle "
            f"oluşturulmasını ve yapay üretilmiş içerik olarak işaretlenmesini kabul ediyorum. Kod: {nonce[:8]}"
        )
        session = DigitalTwinConsentSession.objects.create(
            twin=twin,
            challenge_text=challenge,
            challenge_nonce_hash=hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        twin.status = "verifying_consent"
        twin.consent_status = "pending"
        twin.save(update_fields=["status", "consent_status", "updated_at"])
        _audit(twin=twin, actor=request.user, event="consent.challenge_created", payload={"session_id": str(session.id)})
        payload = DigitalTwinConsentSessionSerializer(session).data
        payload["recording_instructions"] = [
            "Metni kesintisiz ve kameraya bakarak okuyun.",
            "Yüzünüz görünür, sesiniz anlaşılır olsun.",
            "Performans videosundan ayrı bir kayıt yükleyin.",
        ]
        return Response(payload, status=status.HTTP_201_CREATED)


class DigitalTwinTrainingRunView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, twin_id):
        twin = _owner_twin(request, twin_id)
        if not twin or twin.status == "revoked":
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        session = DigitalTwinConsentSession.objects.filter(
            pk=request.data.get("consent_session_id"), twin=twin
        ).first()
        if not session or session.expires_at <= timezone.now():
            return Response({"error": "valid_consent_session_required"}, status=status.HTTP_400_BAD_REQUEST)
        performance_video = request.FILES.get("performance_video")
        consent_video = request.FILES.get("consent_video")
        if not performance_video or not consent_video:
            return Response({"error": "performance_and_consent_videos_required"}, status=status.HTTP_400_BAD_REQUEST)
        key = _idempotency(request, f"train:{twin.id}")
        if key:
            existing = DigitalTwinTrainingRun.objects.filter(twin=twin, idempotency_key=key).first()
            if existing:
                return Response(DigitalTwinTrainingRunSerializer(existing).data)
        source_dir = Path(settings.STORAGE_ROOT) / "digital_twins" / str(twin.id) / "source"
        try:
            performance_path = _save_upload(performance_video, source_dir, "performance", _VIDEO_EXTENSIONS, 500 * 1024 * 1024)
            consent_path = _save_upload(consent_video, source_dir, f"consent-{session.id}", _VIDEO_EXTENSIONS, 100 * 1024 * 1024)
            voice_upload = request.FILES.get("voice_reference")
            voice_path = (
                _save_upload(voice_upload, source_dir, "voice-reference", _AUDIO_EXTENSIONS, 25 * 1024 * 1024)
                if voice_upload else ""
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        session.consent_video_path = consent_path
        session.status = "pending"
        session.save(update_fields=["consent_video_path", "status", "updated_at"])
        run = DigitalTwinTrainingRun.objects.create(
            twin=twin,
            consent_session=session,
            idempotency_key=key,
            input_manifest={
                "performance_video_path": performance_path,
                "consent_video_path": consent_path,
                "voice_reference_path": voice_path,
                "reference_type": "video",
            },
        )
        _audit(twin=twin, actor=request.user, event="training.evidence_uploaded", payload={"run_id": str(run.id)})
        try:
            task = _celery.send_task(
                "worker.digital_twin.verify_consent",
                kwargs={"training_run_id": str(run.id)},
                queue=_queue("CELERY_DIGITAL_TWIN_VERIFY_QUEUE", "avatar-verify"),
            )
            run.task_id = str(task.id or "")
            run.stage = "verification_queued"
            run.save(update_fields=["task_id", "stage", "updated_at"])
        except Exception as exc:
            session.status = "pending_review"
            session.decision = {"decision": "pending_review", "error": "verification_dispatch_failed"}
            session.save(update_fields=["status", "decision", "updated_at"])
            run.stage = "waiting_for_manual_review"
            run.error_code = "verification_dispatch_failed"
            run.error_message = str(exc)[:500]
            run.save(update_fields=["stage", "error_code", "error_message", "updated_at"])
            _audit(twin=twin, actor=request.user, event="consent.verification_dispatch_failed", payload={"run_id": str(run.id)})
        return Response(DigitalTwinTrainingRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)


class DigitalTwinConsentDecisionView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, twin_id, session_id):
        twin = _owner_twin(request, twin_id)
        session = DigitalTwinConsentSession.objects.filter(pk=session_id, twin=twin).first() if twin else None
        if not session:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        decision = str(request.data.get("decision") or "").lower()
        if decision not in {"approved", "rejected"}:
            return Response({"error": "decision_must_be_approved_or_rejected"}, status=status.HTTP_400_BAD_REQUEST)
        reason = str(request.data.get("reason") or "").strip()
        if decision == "approved" and not reason:
            return Response({"error": "approval_reason_required"}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            session.status = decision
            session.verified_at = timezone.now()
            session.decision = {
                "decision": decision,
                "automated": False,
                "reviewer_id": request.user.pk,
                "reason": reason[:500],
                "face_match_score": request.data.get("face_match_score"),
                "liveness_score": request.data.get("liveness_score"),
            }
            session.save(update_fields=["status", "verified_at", "decision", "updated_at"])
            twin.consent_status = decision
            twin.consent_decision = session.decision
            twin.status = "training" if decision == "approved" else "failed"
            twin.failure_code = "" if decision == "approved" else "consent_rejected"
            twin.save(update_fields=["consent_status", "consent_decision", "status", "failure_code", "updated_at"])
            runs = list(session.training_runs.filter(status="pending_consent"))
            if decision == "approved":
                session.training_runs.filter(status="pending_consent").update(status="queued", stage="queued")
            else:
                session.training_runs.filter(status="pending_consent").update(
                    status="cancelled", stage="consent_rejected", error_code="consent_rejected"
                )
        if decision == "approved":
            for run in runs:
                result = _celery.send_task(
                    "worker.digital_twin.train",
                    kwargs={"training_run_id": str(run.id)},
                    queue=_queue("CELERY_DIGITAL_TWIN_TRAIN_QUEUE", "avatar-train"),
                )
                DigitalTwinTrainingRun.objects.filter(pk=run.id).update(task_id=str(result.id or ""))
        _audit(twin=twin, actor=request.user, event=f"consent.{decision}", payload={"session_id": str(session.id)})
        return Response(DigitalTwinConsentSessionSerializer(session).data)


class DigitalTwinRenderCollectionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, twin_id):
        twin = _owner_twin(request, twin_id)
        if not twin:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        if twin.status != "ready" or twin.consent_status != "approved":
            return Response({"error": "digital_twin_not_ready"}, status=status.HTTP_409_CONFLICT)
        script = str(request.data.get("script") or "").strip()
        if not script:
            return Response({"error": "script_required"}, status=status.HTTP_400_BAD_REQUEST)
        if len(script) > 10_000:
            return Response({"error": "script_too_long"}, status=status.HTTP_400_BAD_REQUEST)
        mode = str(request.data.get("render_mode") or "portrait")
        if mode not in {"portrait", "full_body"}:
            return Response({"error": "invalid_render_mode"}, status=status.HTTP_400_BAD_REQUEST)
        if mode == "full_body" and "full_body" not in twin.capabilities:
            return Response({"error": "full_body_capability_not_trained"}, status=status.HTTP_409_CONFLICT)
        key = _idempotency(request, f"render:{twin.id}")
        if key:
            existing = DigitalTwinRender.objects.filter(twin=twin, idempotency_key=key).first()
            if existing:
                return Response(DigitalTwinRenderSerializer(existing).data)
        try:
            motion_intensity = _motion_intensity(request.data.get("motion_intensity"))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        render = DigitalTwinRender.objects.create(
            twin=twin,
            render_mode=mode,
            request_payload={
                "script": script,
                "language": str(request.data.get("language") or twin.locale),
                "emotion": str(request.data.get("emotion") or "neutral"),
                "motion_intensity": motion_intensity,
                "quality_preset": str(request.data.get("quality_preset") or "high"),
            },
            watermark_required=True,
            idempotency_key=key,
        )
        result = _celery.send_task(
            "worker.digital_twin.render",
            kwargs={"render_id": str(render.id)},
            queue=_queue("CELERY_DIGITAL_TWIN_RENDER_QUEUE", "avatar-render"),
        )
        render.task_id = str(result.id or "")
        render.save(update_fields=["task_id", "updated_at"])
        _audit(twin=twin, actor=request.user, event="render.queued", payload={"render_id": str(render.id)})
        return Response(DigitalTwinRenderSerializer(render).data, status=status.HTTP_202_ACCEPTED)


class DigitalTwinRenderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, twin_id, render_id):
        twin = _owner_twin(request, twin_id)
        render = DigitalTwinRender.objects.filter(pk=render_id, twin=twin).first() if twin else None
        if not render:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(DigitalTwinRenderSerializer(render).data)


class DigitalTwinRevokeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, twin_id):
        twin = _owner_twin(request, twin_id)
        if not twin:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        twin.status = "revoked"
        twin.consent_status = "revoked"
        twin.revoked_at = timezone.now()
        twin.save(update_fields=["status", "consent_status", "revoked_at", "updated_at"])
        twin.training_runs.filter(status__in=["pending_consent", "queued"]).update(status="cancelled", stage="revoked")
        twin.renders.filter(status="queued").update(status="cancelled", error_code="consent_revoked")
        _audit(twin=twin, actor=request.user, event="digital_twin.revoked")
        return Response(DigitalTwinSerializer(twin).data)
