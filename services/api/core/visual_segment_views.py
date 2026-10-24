"""Owner-only private engineering API. No normal render or playback integration."""
from django.db import OperationalError
from django.db.models import Q
from django.http import HttpResponse, StreamingHttpResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from core.models import Project, VisualSegmentRun
from core.visual_design.schemas import DesignError
from core.visual_design.render_snapshot import FrozenRenderSnapshot
from core.visual_design_views import OwnerVisualView
from core import visual_segment_runs as controls
from core.visual_segment_dispatch import reserve_and_submit, require_ready
from core.visual_segment_delivery import PrivateDelivery, ROLES, byte_range


class PrivateSegmentThrottle(UserRateThrottle):
    scope = "private_visual_segment"
    rate = "20/min"


class ReservationRequest(serializers.Serializer):
    snapshot_id = serializers.UUIDField()
    expected_snapshot_hash = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")
    expected_revision_hash = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")
    page_key = serializers.CharField(max_length=64,trim_whitespace=False)
    producer_receipt_id = serializers.UUIDField()

    def to_internal_value(self,data):
        if type(data) is not dict or set(data) != set(self.fields) or any(type(v) is not str for v in data.values()):
            raise serializers.ValidationError({"non_field_errors":["Only the six explicit saved-snapshot reservation fields are accepted."]})
        result = super().to_internal_value(data)
        for key in ("snapshot_id","producer_receipt_id"): result[key] = str(result[key])
        return result


UNAVAILABLE = {"audio_provenance_unavailable","deployment_not_ready","private_storage","runtime","audio_runtime","font"}
DENIED = {"run_cancelled","evidence_revoked","terminal_failure","owner"}


class OwnerSegmentView(OwnerVisualView):
    throttle_classes = [PrivateSegmentThrottle]

    def project(self,request,project_id):
        return get_object_or_404(Project,pk=project_id,user=request.user,user__is_active=True)

    def handle_exception(self,exc):
        if isinstance(exc,DesignError):
            code = exc.code
            status = 503 if code in UNAVAILABLE else 403 if code in DENIED else 404 if code in {"media_role","completed_media_unavailable"} else 409
            return Response({"error":{"code":code,"message":"Private segment is unavailable or failed validation."},"publishing_allowed":False},status=status)
        if isinstance(exc,OSError):
            return Response({"error":{"code":"private_artifact_unavailable"},"publishing_allowed":False},status=409)
        if isinstance(exc,OperationalError) and any(x in str(exc).lower() for x in ("locked","deadlock","could not serialize")):
            return Response({"error":{"code":"revision_conflict"},"publishing_allowed":False},status=409)
        return super().handle_exception(exc)

    def row(self,request,project_id,run_id):
        self.project(request,project_id)
        return get_object_or_404(VisualSegmentRun,pk=run_id,project_id=project_id,owner=request.user)


def segment_metadata(project,owner,run_id):
    data = controls.read_private_run(project.pk,owner,run_id)
    row = VisualSegmentRun.objects.select_related("snapshot").get(pk=run_id,project=project,owner=owner)
    current = False; source_reason = None
    try:
        controls._current_preflight(project,owner,FrozenRenderSnapshot(row.snapshot.payload))
        current = True
    except (DesignError,OSError,Http404) as exc:
        source_reason = exc.code if isinstance(exc,DesignError) else "source_unavailable"
    available = False; executable = False; availability_reason = "completed_media_unavailable"
    try:
        require_ready()
        if row.completed_receipt is not None:
            decision = controls.private_result_access_policy(project.pk,owner,run_id)
            available,availability_reason = decision.allowed,decision.reason_code
        elif current and not data["reason_code"]:
            decision = controls.check_run_executable(project.pk,owner,run_id)
            executable = decision.allowed
            if not decision.allowed: availability_reason = decision.reason_code
    except (DesignError,OSError,Http404) as exc:
        availability_reason = exc.code if isinstance(exc,DesignError) else "private_artifact_unavailable"
    return {**data,"schema_version":"visual-segment-status.v1","is_current":current,"is_historical":not current,
        "source_reason_code":source_reason,"result_available":available,"execution_enabled":executable,"availability_reason_code":availability_reason,
        "media_roles":list(ROLES) if available else [],"failure_reason":row.terminal_failure_reason or None,
        "dispatch_reason_code":row.dispatch_error or None,"publishing_allowed":False}


