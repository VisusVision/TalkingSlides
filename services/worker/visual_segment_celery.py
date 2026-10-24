"""Tiny broker adapter around the established private Run/media executor."""
from django.shortcuts import get_object_or_404
from django.http import Http404
from core.models import VisualSegmentRun
from core import visual_segment_runs as controls
from core.visual_design.schemas import DesignError, require, digest
from core.visual_design.segment_contracts import _uuid, _integer
from core.visual_segment_dispatch import require_ready, TASK_NAME
from worker.visual_segment_tasks import execute_private_segment
from worker.celery_app import app


def process_private_delivery(run_id, execution_hash, attempt_generation, *, delivery_id):
    """Only IDs arrive over the broker; claim token is created by the server."""
    _uuid(run_id); digest(execution_hash); _integer(attempt_generation); _uuid(str(delivery_id))
    require_ready()
    run = get_object_or_404(VisualSegmentRun.objects.select_related("owner", "job", "snapshot", "confirmation"), pk=run_id)
    controls._stored_identity(run, run.job)
    require(run.execution_hash == execution_hash and str(run.dispatch_task_id) == str(delivery_id),
            "stale_attempt", "Broker identity differs from persisted dispatch intent")
    if run.completed_receipt is not None:
        decision = controls.private_result_access_policy(run.project_id, run.owner, run.pk)
        require(decision.allowed and attempt_generation == run.completed_receipt["attempt_generation"]-1,
                "stale_attempt", "Completed message is stale or unauthorized")
        return {"id": run_id, "status": "done", "reused": True, "publishing_allowed": False}
    # A running duplicate cannot obtain a second token. Expired claims require
    # explicit operator reconciliation; ordinary broker redelivery never takes over.
    require(run.pending_dispatch and run.job.status == "pending" and run.claim_token is None,
            "claim_busy", "Private attempt is not ready for delivery")
    require(run.dispatch_generation == attempt_generation and run.dispatch_attempts > 0,
            "stale_attempt", "No persisted send intent for this generation")
    claim = controls.claim_attempt(run.project_id, run.owner, run.pk, execution_hash=execution_hash,
        expected_generation=attempt_generation, lease_owner="private-segment:"+str(delivery_id), lease_seconds=300)
    execute_private_segment(run_id, execution_hash, claim.attempt_generation, claim_token=claim.claim_token)
    return {"id": run_id, "status": "done", "reused": False, "publishing_allowed": False}


@app.task(bind=True, name=TASK_NAME, max_retries=0, acks_late=True,
          reject_on_worker_lost=True, ignore_result=True, soft_time_limit=240, time_limit=260)
def generate_private_segment(self, run_id, execution_hash, attempt_generation):
    try:
        return process_private_delivery(run_id, execution_hash, attempt_generation, delivery_id=self.request.id)
    except (DesignError, Http404) as exc:
        # Run/receipt state remains authoritative. Never put content, credentials,
        # filesystem paths or receipts in a generic Celery result backend.
        return {"status": "blocked", "reason_code": exc.code if isinstance(exc, DesignError) else "unavailable",
                "publishing_allowed": False}
