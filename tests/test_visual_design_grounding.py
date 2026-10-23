"""Pure grounded evidence tests: no settings, network, GPU or model required."""
from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from core.visual_design import DeterministicPlanner, SlideSourceSnapshot, render_preview
from core.visual_design.grounding import (VisualProposal, design_snapshot, extract_evidence,
                                         grounded_payload, verify_proposal, PROPOSAL_VERSION)
from core.visual_design.schemas import DesignError, SOURCE_VERSION, content_hash

EXAMPLES = json.loads((Path(__file__).parent / "fixtures/visual_design/grounded/examples.json").read_text())


def snapshot(text, original=False):
    data = {"schema_version": SOURCE_VERSION, "project_id": "lesson", "page_key": "source-0-split-1",
            "source_slide_index": 0, "split_index": 1, "source_revision": content_hash("fixture", text),
            "original_display_text": text, "protected_narration": "Öğretmenin değişmeyen anlatımı & facts.", "evidence": []}
    if original: data["original_asset_ref"] = "asset:original"
    return SlideSourceSnapshot(data)


def bullet_proposal(source):
    raw = source.to_dict()["original_display_text"]
    labels = [line[2:] for line in raw.splitlines()[1:]]
    return VisualProposal({"schema_version": PROPOSAL_VERSION, "strategy": "flowchart", "rationale": "Proposed sequence for teacher review.",
        "evidence": {"kind": "process", "data": {"steps": [{"id": f"step-{i+1}", "label": label} for i, label in enumerate(labels)],
                    "order": [f"step-{i+1}" for i in range(len(labels))], "relationship": "sequence"}},
        "references": [{"start": 0, "end": len(raw), "quote": raw}]})


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e:e["name"])
def test_real_text_deterministic_grounding_and_readable_preview(example):
    source = snapshot(example["text"])
    grounding = grounded_payload(source)
    enriched = design_snapshot(grounding)
    plan = DeterministicPlanner().plan(enriched)
    preview = render_preview(enriched, plan)
    assert plan.to_dict()["strategy"] == example["strategy"]
    assert preview.to_dict()["status"] == "valid", preview.to_dict()["errors"]
    assert preview.content_hash == render_preview(enriched, plan).content_hash
    assert grounding == grounded_payload(source)
    assert source.to_dict()["protected_narration"] == enriched.to_dict()["protected_narration"]
    for ref in grounding["supports"]:
        assert example["text"][ref["start"]:ref["end"]] == ref["quote"]
        assert ref["page_key"] == "source-0-split-1"
        assert ref["source_revision"] == source.identity["source_revision"]
    ET.fromstring(preview.to_dict()["svg"])
    if example["strategy"] == "chart":
        assert [p["value"] for p in plan.to_dict()["layout"]["points"]] == [12, 18]
        assert plan.to_dict()["layout"]["unit"] == "cm"


def test_original_visual_does_not_suppress_explicit_source_alternative():
    source = snapshot(EXAMPLES[0]["text"], original=True)
    plan = DeterministicPlanner().plan(design_snapshot(grounded_payload(source)))
    assert plan.to_dict()["strategy"] == "flowchart"
    assert source.to_dict()["original_asset_ref"] == "asset:original"


@pytest.mark.parametrize("text", [
    "Compare process models\n1. A\n2. B", "Not a process\n1. A\n2. B", "Süreç türleri\n1. A\n2. B",
    "Process\n1. A\n3. B", "Process\n1. A\n2. A", "Rank\n1. A\n2. B",
    "Process\n1. A\n2. B\nExtra unsupported assertion",
    "Data\nGroup | Height (cm)\nA | NaN\nB | 18", "Data\nGroup | Height (cm)\nA | 12 kg\nB | 18 cm",
    "Data\nGroup | Height (cm)\nA | -12\nB | 18", "A causes B", "Steps\n1. Only step",
])
def test_unsupported_ambiguous_contradictory_or_incomplete_structures_do_not_become_evidence(text):
    assert extract_evidence(snapshot(text))["evidence"] == []


