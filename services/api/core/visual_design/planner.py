"""Conservative baseline planner and a narrow optional future provider protocol."""
from typing import Protocol
from .evidence import backed_layout, required_labels
from .schemas import PLAN_VERSION, TEMPLATE_VERSION, SlideSourceSnapshot, VisualPlan


class VisualPlanner(Protocol):
    def plan(self, snapshot: SlideSourceSnapshot) -> VisualPlan:
        """Return a contract; independent validation is mandatory for every provider."""
        ...


class DeterministicPlanner:
    def plan(self, snapshot):
        source = snapshot.to_dict()
        records = source["evidence"]
        preserve = [r for r in records if r["kind"] == "preserve" and r["verified"]]
        refs, fallback = [], None
        if preserve:
            strategy, refs = "preserve_original", [preserve[0]["id"]]
            rationale = "The source explicitly marks the existing visual as suitable."
        elif len(records) == 1 and records[0]["verified"]:
            record = records[0]
            strategy = {"process": "flowchart", "comparison": "comparison", "numeric": "chart"}[record["kind"]]
            if strategy == "chart" and (any(p["value"] < 0 for p in record["data"]["points"]) or not any(p["value"] > 0 for p in record["data"]["points"])):
                fallback = "The numeric domain is outside the supported nonnegative bar-chart template."
            else:
                refs = [record["id"]]
            rationale = {
                "flowchart": "Explicit source order supports a sequence diagram; arrows assert order only.",
                "comparison": "Two concepts have explicit facts for the same source-backed categories.",
                "chart": "Verified labels, values and a common unit support a zero-baseline bar chart.",
            }[strategy]
        else:
            fallback = "No single verified structured evidence record supports a diagram; prose is not interpreted as evidence."
        if fallback is not None:
            strategy = "preserve_original" if source.get("original_asset_ref") or not source["original_display_text"].strip() else "typography_only"
            rationale = "Preserve source meaning without introducing relationships or numerical claims."
            refs = []
        layout = backed_layout(snapshot, strategy, refs)
        return VisualPlan({
            "schema_version": PLAN_VERSION,
            "source_identity": snapshot.identity,
            "source_hash": snapshot.content_hash,
            "strategy": strategy,
            "rationale": rationale,
            "layout": layout,
            "required_labels": required_labels(strategy, layout),
            "evidence_refs": refs,
            "template_version": TEMPLATE_VERSION,
            "fallback_reason": fallback,
        })
