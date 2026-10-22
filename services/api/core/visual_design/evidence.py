"""Source-backed layouts; no inference from prose or narration."""
from .schemas import canonical_json, require


def evidence_by_id(snapshot):
    return {record["id"]: record for record in snapshot.to_dict()["evidence"]}


def backed_layout(snapshot, strategy, refs):
    """Reconstruct the only allowed semantic payload from authoritative evidence."""
    source = snapshot.to_dict()
    if strategy == "preserve_original":
        records = evidence_by_id(snapshot)
        require(not refs or (len(refs) == 1 and refs[0] in records and records[refs[0]]["kind"] == "preserve" and records[refs[0]]["verified"]), "evidence_reference", "Invalid preservation evidence")
        return {"asset_ref": source.get("original_asset_ref")}
    if strategy == "typography_only":
        require(not refs, "evidence_reference", "Typography does not infer structured evidence")
        return {"text": source["original_display_text"]}
    require(len(refs) == 1, "evidence_reference", "One complete evidence record is required")
    record = evidence_by_id(snapshot).get(refs[0])
    require(record is not None and record["verified"], "evidence_reference", "Evidence must exist and be explicitly verified")
    kind = {"flowchart": "process", "comparison": "comparison", "chart": "numeric"}[strategy]
    require(record["kind"] == kind, "evidence_type", "Evidence does not support strategy")
    if strategy == "chart":
        values = [p["value"] for p in record["data"]["points"]]
        require(all(v >= 0 for v in values) and max(values) > 0, "chart_domain", "Phase 1 bar charts require nonnegative values and a positive maximum")
    return {"heading": source["original_display_text"], "evidence_id": record["id"], **record["data"]}


def required_labels(strategy, layout):
    if strategy == "preserve_original":
        return []
    if strategy == "typography_only":
        return [layout["text"]] if layout["text"] else []
    labels = [layout["heading"]] if layout["heading"] else []
    if strategy == "flowchart":
        by_id = {s["id"]: s["label"] for s in layout["steps"]}
        return labels + [by_id[key] for key in layout["order"]]
    if strategy == "comparison":
        return labels + layout["categories"] + [item["label"] for item in layout["items"]] + [v for item in layout["items"] for v in item["values"]]
    return labels + [layout["measure"], layout["unit"]] + [value for point in layout["points"] for value in (point["label"], canonical_json(point["value"]))]
