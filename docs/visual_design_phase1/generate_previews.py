"""Rebuild synthetic review artifacts; never reads projects or writes media."""
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))
from core.visual_design import DeterministicPlanner, SlideSourceSnapshot, render_preview  # noqa: E402


def main():
    output = Path(__file__).resolve().parent / "previews"
    output.mkdir(exist_ok=True)
    names = ("process", "comparison", "numerical_chart", "preserve_original", "insufficient_evidence")
    examples, cards = [], []
    sheet = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1840" viewBox="0 0 1920 1840">', '<rect width="1920" height="1840" fill="#e8eef5"/>']
    for i, name in enumerate(names):
        source = SlideSourceSnapshot.from_json((ROOT / "tests" / "fixtures" / "visual_design" / (name + ".json")).read_text(encoding="utf-8"))
        plan = DeterministicPlanner().plan(source)
        preview = render_preview(source, plan)
        data = preview.to_dict()
        filename = name + ".svg" if data["svg"] else None
        if filename:
            (output / filename).write_text(data["svg"], encoding="utf-8")
        examples.append({"fixture": name, "strategy": data["strategy"], "svg_file": filename, "validation": data["status"], "rationale": plan.to_dict()["rationale"], "fallback_reason": plan.to_dict()["fallback_reason"], "source_hash": source.content_hash, "plan_hash": plan.content_hash, "preview_hash": preview.content_hash, "warnings": data["warnings"], "errors": data["errors"]})
        label = f'{name} / {data["strategy"]} / {data["status"]}'
        image = f'<img src="{filename}" alt="{escape(label)}">' if filename else '<div class="preserved">Original retained. No replacement SVG.<br>The fixture has an opaque asset reference; no real source image is included.</div>'
        cards.append(f'<article><h2>{escape(name)}</h2><p class="status">{escape(data["strategy"])} · {escape(data["status"])}</p>{image}<p>{escape(plan.to_dict()["rationale"])}</p></article>')
        x, y = 40 + (i % 2) * 960, 32 + (i // 2) * 600
        sheet.append(f'<text x="{x}" y="{y + 28}" font-family="DejaVu Sans" font-size="24" fill="#172e46">{escape(label)}</text>')
        if data["svg"]:
            body = data["svg"].split(">", 1)[1].rsplit("</svg>", 1)[0]
            sheet.append(f'<g transform="translate({x},{y + 52}) scale(0.46)">{body}</g>')
        else:
            sheet.append(f'<rect x="{x}" y="{y + 52}" width="883" height="497" rx="12" fill="#ffffff"/><text x="{x + 48}" y="{y + 228}" font-family="DejaVu Sans" font-size="28" fill="#172e46">Original retained; no replacement SVG.</text>')
    sheet.append('</svg>')
    (output / "contact_sheet.svg").write_text("".join(sheet), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps({"schema_version": "visual-fixture-manifest.v1", "examples": examples}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TalkingSlides Phase 1 visual previews</title><style>body{margin:0;background:#e8eef5;color:#172e46;font:16px system-ui,sans-serif}main{max-width:1600px;padding:24px;margin:auto}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:24px}article{background:white;padding:20px;border-radius:16px}h1{font-size:28px}h2{font-size:22px}.status{color:#42617a}img{width:100%;height:auto}.preserved{min-height:240px;display:grid;place-content:center;background:#f4f7fb;padding:24px}p{line-height:1.5}</style><main><h1>TalkingSlides · Phase 1 previews</h1><p>Synthetic evidence only. No video, draft, narration or published media changes. Open this file locally with the SVGs alongside it.</p><section>' + "".join(cards) + '</section></main></html>\n'
    (output / "index.html").write_text(html, encoding="utf-8")
    for example in examples:
        print(f'{example["fixture"]}: {example["strategy"]} / {example["validation"]}')
    if any(example["validation"] == "invalid" for example in examples):
        raise SystemExit("At least one fixture could not render")


if __name__ == "__main__":
    main()
