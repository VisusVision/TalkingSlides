from __future__ import annotations

import json
from pathlib import Path

from ..models import CheckRun
from .sanitize import sanitize_data, sanitize_text


def _safe_payload(run: CheckRun) -> dict[str, object]:
    return sanitize_data(run.to_dict())


def render_json(run: CheckRun) -> str:
    return json.dumps(_safe_payload(run), indent=2, ensure_ascii=False) + "\n"


def render_markdown(run: CheckRun) -> str:
    payload = _safe_payload(run)
    lines = [
        "# TalkingSlides Setup Assistant Diagnostic Report",
        "",
        f"- Version: {payload['application_version']}",
        f"- Platform: {payload['platform']}",
        f"- Profile: {payload['profile']}",
        f"- Mode: {payload['mode']}",
        f"- Overall status: {str(payload['status']).upper()}",
        f"- Duration: {payload['duration_ms']} ms",
        "",
        "| Status | Category | Check | Summary |",
        "|---|---|---|---|",
    ]
    for result in payload["results"]:
        summary = sanitize_text(str(result["summary"])).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {str(result['status']).upper()} | {result['category']} | {result['title']} | {summary} |")
    lines.extend(("", "## Details", ""))
    for result in payload["results"]:
        if result.get("technical_details") or result.get("remediation"):
            lines.append(f"### {result['title']}")
            lines.append("")
            if result.get("technical_details"):
                lines.append(f"Technical details: {result['technical_details']}")
            if result.get("remediation"):
                lines.append(f"Suggested fix: {result['remediation']}")
            lines.append("")
    lines.append("Secret-like values and authorization data are redacted.")
    return "\n".join(lines).rstrip() + "\n"


def render_text(run: CheckRun) -> str:
    payload = _safe_payload(run)
    lines = [
        "TalkingSlides Setup Assistant",
        f"Platform: {payload['platform']} | Profile: {payload['profile']} | Mode: {payload['mode']}",
        f"Overall: {str(payload['status']).upper()} | Duration: {payload['duration_ms']} ms",
        "",
    ]
    for result in payload["results"]:
        lines.append(f"[{str(result['status']).upper():7}] {result['category']} / {result['title']}: {result['summary']}")
        if result.get("remediation"):
            lines.append(f"          Suggested fix: {result['remediation']}")
    lines.extend(("", "Secret-like values and authorization data are redacted."))
    return "\n".join(lines) + "\n"


def write_report(run: CheckRun, target: Path, report_format: str) -> Path:
    renderers = {"json": render_json, "markdown": render_markdown, "md": render_markdown, "text": render_text, "txt": render_text}
    try:
        renderer = renderers[report_format.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported report format: {report_format}") from exc
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(renderer(run), encoding="utf-8")
    return target
