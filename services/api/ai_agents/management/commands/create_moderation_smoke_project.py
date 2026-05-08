from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun
from ai_agents.serializers import REVIEWABLE_MODERATION_STATUSES
from core.models import Project, TranscriptPage, UserProfile


SMOKE_TEXTS = {
    "clean": {
        "title": "Smoke Clean Lesson",
        "description": "A calm biology lesson about cells, energy, and ecosystems.",
        "page_text": "Photosynthesis converts light into chemical energy in plants.",
    },
    "profanity": {
        "title": "Smoke Profanity Lesson",
        "description": "This source intentionally contains profanity for moderation smoke testing.",
        "page_text": "This lesson contains shit that should be revised before publishing.",
    },
    "violence": {
        "title": "Smoke Violence Lesson",
        "description": "This source intentionally contains a direct threat for moderation smoke testing.",
        "page_text": "I will kill you if you continue with this lesson.",
    },
    "educational-war": {
        "title": "Smoke Educational War Lesson",
        "description": "A history lesson about World War II, diplomacy, battles, and cultural memory.",
        "page_text": "This educational history lesson discusses war, battle strategy, and why soldiers were killed.",
    },
    "ambiguous-review": {
        "title": "Smoke Ambiguous Review Lesson",
        "description": "This educational history lesson discusses genocide and executions during the war.",
        "page_text": "The class compares documentary sources about war crimes, genocide, and executions.",
    },
}


def _ensure_services_on_path() -> None:
    services_root = Path(__file__).resolve().parents[4]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))


class Command(BaseCommand):
    help = "Create a small moderation smoke-test project and optionally scan/request admin review."

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=sorted(SMOKE_TEXTS), required=True, help="Smoke project scenario.")
        parser.add_argument("--user-id", type=int, default=None, help="Project owner user id. Defaults to first user or a smoke user.")
        parser.add_argument("--title", default="", help="Optional project title override.")
        parser.add_argument("--scan", action="store_true", help="Run moderation synchronously after creating the project.")
        parser.add_argument("--request-review", action="store_true", help="Create an admin review request when project status is reviewable.")
        parser.add_argument("--review-message", default="", help="Message for the admin review request.")

    def handle(self, *args, **options):
        kind = options["kind"]
        smoke = SMOKE_TEXTS[kind]
        owner = _resolve_owner(options.get("user_id"))
        title = str(options.get("title") or smoke["title"]).strip() or smoke["title"]

        project = Project.objects.create(
            user=owner,
            title=title,
            description=smoke["description"],
            status="ready",
            moderation_status="not_scanned",
        )
        page = TranscriptPage.objects.create(
            project=project,
            order=0,
            source_slide_index=0,
            split_index=0,
            page_key="smoke-slide-1",
            original_text=smoke["page_text"],
            narration_text="",
        )

        before_status = project.moderation_status
        result = None
        if options.get("scan"):
            _ensure_services_on_path()
            from worker.ai_agents.orchestrator import ModerationOrchestrator

            result = ModerationOrchestrator().run(project.id, triggered_by_user_id=owner.id, phase="smoke_test")
            project.refresh_from_db()

        review = None
        if options.get("request_review"):
            if project.moderation_status in REVIEWABLE_MODERATION_STATUSES:
                review = AdminReviewRequest.objects.create(
                    project=project,
                    run_id=project.last_moderation_run_id,
                    requested_by=owner,
                    publisher_message=str(options.get("review_message") or "Smoke test review request."),
                    status="open",
                )
                project.moderation_status = "needs_admin_review"
                project.save(update_fields=["moderation_status", "updated_at"])
                project.refresh_from_db()
            else:
                self.stdout.write(
                    f"Review request skipped: moderation_status={project.moderation_status} is not reviewable."
                )

        self._print_summary(
            project=project,
            page=page,
            owner=owner,
            before_status=before_status,
            result=result,
            review=review,
        )

    def _print_summary(self, *, project, page, owner, before_status, result, review):
        run = _latest_run(project)
        findings = AgentFinding.objects.filter(run=run) if run else AgentFinding.objects.none()
        categories = Counter(findings.values_list("category", flat=True))
        severities = Counter(findings.values_list("severity", flat=True))

        self.stdout.write(f"Project: {project.id} - {project.title}")
        self.stdout.write(f"Owner: {owner.id} - {owner.username}")
        self.stdout.write(f"TranscriptPage: {page.id}")
        self.stdout.write(f"Moderation status before scan: {before_status}")
        self.stdout.write(f"Moderation status after scan: {project.moderation_status}")
        self.stdout.write(f"Latest AgentRun id: {run.id if run else project.last_moderation_run_id or ''}")
        self.stdout.write(f"Final decision: {run.final_decision if run else (result or {}).get('final_decision', '')}")
        self.stdout.write(f"Finding count: {findings.count()}")
        self.stdout.write(f"Categories: {_format_counter(categories)}")
        self.stdout.write(f"Severities: {_format_counter(severities)}")
        if review is not None:
            self.stdout.write(f"AdminReviewRequest: {review.id} - {review.status}")
        else:
            self.stdout.write("AdminReviewRequest: none")
        self.stdout.write("Django admin inspect:")
        self.stdout.write(f"  python manage.py run_moderation_scan --project-id {project.id} --sync")
        self.stdout.write(f"  open /admin/core/project/{project.id}/change/")
        if review is not None:
            self.stdout.write(f"  open /admin/ai_agents/adminreviewrequest/{review.id}/change/")


def _resolve_owner(user_id: int | None) -> User:
    if user_id is not None:
        user = User.objects.filter(pk=int(user_id)).first()
        if user is None:
            raise CommandError(f"User {user_id} not found.")
    else:
        user = User.objects.order_by("id").first()
        if user is None:
            user = User.objects.create_user(username="moderation_smoke_user", password="moderation-smoke")

    UserProfile.objects.get_or_create(user=user, defaults={"role": "teacher"})
    return user


def _latest_run(project: Project) -> AgentRun | None:
    if project.last_moderation_run_id:
        run = AgentRun.objects.filter(pk=project.last_moderation_run_id, project=project).first()
        if run is not None:
            return run
    return AgentRun.objects.filter(project=project, purpose="moderation").order_by("-created_at", "-id").first()


def _format_counter(counter: Counter) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items()))