def test_teacher_review_preserves_all_bullets_and_distinguishes_inferred_order():
    source = snapshot(EXAMPLES[-1]["text"])
    proposal = bullet_proposal(source)
    checked = verify_proposal(source, proposal)
    assert checked["inferred"] is True
    assert checked["warnings"]
    assert extract_evidence(source)["evidence"] == []
    result = grounded_payload(source, proposal, "proposal-id")
    assert result["origin"] == "teacher_confirmed"
    assert render_preview(design_snapshot(result), DeterministicPlanner().plan(design_snapshot(result))).to_dict()["status"] == "valid"


@pytest.mark.parametrize("attack", ["invented_step", "missing_step", "invented_quote", "unknown_field", "causal", "missing_refs", "markup"])
def test_model_hallucinations_and_unsafe_proposals_fail_closed(attack):
    source = snapshot(EXAMPLES[-1]["text"])
    payload = bullet_proposal(source).to_dict()
    if attack == "invented_step": payload["evidence"]["data"]["steps"][0]["label"] = "Invented fact"
    if attack == "missing_step":
        payload["evidence"]["data"]["steps"].pop(); payload["evidence"]["data"]["order"].pop()
    if attack == "invented_quote": payload["references"][0]["quote"] = "Fake source"
    if attack == "unknown_field": payload["svg"] = "<svg/>"
    if attack == "causal": payload["evidence"]["data"]["relationship"] = "causes"
    if attack == "missing_refs": payload["references"] = []
    if attack == "markup": payload["rationale"] = '<script>alert(1)</script>'
    with pytest.raises(DesignError): verify_proposal(source, VisualProposal(payload))


def test_model_numeric_values_cannot_override_authoritative_records():
    source = snapshot(EXAMPLES[2]["text"])
    record = extract_evidence(source)["evidence"][0]
    payload = {"schema_version": PROPOSAL_VERSION, "strategy": "chart", "rationale": "Explicit table.",
        "evidence": {"kind": "numeric", "data": deepcopy(record["data"])},
        "references": [{"start": 0, "end": len(EXAMPLES[2]["text"]), "quote": EXAMPLES[2]["text"]}]}
    assert not verify_proposal(source, VisualProposal(payload))["inferred"]
    payload["evidence"]["data"]["points"][0]["value"] = 99
    with pytest.raises(DesignError): verify_proposal(source, VisualProposal(payload))
    payload["evidence"]["data"]["points"][0]["value"] = float('inf')
    with pytest.raises(DesignError): VisualProposal(payload)


def test_committed_grounded_preview_artifacts_match_versioned_contracts():
    root = Path(__file__).resolve().parents[1] / "docs/visual_design/phase2c"
    manifest = json.loads((root / "manifest.json").read_text())
    assert len(manifest) == 8
    for item in manifest:
        raw = {"schema_version": SOURCE_VERSION, "project_id": "grounded-examples", "page_key": item["fixture"] + ".source-0.split-1",
               "source_slide_index": 0, "split_index": 1, "original_display_text": item["source_text"],
               "protected_narration": "Narration preserved verbatim.", "source_revision": content_hash("phase2c-example", item["source_text"]), "evidence": []}
        base = SlideSourceSnapshot(raw)
        proposal = bullet_proposal(base) if item["fixture"] == "teacher_confirmed" else None
        bundle = grounded_payload(base, proposal, "synthetic-confirmation" if proposal else None)
        enriched = design_snapshot(bundle)
        plan = DeterministicPlanner().plan(enriched)
        preview = render_preview(enriched, plan)
        assert item["source_hash"] == base.content_hash
        assert item["plan_hash"] == plan.content_hash
        assert item["preview_hash"] == preview.content_hash
        assert (root / item["file"]).read_text() == preview.to_dict()["svg"]


def test_explicit_markdown_process_heading_retains_exact_authored_title():
    source = snapshot("# Steps: Measurement\n1. Collect sample\n2. Record value")
    result = extract_evidence(source)
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["heading"] == "# Steps: Measurement"
