"""Private visual choices, isolated from Studio's promotable video draft.

Mutations lock the owner project and saved pages, then use an optimistic CAS.
They NEVER save Project/TranscriptPage, mark render dirtiness or dispatch tasks.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from core.models import Project, TranscriptPage, VisualDesignSuggestion, VisualDraftDecision, VisualDraftSelection
from core.visual_design import DesignError, VisualPlan, VisualPreview, render_preview, validate_plan, validate_svg
from core.visual_design.schemas import PLAN_VERSION, PREVIEW_VERSION, RENDERER_VERSION, TEMPLATE_VERSION
from core.visual_design_source import build_studio_visual_source
from core.visual_suggestions import SUGGESTION_VERSION, suggestion_metadata

DRAFT_VERSION = "visual-draft.v1"


class VisualDraftConflict(APIException):
    status_code = 409
    default_detail = "Saved source or visual draft changed. Refresh before saving."
    default_code = "visual_revision_conflict"


def verified_preview(candidate, source=None):
    """Recheck immutable contracts, versioned hashes, SVG and source evidence.

    Historical preview delivery may omit source; selection always supplies it.
    Re-rendering a bounded SVG here is validation, never video rendering.
    """
    try:
        if candidate.schema_version != SUGGESTION_VERSION or candidate.status != "ready" or candidate.validation_errors:
            raise DesignError("unsupported", "Candidate is not selectable")
        plan, preview = VisualPlan(candidate.plan_payload), VisualPreview(candidate.preview_payload)
        p, v = plan.to_dict(), preview.to_dict()
        if (p["schema_version"] != PLAN_VERSION or p["template_version"] != TEMPLATE_VERSION
            or v["schema_version"] != PREVIEW_VERSION or v["renderer_version"] != RENDERER_VERSION
            or v["status"] != "valid" or v["errors"]
            or plan.content_hash != candidate.plan_hash or preview.content_hash != candidate.preview_hash
            or v["plan_hash"] != candidate.plan_hash or v["source_hash"] != candidate.source_hash
            or p["source_hash"] != candidate.source_hash
            or p["source_identity"]["project_id"] != str(candidate.project_id)
            or p["source_identity"]["page_key"] != candidate.page_key
            or p["source_identity"]["source_revision"] != candidate.source_revision
            or not validate_svg(v["svg"], plan).valid):
            raise DesignError("integrity", "Stored preview failed validation")
        if source is not None:
            if (source.snapshot is None or candidate.source_hash != source.source_hash
                or candidate.source_revision != source.source_revision or candidate.source_mode != source.source_mode
                or not validate_plan(source.snapshot, plan).valid
                or render_preview(source.snapshot, plan).content_hash != candidate.preview_hash):
                raise DesignError("stale_source", "Candidate does not match the saved source")
        return v
    except (DesignError, KeyError, TypeError, ValueError) as exc:
        raise VisualDraftConflict("Candidate is stale, unsupported or failed integrity validation.") from exc


def design_identity(candidate, preview):
    return {
        "source_hash": candidate.source_hash, "source_revision": candidate.source_revision,
        "plan_hash": candidate.plan_hash, "preview_hash": candidate.preview_hash,
        "contains_text": preview["contains_text"],
        "design_versions": {"suggestion": candidate.schema_version,
                            "plan": candidate.plan_payload["schema_version"],
                            "preview": preview["schema_version"],
                            "template": candidate.plan_payload["template_version"],
                            "renderer": preview["renderer_version"]},
    }


def current_source(project, page_key):
    try:
        return build_studio_visual_source(project, page_key)
    except (NotFound, ValidationError):
        return None


def visual_draft_metadata(project, owner, page_key):
    source = current_source(project, page_key)
    row = VisualDraftSelection.objects.select_related("candidate").filter(project=project, owner=owner, page_key=page_key).first()
    state = {"state": "original", "candidate_id": None, "version": 0, "is_stale": False,
             "contains_text": False, "applied": False}
    selected = None
    if row:
        state.update({"state": row.state, "candidate_id": str(row.candidate_id) if row.candidate_id else None,
                      "version": row.version, "source_hash": row.source_hash, "source_revision": row.source_revision,
                      "plan_hash": row.plan_hash, "preview_hash": row.preview_hash,
                      "design_versions": row.design_versions, "contains_text": row.contains_text,
                      "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()})
        if row.state == "selected":
            try:
                if source is None or row.candidate is None or row.candidate.owner_id != owner.pk:
                    raise VisualDraftConflict()
                preview = verified_preview(row.candidate, source)
                if any(getattr(row, key) != value for key, value in design_identity(row.candidate, preview).items()):
                    raise VisualDraftConflict()
                state["is_stale"] = False
            except VisualDraftConflict:
                state["is_stale"] = True
            selected = suggestion_metadata(row.candidate, source) if row.candidate else None
    history = [] if row is None else [{"action": d.action, "candidate_id": str(d.candidate_id) if d.candidate_id else None,
                "version": d.version, "source_hash": d.source_hash, "result": d.result,
                "created_at": d.created_at.isoformat()} for d in row.decisions.all()[:20]]
    # At most 21 IDs; never scan an unbounded decision history in Python.
    candidate_ids = list(VisualDesignSuggestion.objects.filter(project=project, owner=owner, page_key=page_key).values_list("pk", flat=True)[:20])
    if row and row.candidate_id and row.candidate_id not in candidate_ids:
        candidate_ids.append(row.candidate_id)
    rejected = []
    if row:
        for candidate_id in candidate_ids:
            latest = row.decisions.filter(candidate_id=candidate_id, action__in=["select", "reject"]).first()
            if latest and latest.action == "reject":
                rejected.append(str(candidate_id))
    return {"schema_version": DRAFT_VERSION, "page_key": page_key, "source": source.metadata() if source else None,
            "selection": state, "selected_candidate": selected, "rejected_ids": rejected,
            "history": history, "history_limit": 20, "applied": False}


@transaction.atomic
def decide_visual_draft(project_id, owner, payload):
    project = get_object_or_404(Project.objects.select_for_update(), pk=project_id, user=owner)
    # Match promotion's lock order and hold saved active-page revisions stable.
    list(TranscriptPage.objects.select_for_update().filter(project=project).order_by("id").values_list("id", flat=True))
    page_key, action = payload["page_key"], payload["action"]
    source = current_source(project, page_key)
    if source is None or source.source_hash != payload["expected_source_hash"]:
        raise VisualDraftConflict()
    candidate, identity = None, {}
    if action != "restore":
        candidate = get_object_or_404(VisualDesignSuggestion, pk=payload["candidate_id"], project=project, owner=owner, page_key=page_key)
        if action == "select":
            identity = design_identity(candidate, verified_preview(candidate, source))
    row, _ = VisualDraftSelection.objects.get_or_create(project=project, owner=owner, page_key=page_key)
    latest = row.decisions.first()
    same_event = (latest is not None and latest.action == action and latest.candidate_id == (candidate.pk if candidate else None)
                  and latest.source_hash == source.source_hash)
    # Allow an exact retry of only the immediately preceding request. An older
    # request must never overwrite a later teacher decision, even if it looks alike.
    if payload["expected_version"] != row.version:
        if same_event and payload["expected_version"] == row.version - 1:
            return visual_draft_metadata(project, owner, page_key), True
        raise VisualDraftConflict()
    if same_event:
        return visual_draft_metadata(project, owner, page_key), True
    values = {}
    if action == "select":
        values = {"state": "selected", "candidate_id": candidate.pk, **identity}
    elif action == "restore" or row.state == "original" or row.candidate_id == (candidate.pk if candidate else None):
        values = {"state": "original", "candidate_id": None, "source_hash": source.source_hash,
                  "source_revision": source.source_revision, "plan_hash": "", "preview_hash": "",
                  "design_versions": {}, "contains_text": False}
    next_version = row.version + 1
    if VisualDraftSelection.objects.filter(pk=row.pk, version=row.version).update(**values, version=next_version, updated_at=timezone.now()) != 1:
        raise VisualDraftConflict()
    row.refresh_from_db()
    VisualDraftDecision.objects.create(selection=row, candidate=candidate, action=action, version=next_version,
        source_hash=source.source_hash, result={"state": row.state, "candidate_id": str(row.candidate_id) if row.candidate_id else None,
                                             "plan_hash": row.plan_hash, "preview_hash": row.preview_hash})
    # Recheck in-transaction; any racing source revision rolls back row + history.
    checked_source = current_source(project, page_key)
    if checked_source is None or checked_source.source_hash != source.source_hash:
        raise VisualDraftConflict()
    return visual_draft_metadata(project, owner, page_key), False
