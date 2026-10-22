"""Fixed educational templates. Text is escaped; no supplied SVG is executed."""
from html import escape
import math
from .schemas import DesignError, PREVIEW_VERSION, RENDERER_VERSION, VisualPreview, canonical_json, require
from .validation import FONT_FAMILY, text_width, validate_plan, validate_svg

INK, MUTED, ACCENT = "#172e46", "#42617a", "#147d92"
PALETTE = ("#147d92", "#5268b5", "#ad652e", "#4a8261")


def _n(value):
    require(math.isfinite(value), "geometry", "Nonfinite geometry")
    return str(round(value, 3)).rstrip("0").rstrip(".") if not float(value).is_integer() else str(int(value))


class _Canvas:
    def __init__(self):
        self.parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">']
        self.rect(0, 0, 1920, 1080, "#f4f7fb")
        self.rect(48, 48, 1824, 984, "#ffffff", radius=28)
        self.rect(96, 104, 8, 152, ACCENT, radius=4)
        self.line(112, 316, 1808, 316, "#d9e3ec", 2)

    def rect(self, x, y, width, height, color, radius=0, extra=""):
        self.parts.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(width)}" height="{_n(height)}" rx="{_n(radius)}" fill="{color}"{extra}/>')

    def line(self, x1, y1, x2, y2, color, width=4):
        self.parts.append(f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" stroke="{color}" stroke-width="{width}"/>')

    def text_box(self, label, x, y, width, height, size=36, bold=False, color=INK):
        """Wrap whitespace only; never truncate or split/alter an authored word."""
        if not label:
            return
        words = label.split()
        require(width > 0 and all(text_width(word, size) <= width for word in words), "text_overflow", "An unbreakable word cannot fit the text box")
        lines, current = [], ""
        for word in words:
            proposed = current + " " + word if current else word
            if text_width(proposed, size) > width:
                lines.append(current)
                current = word
            else:
                current = proposed
        if current:
            lines.append(current)
        line_height = size * 1.3
        require(bool(lines) and len(lines) * line_height <= height, "text_overflow", "Required text exceeds the available text-box height")
        baseline = y + size
        self.parts.append(f'<text x="{_n(x)}" y="{_n(baseline)}" font-size="{size}" font-family="{FONT_FAMILY}" font-weight="{700 if bold else 400}" fill="{color}" aria-label="{escape(label, quote=True)}">')
        for i, line in enumerate(lines):
            self.parts.append(f'<tspan x="{_n(x)}" y="{_n(baseline + i * line_height)}">{escape(line)}</tspan>')
        self.parts.append('</text>')

    def heading(self, text):
        self.text_box(text, 136, 108, 1640, 192, 48, True)

    def finish(self):
        return "".join(self.parts) + "</svg>"


def _flowchart(canvas, layout):
    ordered = {step["id"]: step["label"] for step in layout["steps"]}
    count = len(layout["order"])
    require(count <= 4, "layout_limit", "Phase 1 supports at most four visible process steps")
    gap = 64
    width = (1696 - gap * (count - 1)) / count
    for i, step_id in enumerate(layout["order"]):
        x = 112 + i * (width + gap)
        color = PALETTE[i]
        canvas.rect(x, 392, width, 492, "#edf4f8", radius=20)
        canvas.rect(x + 24, 420, width - 48, 8, color, radius=4)
        canvas.text_box(ordered[step_id], x + 28, 476, width - 56, 360, 44, True)
        if i < count - 1:
            left, right = x + width + 8, x + width + gap - 12
            canvas.line(left, 638, right, 638, ACCENT, 4)
            canvas.parts.append(f'<polygon points="{_n(right)},626 {_n(right + 12)},638 {_n(right)},650" fill="{ACCENT}"/>')


def _comparison(canvas, layout):
    category_x, left_x, right_x = 112, 568, 1200
    widths = (424, 600, 600)
    for i, item in enumerate(layout["items"]):
        x = (left_x, right_x)[i]
        canvas.rect(x, 356, widths[i + 1], 132, PALETTE[i], radius=16)
        canvas.text_box(item["label"], x + 24, 376, widths[i + 1] - 48, 100, 40, True, "#ffffff")
    row_height = min(212, 472 / len(layout["categories"]))
    for row, category in enumerate(layout["categories"]):
        y = 512 + row * row_height
        canvas.rect(category_x, y, widths[0], row_height - 12, "#e6eef5", radius=12)
        canvas.text_box(category, category_x + 20, y + 18, widths[0] - 40, row_height - 48, 34, True)
        for col, item in enumerate(layout["items"]):
            x = (left_x, right_x)[col]
            canvas.rect(x, y, widths[col + 1], row_height - 12, "#f0f5f9", radius=12)
            canvas.text_box(item["values"][row], x + 24, y + 18, widths[col + 1] - 48, row_height - 48, 36)


def _chart(canvas, layout):
    canvas.text_box(layout["measure"], 112, 344, 1280, 80, 34, True)
    canvas.text_box(layout["unit"], 1488, 344, 312, 80, 30, color=MUTED)
    points = layout["points"]
    maximum = max(p["value"] for p in points)
    row_height = 504 / len(points)
    # Zero is the shared origin. Values are never normalized into new data labels.
    canvas.line(560, 438, 560, 960, "#a0b3c3", 2)
    for i, point in enumerate(points):
        y = 448 + i * row_height
        canvas.text_box(point["label"], 112, y + 8, 412, row_height - 24, 32)
        bar_height = min(100, row_height - 32)
        canvas.rect(560, y, 1056, bar_height, "#e6eef5", radius=8)
        width = point["value"] / maximum * 1056
        require(point["value"] == 0 or width >= 2, "chart_resolution", "A nonzero value is below the visible bar resolution")
        # data-point is structural metadata, not authoritative numeric evidence.
        canvas.rect(560, y, width, bar_height, PALETTE[i % len(PALETTE)], radius=0, extra=f' data-point="{i}"')
        canvas.text_box(canonical_json(point["value"]), 1652, y + 8, 148, row_height - 24, 36, True)


def render_preview(snapshot, plan):
    """Return an invalid result with no SVG on semantic, resource or layout failure."""
    data = plan.to_dict()
    result = {
        "schema_version": PREVIEW_VERSION,
        "source_hash": snapshot.content_hash,
        "plan_hash": plan.content_hash,
        "renderer_version": RENDERER_VERSION,
        "width": 1920, "height": 1080,
        "svg": None, "status": "invalid", "warnings": [], "errors": [],
        "contains_text": False, "strategy": data["strategy"],
    }
    checks = validate_plan(snapshot, plan)
    if not checks.valid:
        result["errors"] = list(checks.errors)
    elif data["strategy"] == "preserve_original":
        result["status"] = "preserved"
        if data["layout"]["asset_ref"] is None:
            result["warnings"] = ["No original asset reference is available; no replacement preview was generated."]
    else:
        try:
            canvas = _Canvas()
            layout = data["layout"]
            if data["strategy"] == "typography_only":
                canvas.text_box(layout["text"], 136, 128, 1640, 800, 48)
            else:
                canvas.heading(layout["heading"])
                {"flowchart": _flowchart, "comparison": _comparison, "chart": _chart}[data["strategy"]](canvas, layout)
            svg = canvas.finish()
            checks = validate_svg(svg, plan)
            if checks.valid:
                result.update(svg=svg, status="valid", contains_text=True, warnings=list(checks.warnings))
            else:
                result["errors"] = list(checks.errors)
        except DesignError as exc:
            result["errors"] = [f"{exc.code}: {exc}"]
    return VisualPreview(result)
