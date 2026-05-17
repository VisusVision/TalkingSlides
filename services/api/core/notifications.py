"""Small service layer for in-app notifications.

Notification helpers intentionally keep payloads compact and frontend-route
only. They never store raw storage paths, media URLs, tracebacks, or render
artifacts.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import User
from django.db.models import Model

from core.models import Job, LessonComment, Notification, Project, PublisherFollow

logger = logging.getLogger(__name__)

PUBLIC_MODERATION_STATUSES = {"approved", "admin_approved", "not_scanned"}
SAFE_METADATA_KEYS = {
    "project_id",
    "lesson_id",
    "comment_id",
    "job_id",
    "avatar_job_id",
    "base_job_id",
    "status",
    "event",
    "is_published",
}
FRONTEND_ROUTE_PREFIXES = (
    "/watch",
    "/studio",
    "/analytics",
    "/settings",
    "/library",
    "/history",
    "/channel",
    "/playlist",
)


def _instance_or_none(model: type[Model], value: Any) -> Model | None:
    if value is None:
        return None
    if isinstance(value, model):
        return value
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return None
    return model.objects.filter(pk=pk).first()


def _safe_frontend_action_url(action_url: str) -> str:
    raw = str(action_url or "").strip()
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return ""
    if "\\" in raw or raw.lower().startswith(("/api/", "/media/", "/stream/")):
        return ""
    if not any(raw == prefix or raw.startswith(f"{prefix}?") or raw.startswith(f"{prefix}/") for prefix in FRONTEND_ROUTE_PREFIXES):
        return ""
    return raw[:500]


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        clean_key = str(key or "").strip()
        if clean_key not in SAFE_METADATA_KEYS:
            continue
        if isinstance(value, bool) or value is None:
            safe[clean_key] = value
        elif isinstance(value, int):
            safe[clean_key] = value
        elif isinstance(value, str):
            compact = value.strip()[:120]
            if "/" in compact or "\\" in compact or "storage" in compact.lower():
                continue
            safe[clean_key] = compact
    return safe


def _display_title(project: Project | None) -> str:
    title = str(getattr(project, "title", "") or "").strip()
    return title[:120] if title else "your lesson"


def _publisher_display_name(user: User | None) -> str:
    if not user:
        return "A publisher"
    return (user.get_full_name() or user.username or "A publisher").strip()


def _project_has_done_render(project: Project | None) -> bool:
    if project is None or not getattr(project, "pk", None):
        return False
    return project.jobs.filter(job_type="video_export", status="done").exists()


def _public_lesson_is_visible(project: Project | None) -> bool:
    if project is None:
        return False
    if not bool(getattr(project, "is_published", False)):
        return False
    if str(getattr(project, "status", "") or "") != "ready":
        return False
    if str(getattr(project, "moderation_status", "") or "") not in PUBLIC_MODERATION_STATUSES:
        return False
    return _project_has_done_render(project)


def _latest_project_job(project: Project | None, *, job_type: str, statuses: set[str] | None = None) -> Job | None:
    if project is None or not getattr(project, "pk", None):
        return None
    queryset = project.jobs.filter(job_type=job_type)
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    return queryset.order_by("-updated_at", "-id").first()


def create_notification(
    *,
    recipient_user: User | None,
    event_type: str,
    title: str,
    body: str = "",
    actor_user: User | None = None,
    project: Project | int | None = None,
    lesson_comment: LessonComment | int | None = None,
    job: Job | int | None = None,
    action_url: str = "",
    metadata: dict[str, Any] | None = None,
    idempotency_filter: dict[str, Any] | None = None,
) -> Notification | None:
    """Create a notification, returning None when skipped or deduped."""
    if recipient_user is None or not getattr(recipient_user, "pk", None):
        return None

    try:
        resolved_project = _instance_or_none(Project, project)
        resolved_comment = _instance_or_none(LessonComment, lesson_comment)
        resolved_job = _instance_or_none(Job, job)

        if idempotency_filter:
            if Notification.objects.filter(**idempotency_filter).exists():
                return None

        return Notification.objects.create(
            recipient_user=recipient_user,
            actor_user=actor_user if getattr(actor_user, "pk", None) else None,
            event_type=str(event_type),
            project=resolved_project,
            lesson_comment=resolved_comment,
            job=resolved_job,
            title=str(title or "Notification").strip()[:200],
            body=str(body or "").strip(),
            action_url=_safe_frontend_action_url(action_url),
            metadata=_safe_metadata(metadata),
        )
    except Exception:
        logger.warning(
            "Notification creation failed recipient=%s event_type=%s",
            getattr(recipient_user, "pk", None),
            event_type,
            exc_info=True,
        )
        return None


def notify_lesson_commented(comment: LessonComment | int | None) -> Notification | None:
    try:
        resolved_comment = _instance_or_none(LessonComment, comment)
        if resolved_comment is None:
            return None
        project = resolved_comment.project
        recipient = getattr(project, "user", None)
        actor = resolved_comment.user
        if recipient is None or actor_id_matches(actor, recipient):
            return None
        return create_notification(
            recipient_user=recipient,
            actor_user=actor,
            event_type=Notification.EventType.PUBLISHER_COMMENT_ON_LESSON,
            project=project,
            lesson_comment=resolved_comment,
            title="New comment on your lesson",
            body=f"A viewer commented on {_display_title(project)}.",
            action_url=f"/studio?lesson={project.id}",
            metadata={
                "project_id": project.id,
                "lesson_id": project.id,
                "comment_id": resolved_comment.id,
            },
        )
    except Exception:
        logger.warning("Comment notification failed comment=%s", getattr(comment, "pk", comment), exc_info=True)
        return None


def actor_id_matches(actor: User | None, recipient: User | None) -> bool:
    return bool(actor and recipient and getattr(actor, "pk", None) == getattr(recipient, "pk", None))


def notify_publisher_posted_lesson(project: Project | int | None) -> int:
    """Notify followers only after a lesson is public and visible."""
    try:
        resolved_project = _instance_or_none(Project, project)
        if resolved_project is None or not _public_lesson_is_visible(resolved_project):
            return 0
        publisher = resolved_project.user
        if publisher is None:
            return 0
        followers = (
            PublisherFollow.objects.filter(publisher=publisher)
            .select_related("follower")
            .order_by("id")
        )
        created = 0
        for follow in followers:
            notification = create_notification(
                recipient_user=follow.follower,
                actor_user=publisher,
                event_type=Notification.EventType.STUDENT_FOLLOWED_PUBLISHER_NEW_LESSON,
                project=resolved_project,
                title=f"New lesson from {_publisher_display_name(publisher)}",
                body=f"{_display_title(resolved_project)} is now available.",
                action_url=f"/watch?lesson={resolved_project.id}",
                metadata={
                    "project_id": resolved_project.id,
                    "lesson_id": resolved_project.id,
                    "is_published": True,
                },
            )
            if notification is not None:
                created += 1
        return created
    except Exception:
        logger.warning("Follower lesson notification failed project=%s", getattr(project, "pk", project), exc_info=True)
        return 0


def notify_render_completed(project: Project | int | None, job: Job | int | None = None) -> Notification | None:
    try:
        resolved_project = _instance_or_none(Project, project)
        if resolved_project is None:
            return None
        resolved_job = _instance_or_none(Job, job) or _latest_project_job(
            resolved_project,
            job_type="video_export",
            statuses={"done"},
        )
        recipient = resolved_project.user
        return create_notification(
            recipient_user=recipient,
            event_type=Notification.EventType.PUBLISHER_LESSON_RENDER_DONE,
            project=resolved_project,
            job=resolved_job,
            title="Lesson render completed",
            body=f"{_display_title(resolved_project)} is ready in Studio.",
            action_url=f"/studio?lesson={resolved_project.id}",
            metadata={
                "project_id": resolved_project.id,
                "lesson_id": resolved_project.id,
                "job_id": getattr(resolved_job, "id", None),
                "status": "done",
            },
            idempotency_filter={
                "recipient_user": recipient,
                "event_type": Notification.EventType.PUBLISHER_LESSON_RENDER_DONE,
                "project": resolved_project,
                "job": resolved_job,
            },
        )
    except Exception:
        logger.warning("Render completion notification failed project=%s", getattr(project, "pk", project), exc_info=True)
        return None


def notify_render_failed(project: Project | int | None, job: Job | int | None = None) -> Notification | None:
    try:
        resolved_project = _instance_or_none(Project, project)
        if resolved_project is None:
            return None
        resolved_job = _instance_or_none(Job, job) or _latest_project_job(
            resolved_project,
            job_type="video_export",
            statuses={"failed"},
        )
        recipient = resolved_project.user
        return create_notification(
            recipient_user=recipient,
            event_type=Notification.EventType.PUBLISHER_LESSON_RENDER_FAILED,
            project=resolved_project,
            job=resolved_job,
            title="Lesson render failed",
            body=f"Render failed for {_display_title(resolved_project)}. Check Studio for details.",
            action_url=f"/studio?lesson={resolved_project.id}",
            metadata={
                "project_id": resolved_project.id,
                "lesson_id": resolved_project.id,
                "job_id": getattr(resolved_job, "id", None),
                "status": "failed",
            },
            idempotency_filter={
                "recipient_user": recipient,
                "event_type": Notification.EventType.PUBLISHER_LESSON_RENDER_FAILED,
                "project": resolved_project,
                "job": resolved_job,
            },
        )
    except Exception:
        logger.warning("Render failure notification failed project=%s", getattr(project, "pk", project), exc_info=True)
        return None


def _avatar_action_url(project: Project) -> str:
    if _public_lesson_is_visible(project):
        return f"/watch?lesson={project.id}"
    return f"/studio?lesson={project.id}"


def notify_avatar_completed(project: Project | int | None, job: Job | int | None = None) -> Notification | None:
    try:
        resolved_project = _instance_or_none(Project, project)
        if resolved_project is None:
            return None
        resolved_job = _instance_or_none(Job, job) or _latest_project_job(
            resolved_project,
            job_type="avatar_render",
            statuses={"done"},
        )
        recipient = resolved_project.user
        return create_notification(
            recipient_user=recipient,
            event_type=Notification.EventType.PUBLISHER_AVATAR_RENDER_DONE,
            project=resolved_project,
            job=resolved_job,
            title="Avatar render completed",
            body=f"Avatar render completed for {_display_title(resolved_project)}.",
            action_url=_avatar_action_url(resolved_project),
            metadata={
                "project_id": resolved_project.id,
                "lesson_id": resolved_project.id,
                "avatar_job_id": getattr(resolved_job, "id", None),
                "status": "done",
            },
            idempotency_filter={
                "recipient_user": recipient,
                "event_type": Notification.EventType.PUBLISHER_AVATAR_RENDER_DONE,
                "project": resolved_project,
            },
        )
    except Exception:
        logger.warning("Avatar completion notification failed project=%s", getattr(project, "pk", project), exc_info=True)
        return None


def notify_avatar_failed(project: Project | int | None, job: Job | int | None = None) -> Notification | None:
    try:
        resolved_project = _instance_or_none(Project, project)
        if resolved_project is None:
            return None
        resolved_job = _instance_or_none(Job, job) or _latest_project_job(
            resolved_project,
            job_type="avatar_render",
            statuses={"failed"},
        )
        recipient = resolved_project.user
        return create_notification(
            recipient_user=recipient,
            event_type=Notification.EventType.PUBLISHER_AVATAR_RENDER_FAILED,
            project=resolved_project,
            job=resolved_job,
            title="Avatar render failed",
            body=f"Avatar render failed for {_display_title(resolved_project)}. Check Studio for details.",
            action_url=f"/studio?lesson={resolved_project.id}",
            metadata={
                "project_id": resolved_project.id,
                "lesson_id": resolved_project.id,
                "avatar_job_id": getattr(resolved_job, "id", None),
                "status": "failed",
            },
            idempotency_filter={
                "recipient_user": recipient,
                "event_type": Notification.EventType.PUBLISHER_AVATAR_RENDER_FAILED,
                "project": resolved_project,
            },
        )
    except Exception:
        logger.warning("Avatar failure notification failed project=%s", getattr(project, "pk", project), exc_info=True)
        return None
