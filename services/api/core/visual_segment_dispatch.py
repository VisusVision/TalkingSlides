"""Private segment outbox/claim handoff. No normal render/finalizer dependency.

The deployment gate is deliberately CLOSED until separately authorized 3B-6
attests built-image shared storage, UID and native runtime. No environment/API
synthetic bypass exists. Tests replace only this private dependency seam.
"""
from datetime import timedelta
from functools import partial
import logging

from django.db import transaction
from django.http import Http404

from core import visual_segment_runs as controls
from core.models import VisualSegmentRun
from core.visual_design.schemas import DesignError, require
from core.visual_design.segment_contracts import _uuid, _integer
from core.visual_segment_artifacts import PrivateSegmentMedia, _root_fd

TASK_NAME = "worker.visual_segments.generate_private_segment"
DISPATCH_COOLDOWN_SECONDS = 30
MAX_SENDS_PER_GENERATION = 3
logger = logging.getLogger(__name__)


def _deployment_ready():
    """Future server-verified deployment attestation, NOT a settings flag."""
    return False


def require_ready():
    controls._dependencies()  # Preserve explicit provenance-unavailable failure.
    require(_deployment_ready() is True, "deployment_not_ready", "Private worker deployment is not verified")


def _send(message, task_id):
    # Reuse the existing API send-only Celery client; never import worker code
    # into the API image. Explicit queue, ID and bounded non-retrying transport.
    from core.views import _celery_app, _render_queue_name
    with _celery_app.connection_for_write(connect_timeout=5, transport_options={"socket_timeout":5,"socket_connect_timeout":5,"confirm_timeout":5,"max_retries":0}) as connection:
        return _celery_app.send_task(TASK_NAME, kwargs=message, task_id=task_id,
            queue=_render_queue_name(), connection=connection, retry=False, ignore_result=True)


def dispatch_pending(project_id, owner, run_id, *, allow_retry=False):
    """Short DB intent/CAS, bounded broker I/O outside locks, conditional ack.

    Pending remains true until a worker claims. Send/ack loss is replayed with
    the same task UUID and immutable generation. Duplicate sends are safe because
    database claims/receipts, not Celery task IDs, determine execution authority.
    """
    require(type(allow_retry) is bool, "dispatch", "Explicit operator retry policy required")
    require_ready()
    with controls._executable_run(project_id, owner, run_id) as (run, job):
        store = PrivateSegmentMedia(owner.pk, project_id)
        with _root_fd(store.root, create=False): pass
        require(run.pending_dispatch and job.status == "pending" and run.claim_token is None,
                "dispatch_not_pending", "Only an unclaimed committed pending Run may be sent")
        require(run.attempt_count == 0 or allow_retry, "operator_retry_required", "Claimed attempts require explicit operator retry")
        require(run.attempt_count < 3, "attempt_limit", "No further private claims allowed")
        now = controls._now()
        same = run.dispatch_generation == run.attempt_generation
        if same and run.dispatch_next_at and run.dispatch_next_at > now:
            return "cooldown"
        count = run.dispatch_attempts if same else 0
        require(count < MAX_SENDS_PER_GENERATION, "dispatch_limit", "Bounded broker sends exhausted")
        generation, execution_hash, task_id = run.attempt_generation, run.execution_hash, str(run.dispatch_task_id)
        controls._write_run(run, dispatch_generation=generation, dispatch_attempts=count+1,
            dispatch_next_at=now+timedelta(seconds=DISPATCH_COOLDOWN_SECONDS), dispatch_error="send_unconfirmed")
        message = {"run_id": str(run.pk), "execution_hash": execution_hash, "attempt_generation": generation}
    error = ""
    try:
        require_ready()
        _send(message, task_id)
    except DesignError as exc:
        error = exc.code
    except Exception:
        # No broker exception text/credentials/paths in metadata or HTTP results.
        error = "broker_unavailable"
    # Ack must not mutate a Run claimed/completed/cancelled while send returned.
    with controls._locked_project(project_id, owner) as project:
        run, job = controls._locked_run(project, owner, run_id)
        if (run.attempt_generation == generation and run.pending_dispatch and job.status == "pending"
                and run.dispatch_generation == generation and controls._terminal(run, job) is None):
            controls._write_run(run, dispatch_error=error)
    return error or "sent"


def _after_commit(project_id, owner, run_id):
    try:
        dispatch_pending(project_id, owner, run_id)
    except (DesignError, OSError, Http404):
        logger.info("Private segment remains unsent or fenced run=%s", run_id)


def reserve_and_submit(project_id, owner, **reservation):
    require_ready()
    run, reused = controls.reserve_visual_segment(project_id, owner, **reservation)
    # The persisted pending intent and UUID already exist. Also safe when called
    # within an outer transaction: no broker call is made until that commits.
    transaction.on_commit(partial(_after_commit, project_id, owner, run.pk))
    return run, reused


def reconcile_pending(*, limit=20, run_id=None, retry_claimed=False):
    """Operator command only. No scheduler, automatic media retry or new input.

    Default reconciles only never-claimed pending intents. Retrying an expired
    worker or transiently released claim requires --retry-claimed AND --run-id.
    """
    _integer(limit, 1, 50)
    require(type(retry_claimed) is bool and (not retry_claimed or run_id is not None),
            "operator_retry_required", "Retries must name one exact Run")
    require_ready()
    rows = VisualSegmentRun.objects.filter(job__status__in=("pending", "running"), cancelled_at__isnull=True,
        completed_receipt__isnull=True, terminal_failure_reason="", owner__is_active=True)
    if run_id is not None:
        _uuid(str(run_id)); rows = rows.filter(pk=run_id)
    if not retry_claimed: rows = rows.filter(pending_dispatch=True, attempt_count=0)
    results = []
    for row in rows.select_related("owner").order_by("created_at", "pk")[:limit]:
        try:
            if retry_claimed and row.lease_expires_at is not None:
                require(row.lease_expires_at <= controls._now(), "claim_busy", "Worker still owns a live lease")
                controls.expire_claim(row.project_id, row.owner, row.pk, expected_generation=row.attempt_generation)
            result = dispatch_pending(row.project_id, row.owner, row.pk, allow_retry=retry_claimed)
        except (DesignError, OSError, Http404) as exc:
            result = exc.code if isinstance(exc, DesignError) else "unavailable"
        results.append({"run_id": str(row.pk), "result": result})
    return results
