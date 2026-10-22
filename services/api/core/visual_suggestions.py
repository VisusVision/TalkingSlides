"""Private bounded candidate generation. No task dispatch or lesson mutations."""
from core.models import VisualDesignSuggestion
from core.visual_design import DeterministicPlanner, render_preview
from core.visual_design.schemas import PLAN_VERSION, PREVIEW_VERSION, RENDERER_VERSION, TEMPLATE_VERSION, content_hash

SUGGESTION_VERSION = "visual-suggestion.v1"
GENERATOR_VERSION = "deterministic-studio.v1"


def generate_suggestion(project, owner, source):
    key = content_hash(GENERATOR_VERSION, {
        "source_hash": source.source_hash, "source_mode": source.source_mode,
        "plan_version": PLAN_VERSION, "preview_version": PREVIEW_VERSION,
        "renderer_version": RENDERER_VERSION, "template_version": TEMPLATE_VERSION,
    })
    existing = VisualDesignSuggestion.objects.filter(project=project, owner=owner, candidate_key=key).first()
    if existing:
        return existing, False
    plan_payload, preview_payload, plan_hash, preview_hash = {}, {}, "", ""
    errors, state = list(source.errors), "unsupported"
    if source.snapshot is not None:
        plan = DeterministicPlanner().plan(source.snapshot)
        preview = render_preview(source.snapshot, plan)
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
                  "plan_hash": plan_hash, "preview_hash": preview_hash, "validation_errors": errors},
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
        "is_stale": stale, "compatible": compatible,
        "stale_reason": "version_changed" if not compatible else "page_unavailable" if current_source is None else "source_changed" if stale else None,
        "contains_text": preview.get("contains_text", False),
        "renderer_version": preview.get("renderer_version"),
        "plan_schema_version": plan.get("schema_version"),
        "created_at": candidate.created_at.isoformat(),
        "preview_url": f"/api/v1/projects/{candidate.project_id}/visual-suggestions/{candidate.pk}/preview/" if candidate.status == "ready" and compatible else None,
        "applied": False, "can_apply": False,
    }
