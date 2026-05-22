
from __future__ import annotations

import logging
import os
from datetime import timedelta

from celery import Celery
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_agents.models import AdminReviewRequest, ModerationAuditEvent, ModerationReport, PublicationBlockEvent
from ai_agents.policies import APPROVED_MODERATION_STATUSES, manual_moderation_prevents_auto_override
from ai_agents.serializers import (
    REVIEWABLE_MODERATION_STATUSES,
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
        if manual_moderation_prevents_auto_override(project):
            return Response(
                {
                    "error": "This lesson is blocked by a manual moderation decision. Only an admin can change it.",
                    "moderation_status": project.moderation_status,
                    "manual_moderation_status": getattr(project, "manual_moderation_status", ""),
                },
                status=status.HTTP_409_CONFLICT,
            )
        if project.moderation_status not in REVIEWABLE_MODERATION_STATUSES:
            return Response(
                {
                    "error": "Admin review can only be requested for blocked, review-required, rejected, or failed moderation states.",
                    "moderation_status": project.moderation_status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if project.admin_review_requests.filter(status="open").exists():
            return Response(
                {"error": "An open admin review request already exists for this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RequestAdminReviewSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            now = timezone.now()
            review = AdminReviewRequest.objects.create(
                project=project,
                run_id=project.last_moderation_run_id,
                requested_by=request.user if request.user and request.user.is_authenticated else None,
                publisher_message=serializer.validated_data.get("message", ""),
                status="open",
            )
            project.moderation_status = "needs_admin_review"
            project.latest_review_requested_at = now
            project.save(update_fields=["moderation_status", "latest_review_requested_at", "updated_at"])

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


class AdminModerationReviewRequestListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def get(self, request):
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
        if action == "approve" and not project.is_published and was_published_before_action:
            project.is_published = True
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


def _start_project_moderation_rescan(
    project: Project,
    *,
    actor,
    phase: str,
    audit_action: str = "",
    reason: str = "",
):
    previous_status = str(project.moderation_status or "")
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
