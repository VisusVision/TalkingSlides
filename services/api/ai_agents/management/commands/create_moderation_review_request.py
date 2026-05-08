from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from ai_agents.models import AdminReviewRequest
from ai_agents.serializers import REVIEWABLE_MODERATION_STATUSES
from core.models import Project


class Command(BaseCommand):
    help = "Create an admin moderation review request for terminal smoke testing."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int, required=True, help="Project id to request review for.")
        parser.add_argument("--user-id", type=int, default=None, help="Optional requesting user id.")
        parser.add_argument("--message", default="", help="Publisher/admin review message.")

    def handle(self, *args, **options):
        project_id = int(options["project_id"])
        project = Project.objects.filter(pk=project_id).first()
        if project is None:
            raise CommandError(f"Project {project_id} not found.")
        if project.moderation_status not in REVIEWABLE_MODERATION_STATUSES:
            raise CommandError(
                "Project is not in a reviewable moderation state "
                f"({project.moderation_status})."
            )
        if AdminReviewRequest.objects.filter(project=project, status="open").exists():
            raise CommandError(f"An open review request already exists for project {project.id}.")

        user = None
        user_id = options.get("user_id")
        if user_id is not None:
            user = User.objects.filter(pk=int(user_id)).first()
            if user is None:
                raise CommandError(f"User {user_id} not found.")

        review = AdminReviewRequest.objects.create(
            project=project,
            run_id=project.last_moderation_run_id,
            requested_by=user,
            publisher_message=str(options.get("message") or ""),
            status="open",
        )
        project.moderation_status = "needs_admin_review"
        project.save(update_fields=["moderation_status", "updated_at"])

        self.stdout.write(f"Created AdminReviewRequest: {review.id}")
        self.stdout.write(f"Project: {project.id} - {project.title}")
        self.stdout.write(f"moderation_status: {project.moderation_status}")
