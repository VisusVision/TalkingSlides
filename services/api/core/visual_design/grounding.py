"""Bounded text grounding. Source assertions are not independent scientific proof.

Only entire, explicit structures are extracted; no narration or image inference.
Offsets count Unicode code points in the exact saved original_display_text.
"""
import re
from .schemas import (DesignError, SlideSourceSnapshot, _Contract, array, fields,
                      text, require, evidence_record)

EXTRACTOR_VERSION = "text-evidence.v1"
GROUNDING_VERSION = "grounded-design.v1"
PROPOSAL_VERSION = "visual-proposal.v1"


def source_lines(snapshot):
    result, offset = [], 0
    for raw in snapshot.to_dict()["original_display_text"].splitlines(keepends=True):
        stripped = raw.strip()
        if stripped:
            start = offset + raw.index(stripped)
            result.append({"start": start, "end": start + len(stripped), "quote": stripped})
        offset += len(raw)
    return result


def support(snapshot, lines):
    return [{"page_key": snapshot.identity["page_key"], "source_revision": snapshot.identity["source_revision"],
             "locator": f"display:{line['start']}:{line['end']}", **line} for line in lines]


def _record(snapshot, kind, data):
    record = {"id": "text-" + kind, "kind": kind, "source_revision": snapshot.identity["source_revision"],
              "locator": "original_display_text", "verified": True, "data": data,
              "heading": source_lines(snapshot)[0]["quote"]}
    evidence_record(record, snapshot.identity["source_revision"])
    return record


def _cells(line):
    return [part.strip() for part in line.strip("|").split("|")]


def _number(value):
    require(re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?", value) is not None,
            "number", "Only explicit nonnegative decimal values are supported")
    return float(value) if "." in value else int(value)


def extract_evidence(snapshot):
    """Return zero/one verified record and exact source excerpts, never guessed data.

    Grammar: process heading + contiguous 1..N numbered lines, or a complete
    pipe table (two concepts; or a measure column with explicit common unit).
    Extra prose/malformed rows/duplicate labels invalidate the entire structure.
    """
    lines = source_lines(snapshot)
    result = {"version": EXTRACTOR_VERSION, "evidence": [], "supports": support(snapshot, lines),
              "warnings": [], "structure": "text"}
    if not lines:
        return result
    heading, body = lines[0]["quote"], [line["quote"] for line in lines[1:]]
    # Mentioning a process ("Compare process models") does not establish order.
    cue_heading = re.sub(r"^#{1,6} +", "", heading)
    process_heading = (
        re.search(r"^(?:process|steps|procedure|süreç|adımlar|işlem sırası)\s*(?::|$)|\b(?:process|procedure|süreci|adımları|işlem sırası)$", cue_heading, re.I)
        and not re.search(r"\b(?:not|no|versus|comparison|compare|alternatives|değil|karşılaştırma)\b", cue_heading, re.I)
    )
    numbered = [re.fullmatch(r"([1-9][0-9]?)\s*[.)]\s+(.+)", line) for line in body]
    try:
        if process_heading and 2 <= len(body) <= 4 and all(numbered):
            require([int(m[1]) for m in numbered] == list(range(1, len(body) + 1)), "order", "Numbering must be contiguous from one")
            labels = [m[2] for m in numbered]
            require(len(set(labels)) == len(labels), "contradiction", "Repeated process steps are ambiguous")
            steps = [{"id": f"step-{i+1}", "label": label} for i, label in enumerate(labels)]
            record = _record(snapshot, "process", {"steps": steps, "order": [s["id"] for s in steps], "relationship": "sequence"})
        elif 2 <= len(body) <= 8 and all("|" in line for line in body):
            rows = [_cells(line) for line in body]
            require(all(all(row) for row in rows), "table", "Empty cells are not evidence")
            header, values = rows[0], rows[1:]
            # A Markdown separator is syntax, not a data row.
            if values and all(re.fullmatch(r":?-{3,}:?", cell) for cell in values[0]):
                values = values[1:]
            require(bool(values) and all(len(row) == len(header) for row in values), "table", "Conflicting table columns")
            if len(header) == 2:
                match = re.fullmatch(r"(.+?)\s+\(([^()]+)\)", header[1])
                require(match is not None and 2 <= len(values) <= 6, "units", "Chart requires a measure and explicit common unit")
                record = _record(snapshot, "numeric", {"measure": match[1].strip(), "unit": match[2].strip(),
                    "points": [{"label": row[0], "value": _number(row[1])} for row in values]})
            elif len(header) == 3:
                require(1 <= len(values) <= 4, "table", "Comparison supports one to four categories")
                record = _record(snapshot, "comparison", {"categories": [row[0] for row in values],
                    "items": [{"label": header[col], "values": [row[col] for row in values]} for col in (1, 2)]})
            else:
                raise DesignError("table", "Unsupported table shape")
        else:
            result["structure"] = "list" if body and all(re.fullmatch(r"[-*•] +.+", line) for line in body) else "text"
            return result
        result.update(evidence=[record], structure=record["kind"])
    except DesignError as exc:
        result["warnings"] = [f"{exc.code}: explicit structure could not be verified; source text was retained."]
    return result


