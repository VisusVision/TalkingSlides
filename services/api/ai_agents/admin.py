import json

from django.contrib import admin
from django.db import transaction
from django.utils import timezone
from django.utils.html import format_html

from ai_agents.policies import enforce_unpublished_for_unresolved_moderation

from .models import (
    AdminReviewRequest,
    AgentDefinition,
    AgentFinding,
    AgentRun,
    ModerationAuditEvent,
    ModerationReport,
    PublicationBlockEvent,
)


def _compact_json(value):
    return json.dumps(value or {}, indent=2, sort_keys=True, ensure_ascii=True)


def _moderation_summary_message(status):
    if status == "admin_approved":
        return "Admin approved this lesson for publishing."
    if status == "admin_rejected":
        return "Admin rejected this moderation review request. Please revise the lesson and scan again."
    return ""


def _update_project_moderation_status(project, status, review):
    summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
    summary.update(
        {
            "moderation_status": status,
            "message": _moderation_summary_message(status),
            "admin_review_request_id": review.id,
        }
    )
    project.moderation_status = status
    project.moderation_summary = summary
    update_fields = ["moderation_status", "moderation_summary", "updated_at"]
    if status != "admin_approved" and enforce_unpublished_for_unresolved_moderation(project, save=False):
        update_fields.append("is_published")
    project.save(update_fields=update_fields)


@admin.register(AgentDefinition)
class AgentDefinitionAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "kind", "modality", "version", "enabled", "is_blocking")
    search_fields = ("slug", "name", "kind", "modality")
    list_filter = ("enabled", "is_blocking", "kind", "modality")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "final_decision", "phase", "finding_count", "created_at", "completed_at")
    search_fields = ("project__title", "purpose", "phase", "status", "final_decision")
    list_filter = ("status", "final_decision", "phase", "purpose")
    readonly_fields = ("created_at", "completed_at")
    list_select_related = ("project",)

    @admin.display(description="Findings")
    def finding_count(self, obj):
        return obj.findings.count()


@admin.register(AgentFinding)
class AgentFindingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "run",
        "category",
        "severity",
        "decision",
        "confidence",
        "content_type",
        "object_type",
    )
    search_fields = ("run__project__title", "agent_slug", "category", "user_message", "admin_message", "evidence_excerpt")
    list_filter = ("content_type", "category", "severity", "decision", "provider")
    readonly_fields = ("location_pretty", "created_at")
    list_select_related = ("run", "run__project")

    @admin.display(description="Project")
    def project(self, obj):
        return obj.run.project if obj.run_id else None

    @admin.display(description="Location")
    def location_pretty(self, obj):
        return format_html("<pre style='white-space: pre-wrap'>{}</pre>", _compact_json(obj.location))


@admin.register(PublicationBlockEvent)
class PublicationBlockEventAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "blocked_by", "reason_category", "highest_severity", "resolved", "created_at")
    search_fields = ("project__title", "blocked_by", "reason_category", "message_to_user", "message_to_admin")
    list_filter = ("resolved", "reason_category", "highest_severity", "created_at")
    readonly_fields = ("created_at", "resolved_at")
    list_select_related = ("project", "run", "resolved_by")
    actions = ("mark_resolved",)

    @admin.action(description="Mark selected publication block events resolved")
    def mark_resolved(self, request, queryset):
        now = timezone.now()
        updated = queryset.filter(resolved=False).update(
            resolved=True,
            resolved_by=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            resolved_at=now,
        )
        skipped = queryset.count() - updated
        self.message_user(request, f"Marked {updated} publication block event(s) resolved; skipped {skipped}.")


@admin.register(ModerationAuditEvent)
class ModerationAuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "action", "actor", "previous_status", "new_status", "created_at")
    search_fields = ("project__title", "actor__username", "action", "reason")
    list_filter = ("action", "created_at")
    readonly_fields = ("created_at", "metadata_pretty")
    list_select_related = ("project", "actor")

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj):
        return format_html("<pre style='white-space: pre-wrap'>{}</pre>", _compact_json(obj.metadata))


@admin.register(ModerationReport)
class ModerationReportAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "category", "status", "reporter", "publisher", "created_at", "reviewed_by")
    search_fields = ("project__title", "reporter__username", "publisher__username", "message")
    list_filter = ("status", "category", "created_at")
    readonly_fields = ("created_at", "reviewed_at")
    list_select_related = ("project", "reporter", "publisher", "reviewed_by", "admin_review_request")


@admin.register(AdminReviewRequest)
class AdminReviewRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "requested_by", "status", "created_at", "reviewed_by", "reviewed_at")
    search_fields = ("project__title", "requested_by__username", "publisher_message", "admin_response")
    list_filter = ("status", "created_at")
    readonly_fields = ("created_at", "reviewed_at")
    list_select_related = ("project", "requested_by", "reviewed_by", "run")
    actions = ("approve_selected_open_requests", "reject_selected_open_requests")

    @admin.action(description="Approve selected open review requests")
    def approve_selected_open_requests(self, request, queryset):
        changed, skipped = self._complete_selected_requests(
            request,
            queryset,
            review_status="approved",
            project_status="admin_approved",
            resolve_blocks=True,
        )
        self.message_user(request, f"Approved {changed} review request(s); skipped {skipped} non-open request(s).")

    @admin.action(description="Reject selected open review requests")
    def reject_selected_open_requests(self, request, queryset):
        changed, skipped = self._complete_selected_requests(
            request,
            queryset,
            review_status="rejected",
            project_status="admin_rejected",
            resolve_blocks=False,
        )
        self.message_user(request, f"Rejected {changed} review request(s); skipped {skipped} non-open request(s).")

    def _complete_selected_requests(self, request, queryset, *, review_status, project_status, resolve_blocks):
        now = timezone.now()
        actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        changed = 0
        skipped = 0
        for review in queryset.select_related("project"):
            if review.status != "open":
                skipped += 1
                continue
            with transaction.atomic():
                review.status = review_status
                review.reviewed_by = actor
                review.reviewed_at = now
                review.save(update_fields=["status", "reviewed_by", "reviewed_at"])
                _update_project_moderation_status(review.project, project_status, review)
                if resolve_blocks:
                    PublicationBlockEvent.objects.filter(project=review.project, resolved=False).update(
                        resolved=True,
                        resolved_by=actor,
                        resolved_at=now,
                    )
            changed += 1
        return changed, skipped
