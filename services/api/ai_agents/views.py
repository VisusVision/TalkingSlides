
from __future__ import annotations

import logging
import mimetypes
import os
import sys
from datetime import timedelta
from pathlib import Path

from celery import Celery
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_agents.models import AgentFinding, AdminReviewRequest, ModerationAuditEvent, ModerationReport, PublicationBlockEvent
from ai_agents.policies import (
    APPROVED_MODERATION_STATUSES,
    enforce_unpublished_for_unresolved_moderation,
    manual_moderation_prevents_auto_override,
)
from ai_agents.serializers import (
    REVIEWABLE_MODERATION_STATUSES,
    AdminModerationReportActionSerializer,
    AdminProjectModerationActionSerializer,
    AdminReviewDecisionSerializer,
    AdminReviewRequestSerializer,
    ModerationReportCreateSerializer,
    RequestAdminReviewSerializer,
    RescanModerationSerializer,
    admin_review_detail_payload,
    admin_review_list_payload,
    admin_project_queue_payload,
    moderation_report_payload,
    moderation_summary_payload,
)
from core.notifications import notify_publisher_moderation_action
from core.models import Project

from .permissions import IsStaffUser, can_manage_project_moderation, is_staff_user


_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
_celery_app = Celery(broker=_BROKER_URL)
_RUN_PROJECT_MODERATION_TASK = "worker.tasks.run_project_moderation"
logger = logging.getLogger(__name__)
REPORT_DEDUPE_WINDOW = timedelta(hours=24)


def _moderation_task_queue_name() -> str:
    return os.environ.get("CELERY_RENDER_QUEUE", "render").strip() or "render"


def _dispatch_moderation_task(project_id: int, *, triggered_by_user_id: int | None, phase: str):
    queue_name = _moderation_task_queue_name()
    signature = _celery_app.signature(
        _RUN_PROJECT_MODERATION_TASK,
        args=[int(project_id)],
        kwargs={"triggered_by_user_id": triggered_by_user_id, "phase": phase},
    )
    return signature.apply_async(queue=queue_name)


class ProjectModerationSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id: int):
        project = _get_project(project_id)
        if project is None:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_project_moderation(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        payload = moderation_summary_payload(project, include_admin_fields=is_staff_user(request.user))
        return Response(payload, status=status.HTTP_200_OK)


class ProjectModerationPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, project_id: int, finding_id: int):
        project = _get_project(project_id)
        if project is None:
            raise Http404
        if not can_manage_project_moderation(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        finding = (
            AgentFinding.objects.select_related("run", "run__project")
            .filter(pk=int(finding_id), run__project_id=int(project.id))
            .first()
        )
        if finding is None:
            raise Http404

        full_path = _moderation_preview_file(project, finding)
        if full_path is None:
            raise Http404

        content_type, _ = mimetypes.guess_type(str(full_path))
        response = FileResponse(full_path.open("rb"), content_type=content_type or "application/octet-stream")
        response["Cache-Control"] = "private, no-store"
        return response


def _moderation_preview_file(project: Project, finding: AgentFinding) -> Path | None:
    location = finding.location if isinstance(finding.location, dict) else {}
    asset_kind = _moderation_preview_asset_kind(finding, location)
    raw_path = str(location.get("frame_path") or location.get("image_path") or "").strip()
    resolved = _resolve_moderation_preview_path(raw_path)
    if resolved is not None:
        return resolved

    if asset_kind == "cover":
        return _resolve_moderation_preview_path(
            str(getattr(project, "cover_image_processed", "") or getattr(project, "cover_image_original", ""))
        )
    if asset_kind == "custom_background":
        page = _moderation_preview_page(project, location)
        if page is None:
            return None
        scene = page.editor_document.get("scene") if isinstance(page.editor_document, dict) else {}
        if not isinstance(scene, dict):
            return None
        return _resolve_moderation_preview_path(str(scene.get("custom_background_path") or ""))
    return None


def _moderation_preview_asset_kind(finding: AgentFinding, location: dict) -> str:
    raw = str(location.get("asset_type") or finding.object_type or "").strip().lower()
    aliases = {
        "lesson_cover": "cover",
        "background": "custom_background",
        "custom-background": "custom_background",
        "slide": "slide_image",
        "frame": "video_frame",
        "avatar_image": "profile_image",
        "profile_logo": "channel_logo",
        "profile_banner": "channel_banner",
    }
    return aliases.get(raw, raw)


def _moderation_preview_page(project: Project, location: dict):
    transcript_page_id = _safe_int(location.get("transcript_page_id"))
    if transcript_page_id is not None:
        page = project.transcript_pages.filter(pk=transcript_page_id, is_active=True).first()
        if page is not None:
            return page
    page_key = str(location.get("page_key") or "").strip()
    if page_key:
        page = project.transcript_pages.filter(page_key=page_key, is_active=True).first()
        if page is not None:
            return page
    slide_index = _safe_int(location.get("slide_order"))
    if slide_index is None:
        slide_index = _safe_int(location.get("slide_index"))
    if slide_index is None:
        slide_number = _safe_int(location.get("slide_number"))
        slide_index = slide_number - 1 if slide_number is not None and slide_number > 0 else None
    if slide_index is not None:
        return (
            project.transcript_pages
            .filter(is_active=True)
            .filter(Q(source_slide_index=slide_index) | Q(order=slide_index))
            .order_by("order", "id")
            .first()
        )
    return None


def _resolve_moderation_preview_path(raw_path: str) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    normalized = raw.replace("\\", "/").lstrip("/")
    if not normalized or ".." in normalized.split("/"):
        return None

    storage_root = Path(str(getattr(settings, "STORAGE_ROOT", "storage_local") or "storage_local")).resolve()
    if normalized == storage_root.name:
        return None
    if normalized.startswith(f"{storage_root.name}/"):
        normalized = normalized[len(storage_root.name) + 1 :]

    direct = Path(raw)
    candidates = [direct] if direct.is_absolute() else []
    candidates.append(storage_root / normalized)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(storage_root)
        except Exception:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _safe_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ProjectModerationRescanView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id: int):
        project = _get_project(project_id)
        if project is None:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_project_moderation(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        serializer = RescanModerationSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        phase = serializer.validated_data["phase"]

        return _start_project_moderation_rescan(
            project,
            actor=request.user if request.user and request.user.is_authenticated else None,
            phase=phase,
            audit_action="rescan" if is_staff_user(request.user) else "",
            reason=str((request.data or {}).get("reason") or ""),
        )


class ProjectModerationAdminReviewRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id: int):
        project = _get_project(project_id)
        if project is None:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_project_moderation(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        if project.admin_review_requests.filter(status="open").exists():
            return Response(
                {"error": "A review request is already open. Please wait for an admin response."},
                status=status.HTTP_409_CONFLICT,
            )
        manual_status = str(getattr(project, "manual_moderation_status", "") or "")
        manually_reviewable = manual_status in {"blocked", "rejected", "request_changes", "needs_review"} or bool(
            getattr(project, "moderation_blocked_until_review", False)
        )
        if project.moderation_status not in REVIEWABLE_MODERATION_STATUSES and not manually_reviewable:
            return Response(
                {
                    "error": "Admin review can only be requested for blocked, review-required, rejected, or failed moderation states.",
                    "moderation_status": project.moderation_status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RequestAdminReviewSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            now = timezone.now()
            summary = dict(project.moderation_summary or {})
            summary.update(
                {
                    "moderation_status": "needs_admin_review",
                    "message": "Publisher requested admin recheck.",
                    "latest_review_requested_at": now.isoformat(),
                }
            )
            review = AdminReviewRequest.objects.create(
                project=project,
                run_id=project.last_moderation_run_id,
                requested_by=request.user if request.user and request.user.is_authenticated else None,
                publisher_message=serializer.validated_data.get("message", ""),
                status="open",
            )
            summary["admin_review_request_id"] = review.id
            project.moderation_status = "needs_admin_review"
            project.moderation_summary = summary
            project.latest_review_requested_at = now
            project.save(update_fields=["moderation_status", "moderation_summary", "latest_review_requested_at", "updated_at"])

        return Response(AdminReviewRequestSerializer(review).data, status=status.HTTP_201_CREATED)


class ProjectModerationReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id: int):
        project = _get_project(project_id)
        if project is None:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not is_staff_user(request.user) and not _project_accepts_user_reports(project):
            return Response({"error": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)
        if project.user_id and project.user_id == request.user.id and not is_staff_user(request.user):
            return Response(
                {"error": "Use moderation review tools for your own lesson."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ModerationReportCreateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        category = serializer.validated_data["category"]
        message = serializer.validated_data.get("message", "")
        dedupe_since = timezone.now() - REPORT_DEDUPE_WINDOW

        with transaction.atomic():
            existing = (
                ModerationReport.objects.select_for_update()
                .select_related("project", "publisher", "reporter", "reviewed_by", "admin_review_request")
                .filter(
                    reporter=request.user,
                    project=project,
                    category=category,
                    status="open",
                    created_at__gte=dedupe_since,
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if existing is not None:
                return Response(moderation_report_payload(existing, deduped=True), status=status.HTTP_200_OK)

            review = (
                AdminReviewRequest.objects.select_for_update()
                .filter(project=project, status="open")
                .order_by("-created_at", "-id")
                .first()
            )
            if review is None:
                review_message = _report_review_message(category=category, message=message)
                review = AdminReviewRequest.objects.create(
                    project=project,
                    run_id=project.last_moderation_run_id,
                    requested_by=request.user,
                    publisher_message=review_message,
                    status="open",
                )

            report = ModerationReport.objects.create(
                reporter=request.user,
                project=project,
                publisher=project.user,
                admin_review_request=review,
                category=category,
                message=message,
                status="open",
            )

        report = (
            ModerationReport.objects.select_related("project", "publisher", "reporter", "reviewed_by", "admin_review_request")
            .get(pk=report.pk)
        )
        return Response(moderation_report_payload(report), status=status.HTTP_201_CREATED)


class AdminProjectModerationActionView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, project_id: int):
        return _admin_project_action_response(request, project_id=project_id)


class AdminProjectModerationBlockView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, project_id: int):
        return _admin_project_action_response(request, project_id=project_id, forced_action="block")


class AdminProjectModerationApproveView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, project_id: int):
        return _admin_project_action_response(request, project_id=project_id, forced_action="approve")


class AdminProjectModerationRequestChangesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, project_id: int):
        return _admin_project_action_response(request, project_id=project_id, forced_action="request_changes")


class AdminModerationReportListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def get(self, request):
        queryset = (
            ModerationReport.objects.select_related(
                "project",
                "publisher",
                "reporter",
                "reviewed_by",
                "admin_review_request",
            )
            .all()
            .order_by("-created_at", "-id")
        )
        requested_status = str(request.query_params.get("status", "open") or "open").strip().lower()
        allowed_statuses = {"open", "reviewed", "resolved", "dismissed", "all"}
        if requested_status not in allowed_statuses:
            return Response(
                {"error": "status must be one of: all, dismissed, open, resolved, reviewed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_status != "all":
            queryset = queryset.filter(status=requested_status)
        payload = [moderation_report_payload(report) for report in queryset]
        return Response(payload, status=status.HTTP_200_OK)


class AdminModerationReportActionView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, report_id: int):
        serializer = AdminModerationReportActionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        report = (
            ModerationReport.objects.select_related(
                "project",
                "publisher",
                "reporter",
                "reviewed_by",
                "admin_review_request",
            )
            .filter(pk=int(report_id))
            .first()
        )
        if report is None:
            return Response({"error": "Moderation report not found."}, status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        if action == "dismiss":
            report.status = "dismissed"
        elif action == "resolve":
            report.status = "resolved"
        else:
            report.status = "open"
        report.reviewed_by = request.user if action != "reopen" else None
        report.reviewed_at = now if action != "reopen" else None
        report.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        return Response(moderation_report_payload(report), status=status.HTTP_200_OK)


class AdminModerationReviewRequestListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def get(self, request):
        requested_tab = str(request.query_params.get("tab", "") or "").strip().lower()
        requested_filter = str(request.query_params.get("filter", "") or "").strip().lower()
        if requested_tab or requested_filter:
            return _admin_moderation_tab_response(requested_tab or "open", requested_filter or "all")

        requested_queue = str(request.query_params.get("queue", "") or "").strip().lower()
        if requested_queue:
            return _admin_moderation_queue_response(requested_queue)

        queryset = (
            AdminReviewRequest.objects.select_related("project", "requested_by", "reviewed_by", "run")
            .all()
            .order_by("-created_at", "-id")
        )
        requested_status = str(request.query_params.get("status", "open") or "open").strip().lower()
        allowed_statuses = {"open", "approved", "rejected", "closed", "all"}
        if requested_status not in allowed_statuses:
            return Response(
                {"error": "status must be one of: all, approved, closed, open, rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_status != "all":
            queryset = queryset.filter(status=requested_status)
        payload = [admin_review_list_payload(review) for review in queryset]
        return Response(payload, status=status.HTTP_200_OK)


class AdminModerationReviewRequestDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def get(self, request, review_id: int):
        review = _get_review_request(review_id)
        if review is None:
            return Response({"error": "Review request not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(admin_review_detail_payload(review), status=status.HTTP_200_OK)


def _admin_moderation_tab_response(requested_tab: str, requested_filter: str):
    tab = requested_tab if requested_tab in {"open", "history"} else "open"
    item_filter = requested_filter or "all"
    if tab == "open":
        allowed_filters = {
            "all",
            "review_requested",
            "auto_blocked",
            "manually_blocked",
            "visual",
            "text_ocr",
            "reports",
            "request_changes",
            "provider_unavailable",
            "copyright",
            "other",
        }
        if item_filter not in allowed_filters:
            return Response(
                {"error": "filter must be one of: all, auto_blocked, copyright, manually_blocked, other, provider_unavailable, reports, request_changes, review_requested, text_ocr, visual."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        items = _open_moderation_items()
    else:
        allowed_filters = {
            "all",
            "approved",
            "rejected_blocked",
            "requested_changes",
            "auto_blocked",
            "reports_resolved",
            "copyright",
            "other",
        }
        if item_filter not in allowed_filters:
            return Response(
                {"error": "filter must be one of: all, approved, auto_blocked, copyright, other, rejected_blocked, reports_resolved, requested_changes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        items = _history_moderation_items()

    filtered = [_item for _item in items if _moderation_item_matches_filter(_item, item_filter)]
    filtered.sort(key=lambda row: row.get("item_time") or row.get("updated_at") or row.get("created_at") or timezone.now(), reverse=True)
    return Response(filtered[:250], status=status.HTTP_200_OK)


def _open_moderation_items() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    reports = (
        ModerationReport.objects.select_related("project", "publisher", "reporter", "reviewed_by", "admin_review_request")
        .filter(status="open")
        .order_by("-created_at", "-id")[:200]
    )
    for report in reports:
        payload = moderation_report_payload(report)
        payload["queue"] = "open"
        _add_unique_item(items, seen, payload)

    reviews = (
        AdminReviewRequest.objects.select_related("project", "project__user", "requested_by", "reviewed_by", "run")
        .filter(status="open")
        .order_by("-created_at", "-id")[:200]
    )
    for review in reviews:
        payload = admin_review_list_payload(review)
        payload["queue"] = "open"
        _add_unique_item(items, seen, payload)

    projects = (
        Project.objects.select_related("user")
        .filter(
            Q(moderation_status__in=["revision_required", "needs_admin_review", "failed", "pending", "not_scanned"])
            | Q(manual_moderation_status__in=["blocked", "rejected", "request_changes", "needs_review"])
            | Q(moderation_blocked_until_review=True)
        )
        .order_by("-updated_at", "-id")[:300]
    )
    for project in projects:
        payload = admin_project_queue_payload(project, queue=_project_open_queue(project))
        _add_unique_item(items, seen, payload)
    return items


def _history_moderation_items() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    reviews = (
        AdminReviewRequest.objects.select_related("project", "project__user", "requested_by", "reviewed_by", "run")
        .exclude(status="open")
        .order_by("-reviewed_at", "-created_at", "-id")[:200]
    )
    for review in reviews:
        payload = admin_review_list_payload(review)
        payload["queue"] = "history"
        _add_unique_item(items, seen, payload)

    reports = (
        ModerationReport.objects.select_related("project", "publisher", "reporter", "reviewed_by", "admin_review_request")
        .exclude(status="open")
        .order_by("-reviewed_at", "-created_at", "-id")[:200]
    )
    for report in reports:
        payload = moderation_report_payload(report)
        payload["queue"] = "history"
        _add_unique_item(items, seen, payload)

    projects = (
        Project.objects.select_related("user")
        .filter(
            Q(moderation_status__in=["admin_approved", "approved", "admin_rejected"])
            | Q(manual_moderation_status__in=["approved", "blocked", "rejected", "request_changes"])
        )
        .order_by("-updated_at", "-id")[:300]
    )
    for project in projects:
        payload = admin_project_queue_payload(project, queue=_project_history_queue(project))
        _add_unique_item(items, seen, payload)
    return items


def _add_unique_item(items: list[dict], seen: set[str], payload: dict) -> None:
    key = f"{payload.get('source_type')}:{payload.get('id')}"
    if key in seen:
        return
    seen.add(key)
    items.append(payload)


def _project_open_queue(project: Project) -> str:
    manual_status = str(getattr(project, "manual_moderation_status", "") or "")
    if manual_status == "request_changes":
        return "request_changes"
    if manual_status in {"blocked", "rejected", "needs_review"}:
        return "rejected_blocked"
    if str(getattr(project, "moderation_status", "") or "") in {"revision_required", "needs_admin_review"}:
        return "auto_rejected"
    return "needs_review"


def _project_history_queue(project: Project) -> str:
    if str(getattr(project, "manual_moderation_status", "") or "") == "request_changes":
        return "request_changes"
    if str(getattr(project, "moderation_status", "") or "") in APPROVED_MODERATION_STATUSES:
        return "approved_resolved"
    return "rejected_blocked"


def _moderation_item_matches_filter(item: dict, item_filter: str) -> bool:
    if item_filter == "all":
        return True
    source_type = str(item.get("source_type") or "")
    source = str(item.get("source") or "")
    queue = str(item.get("queue") or "")
    status_value = str(item.get("status") or "").lower()
    moderation_status = str(item.get("moderation_status") or "").lower()
    manual_status = str(item.get("manual_moderation_status") or "").lower()
    category = str(item.get("reason_category") or item.get("category") or item.get("highest_category") or "").lower()
    badges = {str(badge).lower() for badge in item.get("finding_badges", []) or []}
    if item_filter == "review_requested":
        return source_type == "review_request" and status_value == "open"
    if item_filter == "auto_blocked":
        return queue == "auto_rejected" or (manual_status == "" and moderation_status in {"revision_required", "needs_admin_review"})
    if item_filter == "manually_blocked":
        return manual_status in {"blocked", "rejected", "needs_review"} or moderation_status == "admin_rejected"
    if item_filter == "visual":
        return "visual" in badges or category in {"sexual", "violence", "graphic_content", "self_harm", "provider_unavailable"}
    if item_filter == "text_ocr":
        return "text_ocr" in badges or category in {"profanity", "hate_or_harassment", "dangerous_instruction"}
    if item_filter == "reports":
        return source_type == "report"
    if item_filter == "request_changes":
        return manual_status == "request_changes" or queue == "request_changes"
    if item_filter == "provider_unavailable":
        return "provider_unavailable" in badges or category == "provider_unavailable" or _summary_has_provider_unavailable(item)
    if item_filter == "approved":
        return status_value == "approved" or moderation_status in {"approved", "admin_approved"}
    if item_filter == "rejected_blocked":
        return status_value == "rejected" or moderation_status == "admin_rejected" or manual_status in {"blocked", "rejected"}
    if item_filter == "requested_changes":
        return manual_status == "request_changes" or queue == "request_changes"
    if item_filter == "reports_resolved":
        return source_type == "report" and status_value in {"reviewed", "resolved", "dismissed"}
    if item_filter in {"copyright", "other"}:
        return category == item_filter or source == item_filter
    return True


def _summary_has_provider_unavailable(item: dict) -> bool:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("latest_message", "publication_block_message", "reason_category", "highest_category")
    ).lower()
    return "provider" in text and ("unavailable" in text or "missing" in text)


def _admin_moderation_queue_response(requested_queue: str):
    allowed_queues = {
        "needs_review",
        "auto_rejected",
        "rejected_blocked",
        "request_changes",
        "approved_resolved",
    }
    if requested_queue not in allowed_queues:
        return Response(
            {"error": "queue must be one of: approved_resolved, auto_rejected, needs_review, rejected_blocked, request_changes."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if requested_queue == "needs_review":
        queryset = (
            AdminReviewRequest.objects.select_related("project", "project__user", "requested_by", "reviewed_by", "run")
            .filter(status="open")
            .exclude(project__manual_moderation_status="request_changes")
            .order_by("-created_at", "-id")
        )
        return Response([admin_review_list_payload(review) for review in queryset], status=status.HTTP_200_OK)

    projects = Project.objects.select_related("user").all().order_by("-updated_at", "-id")
    if requested_queue == "auto_rejected":
        projects = projects.filter(
            manual_moderation_status="",
            moderation_status__in=["revision_required", "needs_admin_review"],
        )
    elif requested_queue == "rejected_blocked":
        projects = projects.filter(
            Q(manual_moderation_status__in=["blocked", "rejected"])
            | Q(moderation_status="admin_rejected")
        )
    elif requested_queue == "request_changes":
        projects = projects.filter(
            Q(manual_moderation_status="request_changes")
            | Q(moderation_status="revision_required", manual_moderation_status="request_changes")
        )
    elif requested_queue == "approved_resolved":
        projects = projects.filter(
            moderation_status__in=list(APPROVED_MODERATION_STATUSES),
            moderation_blocked_until_review=False,
        )

    return Response(
        [admin_project_queue_payload(project, queue=requested_queue) for project in projects.distinct()[:200]],
        status=status.HTTP_200_OK,
    )


class AdminModerationReviewRequestApproveView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, review_id: int):
        return _complete_admin_review_request(
            request,
            review_id=review_id,
            decision_status="approved",
            project_moderation_status="admin_approved",
        )


class AdminModerationReviewRequestResponseView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, review_id: int):
        serializer = AdminReviewDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            review = (
                AdminReviewRequest.objects.select_for_update()
                .filter(pk=int(review_id))
                .first()
            )
            if review is None:
                return Response({"error": "Review request not found."}, status=status.HTTP_404_NOT_FOUND)
            review.admin_response = serializer.validated_data.get("admin_response", "")
            review.save(update_fields=["admin_response"])

        review = _get_review_request(review_id)
        return Response(admin_review_detail_payload(review), status=status.HTTP_200_OK)


class AdminModerationReviewRequestRejectView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def post(self, request, review_id: int):
        return _complete_admin_review_request(
            request,
            review_id=review_id,
            decision_status="rejected",
            project_moderation_status="admin_rejected",
        )


def _admin_project_action_response(request, *, project_id: int, forced_action: str | None = None):
    project = _get_project(project_id)
    if project is None:
        return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data or {})
    if forced_action:
        data["action"] = forced_action
    serializer = AdminProjectModerationActionSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    action = serializer.validated_data["action"]
    reason = (
        serializer.validated_data.get("reason")
        or serializer.validated_data.get("note")
        or ""
    )
    if action == "request_changes" and not str(reason or "").strip():
        return Response({"error": "A message is required when requesting changes."}, status=status.HTTP_400_BAD_REQUEST)

    if action == "rescan":
        phase = serializer.validated_data["phase"]
        result = _start_project_moderation_rescan(
            project,
            actor=request.user,
            phase=phase,
            audit_action="rescan",
            reason=reason,
        )
        return result

    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.id)
        previous_status = str(project.moderation_status or "")
        new_status = previous_status
        message = ""
        blocks_public = False
        now = timezone.now()

        summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
        admin_review = summary.get("admin_review") if isinstance(summary.get("admin_review"), dict) else {}
        was_published_before_action = bool(
            admin_review.get("was_published_before_action", project.is_published)
        )

        if action == "approve":
            new_status = "admin_approved"
            message = "Admin approved this lesson for publishing."
        elif action == "block":
            new_status = "admin_rejected"
            blocks_public = True
            was_published_before_action = bool(project.is_published)
            message = "Admin blocked this lesson. It is hidden from public catalog and watch."
        elif action == "needs_review":
            new_status = "needs_admin_review"
            blocks_public = True
            was_published_before_action = bool(project.is_published)
            message = "Admin marked this lesson as needing review."
        elif action == "request_changes":
            new_status = "revision_required"
            blocks_public = bool(serializer.validated_data.get("unpublish", True))
            was_published_before_action = bool(project.is_published)
            message = "Admin requested changes before this lesson can be published."
        elif action == "add_note":
            message = "Admin note added."

        admin_review.update(
            {
                "last_action": action,
                "last_actor_id": request.user.id,
                "last_actor_username": request.user.username,
                "last_action_at": now.isoformat(),
                "reason": reason,
                "unpublish": blocks_public,
                "was_published_before_action": was_published_before_action,
            }
        )
        summary.update(
            {
                "moderation_status": new_status,
                "message": message,
                "admin_review": admin_review,
                "publisher_admin_note": reason,
                "manual_moderation_status": _manual_status_for_action(action),
                "manual_moderation_reason": reason,
                "moderation_blocked_until_review": blocks_public,
            }
        )
        project.moderation_status = new_status
        project.moderation_summary = summary
        update_fields = [
            "moderation_status",
            "moderation_summary",
            "manual_moderation_status",
            "manual_moderation_reason",
            "manual_moderation_by",
            "manual_moderation_at",
            "moderation_blocked_until_review",
            "updated_at",
        ]
        if action in {"approve", "block", "needs_review", "request_changes"}:
            project.manual_moderation_status = _manual_status_for_action(action)
            project.manual_moderation_reason = reason
            project.manual_moderation_by = request.user
            project.manual_moderation_at = now
            project.moderation_blocked_until_review = blocks_public
        if action in {"block", "needs_review", "request_changes"} and blocks_public and project.is_published:
            project.is_published = False
            update_fields.append("is_published")
        if action != "approve" and enforce_unpublished_for_unresolved_moderation(project, save=False):
            update_fields.append("is_published")
        project.save(update_fields=update_fields)

        audit = _create_moderation_audit_event(
            project=project,
            actor=request.user,
            action=action,
            reason=reason,
            previous_status=previous_status,
            new_status=new_status,
            metadata={"blocks_public": blocks_public},
        )

        if action == "approve":
            AdminReviewRequest.objects.filter(project=project, status="open").update(
                status="approved",
                reviewed_by=request.user,
                reviewed_at=now,
                admin_response=reason,
            )
            PublicationBlockEvent.objects.filter(project=project, resolved=False).update(
                resolved=True,
                resolved_by=request.user,
                resolved_at=now,
            )
        elif action in {"block", "needs_review", "request_changes"}:
            if action in {"block", "request_changes"}:
                AdminReviewRequest.objects.filter(project=project, status="open").update(
                    status="rejected",
                    reviewed_by=request.user,
                    reviewed_at=now,
                    admin_response=reason,
                )
            _ensure_manual_publication_block(
                project=project,
                actor=request.user,
                action=action,
                reason=reason,
            )
        if action in {"approve", "block", "request_changes"}:
            ModerationReport.objects.filter(project=project, status="open").update(
                status="resolved" if action in {"block", "request_changes"} else "reviewed",
                reviewed_by=request.user,
                reviewed_at=now,
            )

    project.refresh_from_db()
    if action in {"approve", "block", "request_changes"}:
        notify_publisher_moderation_action(
            project=project,
            actor_user=request.user,
            action=action,
            reason=reason,
            moderation_status=project.moderation_status,
        )
    return Response(
        {
            "project_id": project.id,
            "action": action,
            "moderation_status": project.moderation_status,
            "is_published": project.is_published,
            "audit_event_id": audit.id,
            "message": message,
            "project_moderation": moderation_summary_payload(project, include_admin_fields=True),
        },
        status=status.HTTP_200_OK,
    )


def _project_accepts_user_reports(project: Project) -> bool:
    return (
        bool(getattr(project, "is_published", False))
        and str(getattr(project, "status", "") or "") == "ready"
        and str(getattr(project, "moderation_status", "") or "") in APPROVED_MODERATION_STATUSES
    )


def _report_review_message(*, category: str, message: str) -> str:
    labels = {key: label for key, label in ModerationReport.CATEGORY_CHOICES}
    label = labels.get(category, category)
    clean_message = str(message or "").strip()
    if clean_message:
        return f"User report: {label}. {clean_message}"
    return f"User report: {label}."


def _get_project(project_id: int) -> Project | None:
    return Project.objects.select_related("user").filter(pk=int(project_id)).first()


def _get_review_request(review_id: int) -> AdminReviewRequest | None:
    return (
        AdminReviewRequest.objects.select_related("project", "requested_by", "reviewed_by", "run")
        .filter(pk=int(review_id))
        .first()
    )


def _draft_rescan_result_blocks(result: dict | None) -> bool:
    if not isinstance(result, dict) or not result.get("enabled"):
        return False
    status_value = str(result.get("moderation_status") or result.get("status") or "").strip().lower()
    decision = str(result.get("final_decision") or "").strip().lower()
    return bool(
        result.get("block_render")
        or status_value in {"revision_required", "needs_admin_review", "admin_rejected", "failed", "block", "blocked", "rejected"}
        or decision in {"block", "blocked", "rejected", "revision_required", "needs_admin_review"}
    )


def _draft_rescan_result_passes(result: dict | None) -> bool:
    if not isinstance(result, dict) or not result.get("enabled"):
        return False
    status_value = str(result.get("moderation_status") or result.get("status") or "").strip().lower()
    decision = str(result.get("final_decision") or "").strip().lower()
    return status_value in {"approved", "admin_approved", "done", "completed"} or decision in {"allow", "approved", "admin_approved"}


def _run_saved_draft_text_rescan(project: Project, *, actor) -> dict | None:
    try:
        services_root = Path(__file__).resolve().parents[2]
        if str(services_root) not in sys.path:
            sys.path.insert(0, str(services_root))
        from core.drafts import mark_draft_moderation_failed, mark_draft_moderation_passed
        from worker import tasks as worker_tasks
    except Exception:
        logger.warning("Draft moderation rescan helpers unavailable project=%s", project.id, exc_info=True)
        return None

    result = worker_tasks._run_auto_source_moderation_for_draft(
        project.id,
        triggered_by_user_id=actor.id if actor and getattr(actor, "is_authenticated", False) else None,
    )
    if _draft_rescan_result_blocks(result):
        mark_draft_moderation_failed(project, result or {})
    elif _draft_rescan_result_passes(result):
        mark_draft_moderation_passed(project, result or {}, scope="text")
        project.refresh_from_db(fields=["moderation_summary"])
        summary = dict(project.moderation_summary or {})
        summary.pop("editor_text_changed", None)
        project.moderation_summary = summary
        project.save(update_fields=["moderation_summary", "updated_at"])
    return result if isinstance(result, dict) else None


def _start_project_moderation_rescan(
    project: Project,
    *,
    actor,
    phase: str,
    audit_action: str = "",
    reason: str = "",
):
    previous_status = str(project.moderation_status or "")
    draft_rescan_project_id: int | None = None
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.id)
        previous_status = str(locked.moderation_status or "")
        if manual_moderation_prevents_auto_override(locked):
            if audit_action:
                _create_moderation_audit_event(
                    project=locked,
                    actor=actor,
                    action=audit_action,
                    reason=reason,
                    previous_status=previous_status,
                    new_status=previous_status,
                    metadata={"phase": phase, "skipped": "manual_moderation_block"},
                )
            return Response(
                {
                    "error": "This lesson is blocked by a manual moderation decision. Only an admin can change it.",
                    "project_id": locked.id,
                    "moderation_status": locked.moderation_status,
                    "manual_moderation_status": locked.manual_moderation_status,
                    "phase": phase,
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            from core.drafts import has_dirty_draft
        except Exception:
            has_dirty_draft = None
        if has_dirty_draft is not None and has_dirty_draft(locked):
            draft_rescan_project_id = locked.id
            if audit_action:
                _create_moderation_audit_event(
                    project=locked,
                    actor=actor,
                    action=audit_action,
                    reason=reason,
                    previous_status=previous_status,
                    new_status=previous_status,
                    metadata={"phase": phase, "draft_rescan": True},
                )
            summary = dict(locked.moderation_summary or {})
            summary.update(
                {
                    "message": "Moderation scan is running for the saved Studio draft.",
                    "phase": phase,
                }
            )
            locked.moderation_summary = summary
            locked.save(update_fields=["moderation_summary", "updated_at"])
        else:
            summary = dict(locked.moderation_summary or {})
            summary.update(
                {
                    "moderation_status": "pending",
                    "message": "Moderation scan is running.",
                    "phase": phase,
                }
            )
            locked.moderation_status = "pending"
            locked.moderation_summary = summary
            locked.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])
            if audit_action:
                _create_moderation_audit_event(
                    project=locked,
                    actor=actor,
                    action=audit_action,
                    reason=reason,
                    previous_status=previous_status,
                    new_status="pending",
                    metadata={"phase": phase},
                )

    if draft_rescan_project_id:
        result = _run_saved_draft_text_rescan(project, actor=actor)
        project.refresh_from_db()
        draft_metadata = {}
        try:
            from core.drafts import get_project_draft_data

            draft_data = get_project_draft_data(project)
            metadata = draft_data.get("metadata") if isinstance(draft_data, dict) else {}
            draft_metadata = metadata if isinstance(metadata, dict) else {}
        except Exception:
            draft_metadata = {}
        return Response(
            {
                "project_id": project.id,
                "moderation_status": project.moderation_status,
                "phase": phase,
                "draft_rescan": True,
                "draft_metadata": draft_metadata,
                "moderation": result,
                "message": str((result or {}).get("message") or "Saved draft moderation scan completed."),
            },
            status=status.HTTP_200_OK,
        )

    queue_name = _moderation_task_queue_name()
    try:
        task_result = _dispatch_moderation_task(
            project.id,
            triggered_by_user_id=actor.id if actor and getattr(actor, "is_authenticated", False) else None,
            phase=phase,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not start moderation scan project=%s phase=%s queue=%s", project.id, phase, queue_name)
        project.refresh_from_db()
        if manual_moderation_prevents_auto_override(project):
            return Response(
                {
                    "error": "Could not start moderation scan; manual moderation block remains active.",
                    "project_id": project.id,
                    "moderation_status": project.moderation_status,
                    "phase": phase,
                    "queue": queue_name,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        summary = dict(project.moderation_summary or {})
        summary.update({
            "moderation_status": "failed",
            "message": "Could not start moderation scan.",
            "phase": phase,
        })
        project.moderation_status = "failed"
        project.moderation_summary = summary
        project.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])
        return Response(
            {
                "error": "Could not start moderation scan.",
                "project_id": project.id,
                "moderation_status": project.moderation_status,
                "phase": phase,
                "queue": queue_name,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    project.refresh_from_db(fields=["moderation_status"])
    return Response(
        {
            "project_id": project.id,
            "moderation_status": project.moderation_status,
            "phase": phase,
            "task_id": str(getattr(task_result, "id", "") or ""),
            "queue": queue_name,
        },
        status=status.HTTP_202_ACCEPTED,
    )


def _create_moderation_audit_event(
    *,
    project: Project,
    actor,
    action: str,
    reason: str,
    previous_status: str,
    new_status: str,
    metadata: dict | None = None,
) -> ModerationAuditEvent:
    return ModerationAuditEvent.objects.create(
        project=project,
        actor=actor if actor and getattr(actor, "is_authenticated", False) else None,
        action=action,
        reason=reason,
        previous_status=previous_status,
        new_status=new_status,
        metadata=metadata or {},
    )


def _manual_status_for_action(action: str) -> str:
    return {
        "approve": "approved",
        "block": "blocked",
        "needs_review": "needs_review",
        "request_changes": "request_changes",
    }.get(str(action or ""), "")


def _ensure_manual_publication_block(*, project: Project, actor, action: str, reason: str) -> None:
    reason_category = {
        "block": "manual_admin_block",
        "needs_review": "manual_admin_review",
        "request_changes": "manual_admin_request_changes",
    }.get(action, "manual_admin_moderation")
    if PublicationBlockEvent.objects.filter(project=project, resolved=False, reason_category=reason_category).exists():
        return
    PublicationBlockEvent.objects.create(
        project=project,
        run_id=project.last_moderation_run_id,
        blocked_by="admin_manual_action",
        reason_category=reason_category,
        highest_severity="high" if action == "block" else "medium",
        message_to_user=reason or "An admin reviewed this lesson and requested moderation follow-up.",
        message_to_admin=reason,
    )


def _complete_admin_review_request(
    request,
    *,
    review_id: int,
    decision_status: str,
    project_moderation_status: str,
):
    serializer = AdminReviewDecisionSerializer(data=request.data or {})
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        review = (
            AdminReviewRequest.objects.select_for_update()
            .filter(pk=int(review_id))
            .first()
        )
        if review is None:
            return Response({"error": "Review request not found."}, status=status.HTTP_404_NOT_FOUND)
        if review.status != "open":
            return Response(
                {"error": "Only open admin review requests can be approved or rejected.", "status": review.status},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        previous_status = str(review.project.moderation_status or "")
        review.status = decision_status
        review.reviewed_by = request.user
        review.reviewed_at = now
        review.admin_response = serializer.validated_data.get("admin_response", "")
        review.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_response"])

        project = review.project
        project.moderation_status = project_moderation_status
        summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
        manual_action = "approve" if decision_status == "approved" else "block"
        summary.update(
            {
                "moderation_status": project_moderation_status,
                "message": (
                    "Admin approved this lesson for publishing."
                    if decision_status == "approved"
                    else "Admin rejected this moderation review request. Please revise the lesson and scan again."
                ),
                "admin_review_request_id": review.id,
                "publisher_admin_note": review.admin_response,
                "manual_moderation_status": _manual_status_for_action(manual_action),
                "manual_moderation_reason": review.admin_response,
                "moderation_blocked_until_review": decision_status != "approved",
            }
        )
        project.moderation_summary = summary
        project.manual_moderation_status = _manual_status_for_action(manual_action)
        project.manual_moderation_reason = review.admin_response
        project.manual_moderation_by = request.user
        project.manual_moderation_at = now
        project.moderation_blocked_until_review = decision_status != "approved"
        update_fields = [
            "moderation_status",
            "moderation_summary",
            "manual_moderation_status",
            "manual_moderation_reason",
            "manual_moderation_by",
            "manual_moderation_at",
            "moderation_blocked_until_review",
            "updated_at",
        ]
        if decision_status != "approved" and project.is_published:
            project.is_published = False
            update_fields.append("is_published")
        if decision_status != "approved" and enforce_unpublished_for_unresolved_moderation(project, save=False):
            update_fields.append("is_published")
        project.save(update_fields=update_fields)

        _create_moderation_audit_event(
            project=project,
            actor=request.user,
            action="approve" if decision_status == "approved" else "block",
            reason=review.admin_response,
            previous_status=previous_status,
            new_status=project_moderation_status,
            metadata={"admin_review_request_id": review.id},
        )

        if decision_status == "approved":
            PublicationBlockEvent.objects.filter(project=project, resolved=False).update(
                resolved=True,
                resolved_by=request.user,
                resolved_at=now,
            )
        else:
            _ensure_manual_publication_block(
                project=project,
                actor=request.user,
                action="block",
                reason=review.admin_response,
            )
        ModerationReport.objects.filter(project=project, status="open").update(
            status="resolved" if decision_status != "approved" else "reviewed",
            reviewed_by=request.user,
            reviewed_at=now,
        )

    review = _get_review_request(review_id)
    notify_publisher_moderation_action(
        project=review.project,
        actor_user=request.user,
        action="approve" if decision_status == "approved" else "block",
        reason=review.admin_response,
        moderation_status=review.project.moderation_status,
    )
    return Response(admin_review_detail_payload(review), status=status.HTTP_200_OK)