class VisualProposal(_Contract):
    """Model instructions are data only. No layout/rendering operations accepted."""
    def _validate(self, value):
        fields(value, ("schema_version", "strategy", "rationale", "evidence", "references"))
        require(value["schema_version"] == PROPOSAL_VERSION, "version", "Unsupported proposal version")
        require(value["strategy"] in ("flowchart", "comparison", "chart"), "operation", "Unsupported proposed strategy")
        text(value["rationale"], 1000)
        fields(value["evidence"], ("kind", "data"))
        kind = {"flowchart": "process", "comparison": "comparison", "chart": "numeric"}[value["strategy"]]
        require(value["evidence"]["kind"] == kind, "evidence_type", "Strategy/evidence mismatch")
        evidence_record({"id": "proposal", "kind": kind, "source_revision": "proposal", "locator": "proposal",
                         "verified": False, "data": value["evidence"]["data"]}, "proposal")
        array(value["references"], 1, 12)
        for ref in value["references"]:
            fields(ref, ("start", "end", "quote"))
            require(type(ref["start"]) is int and type(ref["end"]) is int and 0 <= ref["start"] < ref["end"] <= 2000,
                    "reference", "Invalid source span")
            text(ref["quote"], 2000)


def verify_proposal(snapshot, proposal):
    """Validate quotes and all facts; only list sequence may require teacher judgment.

    No open-ended factual approval: numeric/comparison proposals must exactly
    reproduce deterministic evidence. An inferred process may only arrange ALL
    explicit bullet items, with no new steps or causal interpretation.
    """
    value = proposal.to_dict()
    raw = snapshot.to_dict()["original_display_text"]
    refs = value["references"]
    for ref in refs:
        require(raw[ref["start"]:ref["end"]] == ref["quote"], "reference", "Supporting excerpt does not match saved source")
    require(len({(r["start"], r["end"]) for r in refs}) == len(refs), "reference", "Duplicate references")
    lines = source_lines(snapshot)
    require(all(any(r["start"] <= line["start"] and r["end"] >= line["end"] for r in refs) for line in lines),
            "coverage", "Every source line must remain available for review")
    extracted = extract_evidence(snapshot)
    kind, data = value["evidence"]["kind"], value["evidence"]["data"]
    exact = extracted["evidence"] and {"kind": extracted["evidence"][0]["kind"], "data": extracted["evidence"][0]["data"]} == value["evidence"]
    if not exact:
        require(kind == "process" and extracted["structure"] == "list", "unsupported_inference", "Only a complete explicit bullet list may propose an inferred order")
        labels = [re.fullmatch(r"[-*•] +(.+)", line["quote"])[1] for line in lines[1:]]
        require(2 <= len(labels) <= 4 and len(set(labels)) == len(labels), "process", "Ambiguous or oversized list")
        require(len(data["steps"]) == len(labels) and {s["label"] for s in data["steps"]} == set(labels),
                "unsupported_fact", "Proposed steps must match every authored bullet exactly")
    return {"evidence": _record(snapshot, kind, data), "supports": support(snapshot, refs),
            "warnings": ["Teacher must verify the proposed organization and sequence; arrows assert order only."] if not exact else
                        ["Verify the source excerpts and proposed structure before confirming."],
            "inferred": not bool(exact)}


def grounded_payload(snapshot, proposal=None, proposal_id=None):
    analysis = extract_evidence(snapshot) if proposal is None else verify_proposal(snapshot, proposal)
    evidence = analysis["evidence"] if proposal is None else [analysis["evidence"]]
    return {"schema_version": GROUNDING_VERSION, "extractor_version": EXTRACTOR_VERSION,
            "base_snapshot": snapshot.to_dict(), "evidence": evidence, "supports": analysis["supports"],
            "warnings": analysis["warnings"], "origin": "source_text" if proposal is None else "teacher_confirmed",
            "proposal_id": proposal_id}


def design_snapshot(payload):
    """Caller must independently verify grounding before using this conversion."""
    base = dict(payload["base_snapshot"])
    base["evidence"] = payload["evidence"]
    return SlideSourceSnapshot(base)
