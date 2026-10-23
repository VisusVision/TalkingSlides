"""Reproduce bounded synthetic grounded previews without Django/network/models."""
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/api"))
from core.visual_design import SlideSourceSnapshot, DeterministicPlanner, render_preview
from core.visual_design.schemas import SOURCE_VERSION, content_hash
from core.visual_design.grounding import grounded_payload, design_snapshot, VisualProposal, PROPOSAL_VERSION


def main():
    examples = json.loads((ROOT / "tests/fixtures/visual_design/grounded/examples.json").read_text())
    output = Path(__file__).resolve().parent
    manifest = []
    for example in examples + [{"name": "teacher_confirmed", "text": "Process ideas\n- Collect sample\n- Record value\n- Check result"}]:
        source = SlideSourceSnapshot({"schema_version": SOURCE_VERSION, "project_id": "grounded-examples",
            "page_key": example["name"] + ".source-0.split-1", "source_slide_index": 0, "split_index": 1,
            "original_display_text": example["text"], "protected_narration": "Narration preserved verbatim.",
            "source_revision": content_hash("phase2c-example", example["text"]), "evidence": []})
        proposal = None
        if example["name"] == "teacher_confirmed":
            labels = [line[2:] for line in example["text"].splitlines()[1:]]
            proposal = VisualProposal({"schema_version": PROPOSAL_VERSION, "strategy": "flowchart",
                "rationale": "Synthetic teacher-confirmed sequence, not automatically inferred evidence.",
                "evidence": {"kind": "process", "data": {"steps": [{"id": f"step-{i+1}", "label": label} for i, label in enumerate(labels)],
                    "order": [f"step-{i+1}" for i in range(len(labels))], "relationship": "sequence"}},
                "references": [{"start": 0, "end": len(example["text"]), "quote": example["text"]}]})
        bundle = grounded_payload(source, proposal, "synthetic-confirmation" if proposal else None)
        grounded = design_snapshot(bundle)
        plan = DeterministicPlanner().plan(grounded)
        preview = render_preview(grounded, plan)
        result = preview.to_dict()
        assert result["status"] == "valid", result
        name = example["name"] + ".svg"
        (output / name).write_text(result["svg"], encoding="utf-8")
        manifest.append({"fixture": example["name"], "source_text": example["text"], "strategy": result["strategy"],
            "file": name, "status": result["status"], "source_hash": source.content_hash,
            "design_source_hash": grounded.content_hash, "plan_hash": plan.content_hash, "preview_hash": preview.content_hash,
            "rationale": plan.to_dict()["rationale"], "origin": bundle["origin"], "supports": bundle["supports"], "warnings": bundle["warnings"]})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards = ''.join('<figure><img src="'+m["file"]+'" alt="'+m["fixture"]+'"><figcaption><h2>'+m["fixture"]+'</h2><p>'+m["strategy"]+' · '+m["status"]+'</p><p>'+escape(m["rationale"])+ '</p></figcaption></figure>' for m in manifest)
    (output / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Grounded visual previews</title><style>body{font:16px system-ui;background:#edf2f7;margin:2rem;color:#172e46}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:24px}figure{margin:0;background:white;padding:16px;border-radius:16px}img{width:100%;aspect-ratio:16/9}p{line-height:1.5}</style><h1>Phase 2C · Grounded visual previews</h1><p>Synthetic source assertions. No live model, narration changes or video rendering.</p><main>'+cards+'</main></html>', encoding="utf-8")
    sheet = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1200" viewBox="0 0 1920 1200"><rect width="1920" height="1200" fill="#e8eef5"/>']
    for i, name in enumerate(("process_tr", "comparison_en", "dataset_tr", "teacher_confirmed")):
        x, y = (i % 2) * 960 + 24, (i // 2) * 600 + 16
        svg = (output / (name + ".svg")).read_text(encoding="utf-8")
        sheet.append(f'<text x="{x}" y="{y+24}" font-family="DejaVu Sans" font-size="24" fill="#172e46">{name}</text>')
        sheet.append(f'<g transform="translate({x},{y+48}) scale(0.475)">'+svg.split(">", 1)[1].rsplit("</svg>", 1)[0]+"</g>")
    (output / "contact_sheet.svg").write_text("".join(sheet)+"</svg>", encoding="utf-8")
    print(f"Rendered {len(manifest)} deterministic examples")


if __name__ == "__main__":
    main()
