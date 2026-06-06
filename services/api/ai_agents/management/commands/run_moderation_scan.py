from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from celery import Celery
from django.core.management.base import BaseCommand, CommandError

from ai_agents.models import AgentFinding, AgentRun
from core.models import Project


REVIEW_HINT_STATUSES = {"revision_required", "needs_admin_review", "failed", "admin_rejected"}
TASK_NAME = "worker.tasks.run_project_moderation"


def _ensure_services_on_path() -> None:
    services_root = Path(__file__).resolve().parents[4]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))


class Command(BaseCommand):
    help = "Run or dispatch a project moderation scan for terminal smoke testing."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int, required=True, help="Project id to scan.")
        parser.add_argument("--phase", default="source_scan", help="Moderation phase label. Defaults to source_scan.")
        parser.add_argument("--sync", action="store_true", help="Run directly without Celery.")
        parser.add_argument("--triggered-by-user-id", type=int, default=None, help="Optional user id recorded on the AgentRun.")

    def handle(self, *args, **options):
        project_id = int(options["project_id"])
        phase = str(options.get("phase") or "source_scan").strip() or "source_scan"
        triggered_by_user_id = options.get("triggered_by_user_id")
        project = Project.objects.filter(pk=project_id).first()
        if project is None:
            raise CommandError(f"Project {project_id} not found.")

        old_status = project.moderation_status
        self.stdout.write(f"Project: {project.id} - {project.title}")
        self.stdout.write(f"Old moderation_status: {old_status}")
        self.stdout.write(f"Phase: {phase}")

        if options.get("sync"):
            _ensure_services_on_path()
            from worker.ai_agents.orchestrator import ModerationOrchestrator

            result = ModerationOrchestrator().run(
                project.id,
                triggered_by_user_id=triggered_by_user_id,
                phase=phase,
            )
            self.stdout.write("Mode: sync")
            self._print_result(project.id, result=result)
            return

        broker_url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
        task_result = Celery(broker=broker_url).signature(
            TASK_NAME,
            args=[project.id],
            kwargs={"triggered_by_user_id": triggered_by_user_id, "phase": phase},
        ).apply_async()
        self.stdout.write("Mode: celery")
        self.stdout.write(f"Task id: {getattr(task_result, 'id', '')}")
        self.stdout.write("Run with --sync for immediate local smoke-test output.")
        self._print_result(project.id, result=None)

    def _print_result(self, project_id: int, *, result: dict | None) -> None:
        project = Project.objects.get(pk=project_id)
        run = _latest_run(project)
        findings = AgentFinding.objects.filter(run=run) if run else AgentFinding.objects.none()
        finding_count = findings.count()
        categories = Counter(findings.values_list("category", flat=True))
        severities = Counter(findings.values_list("severity", flat=True))

        self.stdout.write(f"New moderation_status: {project.moderation_status}")
        self.stdout.write(f"Latest run id: {run.id if run else project.last_moderation_run_id or ''}")
        self.stdout.write(f"Final decision: {run.final_decision if run else (result or {}).get('final_decision', '')}")
        self.stdout.write(f"Finding count: {finding_count}")
        self.stdout.write(f"Categories: {_format_counter(categories)}")
        self.stdout.write(f"Severities: {_format_counter(severities)}")
        if project.moderation_status in REVIEW_HINT_STATUSES:
            self.stdout.write(
                "Admin review request hint: python manage.py create_moderation_review_request "
                f"--project-id {project.id} --message \"AI misunderstood educational context\""
            )


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
