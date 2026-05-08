
from __future__ import annotations

import logging
import os

from celery import Celery
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_agents.models import AdminReviewRequest, PublicationBlockEvent
from ai_agents.serializers import (
    REVIEWABLE_MODERATION_STATUSES,
    AdminReviewDecisionSerializer,
    AdminReviewRequestSerializer,
    RequestAdminReviewSerializer,
    RescanModerationSerializer,
    admin_review_detail_payload,
    admin_review_list_payload,
    moderation_summary_payload,
)
from core.models import Project

from .permissions import IsStaffUser, can_manage_project_moderation, is_staff_user


_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
_celery_app = Celery(broker=_BROKER_URL)
_RUN_PROJECT_MODERATION_TASK = "worker.tasks.run_project_moderation"
logger = logging.getLogger(__name__)


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

        with transaction.atomic():
            project.moderation_status = "pending"
            project.moderation_summary = {
                "moderation_status": "pending",
                "message": "Moderation scan is running.",
                "phase": phase,
            }
            project.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])

        queue_name = _moderation_task_queue_name()
        try:
            task_result = _dispatch_moderation_task(
                project.id,
                triggered_by_user_id=request.user.id if request.user and request.user.is_authenticated else None,
                phase=phase,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not start moderation scan project=%s phase=%s queue=%s", project.id, phase, queue_name)
            project.moderation_status = "failed"
            project.moderation_summary = {
                "moderation_status": "failed",
                "message": "Could not start moderation scan.",
                "phase": phase,
            }
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


class ProjectModerationAdminReviewRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id: int):
        project = _get_project(project_id)
        if project is None:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)
        if not can_manage_project_moderation(request.user, project):
            return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
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
            review = AdminReviewRequest.objects.create(
                project=project,
                run_id=project.last_moderation_run_id,
                requested_by=request.user if request.user and request.user.is_authenticated else None,
                publisher_message=serializer.validated_data.get("message", ""),
                status="open",
            )
            project.moderation_status = "needs_admin_review"
            project.save(update_fields=["moderation_status", "updated_at"])

        return Response(AdminReviewRequestSerializer(review).data, status=status.HTTP_201_CREATED)


class AdminModerationReviewRequestListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsStaffUser]

    def get(self, request):
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


def _get_project(project_id: int) -> Project | None:
    return Project.objects.select_related("user").filter(pk=int(project_id)).first()


def _get_review_request(review_id: int) -> AdminReviewRequest | None:
    return (
        AdminReviewRequest.objects.select_related("project", "requested_by", "reviewed_by", "run")
        .filter(pk=int(review_id))
        .first()
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
        review.status = decision_status
        review.reviewed_by = request.user
        review.reviewed_at = now
        review.admin_response = serializer.validated_data.get("admin_response", "")
        review.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_response"])

        project = review.project
        project.moderation_status = project_moderation_status
        summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
        summary.update(
            {
                "moderation_status": project_moderation_status,
                "message": (
                    "Admin approved this lesson for publishing."
                    if decision_status == "approved"
                    else "Admin rejected this moderation review request. Please revise the lesson and scan again."
                ),
                "admin_review_request_id": review.id,
            }
        )
        project.moderation_summary = summary
        project.save(update_fields=["moderation_status", "moderation_summary", "updated_at"])

        if decision_status == "approved":
            PublicationBlockEvent.objects.filter(project=project, resolved=False).update(
                resolved=True,
                resolved_by=request.user,
                resolved_at=now,
            )

    review = _get_review_request(review_id)
    return Response(admin_review_detail_payload(review), status=status.HTTP_200_OK)
