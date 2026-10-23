"""Private bounded candidate generation. No task dispatch or lesson mutations."""
from core.models import VisualDesignSuggestion
from core.visual_design import DeterministicPlanner, render_preview
from core.visual_design.schemas import PLAN_VERSION, PREVIEW_VERSION, RENDERER_VERSION, TEMPLATE_VERSION, content_hash
from core.visual_design.grounding import grounded_payload, design_snapshot, VisualProposal

SUGGESTION_VERSION = "visual-suggestion.v1"
GENERATOR_VERSION = "grounded-studio.v1"


def generate_suggestion(project, owner, source, *, proposal=None):
    grounding = {}
    if source.snapshot is not None:
        grounding = grounded_payload(source.snapshot, VisualProposal(proposal.payload) if proposal else None,
                                     str(proposal.pk) if proposal else None)
    # A model proposal only enters the rendering path after a matching immutable
    # teacher decision. Revalidate this even for callers outside the HTTP view.
    if proposal is not None:
        from core.models import VisualEvidenceReview
        from core.visual_design.schemas import require
        require(proposal.project_id == project.pk and proposal.owner_id == owner.pk
                and proposal.page_key == source.page_key and proposal.source_hash == source.source_hash
                and proposal.source_revision == source.source_revision
                and VisualProposal(proposal.payload).content_hash == proposal.proposal_hash
                and VisualEvidenceReview.objects.filter(proposal=proposal, owner=owner, action="confirm",
                    source_hash=source.source_hash, proposal_hash=proposal.proposal_hash).exists(),
                "review", "Matching teacher confirmation is required")
    key = content_hash(GENERATOR_VERSION, {
        "source_hash": source.source_hash, "source_mode": source.source_mode,
        "plan_version": PLAN_VERSION, "preview_version": PREVIEW_VERSION,
        "renderer_version": RENDERER_VERSION, "template_version": TEMPLATE_VERSION,
        "grounding_hash": content_hash("grounding.v1", grounding),
    })
    existing = VisualDesignSuggestion.objects.filter(project=project, owner=owner, candidate_key=key).first()
    if existing:
        return existing, False
    plan_payload, preview_payload, plan_hash, preview_hash = {}, {}, "", ""
    errors, state = list(source.errors), "unsupported"
    if source.snapshot is not None:
        snapshot = design_snapshot(grounding)
        plan = DeterministicPlanner().plan(snapshot)
        preview = render_preview(snapshot, plan)
        plan_payload, preview_payload = plan.to_dict(), preview.to_dict()
        plan_hash, preview_hash = plan.content_hash, preview.content_hash
        errors = preview_payload["errors"]
        state = {"valid": "ready", "preserved": "preserved", "invalid": "unsupported"}[preview_payload["status"]]
    # get_or_create + DB uniqueness also handles simultaneous identical requests.
    return VisualDesignSuggestion.objects.get_or_create(
        project=project, owner=owner, candidate_key=key,
        defaults={"page_key": source.page_key, "source_mode": source.source_mode,
                  "source_revision": source.source_revision, "source_hash": source.source_hash,
                  "status": state, "plan_payload": plan_payload, "preview_payload": preview_payload,
                  "plan_hash": plan_hash, "preview_hash": preview_hash, "validation_errors": errors,
                  "grounding_payload": grounding, "proposal": proposal},
    )


def suggestion_metadata(candidate, current_source):
    plan, preview = candidate.plan_payload, candidate.preview_payload
    compatible = (candidate.schema_version == SUGGESTION_VERSION
                  and (not plan or plan.get("schema_version") == PLAN_VERSION and plan.get("template_version") == TEMPLATE_VERSION)
                  and (not preview or preview.get("schema_version") == PREVIEW_VERSION and preview.get("renderer_version") == RENDERER_VERSION))
    stale = (current_source is None or candidate.source_hash != current_source.source_hash
             or candidate.source_mode != current_source.source_mode or not compatible)
    return {
        "schema_version": candidate.schema_version, "id": str(candidate.pk), "project_id": candidate.project_id,
        "page_key": candidate.page_key, "source_mode": candidate.source_mode,
        "source_revision": candidate.source_revision, "source_hash": candidate.source_hash,
        "plan_hash": candidate.plan_hash, "preview_hash": candidate.preview_hash,
        "status": candidate.status, "strategy": plan.get("strategy"),
        "rationale": plan.get("rationale", ""), "fallback_reason": plan.get("fallback_reason"),
        "warnings": preview.get("warnings", []), "errors": candidate.validation_errors,
        "evidence": {key: candidate.grounding_payload.get(key) for key in ("schema_version", "origin", "supports", "warnings", "proposal_id")},
        "design_source_hash": plan.get("source_hash"),
        "is_stale": stale, "compatible": compatible,
        "stale_reason": "version_changed" if not compatible else "page_unavailable" if current_source is None else "source_changed" if stale else None,
        "contains_text": preview.get("contains_text", False),
        "renderer_version": preview.get("renderer_version"),
        "plan_schema_version": plan.get("schema_version"),
        "created_at": candidate.created_at.isoformat(),
        "preview_url": f"/api/v1/projects/{candidate.project_id}/visual-suggestions/{candidate.pk}/preview/" if candidate.status == "ready" and compatible else None,
        "applied": False, "can_apply": False,
    }