class VisualSegmentListView(OwnerSegmentView):
    def get(self,request,project_id):
        project = self.project(request,project_id)
        if set(request.query_params)-{"page_key","limit","before"} or any(len(v)>1 for _,v in request.query_params.lists()):
            raise serializers.ValidationError("Only page_key, before and limit are accepted once.")
        try: limit = int(request.query_params.get("limit",10))
        except (TypeError,ValueError): raise serializers.ValidationError("Invalid limit.")
        if not 1 <= limit <= 20: raise serializers.ValidationError("Limit must be 1–20.")
        rows = VisualSegmentRun.objects.filter(project=project,owner=request.user)
        page_key = request.query_params.get("page_key")
        if page_key is not None:
            if not 0 < len(page_key) <= 64: raise serializers.ValidationError("Invalid page key.")
            rows = rows.filter(page_key=page_key)
        before = request.query_params.get("before")
        if before:
            parsed = serializers.UUIDField().run_validation(before)
            cursor = get_object_or_404(VisualSegmentRun,pk=parsed,project=project,owner=request.user)
            rows = rows.filter(Q(created_at__lt=cursor.created_at) | Q(created_at=cursor.created_at,pk__lt=cursor.pk))
        selected = list(rows.order_by("-created_at","-pk")[:limit])
        return Response({"runs":[segment_metadata(project,request.user,row.pk) for row in selected],
            "next_before":str(selected[-1].pk) if len(selected)==limit else None,
            "limit":limit,"publishing_allowed":False})

    def post(self,request,project_id):
        project = self.project(request,project_id)
        payload = ReservationRequest(data=request.data); payload.is_valid(raise_exception=True)
        row,reused = reserve_and_submit(project_id,request.user,**payload.validated_data)
        return Response({**segment_metadata(project,request.user,row.pk),"reused":reused},status=200 if reused else 201)


class VisualSegmentDetailView(OwnerSegmentView):
    def get(self,request,project_id,run_id):
        self.row(request,project_id,run_id)
        return Response(segment_metadata(self.project(request,project_id),request.user,run_id))


class VisualSegmentCancelView(OwnerSegmentView):
    def post(self,request,project_id,run_id):
        self.row(request,project_id,run_id)
        if type(request.data) is not dict or set(request.data)!={"reason"} or type(request.data.get("reason")) is not str:
            raise serializers.ValidationError("Only an explicit cancellation reason is accepted.")
        reason = serializers.CharField(max_length=160,trim_whitespace=False).run_validation(request.data["reason"])
        controls.cancel_run(project_id,request.user,run_id,reason=reason)
        return Response(segment_metadata(self.project(request,project_id),request.user,run_id))


class VisualSegmentMediaView(OwnerSegmentView):
    def head(self,request,*args,**kwargs): return self.get(request,*args,**kwargs)

    def get(self,request,project_id,run_id,role):
        self.row(request,project_id,run_id)
        if role not in ROLES: raise Http404
        delivery = PrivateDelivery(project_id,request.user,run_id,role)
        try:
            start,end,status = 0,delivery.size-1,200
            # RFC Range applies to GET. Without a matching If-Range validator,
            # return a full response; no public cache validators are issued.
            header = request.headers.get("Range") if request.method=="GET" and not request.headers.get("If-Range") else None
            if header is not None:
                try:
                    if role!="mp4": raise DesignError("invalid_range","Sidecar ranges are unsupported")
                    start,end = byte_range(header,delivery.size); status = 206
                except DesignError:
                    response = HttpResponse(status=416)
                    response["Content-Range"] = f"bytes */{delivery.size}"
                    response["Content-Length"] = "0"
                    delivery.close(); return response
            if request.method=="HEAD":
                response = HttpResponse(content_type=ROLES[role][1]); delivery.close()
            else:
                response = StreamingHttpResponse(delivery.range(start,end),status=status,content_type=ROLES[role][1])
            response["Content-Length"] = str(end-start+1)
            if role=="mp4": response["Accept-Ranges"] = "bytes"
            if status==206: response["Content-Range"] = f"bytes {start}-{end}/{delivery.size}"
            response["Content-Disposition"] = f'inline; filename="private-segment.{role}"'
            response["Cross-Origin-Resource-Policy"] = "same-origin"
            response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
            return response
        except BaseException:
            delivery.close(); raise
