"""Independent semantic and SVG checks, never LLM self-review."""
from collections import Counter
from dataclasses import dataclass
import math
import re
import xml.etree.ElementTree as ET
from .evidence import backed_layout, required_labels
from .schemas import DesignError, canonical_json, require

WIDTH, HEIGHT = 1920, 1080
# Fixed conservative advance bounds for DejaVu Sans regular/bold. No font I/O.
# Repertoire is restricted by schemas.text; changing these bounds needs a renderer bump.
def text_width(value, size):
    narrow = " !\"'(),.:;I[]`ijl|ı\u00a0"
    wide = "MWmw@%&—…"
    return sum(0.55 if char in narrow else 1.2 if char in wide else 0.9 for char in value) * size

FONT_FAMILY = "DejaVu Sans"


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple = ()
    warnings: tuple = ()

    @property
    def valid(self):
        return not self.errors


def validate_plan(snapshot, plan):
    data = plan.to_dict()
    try:
        require(data["source_identity"] == snapshot.identity and data["source_hash"] == snapshot.content_hash, "stale_source", "Plan identity/hash does not match this source revision")
        require(data["strategy"] != "typography_only" or bool(snapshot.to_dict()["original_display_text"].strip()), "required_content", "Empty source text cannot produce a text preview")
        expected = backed_layout(snapshot, data["strategy"], data["evidence_refs"])
        require(canonical_json(data["layout"]) == canonical_json(expected), "evidence_mismatch", "Layout differs from authoritative source evidence")
        require(data["required_labels"] == required_labels(data["strategy"], expected), "required_content", "Required labels must exactly match source-backed content")
    except DesignError as exc:
        return ValidationResult((f"{exc.code}: {exc}",))
    return ValidationResult()


def _number(raw):
    try:
        value = float(raw)
    except (ValueError, TypeError) as exc:
        raise DesignError("geometry", "Invalid numeric geometry") from exc
    require(math.isfinite(value), "geometry", "Nonfinite SVG geometry")
    return value


def _bounds(x, y, width=0, height=0):
    require(0 <= x <= WIDTH and 0 <= y <= HEIGHT and 0 <= width <= WIDTH and 0 <= height <= HEIGHT and x + width <= WIDTH and y + height <= HEIGHT, "geometry", "SVG geometry is outside the canvas")


def normalized_text(value):
    return " ".join(value.split())


def _expected_text_positions(data):
    """Independently bind labels to template cells/steps, not just a label bag."""
    layout, strategy = data["layout"], data["strategy"]
    if strategy == "typography_only":
        return [(layout["text"], 136, 176)]
    expected = [(layout["heading"], 136, 156)] if layout.get("heading") else []
    if strategy == "flowchart":
        width = (1696 - 64 * (len(layout["order"]) - 1)) / len(layout["order"])
        by_id = {step["id"]: step["label"] for step in layout["steps"]}
        expected.extend((by_id[key], 140 + i * (width + 64), 520) for i, key in enumerate(layout["order"]))
    elif strategy == "comparison":
        expected.extend((item["label"], (592, 1224)[i], 416) for i, item in enumerate(layout["items"]))
        row_height = min(212, 472 / len(layout["categories"]))
        for row, category in enumerate(layout["categories"]):
            expected.append((category, 132, 564 + row * row_height))
            expected.extend((item["values"][row], (592, 1224)[col], 566 + row * row_height) for col, item in enumerate(layout["items"]))
    elif strategy == "chart":
        expected.extend(((layout["measure"], 112, 378), (layout["unit"], 1488, 374)))
        row_height = 504 / len(layout["points"])
        for i, point in enumerate(layout["points"]):
            expected.extend(((point["label"], 112, 488 + i * row_height), (canonical_json(point["value"]), 1652, 492 + i * row_height)))
    return expected


def validate_svg(svg, plan=None):
    """Accept only the renderer's tiny SVG dialect, including visible label checks."""
    try:
        require(type(svg) is str and len(svg.encode("utf-8")) <= 50_000, "svg_limit", "SVG exceeds resource limit")
        require(not re.search(r"<!|<\?|url\s*\(|(?:javascript|data)\s*:", svg, re.I), "unsafe_svg", "Active/external SVG syntax is forbidden")
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        require(root.tag == ns + "svg" and root.attrib == {"width": "1920", "height": "1080", "viewBox": "0 0 1920 1080"}, "svg_root", "Unexpected SVG canvas")
        common = {"fill", "stroke", "stroke-width"}
        allowed = {
            "rect": common | {"x", "y", "width", "height", "rx", "data-point"},
            "line": common | {"x1", "y1", "x2", "y2"},
            "polygon": common | {"points"},
            "text": {"x", "y", "font-size", "font-family", "font-weight", "fill", "aria-label"},
            "tspan": {"x", "y"},
        }
        elements = list(root.iter())
        require(len(elements) <= 600, "svg_limit", "Too many SVG elements")
        require(not (root.text or "").strip(), "unsafe_svg", "Unexpected root text")
        parents = {child: parent for parent in elements for child in parent}
        labels, bars = [], {}
        for element in elements[1:]:
            tag = element.tag.removeprefix(ns)
            require(element.tag.startswith(ns) and tag in allowed, "unsafe_svg", "Unsupported SVG operation")
            require(element.attrib.keys() <= allowed[tag], "unsafe_svg", "Unsupported SVG attribute")
            for key in ("fill", "stroke"):
                if key in element.attrib:
                    require(re.fullmatch(r"#[0-9a-f]{6}|none", element.attrib[key]) is not None, "unsafe_svg", "Only literal colors are allowed")
            if "stroke-width" in element.attrib:
                require(0 <= _number(element.attrib["stroke-width"]) <= 8, "geometry", "Unsupported stroke")
            attr = element.attrib
            require(not (element.tail or "").strip(), "unsafe_svg", "Unexpected text outside a label")
            if tag != "tspan":
                require(parents[element] is root, "unsafe_svg", "Unexpected nested operation")
            if tag not in ("text", "tspan"):
                require(not list(element) and not (element.text or "").strip(), "unsafe_svg", "Unexpected element content")
            if tag == "rect":
                _bounds(*[_number(attr.get(k)) for k in ("x", "y", "width", "height")])
                if "data-point" in attr:
                    point_id = attr["data-point"]
                    require(point_id not in bars, "chart_geometry", "Duplicate chart bar")
                    bars[point_id] = attr
                if "rx" in attr:
                    require(0 <= _number(attr["rx"]) <= 40, "geometry", "Unsupported corner radius")
            elif tag == "line":
                _bounds(_number(attr.get("x1")), _number(attr.get("y1")))
                _bounds(_number(attr.get("x2")), _number(attr.get("y2")))
                margin = _number(attr.get("stroke-width", "0")) / 2
                require(all(margin <= _number(attr.get(k)) <= limit - margin for k, limit in (("x1", WIDTH), ("x2", WIDTH), ("y1", HEIGHT), ("y2", HEIGHT))), "geometry", "Line stroke would clip")
            elif tag == "polygon":
                points = attr.get("points", "").split()
                require(len(points) == 3, "geometry", "Only triangle arrows are supported")
                for point in points:
                    xy = point.split(",")
                    require(len(xy) == 2, "geometry", "Invalid arrow coordinates")
                    _bounds(*map(_number, xy))
            elif tag == "text":
                size = _number(attr.get("font-size"))
                require(28 <= size <= 64 and attr.get("font-family") == FONT_FAMILY and attr.get("font-weight") in ("400", "700"), "typography", "Unsupported font configuration")
                require(all(child.tag == ns + "tspan" for child in element) and len(element) > 0 and not (element.text or "").strip(), "text", "Text must use explicit positioned lines")
                label = attr.get("aria-label")
                require(type(label) is str and normalized_text(" ".join(c.text or "" for c in element)) == normalized_text(label), "required_content", "Visible text differs from original label")
                labels.append(label)
                x, y = _number(attr.get("x")), _number(attr.get("y"))
                _bounds(x, y)
                previous_y = None
                for child in element:
                    cx, cy = _number(child.attrib.get("x")), _number(child.attrib.get("y"))
                    require(cx == x and (previous_y is None and cy == y or previous_y is not None and cy >= previous_y + size * 1.25), "geometry", "Inconsistent text lines")
                    _bounds(cx, cy - size, text_width(child.text or "", size), size * 1.3)
                    previous_y = cy
            elif tag == "tspan":
                require(parents[element].tag == ns + "text" and not list(element), "unsafe_svg", "Unexpected nested text")
        data = plan.to_dict() if plan is not None else None
        if data is not None:
            texts = root.findall(ns + "text")
            expected = _expected_text_positions(data)
            require(len(texts) == len(expected), "required_content", "Unexpected template text count")
            for element, (label, x, y) in zip(texts, expected):
                require(element.attrib.get("aria-label") == label and abs(_number(element.attrib.get("x")) - x) <= 0.001 and abs(_number(element.attrib.get("y")) - y) <= 0.001, "label_position", "A label is not in its source-backed template cell")
            arrows = root.findall(ns + "polygon")
            if data["strategy"] == "flowchart":
                count = len(data["layout"]["order"])
                require(len(arrows) == count - 1, "relationship", "Missing/extra sequence arrows")
                width = (1696 - 64 * (count - 1)) / count
                for i, arrow in enumerate(arrows):
                    right = 112 + i * (width + 64) + width + 52
                    coords = [tuple(map(_number, p.split(","))) for p in arrow.attrib["points"].split()]
                    expected_arrow = ((right, 626), (right + 12, 638), (right, 650))
                    require(all(abs(a - b) <= 0.001 for xy, target in zip(coords, expected_arrow) for a, b in zip(xy, target)), "relationship", "Sequence arrow direction/position changed")
            else:
                require(not arrows, "relationship", "Unexpected relationship arrows")
        if data is not None and data["strategy"] == "chart":
            points = data["layout"]["points"]
            require(set(bars) == {str(i) for i in range(len(points))}, "chart_geometry", "Missing or extra chart bars")
            maximum = max(p["value"] for p in points)
            require(maximum > 0, "chart_domain", "Chart requires a positive maximum")
            for i, point in enumerate(points):
                require(point["value"] == 0 or point["value"] / maximum * 1056 >= 2, "chart_resolution", "A nonzero value is below the visible bar resolution")
                expected = {"x": 560, "y": 448 + i * (504 / len(points)), "width": point["value"] / maximum * 1056, "height": min(100, 504 / len(points) - 32)}
                require(all(abs(_number(bars[str(i)].get(k)) - v) <= 0.001 for k, v in expected.items()), "chart_geometry", "Chart bar does not represent its evidence value")
        elif data is not None:
            require(not bars, "chart_geometry", "Chart bars outside a chart")
        require(bool(labels), "required_content", "SVG must contain visible labels")
        if data is not None:
            require(Counter(labels) == Counter(data["required_labels"]), "required_content", "Missing, duplicated or invented labels")
    except (DesignError, ET.ParseError, UnicodeEncodeError) as exc:
        code = exc.code if isinstance(exc, DesignError) else "xml"
        return ValidationResult((f"{code}: {exc}",))
    return ValidationResult(warnings=("Font-dependent pixels: use DejaVu Sans; SVG bounds use a conservative Latin glyph advance, not arbitrary-font shaping.",))
